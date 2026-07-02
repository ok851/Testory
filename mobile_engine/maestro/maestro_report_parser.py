# -*- coding: utf-8 -*-
"""
Maestro JUnit XML 报告解析器。

解析 Maestro 输出的 JUnit XML 报告，转换为统一的 FlowResult / StepResult 格式。
关联截图、录像、日志等输出文件。
"""

from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional

from mobile_engine.engine_interface import (
    FlowResult,
    FlowStep,
    LocatorInfo,
    StepResult,
    StepStatus,
)

try:
    from uat_logger import uat_logger
except ImportError:
    import logging

    uat_logger = logging.getLogger(__name__)


class MaestroReportParser:
    """Maestro JUnit XML 报告 → 统一 FlowResult"""

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    def parse(self, report_xml_path: str) -> FlowResult:
        """
        解析 Maestro JUnit XML 报告文件。

        Args:
            report_xml_path: JUnit XML 文件路径

        Returns:
            FlowResult
        """
        if not os.path.isfile(report_xml_path):
            uat_logger.warning("报告文件不存在: %s", report_xml_path)
            return FlowResult(
                steps=[], total_duration_ms=0.0,
                passed_count=0, failed_count=0,
            )

        report_dir = os.path.dirname(report_xml_path)
        tree = ET.parse(report_xml_path)
        root = tree.getroot()

        # JUnit XML 结构: <testsuite> → <testcase>
        # Maestro 特殊属性: <testcase name="step_name" classname="flow_name" time="...">
        steps: List[StepResult] = []
        total_duration = 0.0
        passed = 0
        failed = 0

        for testsuite in root.iter("testsuite"):
            for testcase in testsuite.iter("testcase"):
                name = testcase.get("name", "")
                classname = testcase.get("classname", "")
                time_str = testcase.get("time", "0")
                try:
                    step_time = float(time_str) * 1000  # 转为毫秒
                except ValueError:
                    step_time = 0.0

                # 查找失败信息
                failure_elem = testcase.find("failure")
                error_elem = testcase.find("error")

                if failure_elem is not None or error_elem is not None:
                    status = StepStatus.FAILED
                    failed += 1
                    error_msg = ""
                    if failure_elem is not None:
                        error_msg = failure_elem.get("message", "") or failure_elem.text or ""
                    elif error_elem is not None:
                        error_msg = error_elem.get("message", "") or error_elem.text or ""
                else:
                    status = StepStatus.SUCCESS
                    passed += 1
                    error_msg = ""

                total_duration += step_time

                # 推断 action 类型 (从步骤名称)
                action = self._infer_action(name)

                # 关联截图 (Maestro 截图在报告目录下的 screenshots/ 子目录中)
                screenshot_path = self._find_screenshot(report_dir, name)

                # 关联 dump 文件
                dump_path = self._find_dump(report_dir, name)

                step_result = StepResult(
                    status=status,
                    action=action,
                    description=name,
                    duration_ms=step_time,
                    error=error_msg.strip() if error_msg else "",
                    screenshot_path=screenshot_path,
                    dump_path=dump_path,
                )
                steps.append(step_result)

        # 关联视频
        video_path = self._find_video(report_dir)

        return FlowResult(
            steps=steps,
            total_duration_ms=total_duration,
            passed_count=passed,
            failed_count=failed,
            video_path=video_path,
            raw_report_path=report_xml_path,
        )

    def parse_to_db_records(
        self,
        report_xml_path: str,
        run_history_id: int,
        case_id: int = 0,
    ) -> List[Dict[str, Any]]:
        """
        解析报告并转为可直接写入 step_results 表的记录。

        Returns:
            [{ "run_history_id": ..., "step_order": ..., "action": ..., ... }, ...]
        """
        flow_result = self.parse(report_xml_path)
        records = []
        for i, step in enumerate(flow_result.steps):
            records.append({
                "run_history_id": run_history_id,
                "step_order": i,
                "action": step.action,
                "description": step.description,
                "status": step.status.value,
                "error": step.error,
                "screenshot": step.screenshot_path,
                "duration": step.duration_ms / 1000.0,
                "healed_locator": "",
                "locator_strategy": "",
                "visual_confidence": step.match_confidence,
            })
        return records

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    @staticmethod
    def _infer_action(name: str) -> str:
        """从 Maestro 步骤名称推断 action 类型"""
        name_lower = name.lower()
        name_clean = name_lower.split("(")[0].strip()

        action_keywords = {
            "tap": "tap",
            "click": "tap",
            "input": "input",
            "swipe": "swipe",
            "scroll": "scroll",
            "assert visible": "assert",
            "assert not visible": "assert",
            "wait": "wait",
            "launch": "launch_app",
            "stop app": "stop_app",
            "back": "back",
            "screenshot": "screenshot",
            "long press": "long_press",
            "press key": "press_key",
        }

        for keyword, action in action_keywords.items():
            if keyword in name_clean:
                return action
        return "unknown"

    @staticmethod
    def _find_screenshot(report_dir: str, step_name: str) -> str:
        """在报告目录中查找对应步骤的截图"""
        # Maestro 截图命名格式: <step_name>.png 或 screenshot_<n>.png
        rp = Path(report_dir)

        # 1) 精确名称匹配
        safe_name = re.sub(r'[^\w\-_]', '_', step_name)
        for ext in (".png", ".jpg", ".jpeg"):
            candidate = rp / f"{safe_name}{ext}"
            if candidate.exists():
                return str(candidate)

        # 2) 在 screenshots/ 子目录查找
        screenshots_dir = rp / "screenshots"
        if screenshots_dir.exists():
            for img in screenshots_dir.glob(f"*{safe_name[:20]}*.png"):
                return str(img)
            all_screenshots = sorted(screenshots_dir.glob("*.png"))
            if all_screenshots:
                return str(all_screenshots[-1])

        # 3) 模糊匹配
        for img in rp.glob(f"*{safe_name[:15]}*.png"):
            return str(img)

        return ""

    @staticmethod
    def _find_dump(report_dir: str, step_name: str) -> str:
        """查找视图层级 dump 文件"""
        rp = Path(report_dir)
        for xml_file in rp.glob("*.xml"):
            if "dump" in xml_file.name.lower() or "hierarchy" in xml_file.name.lower():
                return str(xml_file)
        return ""

    @staticmethod
    def _find_video(report_dir: str) -> str:
        """查找 Maestro 录制视频"""
        rp = Path(report_dir)
        for vid in rp.rglob("*.mp4"):
            return str(vid)
        for vid in rp.rglob("*.webm"):
            return str(vid)
        return ""
