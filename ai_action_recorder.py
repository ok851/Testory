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

from logger import uat_logger


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


def sanitize_target(target: str) -> str:
    """清理浏览器工具 result 中的 UI 噪声后缀，如 (class)、(已聚焦) 等。"""
    if not target:
        return ""
    t = str(target).strip()
    t = _NOISE_PAREN_RE.sub("", t).strip()
    t = re.sub(r"\s+", " ", t).strip()
    return t


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

    def __init__(self, *, vision_enabled: bool = False, platform: str = "web"):
        self.records: List[ActionRecord] = []
        self.vision_enabled = vision_enabled
        self.platform = platform

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
        """
        args = args if isinstance(args, dict) else {}
        name = (name or "tool").strip() or "tool"
        ok: Optional[bool] = None
        verified: Optional[bool] = None
        summary = ""
        target = ""
        is_browser_tool = name.startswith("browser_")
        is_desktop_tool = name.startswith("windows_")
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
                raw_target = str(
                    result.get("matched")
                    or result.get("selector")
                    or result.get("ref")
                    or result.get("element")
                    or result.get("key")
                    or result.get("url")
                    or ""
                )[:80]
                if not raw_target:
                    raw_target = str(
                        args.get("ref")
                        or args.get("text")
                        or args.get("selector")
                        or args.get("url")
                        or args.get("element")
                        or ""
                    )[:80]
                target = sanitize_target(raw_target)
            
            # ── 桌面工具：优先用 app_name/title/label 等窗口标识 ──
            elif is_desktop_tool:
                raw_target = str(
                    result.get("app_name")
                    or result.get("title")
                    or result.get("label")
                    or result.get("matched")
                    or result.get("description")
                    or ""
                )[:80]
                if not raw_target:
                    raw_target = str(
                        args.get("app")
                        or args.get("text")
                        or args.get("app_name")
                        or args.get("title")
                        or args.get("path")
                        or ""
                    )[:80]
                target = sanitize_target(raw_target)
            
            # ── 移动端工具：优先用 package_name/text/resource_id 等元素标识 ──
            elif is_mobile_tool:
                raw_target = str(
                    result.get("text")
                    or result.get("resource_id")
                    or result.get("content_desc")
                    or result.get("package_name")
                    or result.get("app_name")
                    or result.get("description")
                    or ""
                )[:80]
                if not raw_target:
                    raw_target = str(
                        args.get("text")
                        or args.get("selector")
                        or args.get("package")
                        or args.get("app")
                        or args.get("resource_id")
                        or ""
                    )[:80]
                target = sanitize_target(raw_target)
            
            # ── 其他跨端/通用工具 ──
            else:
                target = sanitize_target(str(
                    result.get("matched")
                    or result.get("app_name")
                    or result.get("description")
                    or result.get("key")
                    or args.get("app")
                    or args.get("text")
                    or args.get("instruction")
                    or ""
                )[:80])
        elif result is not None:
            summary = str(result)[:200]
            low = summary.lower()
            if '"ok": false' in low or '"success": false' in low:
                ok = False
            elif '"ok": true' in low or '"success": true' in low:
                ok = True
        if not target:
            raw_target = str(
                args.get("app")
                or args.get("text")
                or args.get("element")
                or args.get("action")
                or args.get("name")
                or args.get("url")
                or args.get("title")
                or args.get("package")
                or name
            )[:80]
            target = sanitize_target(raw_target)
        # 跳过：target 是状态观察文本（如"后变为禁用态"），不应作为独立步骤
        if is_state_observation(target) and is_platform_tool:
            # 平台工具的状态观察：用 args 构造一个有意义的 target
            if is_browser_tool:
                alt = str(args.get("ref") or args.get("text") or args.get("selector") or "")[:60]
            elif is_desktop_tool:
                alt = str(args.get("app") or args.get("text") or args.get("title") or "")[:60]
            elif is_mobile_tool:
                alt = str(args.get("text") or args.get("selector") or args.get("package") or "")[:60]
            else:
                alt = ""
            if alt:
                target = sanitize_target(alt)
            else:
                target = name.replace("browser_", "", 1).replace("windows_", "", 1).replace("mobile_", "", 1)
        if self._is_bad_target(target):
            target = name
        # 跳过负向片段污染的 target（如包含"失败或风险点"）
        if contains_negative_snippet(target) and is_platform_tool:
            if is_browser_tool:
                target = sanitize_target(str(args.get("ref") or args.get("text") or name)[:60])
            elif is_desktop_tool:
                target = sanitize_target(str(args.get("app") or args.get("text") or name)[:60])
            elif is_mobile_tool:
                target = sanitize_target(str(args.get("text") or args.get("selector") or name)[:60])
            else:
                target = sanitize_target(str(args.get("text") or name)[:60])
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
        rec = ActionRecord(
            action_id=f"act_{len(self.records)}",
            action_type=self._normalize_action_type(name, args),
            target=target or name,
            input_data=str(args.get("text") or args.get("input_value") or "")[:500],
            result=summary or f"{name}",
            status=st,
            raw_text=json.dumps({"name": name, "args": args}, ensure_ascii=False)[:2000],
        )
        self.records.append(rec)
        return [rec]

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
                )
            )
        return out

    @staticmethod
    def _is_bad_target(target: str) -> bool:
        t = (target or "").strip().lower()
        return (not t) or t in _BAD_TARGETS or t in ('"ok"', "'ok'")

    @staticmethod
    def _normalize_action_type(name: str, args: Dict[str, Any]) -> str:
        n = (name or "").strip()
        # Web 浏览器工具
        if n.startswith("browser_"):
            return n.replace("browser_", "", 1)
        # 桌面工具
        if n.startswith("windows_"):
            base = n.replace("windows_", "", 1)
            # 映射桌面工具到标准步骤类型
            _DESKTOP_MAP = {
                "launch_app": "launch_app",
                "focus_app": "launch_app",
                "click_text": "click",
                "click": "click",
                "type_text": "input",
                "input": "input",
                "press_key": "hotkey",
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
            from ai_external_browser_bridge import get_probe_registry

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
            step: Dict[str, Any] = {
                "action": rec.action_type,
                "target": rec.target,
                "input_value": rec.input_data,
                "description": rec.result[:100] if rec.result else "",
                "automation_layer": self.platform
                if self.platform in ("web", "desktop", "android")
                else "web",
            }
            # ── 跨端/api 步骤：仅当 raw_name 明确匹配时才做特殊参数恢复 ──
            _raw_name = ""
            _raw_args: Dict[str, Any] = {}
            try:
                raw = json.loads(rec.raw_text or "{}")
                _raw_name = raw.get("name") or ""
                _raw_args = raw.get("args") if isinstance(raw.get("args"), dict) else {}
            except Exception:
                pass
            if _raw_name == "mobile_extract_otp" or rec.action_type == "extract_otp":
                step["action"] = "extract_otp"
                step["automation_layer"] = "cross_end"
                cross_spec = {}
                if _raw_args.get("timeout_sec"):
                    cross_spec["timeout_sec"] = _raw_args["timeout_sec"]
                if _raw_args.get("sender_hint"):
                    cross_spec["sender_hint"] = _raw_args["sender_hint"]
                if _raw_args.get("pattern"):
                    cross_spec["pattern"] = _raw_args["pattern"]
                step["cross_end_spec"] = json.dumps(cross_spec, ensure_ascii=False)
                step["description"] = step.get("description") or "提取手机短信验证码"
            elif _raw_name == "desktop_type_text":
                step["action"] = "input"
                step["automation_layer"] = "desktop"
                if _raw_args.get("text"):
                    step["input_value"] = str(_raw_args["text"])[:500]
                if _raw_args.get("clear") is not None:
                    ds = json.loads(step.get("desktop_spec") or "{}") if step.get("desktop_spec") else {}
                    ds["clear"] = bool(_raw_args["clear"])
                    step["desktop_spec"] = json.dumps(ds, ensure_ascii=False)
            elif _raw_name == "api_call" or rec.action_type == "api_call":
                step["action"] = "api_call"
                step["automation_layer"] = "cross_end"
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
            if rec.locator:
                step["locator"] = rec.locator
            hit = probe_by_text.get((rec.target or "").strip())
            if hit:
                if hit.get("i") is not None:
                    step["probe_index"] = hit.get("i")
                css = (hit.get("css") or hit.get("selector") or "").strip()
                if css and not step.get("locator"):
                    step["locator"] = css
                    step["target"] = css
            if self.platform == "desktop":
                step["selector_type"] = "window"
                if rec.action_type == "launch_app":
                    step["input_value"] = rec.target
                elif rec.action_type == "hotkey":
                    step["input_value"] = rec.target
            if rec.vision_info:
                step["vision_info"] = rec.vision_info
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
        from ai_step_normalization import (
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
        if plat in ("auto", "all", "cross"):
            plat = "web"
        if plat not in ("web", "desktop", "android"):
            plat = "web"
        normalized = [normalize_ai_step(s) for s in raw]
        warnings1 = repair_raw_ai_steps_for_platform(normalized) or []
        clean, warnings2 = dedupe_and_validate_ai_steps(normalized, platform=plat)
        plan = {
            "case_name": (case_name or instruction or "AI 生成用例")[:80],
            "case_url": case_url or "",
            "description": (instruction or "")[:400],
            "steps": clean,
            "platform": plat,
            "meta": {"source": "action_recorder", "platform_type": plat},
        }
        plan, warnings3 = apply_step_normalization_to_plan(plan)
        try:
            from ai_external_browser_bridge import get_probe_registry
            from ai_locator_resolution import resolve_plan_steps_locators_with_snapshot

            registry = get_probe_registry()
            if registry and plan.get("steps"):
                plan = resolve_plan_steps_locators_with_snapshot(plan, registry)
        except Exception:
            pass
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
