# -*- coding: utf-8 -*-
"""
动作记录器：只接受结构化工具事件，禁止从 Hermes 散文/JSON 关键词猜测「input ok」。
不拦截 Hermes 的执行——只观测和记录。
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from modules.core.logger import uat_logger


@dataclass
class ActionRecord:
    action_id: str = ""
    action_type: str = ""  # navigate / click / input / wait / assert / tool name
    target: str = ""
    locator: str = ""
    input_data: str = ""
    result: str = ""
    status: str = "success"  # success / fail / skipped / warning
    timestamp: float = field(default_factory=time.time)
    screenshot: str = ""
    vision_info: Optional[Dict[str, Any]] = None
    raw_text: str = ""
    # 录制期补录：双端 UIA 树稳定锚点（回放定位/自愈用）
    uia_anchor: Optional[Dict[str, Any]] = None
    # 录制期补录：动作后树级结果校验快照（回放逐步复核用）
    verification: Optional[Dict[str, Any]] = None
    # per-record 工具前缀层：browser_→web / windows_|desktop_→desktop / mobile_→android
    platform_layer: str = ""
    # 录制 serial：移动端工具 args.serial 带入，Stage 分组时写 mobile branch.device_id
    device_serial: str = ""


def _layer_for_tool_name(name: str) -> str:
    """按工具前缀判定步骤所属自动化层（多端联动录制时覆盖单一 self.platform）。"""
    nm = (name or "").strip().lower()
    if nm in ("extract_otp", "mobile_extract_otp", "mobile_scrcpy_extract_otp"):
        return "android"
    if nm.startswith("mobile_"):
        return "android"
    if nm.startswith("windows_") or nm.startswith("desktop_"):
        return "desktop"
    if nm.startswith("browser_") or nm in ("navigate", "goto", "click", "type", "fill"):
        return "web"
    if nm == "api_call":
        return "web"
    return ""


_URL_RE = re.compile(r'https?://[^\s<>"\']+', re.IGNORECASE)
_QUOTE_RE = re.compile(r'[""\']([^"\']+)[""\']')
_PAREN_RE = re.compile(r'[（(]([^)）]+)[)）]')
# JSON 字段名，禁止当作操作目标展示
_BAD_TARGETS = frozenset(
    {
        "ok",
        "success",
        "error",
        "reply",
        "partial",
        "verified",
        "true",
        "false",
        "null",
        "stream_empty_text",
        "type",
        "input",
        "status",
    }
)
# 工具名 / 归一化动作名：绝不能当作选择器或输入值落库
_TOOL_NAME_PLACEHOLDERS = frozenset(
    {
        "browser_navigate",
        "browser_goto",
        "browser_click",
        "browser_type",
        "browser_fill",
        "browser_scroll",
        "browser_press",
        "browser_press_key",
        "browser_snapshot",
        "browser_console",
        "browser_get_images",
        "windows_launch_app",
        "windows_focus_app",
        "windows_click_element",
        "windows_click_text",
        "windows_type_text",
        "windows_press_key",
        "mobile_open_app",
        "mobile_tap",
        "mobile_input_text",
        "mobile_swipe",
        "mobile_extract_otp",
        "mobile_scrcpy_extract_otp",
        "navigate",
        "goto",
        "click",
        "type",
        "fill",
        "input",
        "scroll",
        "extract_otp",
        "tap",
        "swipe",
        "launch_app",
        "open_app",
        "hotkey",
        "press_key",
        "tool",
    }
)
_TOOL_NAME_PREFIX_RE = re.compile(
    r"^(?:browser_|windows_|desktop_|mobile_|scrcpy_)[a-z0-9_]+$",
    re.IGNORECASE,
)


def is_tool_name_placeholder(value: str) -> bool:
    """判断文本是否为工具名/动作名占位（不可作 selector / input / URL）。"""
    t = (value or "").strip().lower()
    if not t:
        return False
    if t in _TOOL_NAME_PLACEHOLDERS or t in _BAD_TARGETS:
        return True
    return bool(_TOOL_NAME_PREFIX_RE.match(t))


def _looks_like_url(value: str) -> bool:
    s = (value or "").strip()
    return bool(s) and (
        s.startswith("http://")
        or s.startswith("https://")
        or (s.startswith("/") and len(s) > 1 and " " not in s)
    )


def _first_nonempty_str(*candidates: Any, limit: int = 500) -> str:
    for c in candidates:
        if c is None:
            continue
        s = str(c).strip()
        if s and not is_tool_name_placeholder(s):
            return s[:limit]
    return ""


def _args_pick(args: Dict[str, Any], *keys: str, limit: int = 500) -> str:
    if not isinstance(args, dict):
        return ""
    return _first_nonempty_str(*(args.get(k) for k in keys), limit=limit)
# 浏览器工具 result 中属于 UI 元数据/状态观察的噪声后缀，应从 target 中剥离
_NOISE_PAREN_SUFFIXES = [
    r"[（(]\s*class\s*[)）]",
    r"[（(]\s*已聚焦\s*[)）]",
    r"[（(]\s*已禁用\s*[)）]",
    r"[（(]\s*可见\s*[)）]",
    r"[（(]\s*不可见\s*[)）]",
    r"[（(]\s*disabled\s*[)）]",
    r"[（(]\s*visible\s*[)）]",
]
_NOISE_PAREN_RE = re.compile("|".join(_NOISE_PAREN_SUFFIXES), re.IGNORECASE)
# 状态观察文本：不应作为独立 action 目标
_STATE_OBSERVATION_PATTERNS = [
    re.compile(r"^后变为", re.IGNORECASE),
    re.compile(r"^变为", re.IGNORECASE),
    re.compile(r"^状态[：:]", re.IGNORECASE),
    re.compile(r"^state[：:]", re.IGNORECASE),
    re.compile(r"^\s*disabled\s*$", re.IGNORECASE),
    re.compile(r"^\s*enabled\s*$", re.IGNORECASE),
    re.compile(r"^\s*visible\s*$", re.IGNORECASE),
]
# 错误/负向文本片段（fallback 解析时应跳过）
_ERROR_NEGATIVE_SNIPPETS = [
    "失败",
    "错误",
    "风险点",
    "异常",
    "error",
    "fail",
    "failed",
    "exception",
    "traceback",
    "stacktrace",
    "未找到",
    "不可用",
    "未执行",
]

# 观察/探活工具：不进实时用例、不进可回放 case steps（纯读 DOM/OCR/截图）
_OBSERVATION_TOOL_NAMES = frozenset(
    {
        "browser_snapshot",
        "browser_get_images",
        "browser_vision",
        "get_screen_text",
        "get_screen_description",
        "mobile_get_screen_text",
        "mobile_scrcpy_screenshot",
        "mobile_get_ui_tree",
        "windows_screenshot",
        "windows_get_screen_text",
        "mobile_screenshot",
        "snapshot",
        "console",  # 仅当未提升为 click/input/navigate 时由逻辑丢弃
    }
)

# 可写入「实时用例」的动作类型（提升后的标准化名）
_REPLAYABLE_ACTION_TYPES = frozenset(
    {
        "navigate",
        "goto",
        "click",
        "type",
        "fill",
        "input",
        "input_text",
        "scroll",
        "wait",
        "assert",
        "launch_app",
        "open_app",
        "focus_app",
        "tap",
        "swipe",
        "hotkey",
        "press_key",
        "extract_otp",
        "api_call",
        "back",
        "home",
        "select",
        "hover",
    }
)

# 各自动化层「平台可复用」步骤白名单（实时用例 / 落库 steps）
_WEB_CASE_ACTIONS = frozenset(
    {
        "navigate",
        "goto",
        "click",
        "type",
        "fill",
        "input",
        "input_text",
        "scroll",
        "wait",
        "assert",
        "select",
        "hover",
        "extract_otp",
        "api_call",
    }
)
_DESKTOP_CASE_ACTIONS = frozenset(
    {
        "launch_app",
        "open_app",
        "focus_app",
        "attach_window",
        "click",
        "click_element",
        "double_click",
        "right_click",
        "type",
        "fill",
        "input",
        "input_text",
        "hotkey",
        "press_key",
        "wait",
        "assert",
        "verify",
        "scroll",
        "extract_otp",
        "api_call",
    }
)
_ANDROID_CASE_ACTIONS = frozenset(
    {
        "tap",
        "swipe",
        "input",
        "input_text",
        "type",
        "open_app",
        "launch_app",
        "back",
        "home",
        "wait",
        "assert",
        "extract_otp",
        "api_call",
        "click",
    }
)


def is_case_worthy_for_platform(action_type: str, platform: str = "web") -> bool:
    """实时用例是否收录该动作：必须可回放，且属于当前主平台（+跨端）步骤词汇。

    网页任务不收录 launch_app/windows_* 等桌面模拟开浏览器步骤。
    多端联动（auto/cross/all/cross_end）或未知平台：取三端并集，避免丢桌面/手机/
    回 PC 后的 Web 步骤。
    """
    a = (action_type or "").strip().lower()
    if a.startswith("browser_"):
        a = a[len("browser_") :]
    if a.startswith("windows_") or a.startswith("desktop_"):
        # 桌面工具名归一化前缀剥离后再判
        raw = a.split("_", 1)[-1] if "_" in a else a
        a = {
            "launch_app": "launch_app",
            "focus_app": "focus_app",
            "attach_window": "attach_window",
            "click_element": "click",
            "click_text": "click",
            "type_text": "input",
            "press_key": "hotkey",
            "wait": "wait",
        }.get(raw, raw)
    if a.startswith("mobile_"):
        a = a[len("mobile_") :]
    # 归一化别名：保证与 to_case_steps / normalize_ai_step 一致
    a = {
        "click_element": "click",
        "goto": "navigate",
        "type": "input",
        "fill": "input",
        "focus_app": "launch_app",
        "press_key": "hotkey",
    }.get(a, a)
    if not is_replayable_action_type(a) and a not in _REPLAYABLE_ACTION_TYPES:
        # extract_otp / 已在 replayable 集合
        if a not in ("extract_otp", "api_call"):
            return False
    plat = (platform or "web").strip().lower()
    # 多端 / 未知：用并集，避免 PC→手机→PC 回程步骤被单端白名单丢掉
    if plat in ("auto", "all", "cross", "cross_end", "multi", ""):
        return a in (_WEB_CASE_ACTIONS | _DESKTOP_CASE_ACTIONS | _ANDROID_CASE_ACTIONS)
    if plat in ("web",):
        # Web 主任务仍允许跨端 OTP（否则多端联动实时卡缺手机步）
        return a in _WEB_CASE_ACTIONS or a in ("extract_otp", "api_call")
    if plat in ("desktop", "pc", "windows"):
        return a in _DESKTOP_CASE_ACTIONS
    if plat in ("android", "mobile"):
        return a in _ANDROID_CASE_ACTIONS
    return a in (_WEB_CASE_ACTIONS | _DESKTOP_CASE_ACTIONS | _ANDROID_CASE_ACTIONS)


def _coerce_tool_result_dict(result: Any) -> Optional[Dict[str, Any]]:
    """工具结果常以 JSON 字符串回传；解析失败返回 None，不抛。"""
    if isinstance(result, dict):
        return result
    if isinstance(result, str):
        text = result.strip()
        if not text or text[0] not in "{[":
            return None
        try:
            parsed = json.loads(text)
        except Exception:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


_EPHEMERAL_REF_RE = re.compile(r"^@?e\d+$", re.IGNORECASE)


def _is_ephemeral_browser_ref(value: str) -> bool:
    """Hermes snapshot ref（@e5）仅会话有效，不可落库作选择器。"""
    return bool(_EPHEMERAL_REF_RE.match((value or "").strip()))


def _looks_like_css_or_xpath(value: str) -> bool:
    s = (value or "").strip()
    if not s or _is_ephemeral_browser_ref(s):
        return False
    if s.startswith(("//", "(//", "./", "(", "#", ".", "[", "/")):
        return True
    if s.startswith("text=") or s.startswith("xpath="):
        return True
    return False

# browser_console expression → 可回放步骤
_CONSOLE_CLICK_RE = re.compile(
    r"""(?:\.click\s*\(|click\s*\()""",
    re.IGNORECASE,
)
_CONSOLE_INPUT_RE = re.compile(
    r"""(?:\.value\s*=|setAttribute\s*\(\s*['\"]value['\"]|textContent\s*=|innerText\s*=|\.type\s*\()""",
    re.IGNORECASE,
)
_CONSOLE_NAV_RE = re.compile(
    r"""(?:location\.href\s*=|location\.assign\s*\(|window\.location\s*=|location\.replace\s*\()""",
    re.IGNORECASE,
)
_CONSOLE_SELECTOR_RE = re.compile(
    r"""(?:querySelector(?:All)?|getElementById)\s*\(\s*['\"]([^'\"]{1,120})['\"]""",
    re.IGNORECASE,
)
_CONSOLE_URL_RE = re.compile(
    r"""(?:location\.href\s*=|location\.assign\s*\(|location\.replace\s*\()\s*['\"]([^'\"]{2,240})['\"]""",
    re.IGNORECASE,
)
_CONSOLE_VALUE_ASSIGN_RE = re.compile(
    r"""\.value\s*=\s*['\"]([^'\"]{0,200})['\"]""",
    re.IGNORECASE,
)


def is_observation_tool(name: str) -> bool:
    """是否为纯观察/探活工具（默认不进实时用例）。"""
    n = (name or "").strip()
    if not n:
        return False
    if n in _OBSERVATION_TOOL_NAMES:
        return True
    # browser_console 可能被提升为操作，此处仅作粗判；细判见 lift_console_expression
    if n == "browser_console":
        return True
    if n.endswith("_screenshot") or n.endswith("_get_images"):
        return True
    return False


def lift_console_expression(expression: str) -> Optional[Dict[str, str]]:
    """把 browser_console 的 JS 提升为 click/input/navigate；纯读则返回 None。"""
    expr = (expression or "").strip()
    if not expr:
        return None
    # 纯日志 / 读属性 → 丢弃
    low = expr.lower()
    if low.startswith("console.") and ".click" not in low and ".value" not in low:
        return None
    if _CONSOLE_NAV_RE.search(expr):
        m = _CONSOLE_URL_RE.search(expr)
        target = (m.group(1) if m else "")[:120] or "navigate"
        return {"action_type": "navigate", "target": sanitize_target(target), "input_data": ""}
    if _CONSOLE_INPUT_RE.search(expr):
        sel_m = _CONSOLE_SELECTOR_RE.search(expr)
        val_m = _CONSOLE_VALUE_ASSIGN_RE.search(expr)
        target = (sel_m.group(1) if sel_m else "input")[:80]
        input_data = (val_m.group(1) if val_m else "")[:500]
        return {
            "action_type": "input",
            "target": sanitize_target(target),
            "input_data": input_data,
        }
    if _CONSOLE_CLICK_RE.search(expr):
        sel_m = _CONSOLE_SELECTOR_RE.search(expr)
        target = (sel_m.group(1) if sel_m else "click")[:80]
        return {"action_type": "click", "target": sanitize_target(target), "input_data": ""}
    return None


def is_replayable_action_type(action_type: str) -> bool:
    """动作类型是否适合写入实时/可复用用例。"""
    a = (action_type or "").strip().lower()
    if not a:
        return False
    if a in _REPLAYABLE_ACTION_TYPES:
        return True
    # desktop/mobile aliases already normalized; allow unknown but non-observation names
    if a in ("snapshot", "console", "screenshot", "extract_text", "get_ui_tree", "vision"):
        return False
    return a not in _OBSERVATION_TOOL_NAMES


def sanitize_target(target: str) -> str:
    """清理浏览器工具 result 中的 UI 噪声后缀，如 (class)、(已聚焦) 等。"""
    if not target:
        return ""
    t = str(target).strip()
    t = _NOISE_PAREN_RE.sub("", t).strip()
    # 去掉 OCR/快照截断产生的前缀残字（如「了获取验证码」）
    t = re.sub(r"^[了的在是和与及到]\s*", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def case_step_dedupe_key(action: str, target: str) -> str:
    """实时用例去重键：动作 + 规范化目标。"""
    a = (action or "").strip().lower()
    if a.startswith("browser_"):
        a = a[len("browser_") :]
    t = sanitize_target(target or "").lower()
    t = re.sub(r"[\s\-–—_·•]+", "", t)
    return f"{a}|{t}"


def should_skip_duplicate_case_step(
    prev_action: str,
    prev_target: str,
    action: str,
    target: str,
) -> bool:
    """相邻同类点击/输入视为重复（如「了获取验证码」与「获取验证码」）。"""
    k1 = case_step_dedupe_key(prev_action, prev_target)
    k2 = case_step_dedupe_key(action, target)
    if not k1 or not k2 or "|" not in k1 or "|" not in k2:
        return False
    a1, t1 = k1.split("|", 1)
    a2, t2 = k2.split("|", 1)
    if a1 != a2:
        return False
    if not t1 or not t2:
        return k1 == k2
    if t1 == t2:
        return True
    # 一方包含另一方且长度接近（残字前缀）
    if t1 in t2 or t2 in t1:
        return abs(len(t1) - len(t2)) <= 2
    return False


def is_state_observation(target: str) -> bool:
    """判断 target 是否是状态观察而非实际操作目标。"""
    if not target:
        return True
    t = str(target).strip()
    for pat in _STATE_OBSERVATION_PATTERNS:
        if pat.match(t):
            return True
    return False


def contains_negative_snippet(text: str) -> bool:
    """判断文本是否包含明显的错误/负向片段。"""
    if not text:
        return False
    low = str(text).lower()
    return any(s in low for s in _ERROR_NEGATIVE_SNIPPETS)


def _status_from_flags(*, ok: Optional[bool] = None, verified: Optional[bool] = None) -> str:
    if ok is False:
        return "fail"
    if verified is False:
        return "warning"
    if ok is True:
        return "success"
    return "warning"


class ActionRecorder:
    """结构化动作记录；不再从 Hermes 长文本关键词臆造成功步骤。"""

    def __init__(
        self,
        *,
        vision_enabled: bool = False,
        platform: str = "web",
        case_url: str = "",
    ):
        self.records: List[ActionRecord] = []
        self.vision_enabled = vision_enabled
        self.platform = platform
        # 任务起始 URL：navigate 缺省 args.url 时回填，避免落成工具名
        self.case_url = (case_url or "").strip()

    def capture_from_hermes_result(self, result_text: str) -> List[ActionRecord]:
        """
        仅当返回体含明确结构化 steps / tool 列表时记录。
        散文、裸 JSON 的 ok 字段、stream_empty —— 一律不产出动作（避免「input ok」假绿勾）。
        """
        if not result_text or not str(result_text).strip():
            return []
        text = str(result_text).strip()
        # 空流 / 鉴权失败：禁止抽动作
        if "stream_empty_text" in text or "auth_fatal" in text:
            return []
        try:
            data = json.loads(text)
        except Exception:
            data = None
        if isinstance(data, dict):
            if data.get("stream_empty_text") or data.get("auth_fatal"):
                return []
            if data.get("ok") is False or data.get("success") is False:
                return []
            structured = self._records_from_structured(data)
            if structured:
                self.records.extend(structured)
                return structured
            # 有 JSON 但无 steps/tools：不散文猜测
            return []
        # 非 JSON：明确关闭散文关键词抽取（历史假成功根因）
        return []

    def capture_from_tool_event(
        self,
        *,
        name: str,
        args: Optional[Dict[str, Any]] = None,
        result: Any = None,
        status: str = "",
    ) -> List[ActionRecord]:
        """从 Hermes SSE 工具进度或真实工具结果写入一条可信记录。
        
        支持所有平台工具：Web (browser_*)、桌面 (windows_*)、移动端 (mobile_*)。
        对各平台工具的 target 做清理，剥离 UI 噪声和状态观察。
        观察类工具默认不入库；browser_console 若含 click/input/navigate 则提升为可回放步骤。
        """
        args = args if isinstance(args, dict) else {}
        name = (name or "tool").strip() or "tool"
        if name == "mobile_run_steps":
            # 整体工具名归一化后为 run_steps（不可回放）会被整批丢弃，
            # 导致跨端联动生成的用例缺失手机分支步骤 → 按 steps IR 逐条展开
            return self._records_from_mobile_steps(args, result, status)
        lifted: Optional[Dict[str, str]] = None
        if name == "browser_console":
            expr = str(args.get("expression") or args.get("code") or args.get("script") or "")
            lifted = lift_console_expression(expr)
            if not lifted:
                return []  # 纯读 console → 不进实时用例
        elif is_observation_tool(name):
            return []

        # 桌面/手机工具结果多为 JSON 字符串；必须先解析才能拿到 uia_anchor
        result_dict = _coerce_tool_result_dict(result)
        if result_dict is not None:
            result = result_dict

        ok: Optional[bool] = None
        verified: Optional[bool] = None
        summary = ""
        target = ""
        is_browser_tool = name.startswith("browser_")
        is_desktop_tool = name.startswith("windows_") or name.startswith("desktop_")
        is_mobile_tool = name.startswith("mobile_")
        is_platform_tool = is_browser_tool or is_desktop_tool or is_mobile_tool
        
        if isinstance(result, dict):
            if result.get("ok") is False or result.get("success") is False:
                ok = False
            elif result.get("ok") is True or result.get("success") is True:
                ok = True
            if "verified" in result:
                verified = bool(result.get("verified"))
            summary = str(
                result.get("error")
                or result.get("reply")
                or result.get("message")
                or result.get("effect")
                or result.get("status")
                or ""
            )[:200]
            
            # ── 浏览器工具：优先用 matched/selector/ref 等 DOM 元素标识 ──
            if is_browser_tool:
                _is_type_tool = name in (
                    "browser_type", "browser_fill", "type", "fill",
                ) or name.endswith("_type") or name.endswith("_fill")
                raw_target = _first_nonempty_str(
                    result.get("matched"),
                    result.get("selector"),
                    result.get("elementDescription"),
                    result.get("element_description"),
                    result.get("description"),
                    result.get("label"),
                    result.get("name"),
                    # type/fill 的 text 是输入值，不能当定位
                    (None if _is_type_tool else result.get("text")),
                    result.get("element"),
                    result.get("key"),
                    result.get("url"),
                    result.get("ref"),
                    limit=120,
                )
                if not raw_target:
                    _type_keys = (
                        "elementDescription",
                        "element_description",
                        "description",
                        "label",
                        "name",
                        "selector",
                        "css",
                        "xpath",
                        "url",
                        "element",
                        "ref",
                    )
                    _click_keys = _type_keys + ("text",)
                    raw_target = _args_pick(
                        args,
                        *(_type_keys if _is_type_tool else _click_keys),
                        limit=120,
                    )
                target = sanitize_target(raw_target)
            
            # ── 桌面工具：优先用 app_name/title/label/description 等控件标识 ──
            elif is_desktop_tool:
                raw_target = _first_nonempty_str(
                    result.get("matched"),
                    result.get("description"),
                    result.get("app_name"),
                    result.get("title"),
                    result.get("label"),
                    limit=120,
                )
                if not raw_target:
                    raw_target = _args_pick(
                        args,
                        "description",
                        "locate",
                        "elementDescription",
                        "text",
                        "app",
                        "app_name",
                        "title",
                        "path",
                        "key",
                        limit=120,
                    )
                target = sanitize_target(raw_target)
            
            # ── 移动端工具：优先用 package_name/text/resource_id 等元素标识 ──
            elif is_mobile_tool:
                raw_target = _first_nonempty_str(
                    result.get("text"),
                    result.get("resource_id"),
                    result.get("content_desc"),
                    result.get("package_name"),
                    result.get("app_name"),
                    result.get("description"),
                    limit=120,
                )
                if not raw_target:
                    raw_target = _args_pick(
                        args,
                        "text",
                        "selector",
                        "package",
                        "app",
                        "resource_id",
                        "description",
                        limit=120,
                    )
                target = sanitize_target(raw_target)
            
            # ── 其他跨端/通用工具 ──
            else:
                target = sanitize_target(
                    _first_nonempty_str(
                        result.get("matched"),
                        result.get("app_name"),
                        result.get("description"),
                        result.get("key"),
                        args.get("app") if isinstance(args, dict) else "",
                        args.get("text") if isinstance(args, dict) else "",
                        args.get("instruction") if isinstance(args, dict) else "",
                        limit=120,
                    )
                )
        elif result is not None:
            summary = str(result)[:200]
            low = summary.lower()
            if '"ok": false' in low or '"success": false' in low:
                ok = False
            elif '"ok": true' in low or '"success": true' in low:
                ok = True
        if not target:
            raw_target = _args_pick(
                args,
                "elementDescription",
                "element_description",
                "description",
                "label",
                "app",
                "text",
                "element",
                "selector",
                "url",
                "title",
                "package",
                "name",
                "ref",
                limit=120,
            )
            # 禁止把工具名塞进 target（历史根因：空 args → target=browser_type）
            if not raw_target and name not in _TOOL_NAME_PLACEHOLDERS and not _TOOL_NAME_PREFIX_RE.match(name):
                raw_target = name[:80]
            target = sanitize_target(raw_target)
        # 跳过：target 是状态观察文本（如"后变为禁用态"），不应作为独立步骤
        if is_state_observation(target) and is_platform_tool:
            # 平台工具的状态观察：用 args 构造一个有意义的 target
            if is_browser_tool:
                alt = _args_pick(args, "elementDescription", "description", "text", "selector", "ref", limit=60)
            elif is_desktop_tool:
                alt = _args_pick(args, "description", "locate", "app", "text", "title", limit=60)
            elif is_mobile_tool:
                alt = _args_pick(args, "text", "selector", "package", "description", limit=60)
            else:
                alt = ""
            if alt:
                target = sanitize_target(alt)
            else:
                target = ""
        if self._is_bad_target(target) or is_tool_name_placeholder(target):
            target = ""
        # 跳过负向片段污染的 target（如包含"失败或风险点"）
        if contains_negative_snippet(target) and is_platform_tool:
            if is_browser_tool:
                target = sanitize_target(
                    _args_pick(args, "elementDescription", "description", "text", "selector", "ref", limit=60)
                )
            elif is_desktop_tool:
                target = sanitize_target(_args_pick(args, "description", "app", "text", "title", limit=60))
            elif is_mobile_tool:
                target = sanitize_target(_args_pick(args, "text", "selector", "package", limit=60))
            else:
                target = sanitize_target(_args_pick(args, "text", limit=60))
            if is_tool_name_placeholder(target):
                target = ""
        st = (status or "").strip().lower()
        if st in ("running", "in_progress", "started", "progress"):
            st = "running"
        elif st in ("error", "failed", "fail"):
            st = "fail"
        elif st in ("ok", "done", "success", "completed", "complete"):
            if ok is None and verified is None and result is None:
                st = "warning"
            else:
                st = _status_from_flags(
                    ok=True if ok is None else ok,
                    verified=verified,
                )
        elif not st:
            st = _status_from_flags(ok=ok, verified=verified)
        else:
            st = _status_from_flags(ok=ok, verified=verified) if ok is not None else "warning"
        # 失败步骤不进实时用例 / 记录器（与 UI verified 过滤对齐）
        if st in ("fail", "failed", "error"):
            return []
        action_type = self._normalize_action_type(name, args)
        input_data = _args_pick(
            args,
            "text",
            "input_value",
            "value",
            "content",
            "input",
            limit=500,
        )
        # navigate / hotkey / launch：关键值常在 url/key/app，不在 text
        if action_type in ("navigate", "goto") and not _looks_like_url(input_data):
            input_data = _first_nonempty_str(
                args.get("url") if isinstance(args, dict) else "",
                (result.get("url") if isinstance(result, dict) else ""),
                self.case_url,
                target if _looks_like_url(target) else "",
                limit=500,
            )
            # 仍无真实 URL：尝试当前页
            if not input_data:
                try:
                    from modules.ai import ai_external_browser_bridge as _br

                    _page = getattr(_br, "_page", None)
                    if _page is not None and not getattr(_page, "is_closed", lambda: True)():
                        _u = str(getattr(_page, "url", "") or "").strip()
                        if _looks_like_url(_u) and "about:blank" not in _u.lower():
                            input_data = _u[:500]
                except Exception:
                    pass
        if action_type in ("hotkey", "press_key") and not input_data:
            input_data = _args_pick(args, "key", "keys", limit=200) or (
                target if not is_tool_name_placeholder(target) else ""
            )
        if action_type in ("launch_app", "focus_app", "open_app") and not input_data:
            input_data = _args_pick(
                args, "app", "app_name", "path", "package", limit=500
            ) or (target if not is_tool_name_placeholder(target) else "")
        if action_type == "wait" and not input_data:
            input_data = _args_pick(
                args, "duration_ms", "timeout_ms", "ms", "seconds", limit=40
            )
        if lifted:
            action_type = lifted["action_type"]
            if lifted.get("target") and not is_tool_name_placeholder(lifted["target"]):
                target = lifted["target"]
            if lifted.get("input_data"):
                input_data = lifted["input_data"]
        # Web 别名统一，避免落库 type/fill 后执行器不识别
        if action_type in ("type", "fill"):
            action_type = "input"
        if action_type == "goto":
            action_type = "navigate"
        # Web：用实时 DOM / 焦点元素补全定位（Hermes 常只回 ok，无 matched）
        if is_browser_tool and action_type in ("click", "input", "hover", "double_click", "right_click"):
            weak = (
                not target
                or is_tool_name_placeholder(target)
                or _is_ephemeral_browser_ref(target)
            )
            if weak or (action_type == "input" and input_data):
                try:
                    from modules.ai.ai_external_browser_bridge import resolve_element_from_live_dom

                    hint = _args_pick(
                        args,
                        "elementDescription",
                        "element_description",
                        "description",
                        "label",
                        "text",
                        "name",
                        limit=120,
                    ) or (
                        target
                        if target and not _is_ephemeral_browser_ref(target) and not is_tool_name_placeholder(target)
                        else ""
                    )
                    enriched = resolve_element_from_live_dom(
                        hint=hint,
                        typed_value=input_data if action_type == "input" else "",
                        prefer_focused=(action_type == "input"),
                    )
                    if enriched.get("matched") and (
                        not target or _is_ephemeral_browser_ref(target) or is_tool_name_placeholder(target)
                    ):
                        target = sanitize_target(enriched["matched"])
                    if enriched.get("selector"):
                        args = dict(args) if isinstance(args, dict) else {}
                        if not str(args.get("selector") or args.get("css") or "").strip():
                            args["selector"] = enriched["selector"]
                    if enriched.get("text") and action_type == "click" and (
                        not target or _is_ephemeral_browser_ref(target)
                    ):
                        target = sanitize_target(enriched["text"])
                except Exception:
                    pass
        if not is_replayable_action_type(action_type):
            return []
        # 无有效定位/输入的平台动作：不落库（避免 browser_type 当选择器）
        if is_platform_tool and action_type not in ("extract_otp", "wait", "api_call", "back", "home"):
            if action_type in ("navigate",):
                if not _looks_like_url(input_data):
                    return []
            elif action_type in ("input", "input_text", "type", "fill"):
                # 输入步：至少要有输入值，或非工具名目标/选择器
                has_sel = bool(
                    (target and not is_tool_name_placeholder(target) and not _is_ephemeral_browser_ref(target))
                    or _args_pick(args, "selector", "css", "xpath", "elementDescription", "description")
                )
                if not input_data and not has_sel:
                    return []
            elif action_type in ("click", "tap", "double_click", "right_click", "hover"):
                has_sel = bool(
                    (target and not is_tool_name_placeholder(target))
                    or _args_pick(
                        args,
                        "selector",
                        "css",
                        "xpath",
                        "elementDescription",
                        "description",
                        "text",
                        "ref",
                    )
                )
                if not has_sel:
                    return []
        # 描述优先用人话目标，避免 result 仅有工具名导致用例步骤无意义
        human_desc = ""
        if target and not self._is_bad_target(target) and not is_tool_name_placeholder(target):
            if target not in (name, action_type):
                human_desc = target
        if not human_desc and summary and not is_tool_name_placeholder(summary) and summary not in (name, action_type):
            human_desc = summary
        if not human_desc:
            if action_type == "extract_otp":
                human_desc = "提取手机短信验证码"
            elif action_type == "navigate" and input_data:
                human_desc = f"打开 {input_data[:80]}"
            elif input_data and action_type in ("input", "input_text"):
                human_desc = f"输入 {input_data[:40]}"
            else:
                human_desc = action_type
        # navigate：URL 进 input_data，target 用 URL（勿留空导致描述丢失）
        if action_type == "navigate" and _looks_like_url(input_data):
            target = input_data
        rec = ActionRecord(
            action_id=f"act_{len(self.records)}",
            action_type=action_type,
            target=target or (human_desc if not is_tool_name_placeholder(human_desc) else ""),
            input_data=input_data if not is_tool_name_placeholder(input_data) else "",
            result=(human_desc or summary or action_type)[:200],
            status=st,
            raw_text=json.dumps({"name": name, "args": args}, ensure_ascii=False)[:2000],
            uia_anchor=result.get("uia_anchor") if isinstance(result, dict) else None,
            verification=result.get("verification") if isinstance(result, dict) else None,
            platform_layer=_layer_for_tool_name(name),
            device_serial=str(args.get("serial") or args.get("device_id") or "")[:80],
        )
        # 从 args 直接带上选择器线索（供 to_case_steps 回填，避免只剩 ephemeral ref）
        sel = _args_pick(args, "selector", "css", "xpath", "elementDescription", "description", "text", limit=500)
        if sel and not _is_ephemeral_browser_ref(sel) and not is_tool_name_placeholder(sel):
            rec.locator = sel[:500]
        # 仅有 @eN：把 description 类字段记入 locator 旁路（locate_prompt 在 to_case_steps）
        if not rec.locator:
            hint = _args_pick(args, "elementDescription", "element_description", "description", "label", limit=200)
            if hint:
                rec.locator = hint[:500]
        self.records.append(rec)
        return [rec]

    def _records_from_mobile_steps(
        self,
        args: Dict[str, Any],
        result: Any,
        status: str,
    ) -> List[ActionRecord]:
        """mobile_run_steps 逐步展开：让手机分支步骤进入实时用例。

        该工具只返回整体 digest；action_type 归一化后为 run_steps，
        不在 _REPLAYABLE_ACTION_TYPES 中，会被整条丢弃——跨端联动的
        手机侧步骤因此在生成用例时全部缺失。此处按 steps IR 逐条
        合成 mobile_<action> 工具事件，复用 capture_from_tool_event 的
        归一化/过滤逻辑（失败步骤仍不收录，与 UI verified 过滤对齐）。"""
        steps = args.get("steps") if isinstance(args.get("steps"), list) else []
        if not steps:
            return []
        parsed: Any = result
        if isinstance(parsed, str):
            try:
                parsed = json.loads(parsed)
            except Exception:
                parsed = None
        results_list: List[Any] = []
        overall_ok = True
        if isinstance(parsed, dict):
            if parsed.get("success") is False or parsed.get("ok") is False:
                overall_ok = False
            payload = parsed.get("result_payload")
            if isinstance(payload, dict) and isinstance(payload.get("results"), list):
                results_list = payload["results"]
            elif isinstance(parsed.get("results"), list):
                results_list = parsed["results"]
        if (status or "").strip().lower() in ("error", "failed", "fail"):
            overall_ok = False
        serial = str(args.get("serial") or args.get("device_id") or "")[:80]
        out: List[ActionRecord] = []
        for i, step in enumerate(steps):
            if not isinstance(step, dict):
                continue
            action = str(step.get("action") or "step").strip()
            synth_name = action if action.startswith("mobile_") else f"mobile_{action}"
            step_args = dict(step)
            step_args.pop("stepDescription", None)
            step_args.pop("step_description", None)
            step_args.pop("description", None)
            if serial:
                step_args.setdefault("serial", serial)
            step_res = results_list[i] if i < len(results_list) else None
            if not isinstance(step_res, dict):
                # 无逐条结果：以整体成败近似
                step_res = {"success": True} if overall_ok else {"success": False}
            desc = str(
                step.get("stepDescription")
                or step.get("step_description")
                or step.get("description")
                or ""
            )[:80]
            recs = self.capture_from_tool_event(
                name=synth_name, args=step_args, result=step_res, status=""
            )
            for r in recs:
                if desc and (not r.target or r.target in (action, synth_name)):
                    r.target = sanitize_target(desc) or r.target
                out.append(r)
        return out

    def _records_from_structured(self, data: Dict[str, Any]) -> List[ActionRecord]:
        out: List[ActionRecord] = []
        steps = data.get("steps") or data.get("step_results") or data.get("tool_calls")
        if not isinstance(steps, list) or not steps:
            return []
        for item in steps:
            if not isinstance(item, dict):
                continue
            step = item.get("step") if isinstance(item.get("step"), dict) else item
            act = (
                step.get("action")
                or step.get("action_type")
                or step.get("name")
                or item.get("name")
                or "step"
            )
            tgt = (
                step.get("target")
                or step.get("description")
                or step.get("input_value")
                or item.get("target")
                or ""
            )
            if self._is_bad_target(str(tgt)):
                tgt = str(act)
            ok = item.get("ok")
            if ok is None:
                ok = item.get("success")
            if ok is None and (item.get("status") or "").lower() in ("success", "ok", "done"):
                ok = True
            if ok is None and (item.get("status") or "").lower() in ("failed", "fail", "error"):
                ok = False
            verified = item.get("verified")
            _args_ser = ""
            if isinstance(item.get("args"), dict):
                _args_ser = str(item["args"].get("serial") or item["args"].get("device_id") or "")
            elif isinstance(step.get("args"), dict):
                _args_ser = str(step["args"].get("serial") or step["args"].get("device_id") or "")
            out.append(
                ActionRecord(
                    action_id=f"act_{len(self.records) + len(out)}",
                    action_type=str(act)[:40],
                    target=str(tgt)[:80] or str(act),
                    input_data=str(step.get("input_value") or "")[:100],
                    result=str(item.get("error") or step.get("description") or "")[:200],
                    status=_status_from_flags(
                        ok=bool(ok) if ok is not None else None,
                        verified=bool(verified) if verified is not None else None,
                    ),
                    raw_text=json.dumps(item, ensure_ascii=False)[:300],
                    platform_layer=_layer_for_tool_name(str(act)),
                    device_serial=_args_ser[:80],
                )
            )
        return out

    @staticmethod
    def _is_bad_target(target: str) -> bool:
        t = (target or "").strip().lower()
        return (not t) or t in _BAD_TARGETS or t in ('"ok"', "'ok'") or is_tool_name_placeholder(t)

    @staticmethod
    def _normalize_action_type(name: str, args: Dict[str, Any]) -> str:
        n = (name or "").strip()
        # Web 浏览器工具
        if n.startswith("browser_"):
            return n.replace("browser_", "", 1)
        # 桌面工具
        if n.startswith("windows_"):
            base = n.replace("windows_", "", 1)
            # 映射桌面工具到标准步骤类型（click_element 是 Agent 主点击工具）
            _DESKTOP_MAP = {
                "launch_app": "launch_app",
                "focus_app": "launch_app",
                "attach_window": "attach_window",
                "click_text": "click",
                "click_element": "click",
                "click": "click",
                "double_click": "double_click",
                "right_click": "right_click",
                "type_text": "input",
                "input": "input",
                "press_key": "hotkey",
                "wait": "wait",
                "screenshot": "screenshot",
                "get_screen_text": "extract_text",
                "scroll": "scroll",
            }
            return _DESKTOP_MAP.get(base, base)
        # 移动端工具
        if n.startswith("mobile_"):
            base = n.replace("mobile_", "", 1)
            _MOBILE_MAP = {
                "open_app": "open_app",
                "launch_app": "open_app",
                "tap": "tap",
                "click": "tap",
                "input_text": "input_text",
                "input": "input_text",
                "swipe": "swipe",
                "scroll": "scroll",
                "screenshot": "screenshot",
                "extract_otp": "extract_otp",
                "extract_text": "extract_text",
                "press_key": "hotkey",
                "back": "back",
                "home": "home",
            }
            return _MOBILE_MAP.get(base, base)
        # scrcpy 视觉/控制工具
        if n.startswith("scrcpy_"):
            base = n.replace("scrcpy_", "", 1)
            _SCRCPY_MAP = {
                "tap": "tap",
                "swipe": "swipe",
                "type_text": "input_text",
                "screenshot": "screenshot",
                "ocr_device": "extract_text",
                "extract_otp": "extract_otp",
                "ensure_session": "screenshot",
                "navigate_to_messages": "open_app",
                "capture_frame": "screenshot",
            }
            return _SCRCPY_MAP.get(base, base)
        # 跨端工具
        _CROSS_END_MAP = {
            "mobile_extract_otp": "extract_otp",
            "desktop_type_text": "input",
            "api_call": "api_call",
        }
        if n in _CROSS_END_MAP:
            return _CROSS_END_MAP[n]
        # 计算机使用工具
        if n == "computer_use":
            return str(args.get("action") or "computer_use")[:40]
        # 技能视图工具
        if n == "skill_view":
            return "screenshot"
        return n[:40] or "tool"

    def to_case_steps(self) -> List[Dict[str, Any]]:
        """将动作记录转换为步骤列表（供 ai_step_normalization 处理）。"""
        probe_by_text: Dict[str, Dict[str, Any]] = {}
        try:
            from modules.ai.ai_external_browser_bridge import get_probe_registry

            for entry in get_probe_registry() or []:
                if not isinstance(entry, dict):
                    continue
                for key in ("text", "name", "label", "aria"):
                    val = (entry.get(key) or "").strip()
                    if val and val not in probe_by_text:
                        probe_by_text[val] = entry
        except Exception:
            pass

        steps = []
        for rec in self.records:
            if rec.status in ("fail", "failed", "error") and not rec.target:
                continue
            if not is_replayable_action_type(rec.action_type):
                continue
            # 丢弃 target 仍是工具名的噪声（如 console → browser_console / browser_type）
            tgt_raw = (rec.target or "").strip()
            tgt_low = tgt_raw.lower()
            _keep_despite_bad_tgt = (
                (rec.action_type in ("navigate", "goto") and _looks_like_url(rec.input_data))
                or (rec.action_type in ("input", "input_text") and (rec.input_data or "").strip())
                or rec.action_type in ("extract_otp", "wait", "api_call")
                or ((rec.locator or "").strip() and not is_tool_name_placeholder(rec.locator))
            )
            if tgt_low in (
                "browser_console",
                "browser_snapshot",
                "console",
                "snapshot",
                "browser_get_images",
            ) or is_tool_name_placeholder(tgt_low):
                if not _keep_despite_bad_tgt:
                    continue
                tgt_raw = ""
            # 按 per-record 工具层判断可复用性（多端联动时 desktop 平台也要收录手机步骤）
            _worthy_plat = (
                rec.platform_layer
                if rec.platform_layer in ("web", "desktop", "android")
                else (
                    "auto"
                    if (self.platform or "").strip().lower() in ("auto", "all", "cross", "cross_end", "")
                    else (self.platform or "web")
                )
            )
            if not is_case_worthy_for_platform(rec.action_type, _worthy_plat):
                continue
            _desc = ""
            if (
                tgt_raw
                and not self._is_bad_target(tgt_raw)
                and not is_tool_name_placeholder(tgt_raw)
                and not _is_ephemeral_browser_ref(tgt_raw)
            ):
                _desc = tgt_raw
            elif rec.result and not is_tool_name_placeholder(rec.result):
                _desc = rec.result[:100]
            step: Dict[str, Any] = {
                "action": rec.action_type,
                "target": (
                    tgt_raw
                    if (
                        tgt_raw
                        and not is_tool_name_placeholder(tgt_raw)
                        and not _is_ephemeral_browser_ref(tgt_raw)
                    )
                    else ""
                ),
                "input_value": rec.input_data if not is_tool_name_placeholder(rec.input_data) else "",
                "description": _desc,
                # 多端联动录制：优先 per-record 工具前缀层，其次平台单值
                "automation_layer": rec.platform_layer
                if rec.platform_layer in ("web", "desktop", "android")
                else (self.platform if self.platform in ("web", "desktop", "android") else "web"),
            }
            # 移动端步骤携带录制 serial（Stage 分组时写 mobile branch.device_id）
            if step.get("automation_layer") == "android" and rec.device_serial:
                step["device_id"] = rec.device_serial
            # ── 跨端/api 步骤：仅当 raw_name 明确匹配时才做特殊参数恢复 ──
            _raw_name = ""
            _raw_args: Dict[str, Any] = {}
            try:
                raw = json.loads(rec.raw_text or "{}")
                _raw_name = raw.get("name") or ""
                _raw_args = raw.get("args") if isinstance(raw.get("args"), dict) else {}
            except Exception:
                pass
            if _raw_name in ("mobile_extract_otp", "mobile_scrcpy_extract_otp") or rec.action_type == "extract_otp":
                step["action"] = "extract_otp"
                step["automation_layer"] = "android"
                cross_spec = {}
                if _raw_args.get("timeout_sec"):
                    cross_spec["timeout_sec"] = _raw_args["timeout_sec"]
                if _raw_args.get("sender_hint"):
                    cross_spec["sender_hint"] = _raw_args["sender_hint"]
                if _raw_args.get("pattern"):
                    cross_spec["pattern"] = _raw_args["pattern"]
                step["cross_end_spec"] = json.dumps(cross_spec, ensure_ascii=False)
                step["description"] = step.get("description") or "提取手机短信验证码"
            elif _raw_name in ("desktop_type_text", "windows_type_text"):
                step["action"] = "input"
                step["automation_layer"] = "desktop"
                if _raw_args.get("text"):
                    step["input_value"] = str(_raw_args["text"])[:500]
                if _raw_args.get("clear") is not None:
                    ds = json.loads(step.get("desktop_spec") or "{}") if step.get("desktop_spec") else {}
                    ds["clear"] = bool(_raw_args["clear"])
                    step["desktop_spec"] = json.dumps(ds, ensure_ascii=False)
                # 选择器留给下方 uia_anchor / 回填逻辑；此处只记 locate_prompt
                _desk_label = str(
                    _raw_args.get("description")
                    or _raw_args.get("locate")
                    or rec.target
                    or ""
                ).strip()
                if _desk_label:
                    step["locate_prompt"] = _desk_label[:200]
                    if not step.get("description"):
                        step["description"] = _desk_label[:100]
            elif _raw_name in ("windows_click_element", "windows_click_text"):
                step["action"] = "click"
                step["automation_layer"] = "desktop"
                _desk_label = str(
                    _raw_args.get("description")
                    or _raw_args.get("locate")
                    or _raw_args.get("text")
                    or rec.target
                    or ""
                ).strip()
                if _desk_label:
                    step["description"] = step.get("description") or _desk_label
                    step["locate_prompt"] = _desk_label[:200]
            elif _raw_name == "api_call" or rec.action_type == "api_call":
                step["action"] = "api_call"
                step["automation_layer"] = "web"
                api_spec = {}
                api_spec["method"] = str(_raw_args.get("method") or "GET")
                api_spec["url"] = str(_raw_args.get("url") or "")
                if _raw_args.get("headers"):
                    api_spec["headers"] = _raw_args["headers"]
                if _raw_args.get("body") is not None:
                    api_spec["body"] = _raw_args["body"]
                if _raw_args.get("timeout_sec"):
                    api_spec["timeout_sec"] = _raw_args["timeout_sec"]
                if _raw_args.get("case_id"):
                    api_spec["case_id"] = _raw_args["case_id"]
                step["cross_end_spec"] = json.dumps(api_spec, ensure_ascii=False)
                step["description"] = step.get("description") or f"{api_spec.get('method','GET')} {api_spec.get('url','')}"
            # UIA 树锚点优先于文本回填（automation_id > name）
            if rec.uia_anchor and isinstance(rec.uia_anchor, dict):
                cands = rec.uia_anchor.get("candidates") or []
                if cands:
                    c0 = cands[0] if isinstance(cands[0], dict) else {}
                    # 锚点始终覆盖弱回填，保证 DB 回放优先稳定键
                    if c0.get("value"):
                        step["selector_type"] = c0.get("type") or step.get("selector_type") or ""
                        step["selector_value"] = c0.get("value") or ""
                    step["locator_candidates"] = cands
                step["uia_anchor"] = rec.uia_anchor
            if rec.locator and not _is_ephemeral_browser_ref(rec.locator):
                if not step.get("selector_value"):
                    step["locator"] = rec.locator
            hit = probe_by_text.get((rec.target or "").strip())
            if hit:
                if hit.get("i") is not None:
                    step["probe_index"] = hit.get("i")
                css = (hit.get("css") or hit.get("selector") or "").strip()
                if css and not step.get("locator") and not step.get("selector_value"):
                    step["locator"] = css
                    step["target"] = css
            # ── 生产级回填：把 target/locator 提升为可回放 selector_* ──
            layer = str(step.get("automation_layer") or "web")
            act = str(step.get("action") or "")
            if act == "navigate":
                url = _first_nonempty_str(
                    step.get("input_value"),
                    rec.input_data,
                    rec.target if _looks_like_url(rec.target) else "",
                    self.case_url,
                    limit=500,
                )
                if _looks_like_url(url):
                    step["input_value"] = url
                    step["target"] = url
                    step["selector_type"] = ""
                    step["selector_value"] = ""
                    if not step.get("description") or is_tool_name_placeholder(step.get("description") or ""):
                        step["description"] = f"打开 {url[:80]}"
                else:
                    # 无真实 URL 的导航不可回放
                    continue
            elif not step.get("selector_value"):
                cand = _first_nonempty_str(
                    step.get("locator"),
                    rec.locator,
                    step.get("locate_prompt"),
                    rec.target,
                    limit=500,
                )
                if cand and not _is_ephemeral_browser_ref(cand) and not self._is_bad_target(cand) and not is_tool_name_placeholder(cand):
                    if layer == "web":
                        if _looks_like_css_or_xpath(cand):
                            stype = "xpath" if cand.startswith(("/", "(")) or cand.startswith("xpath=") else "css"
                            if cand.startswith("text="):
                                stype, cand = "text", cand[5:]
                            step["selector_type"] = stype
                            step["selector_value"] = cand[:500]
                        elif act in ("click", "input", "hover", "double_click", "right_click", "verify", "assert", "extract_text"):
                            # 可见文案 → text 定位（平台可回放）
                            step["selector_type"] = "text"
                            step["selector_value"] = cand[:200]
                            step["locate_prompt"] = cand[:200]
                    elif layer == "desktop":
                        step["selector_type"] = step.get("selector_type") or "name"
                        step["selector_value"] = cand[:200]
                        step["locate_prompt"] = cand[:200]
                    elif layer == "android":
                        # 默认 text；有 resource-id 形态时用 id
                        if ":id/" in cand or cand.startswith("com."):
                            step["selector_type"] = "id"
                        else:
                            step["selector_type"] = "text"
                        step["selector_value"] = cand[:200]
                elif cand and _is_ephemeral_browser_ref(cand):
                    # 会话 ref 不可回放：至少保留 locate_prompt，避免空步骤
                    # input 的 text/value 是输入内容，绝不能当定位文案
                    if act in ("input", "input_text", "type", "fill"):
                        hint = _first_nonempty_str(
                            _raw_args.get("elementDescription"),
                            _raw_args.get("element_description"),
                            _raw_args.get("description"),
                            _raw_args.get("label"),
                            _raw_args.get("name"),
                            step.get("description") if not is_tool_name_placeholder(step.get("description") or "") else "",
                            limit=200,
                        )
                    else:
                        hint = _first_nonempty_str(
                            _raw_args.get("elementDescription"),
                            _raw_args.get("element_description"),
                            _raw_args.get("description"),
                            _raw_args.get("text"),
                            step.get("description"),
                            limit=200,
                        )
                    if hint:
                        step["locate_prompt"] = hint[:200]
                        if not step.get("description"):
                            step["description"] = hint[:100]
                        # 有人话描述时用 text 定位，便于平台回放
                        if act in ("click", "input", "hover", "double_click", "right_click"):
                            step["selector_type"] = "text"
                            step["selector_value"] = hint[:200]
                    # 清除 ephemeral target，避免落库 @eN
                    step["target"] = ""
            if step.get("automation_layer") == "desktop":
                # 已有 UIA 锚点（automation_id/name）时保留，否则用 name/locate_prompt
                if not step.get("selector_type"):
                    step["selector_type"] = "window" if act in ("launch_app", "attach_window", "wait") else "name"
                if rec.action_type == "launch_app" and not step.get("input_value"):
                    step["input_value"] = rec.target
                elif rec.action_type == "hotkey" and not step.get("input_value"):
                    step["input_value"] = rec.target or rec.input_data
                # 桌面 click/input 无 selector 时用 description 兜底
                if act in ("click", "input", "double_click", "right_click") and not step.get("selector_value"):
                    label = str(
                        step.get("locate_prompt") or step.get("description") or rec.target or ""
                    ).strip()
                    if label and not self._is_bad_target(label):
                        step["selector_type"] = "name"
                        step["selector_value"] = label[:200]
                        step["locate_prompt"] = label[:200]
            if rec.vision_info:
                step["vision_info"] = rec.vision_info
            if rec.verification and isinstance(rec.verification, dict):
                step["verification"] = rec.verification
            # 相邻重复点击/输入（如「了获取验证码」与「获取验证码」）丢弃后者
            if steps:
                prev = steps[-1]
                if should_skip_duplicate_case_step(
                    str(prev.get("action") or ""),
                    str(prev.get("target") or prev.get("selector_value") or ""),
                    str(step.get("action") or ""),
                    str(step.get("target") or step.get("selector_value") or ""),
                ):
                    continue
            steps.append(step)
        return steps

    def build_normalized_plan(
        self,
        *,
        case_name: str = "",
        case_url: str = "",
        instruction: str = "",
    ) -> tuple:
        """热路径：动作记录 → normalize 全管线 → 可保存用例 plan。"""
        from modules.ai.ai_step_normalization import (
            apply_step_normalization_to_plan,
            dedupe_and_validate_ai_steps,
            normalize_ai_step,
            repair_raw_ai_steps_for_platform,
        )

        raw = self.to_case_steps()
        if not raw:
            return {
                "case_name": (case_name or instruction or "AI 生成用例")[:80],
                "case_url": case_url or "",
                "steps": [],
            }, []

        plat = (self.platform or "web").strip().lower()
        # 多端联动：保留 auto，供校验走三端并集；勿强制降为 web（会丢桌面/手机语义）
        layers_in_raw = {
            str(s.get("automation_layer") or "").strip().lower()
            for s in raw
            if isinstance(s, dict)
        }
        multi_end = bool(layers_in_raw & {"web", "desktop"}) and bool(
            layers_in_raw & {"android", "mobile"}
        )
        if plat in ("auto", "all", "cross") or multi_end:
            plat = "auto"
        elif plat not in ("web", "desktop", "android"):
            plat = "web"
        normalized = [normalize_ai_step(s) for s in raw]
        warnings1 = repair_raw_ai_steps_for_platform(normalized) or []
        clean, warnings2 = dedupe_and_validate_ai_steps(normalized, platform=plat)
        plan_platform = plat
        if multi_end:
            plan_platform = "cross_end"
        elif plat == "auto":
            # 单端 auto：按步骤层推断
            if layers_in_raw <= {"desktop", ""}:
                plan_platform = "desktop"
            elif layers_in_raw <= {"android", "mobile", ""}:
                plan_platform = "android"
            else:
                plan_platform = "web"
        plan = {
            "case_name": (case_name or instruction or "AI 生成用例")[:80],
            "case_url": case_url or "",
            "description": (instruction or "")[:400],
            "steps": clean,
            "platform": plan_platform,
            "meta": {"source": "action_recorder", "platform_type": plan_platform},
        }
        plan, warnings3 = apply_step_normalization_to_plan(plan)
        try:
            from modules.ai.ai_external_browser_bridge import get_probe_registry
            from modules.ai.ai_locator_resolution import resolve_plan_steps_locators_with_snapshot

            registry = get_probe_registry()
            if registry and plan.get("steps"):
                # 注意：第一参数必须是 steps 列表（曾误传整个 plan dict 导致返回值被包成 tuple）
                _resolved, _loc_warns = resolve_plan_steps_locators_with_snapshot(
                    plan.get("steps"), registry
                )
                if isinstance(_resolved, list):
                    plan["steps"] = _resolved
        except Exception:
            pass
        # 阶段3：Stage 级并行产出（录制期分组，供回放 Stage 级并行）
        # 仅当 PC 步骤 ≥1 且手机步骤 ≥1 且端切换 ≥1 才产出；单端用例 stages 为空（兼容红线）
        _final_steps = plan.get("steps") or []
        stages = _group_steps_into_stages(_final_steps)
        plan["stages"] = stages
        # 展开写回每步 stage_info（随 plan 序列化，落库/回放自动携带，无需前端参与）
        for _st in stages:
            for _b in _st.get("branches") or []:
                for _s in _b.get("steps") or []:
                    if isinstance(_s, dict):
                        _s["stage_info"] = {
                            "stage_id": _st.get("id"),
                            "branch": _b.get("name"),
                            "layer": _b.get("layer"),
                            "device_id": _b.get("device_id", ""),
                            "allow_partial": _st.get("allow_partial", False),
                            "timeout_sec": _st.get("timeout_sec", 600),
                        }
        warnings = list(warnings1) + list(warnings2 or []) + list(warnings3 or [])
        return plan, warnings

    def _extract_target_from_text(self, text: str) -> str:
        """保留给兼容调用；过滤 JSON 字段名。"""
        m = _QUOTE_RE.search(text or "")
        if m:
            cand = m.group(1)[:80]
            if not self._is_bad_target(cand):
                return cand
        m = _PAREN_RE.search(text or "")
        if m:
            return m.group(1)[:80]
        m = _URL_RE.search(text or "")
        if m:
            return m.group()
        return (text or "")[:60]


def _side_for_layer(layer: str) -> str:
    """步骤自动化层 → 并行端（pc / mobile / ""=不参与分组）。"""
    layer = (layer or "").strip().lower()
    if layer in ("web", "desktop", "pc"):
        return "pc"
    if layer in ("android", "mobile"):
        return "mobile"
    return ""  # cross_end（extract_otp/api_call）等：留在线性步骤，不进任何分支


def _group_steps_into_stages(steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """将归一化步骤列表按端分组为并行 stage（录制期产出，供回放 Stage 级并行）。

    规则（对齐 multi_device_scheduler 的 stage schema）：
    1. 按 per-step automation_layer 分类：web|desktop → pc 侧；android|mobile → mobile 侧；
       cross_end（extract_otp/api_call）不参与分组（留在线性步骤序列）。
    2. 相邻同侧步骤合并为 run（段），两侧严格交替。
    3. 全局门槛：pc 步骤 ≥1 且 mobile 步骤 ≥1 且端切换 ≥1 次，否则返回 []（单端兼容红线，
       旧单端用例不产出 stages，回放零影响）。
    4. 两两配对 runs → 每个 stage 一个 pc 分支 + 一个 mobile 分支（缺失侧留空列表，
       schema 严格对齐 scheduler）；奇数尾段单独成 stage（单分支，回放端按串行处理）。
    5. mobile 分支 device_id 取该分支首个 android 步骤的 device_id（录制 serial 透传）。
    """
    if not isinstance(steps, list) or not steps:
        return []
    sides: List[str] = []
    for s in steps:
        if not isinstance(s, dict):
            continue
        sides.append(_side_for_layer(str(s.get("automation_layer") or "")))
    # 全局门槛：仅当双端都存在且出现端切换才产出（单端兼容红线）
    has_pc = any(sd == "pc" for sd in sides)
    has_mob = any(sd == "mobile" for sd in sides)
    switches = sum(
        1 for i in range(1, len(sides)) if sides[i] and sides[i - 1] and sides[i] != sides[i - 1]
    )
    if not (has_pc and has_mob and switches >= 1):
        return []

    # 相邻同侧步骤合并为 run（cross_end 步骤作为缝隙跳过，不阻断两侧）
    runs: List[Dict[str, Any]] = []
    cur_side = ""
    cur_steps: List[Dict[str, Any]] = []
    for s, sd in zip(steps, sides):
        if not sd:
            continue  # cross_end 等：不参与分组，但保留在线性步骤
        if cur_side and sd != cur_side:
            runs.append({"side": cur_side, "steps": cur_steps})
            cur_steps = []
        cur_side = sd
        cur_steps.append(s)
    if cur_side and cur_steps:
        runs.append({"side": cur_side, "steps": cur_steps})

    def _branch(run: Dict[str, Any]) -> Dict[str, Any]:
        if run["side"] == "pc":
            # 保留真实层：全 web → web；含 desktop → desktop；混合仍标 desktop 但步骤自带 layer
            layers = {
                str(s.get("automation_layer") or "").strip().lower()
                for s in (run.get("steps") or [])
                if isinstance(s, dict)
            }
            if layers and layers <= {"web"}:
                pc_layer = "web"
            elif "desktop" in layers:
                pc_layer = "desktop"
            else:
                pc_layer = "desktop"
            return {"name": "pc", "layer": pc_layer, "steps": run["steps"]}
        dev_id = ""
        for s in run["steps"]:
            if isinstance(s, dict) and s.get("device_id"):
                dev_id = str(s["device_id"])
                break
        return {"name": "mobile", "layer": "mobile", "steps": run["steps"], "device_id": dev_id}

    def _stage(idx: int, pc_run: Optional[Dict[str, Any]], mob_run: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        branches: List[Dict[str, Any]] = []
        if pc_run:
            branches.append(_branch(pc_run))
        if mob_run:
            branches.append(_branch(mob_run))
        return {
            "id": f"stage_{idx}",
            "cross_end_parallel": True,
            "branches": branches,
            "allow_partial": False,
            "timeout_sec": 600,
        }

    stages: List[Dict[str, Any]] = []
    i = 0
    stage_no = 1
    while i < len(runs):
        cur = runs[i]
        if i + 1 < len(runs):
            nxt = runs[i + 1]
            if cur["side"] != nxt["side"]:
                pc_run = cur if cur["side"] == "pc" else nxt
                mob_run = cur if cur["side"] == "mobile" else nxt
                stages.append(_stage(stage_no, pc_run, mob_run))
                stage_no += 1
                i += 2
                continue
        # 奇数尾段：单独成 stage（单分支，回放端按串行处理）
        pc_run = cur if cur["side"] == "pc" else None
        mob_run = cur if cur["side"] == "mobile" else None
        stages.append(_stage(stage_no, pc_run, mob_run))
        stage_no += 1
        i += 1
    return stages
