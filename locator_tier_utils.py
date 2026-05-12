"""
三层定位契约：DOM 候选（css/xpath/…）→ visual_template → viewport_coord。
visual_template / viewport_coord 仅由执行器 Tier2/Tier3 消费，不得进入 DOM 递归路径。
"""
from __future__ import annotations

import base64
import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

# 与 playwright_automation.convert_selector / 录制侧一致的主干类型
DOM_CANDIDATE_TYPES = frozenset(
    {
        "css",
        "xpath",
        "id",
        "partial_text",
        "text",
        "name",
        "placeholder",
        "label",
        "title",
        "alt",
        "data",
        "aria",
        "class",
        "link_text",
        "partial_link_text",
    }
)

SELECTOR_TYPE_VISUAL = "visual_template"
SELECTOR_TYPE_VIEWPORT_COORD = "viewport_coord"

# 模板 PNG 写入 locator_candidates 时的体积上限（解码后约 96KB）
_MAX_VISUAL_TEMPLATE_BYTES = int(os.environ.get("LOCATOR_VISUAL_TEMPLATE_MAX_BYTES", "98304"))


def is_dom_candidate_type(selector_type: str) -> bool:
    t = (selector_type or "").strip().lower()
    return t in DOM_CANDIDATE_TYPES


def _normalize_candidate_items(raw: Any) -> List[Dict[str, Any]]:
    if raw is None or raw == "":
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return []
    if not isinstance(raw, list):
        return []
    out: List[Dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        st = item.get("selector_type") or item.get("type")
        sv = item.get("selector_value") or item.get("value")
        if not st or sv is None:
            continue
        st = str(st).strip().lower()
        try:
            score = int(item.get("score", 0))
        except Exception:
            score = 0
        out.append({"selector_type": st, "selector_value": str(sv), "score": score})
    return out


def split_locator_candidates(raw: Any) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Returns (dom_candidates, visual_candidates, coord_candidates).
    未知 selector_type 归入 dom，避免破坏旧数据。
    """
    items = _normalize_candidate_items(raw)
    dom: List[Dict[str, Any]] = []
    visual: List[Dict[str, Any]] = []
    coord: List[Dict[str, Any]] = []
    for it in items:
        st = it.get("selector_type") or ""
        if st == SELECTOR_TYPE_VISUAL:
            visual.append(it)
        elif st == SELECTOR_TYPE_VIEWPORT_COORD:
            coord.append(it)
        else:
            dom.append(it)
    visual.sort(key=lambda x: -int(x.get("score") or 0))
    coord.sort(key=lambda x: -int(x.get("score") or 0))
    return dom, visual, coord


def dom_candidates_json_for_pack(raw: Any) -> Any:
    """供持久化或递归 fallback：仅保留 DOM 类候选。"""
    dom, _, _ = split_locator_candidates(raw)
    if not dom:
        return None
    return json.dumps(dom, ensure_ascii=False)


def parse_visual_template_value(selector_value: str) -> Tuple[Optional[bytes], float, Optional[Dict[str, float]]]:
    """
    selector_value 支持：
    - 纯 base64（PNG）
    - JSON: {"png_b64":"...","threshold":0.78,"clip":{"x":0,"y":0,"w":1,"h":1}}  clip 为视口比例 0–1
    """
    raw = (selector_value or "").strip()
    if not raw:
        return None, 0.75, None
    threshold = float(os.environ.get("LOCATOR_VISUAL_MATCH_THRESHOLD", "0.72") or 0.72)
    clip: Optional[Dict[str, float]] = None
    b64: Optional[str] = None
    if raw.startswith("{"):
        try:
            obj = json.loads(raw)
            if isinstance(obj, dict):
                b64 = obj.get("png_b64") or obj.get("b64") or obj.get("data")
                if obj.get("threshold") is not None:
                    try:
                        threshold = float(obj.get("threshold"))
                    except (TypeError, ValueError):
                        pass
                c = obj.get("clip")
                if isinstance(c, dict):
                    clip = {
                        "x": float(c.get("x", 0) or 0),
                        "y": float(c.get("y", 0) or 0),
                        "w": float(c.get("w", 1) or 1),
                        "h": float(c.get("h", 1) or 1),
                    }
        except Exception:
            b64 = None
    else:
        b64 = raw
    if not b64:
        return None, threshold, clip
    try:
        png = base64.b64decode(b64, validate=False)
    except Exception:
        return None, threshold, clip
    if len(png) > _MAX_VISUAL_TEMPLATE_BYTES:
        return None, threshold, clip
    if len(png) < 32:
        return None, threshold, clip
    return png, max(0.5, min(0.99, threshold)), clip


def parse_viewport_coord_value(selector_value: str) -> Optional[Tuple[float, float]]:
    raw = (selector_value or "").strip()
    if not raw:
        return None
    if raw.startswith("{"):
        try:
            obj = json.loads(raw)
            if isinstance(obj, dict):
                fx = float(obj.get("fx", obj.get("x", 0)))
                fy = float(obj.get("fy", obj.get("y", 0)))
                return fx, fy
        except Exception:
            return None
    m = re.match(r"^\s*([0-9.+-eE]+)\s*[,;\s]\s*([0-9.+-eE]+)\s*$", raw)
    if m:
        try:
            return float(m.group(1)), float(m.group(2))
        except ValueError:
            return None
    return None


def clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def build_visual_candidate_png_b64(png_bytes: bytes, score: int = 50) -> Dict[str, Any]:
    b64 = base64.b64encode(png_bytes).decode("ascii")
    return {"selector_type": SELECTOR_TYPE_VISUAL, "selector_value": b64, "score": int(score)}


def build_viewport_coord_candidate(fx: float, fy: float, score: int = 25) -> Dict[str, Any]:
    body = json.dumps({"fx": round(fx, 6), "fy": round(fy, 6)}, ensure_ascii=False)
    return {"selector_type": SELECTOR_TYPE_VIEWPORT_COORD, "selector_value": body, "score": int(score)}


def merge_candidates_json(existing_json: str, extra_items: List[Dict[str, Any]]) -> str:
    dom, vis, coord = split_locator_candidates(existing_json)
    for it in extra_items:
        st = (it.get("selector_type") or "").strip().lower()
        if st == SELECTOR_TYPE_VISUAL:
            vis.append(it)
        elif st == SELECTOR_TYPE_VIEWPORT_COORD:
            coord.append(it)
        else:
            dom.append(it)
    merged = dom + vis + coord
    return json.dumps(merged, ensure_ascii=False)
