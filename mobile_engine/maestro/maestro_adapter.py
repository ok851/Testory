# -*- coding: utf-8 -*-
"""
Maestro 引擎适配器 — 主力引擎实现。

将 MobileTestEngine 抽象接口适配到 Maestro CLI 执行引擎。
完整流程:
  1. FlowStep[] → Maestro YAML (MaestroFlowGenerator)
  2. YAML 文件写入临时目录
  3. 调用 `maestro test flow.yaml` (MaestroCLI)
  4. 解析 JUnit XML 报告 (MaestroReportParser)
  5. 收集截图 → 映射到 StepResult
  6. 返回统一 FlowResult
"""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from mobile_engine.engine_interface import (
    DeviceInfo,
    EngineType,
    FlowResult,
    FlowStep,
    LocatorInfo,
    MobileTestEngine,
    StepResult,
    StepStatus,
)
from mobile_engine.maestro.maestro_binary_manager import MaestroBinaryManager
from mobile_engine.maestro.maestro_cli import MaestroCLI
from mobile_engine.maestro.maestro_device_checker import MaestroDeviceChecker
from mobile_engine.maestro.maestro_flow_generator import MaestroFlowGenerator
from mobile_engine.maestro.maestro_report_parser import MaestroReportParser

try:
    from uat_logger import uat_logger
except ImportError:
    import logging

    uat_logger = logging.getLogger(__name__)


