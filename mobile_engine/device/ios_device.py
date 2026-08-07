# -*- coding: utf-8 -*-
"""
iOS 设备管理 (idb 桥接 + Maestro iOS 集成)。

Maestro 原生支持 iOS（通过 --device udid），本模块补充：
- idb 元素树获取（accessibility hierarchy）
- idb 文本输入
- iOS 环境预检
- Maestro iOS 设备就绪检查
- iOS 模拟器管理
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from mobile_engine.engine_interface import DeviceInfo

try:
    from uat_logger import uat_logger
except ImportError:
    import logging
    uat_logger = logging.getLogger(__name__)


class IOSDeviceManager:
    """iOS 设备管理 — idb 桥接 + Maestro 集成"""

    def __init__(self, idb_path: str = "idb"):
        self._idb = idb_path or "idb"

    # ------------------------------------------------------------------
    # 设备发现
    # ------------------------------------------------------------------

    def list_devices(self) -> List[DeviceInfo]:
        """发现已连接的 iOS 设备 (idb list-targets)。"""
        ok, msg = self.check_idb_available()
        if not ok:
            uat_logger.warning("idb 不可用: %s", msg)
            return []
        try:
            proc = subprocess.run(
                [self._idb, "list-targets", "--json"],
                capture_output=True, text=True, timeout=15, check=False,
            )
            devices: List[DeviceInfo] = []
            # 尝试 JSON 解析
            if proc.returncode == 0 and proc.stdout.strip():
                try:
                    targets = json.loads(proc.stdout)
                    if isinstance(targets, list):
                        for t in targets:
                            udid = str(t.get("udid", "")).strip()
                            if not udid:
                                continue
                            devices.append(DeviceInfo(
                                udid=udid,
                                platform="ios",
                                model=str(t.get("name", "")),
                                os_version=str(t.get("os_version", "")),
                                is_emulator="simulator" in str(t.get("type", "")).lower(),
                                connection_type="usb",
                            ))
                        return devices
                except json.JSONDecodeError:
                    pass
            # 回退：文本解析
            for line in proc.stdout.splitlines():
                line = line.strip()
                if not line:
                    continue
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
                        is_emulator="simulator" in dev_type.lower(),
                        connection_type="usb",
                    ))
            return devices
        except Exception as exc:
            uat_logger.error("idb 设备列表获取失败: %s", exc)
            return []

    def get_device_info(self, udid: str) -> Optional[DeviceInfo]:
        """获取单个 iOS 设备信息。"""
        for d in self.list_devices():
            if d.udid == udid:
                return d
        return None

    # ------------------------------------------------------------------
    # idb 检查
    # ------------------------------------------------------------------

    def check_idb_available(self) -> Tuple[bool, str]:
        """检查 idb 是否可用。"""
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
                "idb 未安装。安装方式: brew install idb-companion && pip3 install fb-idb"
            )
        except Exception as exc:
            return False, str(exc)

    def install_idb_guide(self) -> str:
        """返回 idb 安装引导说明。"""
        if sys.platform == "darwin":
            return (
                "macOS 安装 idb:\n"
                "  1. brew install idb-companion\n"
                "  2. pip3 install fb-idb\n"
                "  参考: https://www.fbidb.io/"
            )
        return (
            "非 macOS 不支持原生 idb。请在 macOS 环境下安装。\n"
            "或使用远程 macOS 机器 + SSH 隧道。"
        )

    # ------------------------------------------------------------------
    # iOS 环境预检
    # ------------------------------------------------------------------

    def check_device_readiness(self, udid: str) -> Dict[str, Any]:
        """iOS 设备就绪检查（对齐 Android check_device_readiness）。"""
        checks: Dict[str, Any] = {
            "idb_available": False,
            "device_found": False,
            "xcode_installed": False,
            "simulator_booted": False,
            "all_passed": False,
            "errors": [],
        }

        # 1. idb 可用性
        ok, msg = self.check_idb_available()
        checks["idb_available"] = ok
        if not ok:
            checks["errors"].append(f"idb 不可用: {msg}")

        # 2. 设备是否存在
        dev = self.get_device_info(udid)
        checks["device_found"] = dev is not None
        if not dev:
            checks["errors"].append(f"设备 {udid} 未找到")

        # 3. Xcode 工具链
        if sys.platform == "darwin":
            try:
                proc = subprocess.run(
                    ["xcode-select", "-p"],
                    capture_output=True, text=True, timeout=5, check=False,
                )
                checks["xcode_installed"] = proc.returncode == 0
                if not checks["xcode_installed"]:
                    checks["errors"].append("Xcode Command Line Tools 未安装")
            except Exception:
                checks["errors"].append("无法检查 Xcode 工具链")

        # 4. 模拟器状态（如果 udid 是模拟器）
        if dev and dev.is_emulator:
            try:
                proc = subprocess.run(
                    ["xcrun", "simctl", "list", "devices", "booted", "-j"],
                    capture_output=True, text=True, timeout=10, check=False,
                )
                if proc.returncode == 0 and proc.stdout.strip():
                    booted = json.loads(proc.stdout)
                    for runtime, devices in booted.get("devices", {}).items():
                        for d in devices:
                            if d.get("udid") == udid and d.get("state") == "Booted":
                                checks["simulator_booted"] = True
                                break
            except Exception:
                pass

        checks["all_passed"] = (
            checks["idb_available"]
            and checks["device_found"]
            and (checks["xcode_installed"] or not sys.platform == "darwin")
        )
        return checks

    # ------------------------------------------------------------------
    # 元素交互（idb accessibility）
    # ------------------------------------------------------------------

    def get_accessibility_tree(self, udid: str) -> Optional[Dict[str, Any]]:
        """获取 iOS 界面的 accessibility 层级树（类似 Android dump）。"""
        try:
            proc = subprocess.run(
                [self._idb, "--udid", udid, "ui", "describe-all"],
                capture_output=True, text=True, timeout=15, check=False,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                try:
                    return json.loads(proc.stdout)
                except json.JSONDecodeError:
                    return {"raw": proc.stdout[:5000]}
            return None
        except Exception as exc:
            uat_logger.error("idb accessibility tree 获取失败: %s", exc)
            return None

    def find_element_by_label(self, udid: str, label: str) -> Optional[Dict[str, Any]]:
        """通过 accessibility label 查找元素。"""
        tree = self.get_accessibility_tree(udid)
        if not tree:
            return None
        return _search_tree(tree, label)

    def input_text(self, udid: str, text: str) -> bool:
        """向当前焦点输入文本（idb keyboard input）。"""
        try:
            proc = subprocess.run(
                [self._idb, "--udid", udid, "ui", "enter-text", text],
                capture_output=True, text=True, timeout=10, check=False,
            )
            return proc.returncode == 0
        except Exception:
            return False

    def clear_text(self, udid: str) -> bool:
        """清除当前输入框文本。"""
        try:
            proc = subprocess.run(
                [self._idb, "--udid", udid, "ui", "clear-text"],
                capture_output=True, text=True, timeout=10, check=False,
            )
            return proc.returncode == 0
        except Exception:
            return False

    def get_foreground_app(self, udid: str) -> Optional[str]:
        """获取前台应用 bundle ID。"""
        try:
            proc = subprocess.run(
                [self._idb, "--udid", udid, "foreground"],
                capture_output=True, text=True, timeout=10, check=False,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                return proc.stdout.strip()
            return None
        except Exception:
            return None

    # ------------------------------------------------------------------
    # 应用管理
    # ------------------------------------------------------------------

    def launch_app(self, udid: str, bundle_id: str) -> Tuple[bool, str]:
        """启动 iOS 应用。"""
        try:
            proc = subprocess.run(
                [self._idb, "--udid", udid, "launch", bundle_id],
                capture_output=True, text=True, timeout=30, check=False,
            )
            if proc.returncode == 0:
                return True, "启动成功"
            return False, proc.stderr or "启动失败"
        except Exception as exc:
            return False, str(exc)

    def install_app(self, udid: str, ipa_path: str) -> Tuple[bool, str]:
        """安装 IPA。"""
        try:
            proc = subprocess.run(
                [self._idb, "--udid", udid, "install", ipa_path],
                capture_output=True, text=True, timeout=300, check=False,
            )
            if proc.returncode == 0:
                return True, "安装成功"
            return False, proc.stderr or "安装失败"
        except Exception as exc:
            return False, str(exc)

    def uninstall_app(self, udid: str, bundle_id: str) -> Tuple[bool, str]:
        """卸载 iOS 应用。"""
        try:
            proc = subprocess.run(
                [self._idb, "--udid", udid, "uninstall", bundle_id],
                capture_output=True, text=True, timeout=30, check=False,
            )
            if proc.returncode == 0:
                return True, "卸载成功"
            return False, proc.stderr or "卸载失败"
        except Exception as exc:
            return False, str(exc)

    # ------------------------------------------------------------------
    # 屏幕操作
    # ------------------------------------------------------------------

    def capture_screenshot(self, udid: str, output_path: str) -> bool:
        """截取 iOS 屏幕。"""
        try:
            proc = subprocess.run(
                [self._idb, "--udid", udid, "screenshot", output_path],
                capture_output=True, text=True, timeout=15, check=False,
            )
            return proc.returncode == 0 and Path(output_path).exists()
        except Exception:
            return False

    def tap(self, udid: str, x: int, y: int) -> bool:
        """点击坐标。"""
        try:
            proc = subprocess.run(
                [self._idb, "--udid", udid, "ui", "tap", str(x), str(y)],
                capture_output=True, text=True, timeout=10, check=False,
            )
            return proc.returncode == 0
        except Exception:
            return False

    def long_press(self, udid: str, x: int, y: int, duration_ms: int = 1000) -> bool:
        """长按坐标。"""
        try:
            proc = subprocess.run(
                [self._idb, "--udid", udid, "ui", "tap", str(x), str(y),
                 "--duration", str(duration_ms / 1000.0)],
                capture_output=True, text=True, timeout=15, check=False,
            )
            return proc.returncode == 0
        except Exception:
            return False

    def swipe(self, udid: str, x1: int, y1: int,
              x2: int, y2: int, duration_ms: int = 400) -> bool:
        """滑动。"""
        try:
            proc = subprocess.run(
                [self._idb, "--udid", udid, "ui", "swipe",
                 str(x1), str(y1), str(x2), str(y2),
                 "--duration", str(duration_ms / 1000.0)],
                capture_output=True, text=True, timeout=15, check=False,
            )
            return proc.returncode == 0
        except Exception:
            return False

    def press_button(self, udid: str, button: str = "HOME") -> bool:
        """按物理按钮 (HOME/VOLUME_UP/VOLUME_DOWN)。"""
        try:
            proc = subprocess.run(
                [self._idb, "--udid", udid, "ui", "button", button],
                capture_output=True, text=True, timeout=10, check=False,
            )
            return proc.returncode == 0
        except Exception:
            return False

    # ------------------------------------------------------------------
    # 应用列表
    # ------------------------------------------------------------------

    def list_apps(self, udid: str) -> List[Dict[str, str]]:
        """列出已安装 app。"""
        try:
            proc = subprocess.run(
                [self._idb, "--udid", udid, "list-apps", "--json"],
                capture_output=True, text=True, timeout=20, check=False,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                try:
                    apps = json.loads(proc.stdout)
                    if isinstance(apps, list):
                        return [{"bundle_id": str(a)} for a in apps]
                except json.JSONDecodeError:
                    pass
            return [{"bundle_id": line.strip()} for line in proc.stdout.splitlines() if line.strip()]
        except Exception:
            return []

    # ------------------------------------------------------------------
    # 模拟器管理
    # ------------------------------------------------------------------

    @staticmethod
    def list_simulators() -> List[Dict[str, Any]]:
        """列出所有 iOS 模拟器（xcrun simctl）。"""
        if sys.platform != "darwin":
            return []
        try:
            proc = subprocess.run(
                ["xcrun", "simctl", "list", "devices", "available", "-j"],
                capture_output=True, text=True, timeout=15, check=False,
            )
            if proc.returncode != 0 or not proc.stdout.strip():
                return []
            raw = json.loads(proc.stdout)
            simulators = []
            for runtime, devices in raw.get("devices", {}).items():
                for d in devices:
                    simulators.append({
                        "udid": d.get("udid", ""),
                        "name": d.get("name", ""),
                        "runtime": runtime,
                        "state": d.get("state", ""),
                        "is_available": d.get("isAvailable", True),
                    })
            return simulators
        except Exception:
            return []

    @staticmethod
    def boot_simulator(udid: str) -> Tuple[bool, str]:
        """启动模拟器。"""
        if sys.platform != "darwin":
            return False, "仅 macOS 支持模拟器"
        try:
            proc = subprocess.run(
                ["xcrun", "simctl", "boot", udid],
                capture_output=True, text=True, timeout=30, check=False,
            )
            if proc.returncode == 0:
                return True, "模拟器已启动"
            # 可能已启动
            if "already" in (proc.stderr or "").lower():
                return True, "模拟器已在运行"
            return False, proc.stderr or "启动失败"
        except Exception as exc:
            return False, str(exc)

    @staticmethod
    def shutdown_simulator(udid: str) -> Tuple[bool, str]:
        """关闭模拟器。"""
        if sys.platform != "darwin":
            return False, "仅 macOS 支持模拟器"
        try:
            proc = subprocess.run(
                ["xcrun", "simctl", "shutdown", udid],
                capture_output=True, text=True, timeout=15, check=False,
            )
            if proc.returncode == 0:
                return True, "模拟器已关闭"
            if "already" in (proc.stderr or "").lower() or "No device" in (proc.stderr or ""):
                return True, "模拟器未运行"
            return False, proc.stderr or "关闭失败"
        except Exception as exc:
            return False, str(exc)


def _search_tree(node: Any, label: str, _depth: int = 0, _max_depth: int = 50) -> Optional[Dict[str, Any]]:
    """递归搜索 accessibility 树中 label 匹配的元素。"""
    if isinstance(node, dict):
        node_label = str(node.get("label", "") or node.get("value", "") or "")
        if label.lower() in node_label.lower():
            return node
        for child in node.get("children", []):
            found = _search_tree(child, label, _depth + 1, _max_depth)
            if found:
                return found
    elif isinstance(node, list):
        for item in node:
            found = _search_tree(item, label, _depth + 1, _max_depth)
            if found:
                return found
    return None


# ------------------------------------------------------------------
# 便捷函数
# ------------------------------------------------------------------

def is_ios_supported() -> bool:
    """检查当前环境是否支持 iOS 自动化。"""
    try:
        from mobile_env_config import ios_enabled
        if not ios_enabled():
            return False
    except ImportError:
        return False
    # idb 需要 macOS 或远程 macOS
    return True  # 允许通过远程 idb 连接


def get_ios_manager() -> Optional[IOSDeviceManager]:
    """获取 iOS 设备管理器。"""
    if not is_ios_supported():
        return None
    try:
        from mobile_env_config import idb_path
        return IOSDeviceManager(idb_path=idb_path())
    except ImportError:
        return IOSDeviceManager()


def check_ios_preflight() -> Dict[str, Any]:
    """iOS 环境预检（供 capability probe 使用）。"""
    out: Dict[str, Any] = {
        "supported": False,
        "idb_available": False,
        "device_count": 0,
        "devices": [],
        "macos": sys.platform == "darwin",
        "error": "",
    }
    mgr = get_ios_manager()
    if not mgr:
        out["error"] = "iOS 支持未启用 (ENABLE_IOS=0)"
        return out
    out["supported"] = True
    ok, msg = mgr.check_idb_available()
    out["idb_available"] = ok
    out["idb_version"] = msg
    if not ok:
        out["error"] = msg
        return out
    devices = mgr.list_devices()
    out["device_count"] = len(devices)
    out["devices"] = [d.to_dict() for d in devices]
    return out
