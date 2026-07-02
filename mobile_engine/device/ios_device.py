# -*- coding: utf-8 -*-
"""
iOS 设备管理 (idb 桥接)。

Maestro 通过 idb (iOS Device Bridge) 与 iOS 真机/模拟器通信。
此模块封装 idb 操作，提供与 Android ADB 类似的设备管理接口。

注意: iOS 支持为首期之后的扩展功能。
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from mobile_engine.engine_interface import DeviceInfo

try:
    from uat_logger import uat_logger
except ImportError:
    import logging

    uat_logger = logging.getLogger(__name__)


class IOSDeviceManager:
    """iOS 设备管理 — idb 桥接"""

    def __init__(self, idb_path: str = "idb"):
        self._idb = idb_path or "idb"

    # ------------------------------------------------------------------
    # 设备发现
    # ------------------------------------------------------------------

    def list_devices(self) -> List[DeviceInfo]:
        """
        发现已连接的 iOS 设备 (idb list-targets)。

        Returns:
            DeviceInfo 列表
        """
        ok, msg = self.check_idb_available()
        if not ok:
            uat_logger.warning("idb 不可用: %s", msg)
            return []

        try:
            proc = subprocess.run(
                [self._idb, "list-targets"],
                capture_output=True, text=True, timeout=15, check=False,
            )
            devices: List[DeviceInfo] = []
            for line in proc.stdout.splitlines():
                line = line.strip()
                if not line:
                    continue
                # idb list-targets 输出格式: udid | name | type | os_version | ...
                parts = line.split("|")
                if len(parts) >= 4:
                    udid = parts[0].strip()
                    name = parts[1].strip()
                    dev_type = parts[2].strip()
                    os_ver = parts[3].strip()
                    devices.append(DeviceInfo(
                        udid=udid,
                        platform="ios",
                        model=name,
                        os_version=os_ver,
                        is_emulator=("simulator" in dev_type.lower()),
                        connection_type="usb",
                    ))
            return devices
        except Exception as exc:
            uat_logger.error("idb 设备列表获取失败: %s", exc)
            return []

    def get_device_info(self, udid: str) -> Optional[DeviceInfo]:
        """获取单个 iOS 设备信息"""
        for d in self.list_devices():
            if d.udid == udid:
                return d
        return None

    # ------------------------------------------------------------------
    # idb 检查
    # ------------------------------------------------------------------

    def check_idb_available(self) -> Tuple[bool, str]:
        """检查 idb 是否可用"""
        try:
            proc = subprocess.run(
                [self._idb, "--version"],
                capture_output=True, text=True, timeout=10, check=False,
            )
            if proc.returncode == 0:
                return True, proc.stdout.strip()
            return False, f"idb 返回非零: {proc.stderr}"
        except FileNotFoundError:
            return False, (
                "idb 未安装。请安装 idb: "
                "brew install idb (macOS) 或参考 https://fbidb.io/"
            )
        except Exception as exc:
            return False, str(exc)

    def install_idb_guide(self) -> str:
        """返回 idb 安装引导说明"""
        import sys

        if sys.platform == "darwin":
            return (
                "macOS 安装 idb:\n"
                "  brew tap facebook/fb\n"
                "  brew install idb-companion\n"
                "  pip3 install fb-idb"
            )
        else:
            return (
                "非 macOS 系统不支持原生 idb。"
                "请在 macOS 环境下安装: brew tap facebook/fb && brew install idb-companion"
            )

    # ------------------------------------------------------------------
    # idb 操作 (iOS 设备交互)
    # ------------------------------------------------------------------

    def launch_app(self, udid: str, bundle_id: str) -> Tuple[bool, str]:
        """启动 iOS 应用"""
        try:
            proc = subprocess.run(
                [self._idb, "launch", bundle_id],
                capture_output=True, text=True, timeout=30, check=False,
            )
            if proc.returncode == 0:
                return True, "启动成功"
            return False, proc.stderr or "启动失败"
        except Exception as exc:
            return False, str(exc)

    def install_app(self, udid: str, ipa_path: str) -> Tuple[bool, str]:
        """安装 IPA"""
        try:
            proc = subprocess.run(
                [self._idb, "install", ipa_path],
                capture_output=True, text=True, timeout=300, check=False,
            )
            if proc.returncode == 0:
                return True, "安装成功"
            return False, proc.stderr or "安装失败"
        except Exception as exc:
            return False, str(exc)

    def uninstall_app(self, udid: str, bundle_id: str) -> Tuple[bool, str]:
        """卸载 iOS 应用"""
        try:
            proc = subprocess.run(
                [self._idb, "uninstall", bundle_id],
                capture_output=True, text=True, timeout=30, check=False,
            )
            if proc.returncode == 0:
                return True, "卸载成功"
            return False, proc.stderr or "卸载失败"
        except Exception as exc:
            return False, str(exc)

    def capture_screenshot(self, udid: str, output_path: str) -> bool:
        """截取 iOS 屏幕"""
        try:
            proc = subprocess.run(
                [self._idb, "screenshot", output_path],
                capture_output=True, text=True, timeout=15, check=False,
            )
            return proc.returncode == 0 and Path(output_path).exists()
        except Exception:
            return False

    def tap(self, udid: str, x: int, y: int) -> bool:
        """点击坐标"""
        try:
            proc = subprocess.run(
                [self._idb, "ui", "tap", str(x), str(y)],
                capture_output=True, text=True, timeout=10, check=False,
            )
            return proc.returncode == 0
        except Exception:
            return False

    def swipe(self, udid: str, x1: int, y1: int,
              x2: int, y2: int, duration_ms: int = 400) -> bool:
        """滑动"""
        try:
            proc = subprocess.run(
                [self._idb, "ui", "swipe",
                 str(x1), str(y1), str(x2), str(y2),
                 "--duration", str(duration_ms / 1000.0)],
                capture_output=True, text=True, timeout=15, check=False,
            )
            return proc.returncode == 0
        except Exception:
            return False

    def press_button(self, udid: str, button: str = "HOME") -> bool:
        """按物理按钮 (HOME/VOLUME_UP/VOLUME_DOWN)"""
        try:
            proc = subprocess.run(
                [self._idb, "ui", "button", button],
                capture_output=True, text=True, timeout=10, check=False,
            )
            return proc.returncode == 0
        except Exception:
            return False

    def list_apps(self, udid: str) -> List[str]:
        """列出已安装 app 的 bundle ID"""
        try:
            proc = subprocess.run(
                [self._idb, "list-apps"],
                capture_output=True, text=True, timeout=20, check=False,
            )
            return [line.strip() for line in proc.stdout.splitlines() if line.strip()]
        except Exception:
            return []


# ------------------------------------------------------------------
# 便捷函数
# ------------------------------------------------------------------

def is_ios_supported() -> bool:
    """检查当前环境是否支持 iOS 自动化"""
    from mobile_env_config import ios_enabled

    if not ios_enabled():
        return False
    import sys

    return sys.platform == "darwin"


def get_ios_manager() -> Optional[IOSDeviceManager]:
    """获取 iOS 设备管理器 (仅在 macOS 且启用 iOS 时)"""
    if not is_ios_supported():
        return None
    from mobile_env_config import idb_path

    return IOSDeviceManager(idb_path=idb_path())
