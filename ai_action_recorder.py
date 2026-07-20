"""
动作记录器：从 Hermes 执行结果中提取结构化动作，按需触发视觉分析。
不拦截 Hermes 的执行——只观测和记录。
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from logger import uat_logger


@dataclass
class ActionRecord:
    action_id: str = ""
    action_type: str = ""        # navigate / click / input / wait / assert / api_request
    target: str = ""             # URL / 选择器 / 接口路径
    locator: str = ""            # 元素定位器
    input_data: str = ""         # 输入值
    result: str = ""             # 执行结果摘要
    status: str = "success"     # success / fail / skipped
    timestamp: float = field(default_factory=time.time)
    screenshot: str = ""         # 截图路径（可选）
    vision_info: Optional[Dict[str, Any]] = None  # 视觉识别结果（可选）
    raw_text: str = ""           # 原始文本片段


# 匹配 URL 的正则
_URL_RE = re.compile(r'https?://[^\s<>"\']+', re.IGNORECASE)
# 匹配引号内容
_QUOTE_RE = re.compile(r'[""\']([^"\']+)[""\']')
# 匹配括号内容
_PAREN_RE = re.compile(r'[（(]([^)）]+)[)）]')


class ActionRecorder:
    """从 Hermes 返回文本中提取结构化动作，按需触发视觉分析。"""

    def __init__(self, *, vision_enabled: bool = False, platform: str = "web"):
        self.records: List[ActionRecord] = []
        self.vision_enabled = vision_enabled
        self.platform = platform

    def capture_from_hermes_result(self, result_text: str) -> List[ActionRecord]:
        """
        从 Hermes 返回的文本中提取结构化动作。
        Hermes 输出包含访问过的 URL、操作描述、发现的元素等。
        """
        if not result_text:
            return []
        new_records: List[ActionRecord] = []
        lines = result_text.split("\n")

        for line in lines:
            line = line.strip()
            if not line:
                continue

            line_lower = line.lower()
            rec: Optional[ActionRecord] = None

            # 检测 URL 导航
            url_match = _URL_RE.search(line)
            if url_match and any(kw in line_lower for kw in
                                 ["访问", "导航", "打开", "navigate", "visited", "opened", "goto", "前往"]):
                rec = ActionRecord(
                    action_id=f"act_{len(self.records)}",
                    action_type="navigate",
                    target=url_match.group(),
                    result=line[:200],
                    raw_text=line,
                )

            # 检测点击操作（增加"按下了""提交了"等常见中文表达）
            elif any(kw in line_lower for kw in
                     ["点击", "click", "press", "tap", "按下", "单击", "按下了", "触发了", "激活了"]):
                rec = ActionRecord(
                    action_id=f"act_{len(self.records)}",
                    action_type="click",
                    target=self._extract_target_from_text(line),
                    result=line[:200],
                    raw_text=line,
                )

            # 检测输入操作（增加"填入了""录入了""清空了"等）
            elif any(kw in line_lower for kw in
                     ["输入", "input", "type", "填写", "enter", "录入", "填入了", "录入了", "键入了", "清空了", "删除了"]):
                rec = ActionRecord(
                    action_id=f"act_{len(self.records)}",
                    action_type="input",
                    target=self._extract_target_from_text(line),
                    input_data=self._extract_input_value(line),
                    result=line[:200],
                    raw_text=line,
                )

            # 检测选择/下拉操作
            elif any(kw in line_lower for kw in
                     ["选择", "select", "下拉", "切换", "checkbox", "radio", "勾选了", "取消了"]):
                rec = ActionRecord(
                    action_id=f"act_{len(self.records)}",
                    action_type="select",
                    target=self._extract_target_from_text(line),
                    result=line[:200],
                    raw_text=line,
                )

            # 检测等待
            elif any(kw in line_lower for kw in
                     ["等待", "wait", "sleep", "暂停", "停顿", "延迟"]):
                rec = ActionRecord(
                    action_id=f"act_{len(self.records)}",
                    action_type="wait",
                    target=self._extract_target_from_text(line),
                    result=line[:200],
                    raw_text=line,
                )

            # 检测断言/验证
            elif any(kw in line_lower for kw in
                     ["验证", "assert", "检查", "verify", "确认", "expect", "校验", "比对", "匹配"]):
                rec = ActionRecord(
                    action_id=f"act_{len(self.records)}",
                    action_type="assert",
                    target=line[:120],
                    result=line[:200],
                    raw_text=line,
                )

            # 检测滚动
            elif any(kw in line_lower for kw in
                     ["滚动", "scroll", "滑动", "拖拽", "drag"]):
                rec = ActionRecord(
                    action_id=f"act_{len(self.records)}",
                    action_type="scroll",
                    target=self._extract_target_from_text(line),
                    result=line[:200],
                    raw_text=line,
                )

            # 检测提交/登录（独立的业务动作）
            elif any(kw in line_lower for kw in
                     ["提交", "submit", "登录", "logout", "登出", "注册", "保存", "发送"]):
                rec = ActionRecord(
                    action_id=f"act_{len(self.records)}",
                    action_type="submit",
                    target=self._extract_target_from_text(line),
                    result=line[:200],
                    raw_text=line,
                )

            if rec:
                new_records.append(rec)

        self.records.extend(new_records)

        # 按需触发视觉分析
        if self.vision_enabled and new_records:
            self._trigger_vision_for_records(new_records)

        return new_records

    def _trigger_vision_for_records(self, records: List[ActionRecord]):
        """
        按需触发视觉分析——只在 vision_enabled 时触发，复用 ai_vision_local 熔断器保护。
        视觉分析失败不影响主流程。
        """
        try:
            from ai_vision_local import ocr_region_png, _vision_breaker
            # 熔断器检查
            if hasattr(_vision_breaker, "allow") and not _vision_breaker.allow():
                return

            from ai_external_browser_bridge import capture_screenshot
            png = capture_screenshot()
            if not png:
                return

            # OCR 提取文本（ocr_region_png 接受 bytes）
            ocr_text = ""
            try:
                ocr_text = ocr_region_png(png)
                if hasattr(_vision_breaker, "record_success"):
                    _vision_breaker.record_success()
            except Exception as e:
                if hasattr(_vision_breaker, "record_failure"):
                    _vision_breaker.record_failure()
                uat_logger.debug("ActionRecorder OCR 失败: %s", e)

            # 将视觉信息附加到最新记录
            if records and ocr_text:
                records[-1].vision_info = {
                    "ocr_text": ocr_text[:500],
                    "screenshot": "captured",
                }
        except ImportError:
            pass
        except Exception as e:
            uat_logger.debug("ActionRecorder 视觉分析失败: %s", e)

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
            step: Dict[str, Any] = {
                "action": rec.action_type,
                "target": rec.target,
                "input_value": rec.input_data,
                "description": rec.result[:100] if rec.result else "",
                "automation_layer": self.platform if self.platform in ("web", "desktop", "android") else "web",
            }
            if rec.locator:
                step["locator"] = rec.locator
            # 尝试把可见文案绑到 probe ref，便于回放
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
            plat = "web"  # normalize 管线默认按 web；步骤可自带 automation_layer
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
        """从文本中提取操作目标。"""
        # 尝试提取引号中的内容
        m = _QUOTE_RE.search(text)
        if m:
            return m.group(1)[:80]
        # 尝试提取括号中的内容
        m = _PAREN_RE.search(text)
        if m:
            return m.group(1)[:80]
        # 尝试提取 URL
        m = _URL_RE.search(text)
        if m:
            return m.group()
        return text[:60]

    def _extract_input_value(self, text: str) -> str:
        """从文本中提取输入值。"""
        # 尝试引号内容
        m = _QUOTE_RE.search(text)
        if m:
            return m.group(1)[:100]
        # 尝试 "输入/填写 xxx 到/into" 模式
        m = re.search(r'(?:输入|input|type|填写|enter|录入)[:\s]+(.+?)(?:到|into|to|$)',
                      text, re.IGNORECASE)
        if m:
            return m.group(1).strip()[:100]
        return ""
