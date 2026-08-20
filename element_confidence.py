# -*- coding: utf-8 -*-
"""元素置信度评估与自恢复策略。

借鉴 Playwright Locator 置信度分级 + Appium 多策略回退 + SWE-Agent 反思重试。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class ElementResult:
    """元素定位结果。"""
    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0
    confidence: float = 0.0
    source: str = ""  # dom / uia / ocr / vlm
    strategy: str = ""  # exact / fuzzy / ensemble
    text: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    candidates: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return self.confidence >= 0.4 and self.width > 0 and self.height > 0

    @property
    def center(self) -> Tuple[int, int]:
        return (self.x + self.width // 2, self.y + self.height // 2)


_CONFIDENCE_THRESHOLDS: Dict[str, float] = {
    "dom_exact": 1.0,
    "dom_text_exact": 0.95,
    "dom_role_exact": 0.9,
    "dom_fuzzy": 0.8,
    "uia_exact": 0.95,
    "uia_controltype_name": 0.88,
    "uia_fuzzy": 0.75,
    "uia_deep_search": 0.65,
    "ocr_exact": 0.75,
    "ocr_semantic": 0.65,
    "ocr_fuzzy": 0.55,
    "ocr_partial": 0.5,
    "vlm_grounding_high": 0.6,
    "vlm_grounding_mid": 0.5,
    "vlm_grounding_low": 0.4,
}

_FALLBACK_ORDER: List[str] = [
    "dom_exact", "dom_text_exact", "dom_role_exact", "dom_fuzzy",
    "uia_exact", "uia_controltype_name", "uia_fuzzy", "uia_deep_search",
    "ocr_exact", "ocr_semantic", "ocr_fuzzy", "ocr_partial",
    "vlm_grounding_high", "vlm_grounding_mid", "vlm_grounding_low",
]

_BUTTON_KEYWORDS = {
    "登录": ["登录", "登陆", "login", "sign in", "signin", "submit", "确定", "ok", "确认", "log in"],
    "确定": ["确定", "确认", "ok", "yes", "好的", "confirm", "submit"],
    "取消": ["取消", "cancel", "close", "关闭", "no", "放弃"],
    "搜索": ["搜索", "查找", "search", "find", "query", "查询"],
    "保存": ["保存", "save", "存储", "store"],
    "删除": ["删除", "delete", "remove", "清空", "clear"],
    "添加": ["添加", "新增", "add", "new", "create", "新建"],
    "发送": ["发送", "提交", "send", "submit", "commit"],
    "下载": ["下载", "download", "export", "导出"],
    "上传": ["上传", "upload", "import", "导入"],
    "编辑": ["编辑", "修改", "edit", "modify", "change"],
    "返回": ["返回", "后退", "back", "return", "←"],
    "下一步": ["下一步", "next", "下一页", "→"],
    "上一步": ["上一步", "prev", "previous", "上一页", "←"],
    "开始": ["开始", "start", "run", "执行", "execute"],
    "停止": ["停止", "stop", "pause", "暂停"],
    "刷新": ["刷新", "refresh", "reload", "更新"],
    "关闭": ["关闭", "close", "exit", "退出", "×"],
}

_INPUT_KEYWORDS = {
    "搜索框": ["搜索框", "搜索", "search", "搜索输入", "search box", "查找框"],
    "输入框": ["输入框", "input", "文本框", "text field", "textbox"],
    "用户名": ["用户名", "user", "username", "账号", "account", "login name"],
    "密码": ["密码", "password", "pwd", "passwd"],
    "邮箱": ["邮箱", "email", "e-mail"],
    "手机号": ["手机号", "电话", "phone", "mobile", "cell"],
}

_POSITION_HINTS: Dict[str, List[str]] = {
    "top_left": ["左上", "top left", "左上角", "左上"],
    "top_right": ["右上", "top right", "右上角", "右上"],
    "bottom_right": ["右下", "bottom right", "右下角", "右下"],
    "bottom_left": ["左下", "bottom left", "左下角", "左下"],
    "center": ["中间", "center", "中央", "居中"],
    "top_bar": ["顶部", "top bar", "菜单栏", "导航栏", "header"],
    "bottom_bar": ["底部", "bottom bar", "状态栏", "footer"],
    "left_sidebar": ["左侧", "left sidebar", "侧边栏", "侧边"],
    "right_sidebar": ["右侧", "right sidebar", "右边栏"],
}


class ElementConfidence:
    """元素置信度评估与自恢复策略。"""

    @staticmethod
    def threshold(strategy: str) -> float:
        return _CONFIDENCE_THRESHOLDS.get(strategy, 0.5)

    @staticmethod
    def should_retry(result: ElementResult) -> bool:
        if not result.is_valid:
            return True
        if result.confidence < 0.5:
            return True
        if result.source == "ocr" and result.confidence < 0.6:
            return True
        if result.source == "uia" and result.confidence < 0.65:
            return True
        return False

    @staticmethod
    def should_try_vlm(result: ElementResult) -> bool:
        return result.confidence < 0.65 and result.source in ("ocr", "uia", "")

    @staticmethod
    def generate_retry_description(original: str, failed_result: ElementResult) -> str:
        candidates = failed_result.candidates
        if candidates:
            best = max(candidates, key=lambda c: c.get("confidence", 0))
            text = best.get("text", "")
            if text and text != original:
                return text
        expanded = ElementConfidence._expand_semantic(original)
        if expanded and expanded[0] != original:
            return expanded[0]
        return original

    @staticmethod
    def _expand_semantic(description: str) -> List[str]:
        desc_lower = description.lower().strip()
        expanded = [description]
        for pattern, aliases in _BUTTON_KEYWORDS.items():
            if pattern in desc_lower or desc_lower in pattern:
                expanded.extend(aliases)
                break
        for pattern, aliases in _INPUT_KEYWORDS.items():
            if pattern in desc_lower or desc_lower in pattern:
                expanded.extend(aliases)
                break
        return list(dict.fromkeys(expanded))

    @staticmethod
    def extract_position_hint(description: str) -> Optional[str]:
        desc_lower = description.lower()
        for pos, keywords in _POSITION_HINTS.items():
            for kw in keywords:
                if kw in desc_lower:
                    return pos
        return None

    @staticmethod
    def semantic_expand(description: str) -> List[str]:
        return ElementConfidence._expand_semantic(description)

    @staticmethod
    def score_candidate_match(description: str, candidate_text: str, partial: bool = False) -> float:
        desc_lower = description.lower().strip()
        cand_lower = candidate_text.lower().strip()
        if not cand_lower:
            return 0.0
        if desc_lower == cand_lower:
            return 1.0
        if desc_lower in cand_lower or cand_lower in desc_lower:
            return 0.85
        if partial:
            desc_chars = set(desc_lower)
            cand_chars = set(cand_lower)
            overlap = len(desc_chars & cand_chars)
            total = max(len(desc_chars | cand_chars), 1)
            return 0.3 + 0.3 * (overlap / total)
        aliases = ElementConfidence._expand_semantic(description)
        for alias in aliases:
            if alias.lower() in cand_lower or cand_lower in alias.lower():
                return 0.7
        return 0.2
