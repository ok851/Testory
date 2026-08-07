# -*- coding: utf-8 -*-
"""
Maestro 设备前置自检。

执行前自动检查 Maestro 运行必要条件:
1. adb 连接状态
2. 无障碍服务 (Accessibility Service) 状态
3. 屏幕锁定状态
4. USB 调试授权
5. 目标 app 是否已安装
6. 系统权限 (悬浮窗等)
"""

from __future__ import annotations

import os
import re
import subprocess
from typing import Any, Dict, List, Optional, Tuple

from mobile_engine.engine_interface import DeviceInfo

try:
    from uat_logger import uat_logger
except ImportError:
    import logging

    uat_logger = logging.getLogger(__name__)


class MaestroDeviceChecker:
    """Maestro 设备前置自检器"""

    def __init__(self, adb_path: str = "adb"):
        self._adb = adb_path or "adb"

    # ------------------------------------------------------------------
    # 综合检查
    # ------------------------------------------------------------------

    def run_checks(self, udid: str,
                   expected_package: str = "") -> Dict[str, Any]:
        """
        运行全部前置检查。

        Args:
            udid: 设备序列号
            expected_package: 期望的目标 app 包名

        Returns:
            {
                "all_passed": bool,
                "checks": [{"name": "...", "passed": bool, "message": "...", "severity": "error"|"warning"}],
                "errors": [...],
                "warnings": [...],
            }
        """
        # iOS 设备走独立检查流程
        if self._is_ios_device(udid):
            return self._run_ios_checks(udid, expected_package)

        checks: List[Dict[str, Any]] = []
        errors: List[str] = []
        warnings: List[str] = []

        def add_check(name: str, passed: bool, message: str,
                      severity: str = "error") -> None:
            checks.append({
                "name": name, "passed": passed,
                "message": message, "severity": severity,
            })
            if not passed:
                if severity == "error":
                    errors.append(f"[{name}] {message}")
                else:
                    warnings.append(f"[{name}] {message}")

        # 1. ADB 连接
        adb_ok, adb_msg = self._check_adb_connection(udid)
        add_check("ADB 连接", adb_ok, adb_msg)
        if not adb_ok:
            return {
                "all_passed": False,
                "checks": checks,
                "errors": errors,
                "warnings": warnings,
            }

        # 2. 无障碍服务
        a11y_ok, a11y_msg = self._check_accessibility_service(udid)
        add_check("无障碍服务", a11y_ok, a11y_msg)

        # 3. 屏幕锁定
        lock_ok, lock_msg = self._check_screen_locked(udid)
        add_check("屏幕锁定", lock_ok, lock_msg)

        # 4. USB 调试
        usb_ok, usb_msg = self._check_usb_debugging(udid)
        add_check("USB 调试", usb_ok, usb_msg, severity="warning")

        # 5. App 安装
        if expected_package:
            app_ok, app_msg = self._check_app_installed(udid, expected_package)
            add_check(f"App 安装 ({expected_package})", app_ok, app_msg)

        # 6. 悬浮窗权限 (部分设备需要)
        overlay_ok, overlay_msg = self._check_overlay_permission(udid)
        add_check("悬浮窗权限", overlay_ok, overlay_msg, severity="warning")

        # 7. 电池优化
        battery_ok, battery_msg = self._check_battery_optimization(udid)
        add_check("电池优化", battery_ok, battery_msg, severity="warning")

        all_passed = len(errors) == 0
        return {
            "all_passed": all_passed,
            "checks": checks,
            "errors": errors,
            "warnings": warnings,
        }

    # ------------------------------------------------------------------
    # iOS 设备支持
    # ------------------------------------------------------------------

    @staticmethod
    def _is_ios_device(udid: str) -> bool:
        """判断是否为 iOS 设备（UDID 格式：为 25-40 位十六进制含连字符）。"""
        import re
        if re.match(r'^[0-9a-fA-F-]{25,40}$', udid):
            return True
        if re.match(r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-', udid):
            return True
        return False

    def _check_idb_connection(self, udid: str) -> Tuple[bool, str]:
        """检查 idb 是否可连接到 iOS 设备。"""
        try:
            idb = os.environ.get("IDB_PATH", "idb")
            proc = subprocess.run(
                [idb, "--udid", udid, "describe", "--json"],
                capture_output=True, text=True, timeout=15, check=False,
            )
            if proc.returncode == 0:
                return True, f"iOS 设备 {udid[:12]}... 已连接"
            return False, f"idb 连接失败: {(proc.stderr or '').strip() or '未知错误'}"
        except FileNotFoundError:
            return False, "idb 未安装 (brew install idb-companion && pip3 install fb-idb)"
        except Exception as exc:
            return False, f"idb 连接检查异常: {exc}"

    def _check_ios_screen_locked(self, udid: str) -> Tuple[bool, str]:
        """检查 iOS 设备是否锁屏。"""
        try:
            idb = os.environ.get("IDB_PATH", "idb")
            proc = subprocess.run(
                [idb, "--udid", udid, "describe", "--json"],
                capture_output=True, text=True, timeout=15, check=False,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                info = json.loads(proc.stdout)
                if info.get("screen_lock") is True:
                    return False, "iOS 设备处于锁屏状态"
                return True, "iOS 设备屏幕已解锁"
            return True, "iOS 屏幕状态无法确定（已视为正常）"
        except Exception:
            return True, "iOS 屏幕检查跳过"

    def _check_ios_app_installed(self, udid: str, bundle_id: str) -> Tuple[bool, str]:
        """检查 iOS App 是否已安装。"""
        try:
            idb = os.environ.get("IDB_PATH", "idb")
            proc = subprocess.run(
                [idb, "--udid", udid, "list-apps"],
                capture_output=True, text=True, timeout=15, check=False,
            )
            if proc.returncode == 0:
                output = proc.stdout or ""
                if bundle_id in output:
                    return True, f"iOS App {bundle_id} 已安装"
            return False, f"iOS App {bundle_id} 未安装，请先通过 idb install 安装"
        except FileNotFoundError:
            return False, "idb 未安装，无法检查 App 安装状态"
        except Exception as exc:
            return False, f"iOS App 安装检查失败: {exc}"

    def _run_ios_checks(self, udid: str, expected_package: str = "") -> Dict[str, Any]:
        """iOS 设备专用前置检查流程。"""
        checks: List[Dict[str, Any]] = []
        errors: List[str] = []
        warnings: List[str] = []

        def add_check(name: str, passed: bool, message: str,
                      severity: str = "error") -> None:
            checks.append({"name": name, "passed": passed, "message": message, "severity": severity})
            if not passed:
                (errors if severity == "error" else warnings).append(f"[{name}] {message}")

        idb_ok, idb_msg = self._check_idb_connection(udid)
        add_check("idb 连接", idb_ok, idb_msg)
        if not idb_ok:
            return {"all_passed": False, "checks": checks, "errors": errors, "warnings": warnings}

        lock_ok, lock_msg = self._check_ios_screen_locked(udid)
        add_check("屏幕锁定", lock_ok, lock_msg, severity="warning")

        if expected_package:
            app_ok, app_msg = self._check_ios_app_installed(udid, expected_package)
            add_check(f"App 安装 ({expected_package})", app_ok, app_msg)

        return {"all_passed": len(errors) == 0, "checks": checks, "errors": errors, "warnings": warnings}

    # ------------------------------------------------------------------
    # 单项检查
    # ------------------------------------------------------------------

    def _check_adb_connection(self, udid: str) -> Tuple[bool, str]:
        """检查 ADB 连接状态"""
        try:
            proc = subprocess.run(
                [self._adb, "-s", udid, "get-state"],
                capture_output=True, text=True, timeout=10, check=False,
            )
            state = (proc.stdout or "").strip()
            if state == "device":
                return True, f"设备 {udid} 已连接"
            elif state == "unauthorized":
                return False, f"设备 {udid} 未授权 USB 调试，请在手机上确认"
            elif state == "offline":
                return False, f"设备 {udid} 离线，请检查连接"
            else:
                return False, f"设备 {udid} 状态异常: {state}"
        except Exception as exc:
            return False, f"ADB 连接检查失败: {exc}"

    def _check_accessibility_service(self, udid: str) -> Tuple[bool, str]:
        """
        检查无障碍服务是否可用。

        Maestro 依赖 UIAutomator，而 UIAutomator 依赖无障碍服务。
        检查 uiautomator 进程是否存在。
        """
        try:
            proc = subprocess.run(
                [self._adb, "-s", udid, "shell",
                 "dumpsys accessibility | grep -i 'enabled' | head -1"],
                capture_output=True, text=True, timeout=10, check=False,
            )
            output = proc.stdout.strip()
            if output and "enabled" in output.lower():
                return True, "无障碍服务已启用"
            # 进一步检查 uiautomator 是否存在
            proc2 = subprocess.run(
                [self._adb, "-s", udid, "shell", "ps | grep uiautomator"],
                capture_output=True, text=True, timeout=10, check=False,
            )
            if "uiautomator" in (proc2.stdout or ""):
                return True, "UIAutomator 服务运行中"
            return False, (
                "无障碍服务未启用。Maestro 依赖 UIAutomator 进行元素定位。"
                "请在设备「设置 → 辅助功能 → 无障碍」中开启相关服务。"
            )
        except Exception as exc:
            return False, f"无障碍检查失败: {exc}"

    def _check_screen_locked(self, udid: str) -> Tuple[bool, str]:
        """检查屏幕是否锁定"""
        try:
            # 方法1: 检查 mDreamingLockscreen
            proc = subprocess.run(
                [self._adb, "-s", udid, "shell",
                 "dumpsys window | grep -i 'mDreamingLockscreen' | head -1"],
                capture_output=True, text=True, timeout=10, check=False,
            )
            output = proc.stdout.strip()
            if "true" in output.lower():
                return False, "屏幕已锁定，请先解锁屏幕"

            # 方法2: 检查 mShowingLockscreen
            proc2 = subprocess.run(
                [self._adb, "-s", udid, "shell",
                 "dumpsys window | grep -i 'mShowingLockscreen' | head -1"],
                capture_output=True, text=True, timeout=10, check=False,
            )
            output2 = proc2.stdout.strip()
            if "true" in output2.lower():
                return False, "锁屏界面显示中，请先解锁屏幕"

            # 方法3: 检查当前焦点窗口
            proc3 = subprocess.run(
                [self._adb, "-s", udid, "shell",
                 "dumpsys window | grep -i 'mCurrentFocus'"],
                capture_output=True, text=True, timeout=10, check=False,
            )
            output3 = proc3.stdout.strip()
            if "StatusBar" in output3 and "Keyguard" in output3:
                return False, "设备在锁屏状态 (Keyguard 活跃)"

            return True, "屏幕已解锁"
        except Exception as exc:
            return True, f"屏幕锁检查无法确定: {exc}"

    def _check_usb_debugging(self, udid: str) -> Tuple[bool, str]:
        """检查 USB 调试是否开启"""
        try:
            proc = subprocess.run(
                [self._adb, "-s", udid, "shell",
                 "settings get global adb_enabled"],
                capture_output=True, text=True, timeout=10, check=False,
            )
            val = (proc.stdout or "").strip()
            if val == "1":
                return True, "USB 调试已开启"
            return False, "USB 调试未开启，请在开发者选项中开启"
        except Exception:
            return True, "USB 调试状态无法检测 (已 ADB 连接视为正常)"

    def _check_app_installed(self, udid: str,
                             package: str) -> Tuple[bool, str]:
        """检查 App 是否已安装"""
        try:
            proc = subprocess.run(
                [self._adb, "-s", udid, "shell", f"pm list packages {package}"],
                capture_output=True, text=True, timeout=15, check=False,
            )
            output = proc.stdout.strip()
            if f"package:{package}" in output:
                return True, f"App {package} 已安装"
            return False, f"App {package} 未安装，请先安装 APK"
        except Exception as exc:
            return False, f"检查 App 安装失败: {exc}"

    def _check_overlay_permission(self, udid: str) -> Tuple[bool, str]:
        """检查悬浮窗权限"""
        try:
            proc = subprocess.run(
                [self._adb, "-s", udid, "shell",
                 "appops get com.android.systemui SYSTEM_ALERT_WINDOW"],
                capture_output=True, text=True, timeout=10, check=False,
            )
            output = proc.stdout.strip()
            if "allow" in output.lower():
                return True, "悬浮窗权限已授予"
            return False, "悬浮窗权限未授予 (可能影响弹窗处理)"
        except Exception:
            return True, "悬浮窗权限检测跳过"

    def _check_battery_optimization(self, udid: str) -> Tuple[bool, str]:
        """检查电池优化是否可能影响 Maestro"""
        try:
            proc = subprocess.run(
                [self._adb, "-s", udid, "shell",
                 "dumpsys deviceidle | grep -i 'mEnabled' | head -1"],
                capture_output=True, text=True, timeout=10, check=False,
            )
            output = proc.stdout.strip()
            if "true" in output.lower():
                return False, "设备处于省电模式，可能影响 Maestro 响应速度"
            return True, "电池状态正常"
        except Exception:
            return True, "电池优化检查跳过"

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------

    def unlock_screen(self, udid: str) -> Tuple[bool, str]:
        """尝试通过 ADB 解锁屏幕 (简单 swipe)"""
        try:
            # 唤醒屏幕
            subprocess.run(
                [self._adb, "-s", udid, "shell", "input keyevent 26"],
                timeout=5, check=False,
            )
            # 向上滑动解锁 (标准 AOSP 锁屏)
            subprocess.run(
                [self._adb, "-s", udid, "shell",
                 "input swipe 500 1800 500 300"],
                timeout=5, check=False,
            )
            return True, "已尝试解锁"
        except Exception as exc:
            return False, f"解锁失败: {exc}"

    def launch_app(self, udid: str, package: str,
                   activity: str = "") -> Tuple[bool, str]:
        """通过 ADB 启动 App"""
        try:
            if activity:
                cmd = [self._adb, "-s", udid, "shell", "am", "start",
                       "-n", f"{package}/{activity}"]
            else:
                cmd = [self._adb, "-s", udid, "shell", "monkey",
                       "-p", package, "-c", "android.intent.category.LAUNCHER", "1"]

            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=15, check=False)
            if proc.returncode == 0:
                return True, f"App {package} 已启动"
            return False, f"启动失败: {proc.stderr or proc.stdout}"
        except Exception as exc:
            return False, f"启动异常: {exc}"
