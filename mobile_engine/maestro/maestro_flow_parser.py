# -*- coding: utf-8 -*-
"""
Maestro YAML 流解析器。

将 Maestro YAML 文件逆向解析为引擎无关的 FlowStep[] 内部 DSL。
支持 Maestro 1.x 和 2.x YAML 格式。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from mobile_engine.engine_interface import (
    FlowStep,
    LocatorInfo,
    LocatorStrategy,
)

try:
    from uat_logger import uat_logger
except ImportError:
    import logging

    uat_logger = logging.getLogger(__name__)


class MaestroFlowParser:
    """将 Maestro YAML 解析为 FlowStep[] 内部 DSL"""

    # Maestro 命令 → FlowStep action 映射
    COMMAND_ACTION_MAP: Dict[str, str] = {
        "launchApp": "launch_app",
        "stopApp": "stop_app",
        "tapOn": "tap",
        "longPressOn": "long_press",
        "inputText": "input",
        "swipe": "swipe",
        "scroll": "scroll",
        "assertVisible": "assert",
        "assertNotVisible": "assert",
        "extendedWaitUntil": "wait",
        "takeScreenshot": "screenshot",
        "back": "back",
        "pressKey": "press_key",
        "eraseText": "input",        # erase + input 视为 input
        "hideKeyboard": "press_key",  # 收起键盘
        "doubleTapOn": "tap",
    }

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    def parse(self, yaml_content: str) -> List[FlowStep]:
        """
        解析 Maestro YAML 内容为 FlowStep 列表。

        Args:
            yaml_content: Maestro YAML 字符串

        Returns:
            FlowStep 列表
        """
        try:
            import yaml as _yaml
        except ImportError:
            uat_logger.warning("PyYAML 未安装，使用简单行解析")
            return self._parse_manual(yaml_content)

        try:
            docs = list(_yaml.safe_load_all(yaml_content))
        except _yaml.YAMLError as exc:
            uat_logger.error("YAML 解析失败: %s", exc)
            return []

        steps: List[FlowStep] = []
        for doc in docs:
            if doc is None:
                continue
            if isinstance(doc, dict):
                # YAML 文档 — 可能是配置头 (appId)
                if "appId" in doc:
                    continue
                parsed = self._parse_command(doc)
                if parsed:
                    steps.append(parsed)
            elif isinstance(doc, list):
                for cmd in doc:
                    parsed = self._parse_command(cmd)
                    if parsed:
                        steps.append(parsed)

        return steps

    def parse_file(self, yaml_path: str) -> List[FlowStep]:
        """从文件解析 Maestro YAML"""
        with open(yaml_path, "r", encoding="utf-8") as f:
            content = f.read()
        return self.parse(content)

    def extract_app_id(self, yaml_content: str) -> str:
        """从 YAML 中提取 appId"""
        m = re.search(r'appId:\s*["\']?([^"\'#\n\r]+)', yaml_content)
        if m:
            return m.group(1).strip()
        return ""

    # ------------------------------------------------------------------
    # 命令解析
    # ------------------------------------------------------------------

    def _parse_command(self, cmd: Any) -> Optional[FlowStep]:
        """解析单个 Maestro YAML 命令"""
        if not isinstance(cmd, dict):
            return None

        for key, value in cmd.items():
            action = self.COMMAND_ACTION_MAP.get(key)
            if action is None:
                continue

            step = FlowStep(action=action)
            locator = self._parse_locator(value)
            if locator:
                step.locator = locator

            # 提取描述
            if isinstance(value, str):
                step.maestro_label = value
            elif isinstance(value, dict):
                step.maestro_label = value.get("text", value.get("id", ""))

            # 特殊处理
            if key == "swipe":
                self._parse_swipe(value, step)
            elif key == "extendedWaitUntil":
                self._parse_wait(value, step)
            elif key == "takeScreenshot":
                step.maestro_label = str(value) if isinstance(value, str) else ""
            elif key == "pressKey":
                step.input_value = str(value) if isinstance(value, str) else ""
            elif key in ("assertNotVisible",):
                step.assert_type = "not_visible"

            return step

        return None

    def _parse_locator(self, value: Any) -> Optional[LocatorInfo]:
        """从 Maestro 选择器值解析 LocatorInfo"""
        if isinstance(value, str):
            val = value.strip()

            # 纯文本定位
            if not val.startswith(("id:", "text:", "point:", "below", "rightOf",
                                    "above", "leftOf")):
                return LocatorInfo.from_text(val)

            # id:xxx 格式
            if val.startswith("id:"):
                return LocatorInfo.from_id(val[3:].strip())

            # text:xxx 格式
            if val.startswith("text:"):
                return LocatorInfo.from_text(val[5:].strip().strip('"'))

            # point: x%,y% 格式
            if val.startswith("point:"):
                return LocatorInfo(
                    strategy=LocatorStrategy.COORDINATE,
                    value=val[6:].strip(),
                )

            # 相对定位
            for direction in ("below", "rightOf", "above", "leftOf"):
                if val.startswith(direction):
                    target = val[len(direction):].strip().strip('"')
                    return LocatorInfo.from_relative(direction, target)

            return LocatorInfo.from_text(val)

        elif isinstance(value, dict):
            # 结构化选择器: {id: xxx, text: xxx, ...}
            if "id" in value:
                return LocatorInfo.from_id(str(value["id"]))
            if "text" in value:
                return LocatorInfo.from_text(str(value["text"]))
            if "point" in value:
                return LocatorInfo(
                    strategy=LocatorStrategy.COORDINATE,
                    value=str(value["point"]),
                )

        return None

    def _parse_swipe(self, value: Any, step: FlowStep) -> None:
        """解析 swipe 命令的附加参数"""
        if isinstance(value, dict):
            step.swipe_direction = str(value.get("direction", "UP")).lower()
            step.swipe_duration_ms = int(value.get("duration", 400))
            if "start" in value:
                step.swipe_start = str(value["start"])
            if "end" in value:
                step.swipe_end = str(value["end"])

    def _parse_wait(self, value: Any, step: FlowStep) -> None:
        """解析 extendedWaitUntil 的超时参数"""
        if isinstance(value, dict):
            step.wait_timeout_ms = int(value.get("timeout", 10000))

    # ------------------------------------------------------------------
    # 手动解析 (无 PyYAML 依赖时的回退)
    # ------------------------------------------------------------------

    def _parse_manual(self, yaml_content: str) -> List[FlowStep]:
        """简单手动行解析 (去除了 YAML 文档分隔符 etc.)"""
        steps: List[FlowStep] = []
        current_step: Optional[FlowStep] = None

        for line in yaml_content.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if stripped == "---":
                continue

            # 匹配 - commandName: value 模式
            m = re.match(r'-\s+(\w+):\s*["\']?(.+)["\']?$', stripped)
            if m:
                cmd = m.group(1)
                val = m.group(2).rstrip('"\'')
                action = self.COMMAND_ACTION_MAP.get(cmd)
                if action:
                    step = FlowStep(action=action)
                    locator = self._parse_locator(val)
                    if locator:
                        step.locator = locator
                    steps.append(step)

        return steps
