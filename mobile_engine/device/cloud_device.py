# -*- coding: utf-8 -*-
"""
云真机代理 (STF / OpenSTF)。

支持通过 STF 代理连接远程云真机，提供与本地设备一致的交互接口。
Maestro 通过 --device 参数直接指定远程 ADB serial (如 192.168.1.100:5555)。

STF API 参考: https://github.com/DeviceFarmer/stf
"""

from __future__ import annotations

import subprocess
import time
from typing import Any, Dict, List, Optional, Tuple

from mobile_engine.engine_interface import DeviceInfo

try:
    import requests
except ImportError:
    requests = None  # type: ignore

try:
    from uat_logger import uat_logger
except ImportError:
    import logging

    uat_logger = logging.getLogger(__name__)


class CloudDeviceProxy:
    """
    云真机代理 — STF 设备租用与管理。

    流程:
    1. 连接 STF API
    2. 租用空闲设备
    3. adb connect <stf_host>:<remote_port>
    4. 执行测试
    5. 释放设备
    """

    def __init__(
        self,
        stf_url: str = "",
        stf_token: str = "",
    ):
        """
        Args:
            stf_url: STF 服务地址 (如 http://stf.example.com)
            stf_token: STF API Token
        """
        from mobile_env_config import cloud_device_endpoint

        self._stf_url = (stf_url or cloud_device_endpoint()).rstrip("/")
        self._stf_token = stf_token
        self._rented_devices: Dict[str, Dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # 设备管理
    # ------------------------------------------------------------------

    def list_devices(self) -> List[DeviceInfo]:
        """
        从 STF 获取可用设备列表。

        Returns:
            DeviceInfo 列表
        """
        if not self._stf_url:
            return []

        try:
            resp = self._stf_get("/api/v1/devices")
            data = resp.json()
            devices = data.get("devices", [])
            result = []
            for d in devices:
                if not d.get("present") or not d.get("ready"):
                    continue
                provider = d.get("provider", {})
                result.append(DeviceInfo(
                    udid=d.get("serial", ""),
                    platform="android",
                    model=d.get("model", ""),
                    os_version=d.get("version", ""),
                    screen_width=d.get("display", {}).get("width", 1080),
                    screen_height=d.get("display", {}).get("height", 1920),
                    is_emulator=False,
                    connection_type="cloud",
                    brand=d.get("manufacturer", ""),
                ))
            return result
        except Exception as exc:
            uat_logger.error("STF 设备列表获取失败: %s", exc)
            return []

    def rent_device(self, udid: str = "",
                    timeout: int = 30) -> Optional[Tuple[DeviceInfo, str]]:
        """
        租用一个 STF 设备。

        Args:
            udid: 指定设备序列号 (为空则自动选择)
            timeout: 租用超时 (秒)

        Returns:
            (DeviceInfo, adb_connect_address) 或 None
        """
        if not self._stf_url:
            return None

        # 查找可用设备
        if udid:
            payload = {"serial": udid}
        else:
            # 自动选第一个可用设备
            devices = self.list_devices()
            available = [d for d in devices if d.udid not in self._rented_devices]
            if not available:
                uat_logger.error("无可用 STF 设备")
                return None
            target = available[0]
            udid = target.udid

        # 请求租用
        try:
            resp = self._stf_post(
                f"/api/v1/user/devices/{udid}/remoteConnect",
                json={},
            )
            data = resp.json()
            if not data.get("success"):
                uat_logger.error("STF 设备租用失败: %s", data.get("description", ""))
                return None

            remote_connect_url = data.get("remoteConnectUrl", "")
            if not remote_connect_url:
                uat_logger.error("STF 未返回 remoteConnectUrl")
                return None

            # adb connect
            ok, msg = self._adb_connect(remote_connect_url)
            if not ok:
                uat_logger.error("ADB 连接远程设备失败: %s", msg)
                return None

            self._rented_devices[udid] = {
                "remote_url": remote_connect_url,
                "rented_at": time.time(),
            }

            device = DeviceInfo(
                udid=udid,
                platform="android",
                connection_type="cloud",
            )
            uat_logger.info("STF 设备租用成功: %s -> %s", udid, remote_connect_url)
            return device, remote_connect_url

        except Exception as exc:
            uat_logger.error("STF 租用异常: %s", exc)
            return None

    def release_device(self, udid: str) -> bool:
        """
        释放租用的设备。

        Returns:
            是否成功释放
        """
        if udid not in self._rented_devices:
            return False

        try:
            remote_url = self._rented_devices[udid].get("remote_url", "")
            if remote_url:
                self._adb_disconnect(remote_url)

            resp = self._stf_delete(
                f"/api/v1/user/devices/{udid}/remoteConnect",
            )
            del self._rented_devices[udid]
            uat_logger.info("STF 设备 %s 已释放", udid)
            return True
        except Exception as exc:
            uat_logger.error("STF 释放设备失败: %s", exc)
            return False

    def release_all(self) -> None:
        """释放所有租用的设备"""
        for udid in list(self._rented_devices.keys()):
            self.release_device(udid)

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _stf_get(self, path: str, **kwargs) -> Any:
        """STF API GET 请求"""
        if not requests:
            raise RuntimeError("requests 库未安装")
        url = f"{self._stf_url}{path}"
        headers = {}
        if self._stf_token:
            headers["Authorization"] = f"Bearer {self._stf_token}"
        resp = requests.get(url, headers=headers, timeout=15, **kwargs)
        resp.raise_for_status()
        return resp

    def _stf_post(self, path: str, **kwargs) -> Any:
        """STF API POST 请求"""
        if not requests:
            raise RuntimeError("requests 库未安装")
        url = f"{self._stf_url}{path}"
        headers = {}
        if self._stf_token:
            headers["Authorization"] = f"Bearer {self._stf_token}"
        resp = requests.post(url, headers=headers, timeout=30, **kwargs)
        resp.raise_for_status()
        return resp

    def _stf_delete(self, path: str, **kwargs) -> Any:
        """STF API DELETE 请求"""
        if not requests:
            raise RuntimeError("requests 库未安装")
        url = f"{self._stf_url}{path}"
        headers = {}
        if self._stf_token:
            headers["Authorization"] = f"Bearer {self._stf_token}"
        resp = requests.delete(url, headers=headers, timeout=15, **kwargs)
        resp.raise_for_status()
        return resp

    @staticmethod
    def _adb_connect(address: str) -> Tuple[bool, str]:
        """adb connect 到远程设备"""
        try:
            proc = subprocess.run(
                ["adb", "connect", address],
                capture_output=True, text=True, timeout=15, check=False,
            )
            output = (proc.stdout + proc.stderr).lower()
            if "connected" in output or "already connected" in output:
                return True, output.strip()
            return False, output.strip()
        except Exception as exc:
            return False, str(exc)

    @staticmethod
    def _adb_disconnect(address: str) -> None:
        """adb disconnect 远程设备"""
        try:
            subprocess.run(
                ["adb", "disconnect", address],
                timeout=10, check=False,
            )
        except Exception:
            pass
