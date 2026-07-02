# -*- coding: utf-8 -*-
"""
Maestro CLI 命令行封装。

封装 maestro 命令行操作:
- maestro test flow.yaml                    # 执行测试流
- maestro test flow.yaml --continue          # 断点恢复
- maestro test flow.yaml --format junit      # 输出 JUnit XML
- maestro test flow.yaml --device <udid>     # 指定设备
- maestro record                             # 录制模式
- maestro studio                             # Studio GUI (调试)
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from mobile_engine.maestro.maestro_binary_manager import MaestroBinaryManager

try:
    from uat_logger import uat_logger
except ImportError:
    import logging

    uat_logger = logging.getLogger(__name__)


@dataclass
class MaestroCLIResult:
    """Maestro CLI 执行结果"""
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: float = 0.0
    # 输出文件路径
    report_xml: str = ""
    report_dir: str = ""
    video_path: str = ""
    screenshot_paths: List[str] = field(default_factory=list)
    success: bool = False


class MaestroCLI:
    """Maestro 命令行封装"""

    # Maestro 默认输出目录名
    DEFAULT_REPORT_DIR = "maestro_report"

    def __init__(self, binary_mgr: Optional[MaestroBinaryManager] = None):
        self._binary_mgr = binary_mgr or MaestroBinaryManager()
        self._last_result: Optional[MaestroCLIResult] = None

    @property
    def last_result(self) -> Optional[MaestroCLIResult]:
        return self._last_result

    # ------------------------------------------------------------------
    # 核心命令
    # ------------------------------------------------------------------

    def run_test(
        self,
        flow_path: str,
        *,
        device_udid: str = "",
        resume: bool = False,
        format: str = "junit",
        env: Optional[Dict[str, str]] = None,
        timeout: int = 600,
        output_dir: str = "",
    ) -> MaestroCLIResult:
        """
        执行 Maestro 测试流。

        Args:
            flow_path: YAML 流文件路径
            device_udid: 目标设备序列号
            resume: 是否为断点恢复模式 (--continue)
            format: 报告格式 (junit)
            env: 额外环境变量
            timeout: 超时秒数
            output_dir: 自定义输出目录

        Returns:
            MaestroCLIResult
        """
        flow_file = Path(flow_path)
        if not flow_file.exists():
            return MaestroCLIResult(
                exit_code=1, stderr=f"流文件不存在: {flow_path}", success=False,
            )

        # 构建命令行参数
        args = ["test", str(flow_file.resolve())]

        if resume:
            args.append("--continue")

        if format:
            args.extend(["--format", format])

        if device_udid:
            args.extend(["--device", device_udid])

        # 测试输出目录 (Maestro 默认生成在 YAML 同级目录下的报告文件夹)
        report_dir = output_dir or os.path.join(
            flow_file.parent, self.DEFAULT_REPORT_DIR,
        )
        # 清理旧报告
        self._clean_report_dir(report_dir)

        uat_logger.info(
            "执行 Maestro: flow=%s device=%s resume=%s",
            flow_file.name, device_udid or "(auto)", resume,
        )

        return self._run_command(args, report_dir=report_dir, env=env, timeout=timeout)

    def run_record(self, device_udid: str = "", output_path: str = "",
                   timeout: int = 600) -> MaestroCLIResult:
        """
        启动 Maestro 录制模式。

        Args:
            device_udid: 目标设备
            output_path: 输出 YAML 文件路径
            timeout: 录制超时

        Returns:
            MaestroCLIResult (包含录制的 YAML 内容)
        """
        args = ["record"]

        if device_udid:
            args.extend(["--device", device_udid])

        uat_logger.info("启动 Maestro 录制: device=%s", device_udid or "(auto)")
        result = self._run_command(args, timeout=timeout)

        # 若指定了输出路径，保存 YAML
        if result.success and output_path and result.stdout:
            Path(output_path).write_text(result.stdout, encoding="utf-8")

        return result

    def run_studio(self, device_udid: str = "") -> MaestroCLIResult:
        """
        启动 Maestro Studio (交互式调试)。

        Note: Studio 是交互式 GUI，此方法仅启动进程。
        """
        args = ["studio"]
        if device_udid:
            args.extend(["--device", device_udid])

        uat_logger.info("启动 Maestro Studio: device=%s", device_udid or "(auto)")
        return self._run_command(args, timeout=3600)

    def list_devices(self) -> List[Dict[str, Any]]:
        """
        通过 adb devices 获取已连接设备列表（供 Maestro 使用）。
        """
        try:
            import subprocess

            proc = subprocess.run(
                ["adb", "devices", "-l"],
                capture_output=True, text=True, timeout=15, check=False,
            )
            devices = []
            for line in proc.stdout.splitlines()[1:]:
                line = line.strip()
                if not line or "offline" in line:
                    continue
                parts = line.split()
                if len(parts) >= 2:
                    devices.append({
                        "udid": parts[0],
                        "state": parts[1],
                    })
            return devices
        except Exception:
            return []

    def check_maestro_version(self) -> str:
        """获取已安装 Maestro 版本"""
        result = self._run_command(["--version"], timeout=10)
        if result.success:
            return result.stdout.strip()
        return ""

    # ------------------------------------------------------------------
    # 辅助命令
    # ------------------------------------------------------------------

    def run_flow_file(self, flow_path: str, device_udid: str = "",
                      env_vars: Optional[Dict[str, str]] = None,
                      timeout: int = 600) -> MaestroCLIResult:
        """
        便捷方法：直接执行 .yaml 文件 (无需先生成 DSL)。
        """
        return self.run_test(
            flow_path, device_udid=device_udid, env=env_vars, timeout=timeout,
        )

    def install_app_via_maestro(self, apk_path: str, device_udid: str = "",
                                timeout: int = 120) -> MaestroCLIResult:
        """
        通过 Maestro 安装 APK。
        (Maestro 没有直接 install 子命令，通过 adb install 实现)
        """
        try:
            proc = subprocess.run(
                ["adb"] + (["-s", device_udid] if device_udid else [])
                + ["install", "-r", apk_path],
                capture_output=True, text=True, timeout=timeout, check=False,
            )
            return MaestroCLIResult(
                exit_code=proc.returncode,
                stdout=proc.stdout,
                stderr=proc.stderr,
                success=proc.returncode == 0,
            )
        except Exception as exc:
            return MaestroCLIResult(
                exit_code=1, stderr=str(exc), success=False,
            )

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _run_command(
        self,
        args: List[str],
        *,
        report_dir: str = "",
        env: Optional[Dict[str, str]] = None,
        timeout: int = 600,
    ) -> MaestroCLIResult:
        """执行 maestro 命令并返回结构化结果"""
        cmd = self._binary_mgr.build_maestro_cmd(*args)

        # 构建环境变量
        proc_env = os.environ.copy()
        if env:
            proc_env.update(env)

        # 设置 MAESTRO_DRIVER_STARTUP_TIMEOUT 等 (毫秒)
        proc_env.setdefault("MAESTRO_DRIVER_STARTUP_TIMEOUT", "60000")

        if report_dir:
            # 将工作目录设在报告目录父级
            cwd = str(Path(report_dir).parent)
        else:
            cwd = os.getcwd()

        uat_logger.debug("Maestro 命令: %s (cwd=%s)", " ".join(cmd), cwd)

        started = time.time()
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                cwd=cwd,
                env=proc_env,
            )
            duration = (time.time() - started) * 1000

            success = proc.returncode == 0

            result = MaestroCLIResult(
                exit_code=proc.returncode,
                stdout=proc.stdout or "",
                stderr=proc.stderr or "",
                duration_ms=duration,
                success=success,
            )

            # 查找报告输出
            if success or proc.returncode != 0:
                self._collect_report_outputs(result, report_dir)

            self._last_result = result

            if success:
                uat_logger.info("Maestro 执行成功 (%.1fs)", duration / 1000)
            else:
                uat_logger.error(
                    "Maestro 执行失败 (exit=%d): %s",
                    proc.returncode,
                    (proc.stderr or proc.stdout or "")[:500],
                )

            return result

        except subprocess.TimeoutExpired:
            duration = (time.time() - started) * 1000
            uat_logger.error("Maestro 执行超时 (%ds)", timeout)
            return MaestroCLIResult(
                exit_code=-1,
                stderr=f"执行超时 ({timeout}s)",
                duration_ms=duration,
                success=False,
            )
        except FileNotFoundError:
            return MaestroCLIResult(
                exit_code=-1,
                stderr=f"找不到 Java 或 Maestro JAR: {' '.join(cmd)}",
                success=False,
            )
        except Exception as exc:
            duration = (time.time() - started) * 1000
            uat_logger.error("Maestro 执行异常: %s", exc)
            return MaestroCLIResult(
                exit_code=-1,
                stderr=str(exc),
                duration_ms=duration,
                success=False,
            )

    def _collect_report_outputs(self, result: MaestroCLIResult,
                                report_dir: str = "") -> None:
        """收集 Maestro 输出报告文件"""
        if not report_dir:
            return

        rp = Path(report_dir)
        if not rp.exists():
            # 有时 Maestro 把报告放在 report/ 或 report-<timestamp>/
            parent = rp.parent
            for candidate in parent.glob("report*"):
                if candidate.is_dir():
                    rp = candidate
                    break

        if not rp.exists():
            return

        result.report_dir = str(rp)

        # 查找 JUnit XML 报告
        for xml_file in rp.rglob("*.xml"):
            if "report" in xml_file.name.lower() or "junit" in xml_file.name.lower():
                result.report_xml = str(xml_file)
                break
        if not result.report_xml:
            xml_files = list(rp.rglob("*.xml"))
            if xml_files:
                result.report_xml = str(xml_files[0])

        # 查找视频
        for vid in rp.rglob("*.mp4"):
            result.video_path = str(vid)
            break

        # 收集截图
        for img in rp.rglob("*.png"):
            result.screenshot_paths.append(str(img))
        result.screenshot_paths.sort()

    @staticmethod
    def _clean_report_dir(report_dir: str) -> None:
        """清理旧报告目录"""
        rp = Path(report_dir)
        parent = rp.parent
        # 清理以 report 开头的旧目录
        for d in parent.glob("report*"):
            if d.is_dir():
                shutil.rmtree(str(d), ignore_errors=True)
