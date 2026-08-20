# -*- coding: utf-8 -*-
"""统一元素定位器：四模融合 + 置信度评估 + 自恢复。

策略优先级：DOM → UIA → OCR → VLM，按平台选择性启用。
借鉴 Playwright Locator / Appium 多策略查找 / SWE-Agent 视觉 grounding。
"""
from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

from element_confidence import ElementConfidence, ElementResult

if TYPE_CHECKING:
    pass


class ElementContext:
    """元素定位上下文：聚合屏幕状态和历史信息。"""

    def __init__(
        self,
        platform: str = "desktop",
        screenshot: Optional[bytes] = None,
        ocr_texts: Optional[List[Dict[str, Any]]] = None,
        ocr_blocks: Optional[List[Dict[str, Any]]] = None,
        dom_tree: Optional[Dict[str, Any]] = None,
        uia_root: Optional[Any] = None,
        window_title: str = "",
        screen_size: Optional[Tuple[int, int]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        self.platform = platform
        self.screenshot = screenshot
        self.ocr_texts = ocr_texts or []
        self.ocr_blocks = ocr_blocks or []
        self.dom_tree = dom_tree
        self.uia_root = uia_root
        self.window_title = window_title
        self.screen_size = screen_size or (1920, 1080)
        self.metadata = metadata or {}

    @property
    def width(self) -> int:
        return self.screen_size[0]

    @property
    def height(self) -> int:
        return self.screen_size[1]


class BaseElementStrategy:
    """元素定位策略基类。"""
    name: str = "base"
    enabled: bool = True

    def find(self, description: str, context: ElementContext) -> List[ElementResult]:
        raise NotImplementedError

    def _empty(self) -> List[ElementResult]:
        return []


class DOMElementStrategy(BaseElementStrategy):
    """Web DOM 元素定位：CSS/XPath/Role/Text。"""
    name = "dom"
    enabled = True

    def find(self, description: str, context: ElementContext) -> List[ElementResult]:
        if context.platform not in ("web", "browser"):
            return self._empty()
        dom = context.dom_tree
        if not dom:
            return self._empty()
        results: List[ElementResult] = []
        results.extend(self._find_by_text(description, dom))
        results.extend(self._find_by_role(description, dom))
        results.extend(self._find_by_css(description, dom))
        return results

    def _find_by_text(self, desc: str, dom: Dict[str, Any]) -> List[ElementResult]:
        results: List[ElementResult] = []
        desc_lower = desc.lower().strip()
        candidates = dom.get("text_candidates", [])
        for cand in candidates:
            text = str(cand.get("text", "")).lower()
            score = ElementConfidence.score_candidate_match(desc, text)
            if score >= 0.5:
                results.append(ElementResult(
                    x=int(cand.get("x", 0)),
                    y=int(cand.get("y", 0)),
                    width=int(cand.get("width", 0)),
                    height=int(cand.get("height", 0)),
                    confidence=score,
                    source="dom",
                    strategy="text_exact" if score >= 0.95 else "text_fuzzy",
                    text=str(cand.get("text", "")),
                    metadata={"selector_type": "text"},
                ))
        results.sort(key=lambda r: r.confidence, reverse=True)
        return results

    def _find_by_role(self, desc: str, dom: Dict[str, Any]) -> List[ElementResult]:
        results: List[ElementResult] = []
        desc_lower = desc.lower().strip()
        elements = dom.get("elements", [])
        for el in elements:
            role = str(el.get("role", "")).lower()
            name = str(el.get("name", "")).lower()
            if not role:
                continue
            score = 0.0
            if desc_lower in name or name in desc_lower:
                score = 0.9
            elif ElementConfidence.score_candidate_match(desc, name) >= 0.6:
                score = 0.75
            if score >= 0.5:
                results.append(ElementResult(
                    x=int(el.get("x", 0)),
                    y=int(el.get("y", 0)),
                    width=int(el.get("width", 0)),
                    height=int(el.get("height", 0)),
                    confidence=score,
                    source="dom",
                    strategy="role_exact" if score >= 0.9 else "role_fuzzy",
                    text=name,
                    metadata={"selector_type": "role", "role": role},
                ))
        results.sort(key=lambda r: r.confidence, reverse=True)
        return results

    def _find_by_css(self, desc: str, dom: Dict[str, Any]) -> List[ElementResult]:
        return self._empty()


class UIAElementStrategy(BaseElementStrategy):
    """UIA 元素定位：accessibility name / control type / deep search。"""
    name = "uia"
    enabled = True

    def find(self, description: str, context: ElementContext) -> List[ElementResult]:
        if context.platform not in ("desktop", "mobile"):
            return self._empty()
        if not context.uia_root:
            return self._empty()
        try:
            return self._find_in_uia(description, context)
        except Exception:
            return self._empty()

    def _find_in_uia(self, desc: str, context: ElementContext) -> List[ElementResult]:
        results: List[ElementResult] = []
        try:
            from desktop_uia_core import find_elements_by_description
            raw = find_elements_by_description(desc, root=context.uia_root, max_depth=12)
            for item in raw:
                score = float(item.get("score", 0) or 0)
                confidence = self._score_to_confidence(score, item)
                results.append(ElementResult(
                    x=int(item.get("x", 0)),
                    y=int(item.get("y", 0)),
                    width=int(item.get("width", 0)),
                    height=int(item.get("height", 0)),
                    confidence=confidence,
                    source="uia",
                    strategy=item.get("strategy", "exact"),
                    text=str(item.get("name", "")),
                    metadata={"control_type": item.get("control_type", ""),
                               "automation_id": item.get("automation_id", "")},
                ))
        except ImportError:
            pass
        except Exception:
            pass
        results.sort(key=lambda r: r.confidence, reverse=True)
        return results

    @staticmethod
    def _score_to_confidence(score: float, item: Dict[str, Any]) -> float:
        if score >= 0.95:
            return 0.95
        if score >= 0.8:
            return 0.88
        if score >= 0.65:
            return 0.75
        if score >= 0.5:
            return 0.65
        return max(0.45, score * 0.9)


class OCRElementStrategy(BaseElementStrategy):
    """OCR 文本匹配：语义扩展 + 位置先验 + 模糊匹配。"""
    name = "ocr"
    enabled = True

    def find(self, description: str, context: ElementContext) -> List[ElementResult]:
        if not context.ocr_texts:
            return self._empty()
        expanded = ElementConfidence.semantic_expand(description)
        pos_hint = ElementConfidence.extract_position_hint(description)
        results: List[ElementResult] = []
        for text_item in context.ocr_texts:
            text = str(text_item.get("text", "")).strip()
            if not text:
                continue
            max_score = 0.0
            best_alias = ""
            for alias in expanded:
                score = ElementConfidence.score_candidate_match(alias, text, partial=True)
                if score > max_score:
                    max_score = score
                    best_alias = alias
            if max_score >= 0.45:
                x = int(text_item.get("x", 0))
                y = int(text_item.get("y", 0))
                w = int(text_item.get("width", 0))
                h = int(text_item.get("height", 0))
                if pos_hint:
                    pos_bonus = self._position_bonus(pos_hint, x, y, w, h, context)
                    max_score = min(1.0, max_score + pos_bonus)
                strategy = "ocr_exact" if max_score >= 0.8 else ("ocr_semantic" if max_score >= 0.65 else "ocr_fuzzy")
                confidence_map = {"ocr_exact": 0.75, "ocr_semantic": 0.65, "ocr_fuzzy": 0.55, "ocr_partial": 0.5}
                confidence = confidence_map.get(strategy, 0.5)
                results.append(ElementResult(
                    x=x, y=y, width=w, height=h,
                    confidence=confidence,
                    source="ocr",
                    strategy=strategy,
                    text=text,
                    metadata={"matched_alias": best_alias, "raw_score": max_score},
                ))
        results.sort(key=lambda r: r.confidence, reverse=True)
        return results

    @staticmethod
    def _position_bonus(pos: str, x: int, y: int, w: int, h: int, ctx: ElementContext) -> float:
        cx = x + w / 2.0
        cy = y + h / 2.0
        W, H = float(ctx.width), float(ctx.height)
        if W <= 0 or H <= 0:
            return 0.0
        pos_map = {
            "top_left": (cx < W * 0.35 and cy < H * 0.35),
            "top_right": (cx > W * 0.65 and cy < H * 0.35),
            "bottom_right": (cx > W * 0.55 and cy > H * 0.55),
            "bottom_left": (cx < W * 0.45 and cy > H * 0.55),
            "center": (abs(cx - W / 2) < W * 0.2 and abs(cy - H / 2) < H * 0.2),
            "top_bar": (cy < H * 0.15),
            "bottom_bar": (cy > H * 0.85),
            "left_sidebar": (cx < W * 0.2),
            "right_sidebar": (cx > W * 0.8),
        }
        return 0.15 if pos_map.get(pos, False) else 0.0


class VLMGroundingStrategy(BaseElementStrategy):
    """VLM 视觉 grounding：截图 + 多模态理解。"""
    name = "vlm"
    enabled = True

    def find(self, description: str, context: ElementContext) -> List[ElementResult]:
        if not context.screenshot:
            return self._empty()
        if context.platform not in ("desktop", "mobile", "web", "browser"):
            return self._empty()
        try:
            from vlm_grounding import get_vlm
            vlm = get_vlm()
            if not vlm.is_available():
                return self._empty()
            result = vlm.find_element(context.screenshot, description)
            if not result:
                return self._empty()
            x = int(result.get("x", 0))
            y = int(result.get("y", 0))
            w = int(result.get("width", 0))
            h = int(result.get("height", 0))
            confidence = float(result.get("confidence", 0.5))
            if confidence >= 0.6:
                strategy = "vlm_grounding_high"
            elif confidence >= 0.5:
                strategy = "vlm_grounding_mid"
            else:
                strategy = "vlm_grounding_low"
            return [ElementResult(
                x=x, y=y, width=w, height=h,
                confidence=confidence,
                source="vlm",
                strategy=strategy,
                text=str(result.get("text", "")),
                metadata={"vlm_model": result.get("model", ""),
                           "vlm_raw_confidence": result.get("raw_confidence", 0)},
            )]
        except ImportError:
            return self._empty()
        except Exception:
            return self._empty()


_PLATFORM_STRATEGIES: Dict[str, List[str]] = {
    "web": ["dom", "ocr", "vlm"],
    "browser": ["dom", "ocr", "vlm"],
    "desktop": ["uia", "ocr", "vlm"],
    "mobile": ["uia", "ocr", "vlm"],
}


class UnifiedElementLocator:
    """统一元素定位器：四模融合 + 置信度评估 + 自恢复重试。"""

    def __init__(self, enable_vlm: bool = True, max_retries: int = 2):
        self._strategies: Dict[str, BaseElementStrategy] = {
            "dom": DOMElementStrategy(),
            "uia": UIAElementStrategy(),
            "ocr": OCRElementStrategy(),
            "vlm": VLMGroundingStrategy(),
        }
        self._enable_vlm = enable_vlm
        self._max_retries = max_retries

    def find_element(
        self,
        description: str,
        context: ElementContext,
        auto_retry: bool = True,
    ) -> ElementResult:
        """查找元素。返回最佳匹配结果。"""
        if not description or not description.strip():
            return ElementResult(confidence=0)
        strategies = self._get_strategies(context.platform)
        all_candidates: List[ElementResult] = []
        for strat_name in strategies:
            if strat_name == "vlm" and not self._enable_vlm:
                continue
            strat = self._strategies.get(strat_name)
            if not strat or not strat.enabled:
                continue
            candidates = strat.find(description, context)
            all_candidates.extend(candidates)
            for cand in candidates:
                if cand.confidence >= self._threshold_for(strat_name, cand.strategy):
                    self._log_hit(description, cand)
                    return cand
        if all_candidates:
            best = max(all_candidates, key=lambda r: r.confidence)
            best.candidates = all_candidates[:10]
            if auto_retry and ElementConfidence.should_retry(best):
                return self._retry_find(description, context, best, strategies)
            self._log_hit(description, best, ensembled=True)
            return best
        if auto_retry:
            return self._retry_find(description, context, ElementResult(confidence=0), strategies)
        return ElementResult(confidence=0)

    def _retry_find(
        self,
        description: str,
        context: ElementContext,
        failed: ElementResult,
        strategies: List[str],
    ) -> ElementResult:
        """基于失败结果的重试：生成新描述或切换策略。"""
        for attempt in range(self._max_retries):
            new_desc = ElementConfidence.generate_retry_description(description, failed)
            if new_desc == description and ElementConfidence.should_try_vlm(failed):
                new_desc = description
            retry_candidates: List[ElementResult] = []
            for strat_name in strategies:
                if strat_name == "vlm":
                    continue
                strat = self._strategies.get(strat_name)
                if not strat or not strat.enabled:
                    continue
                cands = strat.find(new_desc, context)
                retry_candidates.extend(cands)
            if ElementConfidence.should_try_vlm(failed) and self._enable_vlm:
                vlm = self._strategies.get("vlm")
                if vlm:
                    vlm_cands = vlm.find(new_desc, context)
                    retry_candidates.extend(vlm_cands)
            if retry_candidates:
                best = max(retry_candidates, key=lambda r: r.confidence)
                if best.confidence > failed.confidence:
                    best.candidates = retry_candidates[:10]
                    return best
                failed = best
            if new_desc == description:
                break
            description = new_desc
        return failed

    def _get_strategies(self, platform: str) -> List[str]:
        return _PLATFORM_STRATEGIES.get(platform, ["ocr", "vlm"])

    @staticmethod
    def _threshold_for(strat_name: str, strategy: str) -> float:
        return ElementConfidence.threshold(strategy)

    @staticmethod
    def _log_hit(desc: str, result: ElementResult, ensembled: bool = False):
        from logger import uat_logger
        tag = "ensemble" if ensembled else "direct"
        uat_logger.info(
            "element_found[%s]: desc=%r source=%s strategy=%s confidence=%.2f pos=(%d,%d) size=%dx%d",
            tag, desc, result.source, result.strategy, result.confidence,
            result.x, result.y, result.width, result.height,
        )


_default_locator: Optional[UnifiedElementLocator] = None


def get_default_locator() -> UnifiedElementLocator:
    global _default_locator
    if _default_locator is None:
        _default_locator = UnifiedElementLocator(enable_vlm=True)
    return _default_locator


def locate_element(
    description: str,
    *,
    platform: str = "desktop",
    screenshot: Optional[bytes] = None,
    ocr_texts: Optional[List[Dict[str, Any]]] = None,
    ocr_blocks: Optional[List[Dict[str, Any]]] = None,
    uia_root: Optional[Any] = None,
    dom_tree: Optional[Dict[str, Any]] = None,
    window_title: str = "",
    screen_size: Optional[Tuple[int, int]] = None,
) -> ElementResult:
    """便捷函数：定位元素的一站式入口。"""
    ctx = ElementContext(
        platform=platform,
        screenshot=screenshot,
        ocr_texts=ocr_texts,
        ocr_blocks=ocr_blocks,
        dom_tree=dom_tree,
        uia_root=uia_root,
        window_title=window_title,
        screen_size=screen_size,
    )
    locator = get_default_locator()
    return locator.find_element(description, ctx)