class MaestroAdapter(MobileTestEngine):
    """Maestro 引擎适配器 — 主力引擎"""

    def __init__(
        self,
        *,
        binary_mgr: Optional[MaestroBinaryManager] = None,
        cli: Optional[MaestroCLI] = None,
        flow_gen: Optional[MaestroFlowGenerator] = None,
        report_parser: Optional[MaestroReportParser] = None,
        device_checker: Optional[MaestroDeviceChecker] = None,
    ):
        super().__init__()
        self._binary_mgr = binary_mgr or MaestroBinaryManager()
        self._cli = cli or MaestroCLI(binary_mgr=self._binary_mgr)
        self._flow_gen = flow_gen or MaestroFlowGenerator()
        self._report_parser = report_parser or MaestroReportParser()
        self._device_checker = device_checker or MaestroDeviceChecker()
        self._temp_dir: Optional[str] = None
        self._last_flow_file: str = ""

    # ========================================================================
    # 元信息
    # ========================================================================

    @property
    def engine_type(self) -> EngineType:
        return EngineType.MAESTRO

    @property
    def engine_version(self) -> str:
        try:
            ver = self._cli.check_maestro_version()
            return ver or self._binary_mgr.DEFAULT_VERSION
        except Exception:
            return self._binary_mgr.DEFAULT_VERSION

    # ========================================================================
    # 设备管理
    # ========================================================================

    def connect_device(self, device: DeviceInfo) -> bool:
        """连接设备并执行前置检查"""
        self._device = device

        # 自动下载/校验 Maestro
        from modules.mobile.mobile_env_config import maestro_auto_install

        if maestro_auto_install():
            try:
                self._binary_mgr.ensure_installed()
            except Exception as exc:
                uat_logger.error("Maestro 安装失败: %s", exc)
                return False

        # 设备自检
        result = self.check_device_readiness()
        if not result["all_passed"]:
            errors = result.get("errors", [])
            uat_logger.error("设备自检失败: %s", "; ".join(errors))
            return False

        uat_logger.info("Maestro 设备就绪: %s", device.udid)
        return True

    def disconnect_device(self) -> None:
        self._device = None
        self._cleanup_temp()

    def check_device_readiness(self) -> Dict[str, Any]:
        """设备前置自检"""
        if not self._device:
            return {"all_passed": False, "errors": ["未连接设备"], "warnings": [], "checks": []}

        return self._device_checker.run_checks(
            self._device.udid,
            expected_package=self._device.app_package,
        )

    def install_app(self, app_path: str) -> bool:
        if not self._device:
            return False
        result = self._cli.install_app_via_maestro(app_path, self._device.udid)
        return result.success

    def uninstall_app(self, package_name: str) -> bool:
        if not self._device:
            return False
        import subprocess

        try:
            proc = subprocess.run(
                ["adb", "-s", self._device.udid, "uninstall", package_name],
                capture_output=True, text=True, timeout=30, check=False,
            )
            return proc.returncode == 0
        except Exception:
            return False

    def launch_app(self, package_name: str, activity: str = "") -> bool:
        if not self._device:
            return False
        return self._device_checker.launch_app(
            self._device.udid, package_name, activity,
        )[0]

    def stop_app(self, package_name: str) -> bool:
        if not self._device:
            return False
        import subprocess

        try:
            subprocess.run(
                ["adb", "-s", self._device.udid, "shell", "am", "force-stop", package_name],
                timeout=10, check=False,
            )
            return True
        except Exception:
            return False

    def capture_screenshot(self) -> bytes:
        if not self._device:
            return b""
        from modules.mobile.mobile_device_manager import capture_screenshot_png

        png = capture_screenshot_png(self._device.udid)
        return png or b""

    def capture_screenshot_to_file(self, output_path: str) -> str:
        png = self.capture_screenshot()
        Path(output_path).write_bytes(png)
        return output_path

    def start_recording(self) -> None:
        """Maestro 录制由 CLI --record 模式处理"""
        pass

    def stop_recording(self) -> str:
        return ""

    # ========================================================================
    # 测试流执行
    # ========================================================================

    def execute_flow(self, flow: List[FlowStep]) -> FlowResult:
        """
        核心执行路径:
        1. FlowStep[] → Maestro YAML
        2. 写入临时文件
        3. 执行 maestro test
        4. 解析报告
        """
        if not self._device:
            return FlowResult(
                steps=[], total_duration_ms=0, passed_count=0, failed_count=0,
            )

        if not flow:
            return FlowResult(
                steps=[], total_duration_ms=0, passed_count=0, failed_count=0,
            )

        # 生成 YAML
        yaml_content = self._flow_gen.generate(
            flow,
            device=self._device,
            app_package=self._device.app_package,
        )

        # 写入临时文件
        flow_file = self._write_temp_yaml(yaml_content)
        self._last_flow_file = flow_file

        # 执行
        from modules.mobile.mobile_env_config import maestro_timeout_seconds

        timeout = maestro_timeout_seconds()
        cli_result = self._cli.run_test(
            flow_file,
            device_udid=self._device.udid,
            timeout=timeout,
        )

        # 解析报告
        if cli_result.report_xml and os.path.isfile(cli_result.report_xml):
            flow_result = self._report_parser.parse(cli_result.report_xml)
            flow_result.video_path = cli_result.video_path
            flow_result.raw_report_path = cli_result.report_xml
        elif cli_result.success:
            # 成功但没有 JUnit 报告 → 构造简单结果
            steps = [StepResult(
                status=StepStatus.SUCCESS,
                action=step.action,
                description=step.description or step.action,
                duration_ms=cli_result.duration_ms / max(1, len(flow)),
            ) for step in flow]
            flow_result = FlowResult(
                steps=steps,
                total_duration_ms=cli_result.duration_ms,
                passed_count=len(steps),
                failed_count=0,
            )
        else:
            # 失败且没有报告 → 全部标记为失败
            steps = [StepResult(
                status=StepStatus.FAILED,
                action=step.action,
                description=step.description or step.action,
                error=cli_result.stderr[:500],
            ) for step in flow]
            flow_result = FlowResult(
                steps=steps,
                total_duration_ms=cli_result.duration_ms,
                passed_count=0,
                failed_count=len(steps),
            )

        uat_logger.info(
            "Maestro 流执行完成: passed=%d failed=%d duration=%.1fs",
            flow_result.passed_count, flow_result.failed_count,
            flow_result.total_duration_ms / 1000,
        )
        return flow_result

    def execute_step(self, step: FlowStep) -> StepResult:
        """执行单个步骤 (包装为单步流)"""
        result = self.execute_flow([step])
        if result.steps:
            return result.steps[0]
        return StepResult(
            status=StepStatus.FAILED,
            action=step.action,
            error="引擎返回空结果",
        )

    def resume_flow(self, flow: List[FlowStep], from_index: int = 0) -> FlowResult:
        """
        断点恢复执行。
        Maestro 原生支持 --continue，但需要复用之前的 flow 文件。
        """
        if not self._last_flow_file:
            return self.execute_flow(flow[from_index:])

        from modules.mobile.mobile_env_config import maestro_timeout_seconds

        cli_result = self._cli.run_test(
            self._last_flow_file,
            device_udid=self._device.udid if self._device else "",
            resume=True,
            timeout=maestro_timeout_seconds(),
        )

        if cli_result.report_xml and os.path.isfile(cli_result.report_xml):
            return self._report_parser.parse(cli_result.report_xml)

        return self.execute_flow(flow[from_index:])

    # ========================================================================
    # 原子元素交互
    # ========================================================================

    def tap(self, locator: LocatorInfo) -> StepResult:
        step = FlowStep(action="tap", locator=locator)
        return self.execute_step(step)

    def tap_coordinates(self, x: int, y: int) -> StepResult:
        step = FlowStep(action="tap", tap_x=x, tap_y=y)
        return self.execute_step(step)

    def input_text(self, locator: LocatorInfo, text: str) -> StepResult:
        step = FlowStep(action="input", locator=locator, input_value=text)
        return self.execute_step(step)

    def swipe(self, direction: str, duration_ms: int = 400) -> StepResult:
        step = FlowStep(
            action="swipe", swipe_direction=direction, swipe_duration_ms=duration_ms,
        )
        return self.execute_step(step)

    def assert_element(self, locator: LocatorInfo, condition: str) -> StepResult:
        step = FlowStep(action="assert", locator=locator, assert_type=condition)
        return self.execute_step(step)

    def press_back(self) -> StepResult:
        step = FlowStep(action="back")
        return self.execute_step(step)

    # ========================================================================
    # 报告
    # ========================================================================

    def get_structured_log(self) -> Dict[str, Any]:
        cli_result = self._cli.last_result
        if not cli_result:
            return {"status": "no_run"}
        return {
            "exit_code": cli_result.exit_code,
            "duration_ms": cli_result.duration_ms,
            "report_xml": cli_result.report_xml,
            "report_dir": cli_result.report_dir,
            "video_path": cli_result.video_path,
            "screenshots": cli_result.screenshot_paths,
            "stdout": cli_result.stdout,
            "stderr": cli_result.stderr,
            "success": cli_result.success,
        }

    # ========================================================================
    # 便捷方法
    # ========================================================================

    def execute_yaml_file(self, yaml_path: str) -> FlowResult:
        """直接执行现有 Maestro YAML 文件"""
        if not self._device:
            return FlowResult(
                steps=[], total_duration_ms=0, passed_count=0, failed_count=0,
            )

        from modules.mobile.mobile_env_config import maestro_timeout_seconds

        cli_result = self._cli.run_test(
            yaml_path,
            device_udid=self._device.udid,
            timeout=maestro_timeout_seconds(),
        )

        if cli_result.report_xml and os.path.isfile(cli_result.report_xml):
            return self._report_parser.parse(cli_result.report_xml)

        return FlowResult(
            steps=[],
            total_duration_ms=cli_result.duration_ms,
            passed_count=0,
            failed_count=0,
            raw_report_path=cli_result.stderr,
        )

    def record_flow(self, output_path: str = "", timeout: int = 600) -> str:
        """
        启动 Maestro 录制模式。

        Returns:
            录制的 YAML 内容
        """
        udid = self._device.udid if self._device else ""
        result = self._cli.run_record(device_udid=udid, output_path=output_path,
                                      timeout=timeout)
        if result.success:
            return result.stdout
        return ""

    def run_db_case(self, db_steps: List[Dict]) -> FlowResult:
        """执行数据库中的用例步骤"""
        flow = self._flow_gen._convert_db_steps(db_steps)
        return self.execute_flow(flow)

    # ========================================================================
    # 内部
    # ========================================================================

    def _write_temp_yaml(self, yaml_content: str) -> str:
        """写入临时 YAML 文件，返回文件路径"""
        if not self._temp_dir:
            self._temp_dir = tempfile.mkdtemp(prefix="testory_maestro_")
        flow_file = os.path.join(self._temp_dir, "flow.yaml")
        Path(flow_file).write_text(yaml_content, encoding="utf-8")
        return flow_file

    def _cleanup_temp(self) -> None:
        """清理临时文件"""
        if self._temp_dir and os.path.isdir(self._temp_dir):
            import shutil

            shutil.rmtree(self._temp_dir, ignore_errors=True)
            self._temp_dir = None
