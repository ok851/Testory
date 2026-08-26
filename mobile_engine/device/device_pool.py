# -*- coding: utf-8 -*-
"""
设备池管理 — 多设备并行执行。

管理多个设备的并行测试执行:
- 每个设备独立的 Maestro 进程 (通过 --device udid 隔离)
- 进程隔离避免 adb 端口冲突
- 设备状态锁 (防止同一设备并发执行)
- 统一收集所有设备的执行结果
"""

from __future__ import annotations

import concurrent.futures
import threading
import time
from typing import Any, Dict, List, Optional

from mobile_engine.engine_interface import (
    DeviceInfo,
    FlowResult,
    FlowStep,
)

try:
    from uat_logger import uat_logger
except ImportError:
    import logging

    uat_logger = logging.getLogger(__name__)


class DevicePool:
    """
    设备池 — 管理多个设备并发执行。

    Usage:
        pool = DevicePool(max_workers=4)
        devices = pool.discover_devices()
        results = pool.execute_parallel(devices, flow)
    """

    def __init__(self, max_workers: int = 4):
        self._max_workers = max_workers
        self._lock = threading.Lock()
        self._busy_devices: set = set()
        self._results: Dict[str, FlowResult] = {}

    # ------------------------------------------------------------------
    # 设备发现
    # ------------------------------------------------------------------

    def discover_devices(self) -> List[DeviceInfo]:
        """
        发现当前可用设备 (USB 真机 + 模拟器)。

        Returns:
            DeviceInfo 列表
        """
        from modules.mobile.mobile_device_manager import (
            get_device_info,
            list_emulators,
            list_real_usb_devices,
        )

        devices: List[DeviceInfo] = []

        # USB 真机
        for d in list_real_usb_devices():
            udid = d.get("udid", "")
            if not udid:
                continue
            info = get_device_info(udid)
            devices.append(DeviceInfo(
                udid=udid,
                platform="android",
                model=info.get("model", d.get("display_name", "")),
                os_version=info.get("android_release", ""),
                screen_width=info.get("width", 1080),
                screen_height=info.get("height", 1920),
                density=info.get("density", 420),
                is_emulator=False,
                connection_type="usb",
                brand=info.get("brand", ""),
            ))

        # 模拟器
        for d in list_emulators():
            udid = d.get("udid", "")
            if not udid:
                continue
            info = get_device_info(udid)
            devices.append(DeviceInfo(
                udid=udid,
                platform="android",
                model=info.get("model", d.get("display_name", "")),
                os_version=info.get("android_release", ""),
                screen_width=info.get("width", 1080),
                screen_height=info.get("height", 1920),
                density=info.get("density", 420),
                is_emulator=True,
                connection_type="usb",
                brand=info.get("brand", ""),
            ))

        return devices

    def discover_device(self, udid: str) -> Optional[DeviceInfo]:
        """根据 udid 查询单个设备"""
        for d in self.discover_devices():
            if d.udid == udid:
                return d
        return None

    # ------------------------------------------------------------------
    # 并行执行
    # ------------------------------------------------------------------

    def execute_parallel(
        self,
        devices: List[DeviceInfo],
        flow: List[FlowStep],
        *,
        timeout_per_device: int = 600,
    ) -> Dict[str, FlowResult]:
        """
        在多个设备上并行执行相同测试流。

        Args:
            devices: 目标设备列表
            flow: 测试流
            timeout_per_device: 每个设备的超时 (秒)

        Returns:
            {udid: FlowResult}
        """
        results: Dict[str, FlowResult] = {}
        error_results: Dict[str, str] = {}

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(self._max_workers, len(devices)),
        ) as executor:
            futures: Dict[str, concurrent.futures.Future] = {}
            for device in devices:
                if device.udid in self._busy_devices:
                    uat_logger.warning("设备 %s 正忙，跳过", device.udid)
                    continue
                self._mark_busy(device.udid)
                future = executor.submit(
                    self._run_on_device, device, flow,
                )
                futures[device.udid] = future

            for udid, future in futures.items():
                try:
                    result = future.result(timeout=timeout_per_device)
                    results[udid] = result
                except concurrent.futures.TimeoutError:
                    error_results[udid] = f"超时 ({timeout_per_device}s)"
                except Exception as exc:
                    error_results[udid] = str(exc)
                finally:
                    self._mark_idle(udid)

        # 汇总
        self._results.update(results)

        uat_logger.info(
            "并行执行完成: %d 成功, %d 失败",
            len(results), len(error_results),
        )

        return results

    def execute_parallel_with_args(
        self,
        device_flows: Dict[str, List[FlowStep]],
        *,
        timeout_per_device: int = 600,
    ) -> Dict[str, FlowResult]:
        """
        在多个设备上执行不同的测试流。

        Args:
            device_flows: {udid: [FlowStep, ...]}
            timeout_per_device: 每个设备的超时

        Returns:
            {udid: FlowResult}
        """
        devices = []
        for udid in device_flows:
            device = self.discover_device(udid)
            if device:
                devices.append(device)
            else:
                uat_logger.warning("设备未发现: %s", udid)

        results: Dict[str, FlowResult] = {}
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(self._max_workers, len(devices)),
        ) as executor:
            futures = {}
            for device in devices:
                flow = device_flows.get(device.udid, [])
                if not flow:
                    continue
                self._mark_busy(device.udid)
                future = executor.submit(self._run_on_device, device, flow)
                futures[device.udid] = future

            for udid, future in futures.items():
                try:
                    results[udid] = future.result(timeout=timeout_per_device)
                except Exception as exc:
                    uat_logger.error("设备 %s 执行异常: %s", udid, exc)
                finally:
                    self._mark_idle(udid)

        return results

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _run_on_device(
        self,
        device: DeviceInfo,
        flow: List[FlowStep],
    ) -> FlowResult:
        """在单个设备上执行流"""
        from mobile_engine.engine_dispatcher import get_mobile_dispatcher

        dispatcher = get_mobile_dispatcher()
        dispatcher.connect_device(device)

        uat_logger.info("设备 %s 开始执行 (%d 步骤)", device.udid, len(flow))
        started = time.time()

        try:
            result = dispatcher.execute_flow(flow)
        except Exception as exc:
            uat_logger.error("设备 %s 执行异常: %s", device.udid, exc)
            result = FlowResult(
                steps=[], total_duration_ms=0,
                passed_count=0, failed_count=0,
            )

        elapsed = time.time() - started
        uat_logger.info("设备 %s 执行完成 (%.1fs)", device.udid, elapsed)

        return result

    def discover_ios_devices(self) -> List[DeviceInfo]:
        """发现已连接的 iOS 设备。"""
        try:
            from mobile_engine.device.ios_device import IOSDeviceManager
            mgr = IOSDeviceManager()
            return mgr.list_devices()
        except Exception:
            return []

    def discover_all_devices(self) -> List[DeviceInfo]:
        """发现所有平台设备（Android + iOS）。"""
        devices = self.discover_devices()
        ios_devices = self.discover_ios_devices()
        devices.extend(ios_devices)
        return devices

    def _mark_busy(self, udid: str) -> None:
        with self._lock:
            self._busy_devices.add(udid)

    def _mark_idle(self, udid: str) -> None:
        with self._lock:
            self._busy_devices.discard(udid)

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def get_busy_devices(self) -> List[str]:
        """获取当前正忙的设备 udid 列表"""
        with self._lock:
            return list(self._busy_devices)

    def get_results(self) -> Dict[str, FlowResult]:
        """获取已缓存的执行结果"""
        return dict(self._results)

    def clear_results(self) -> None:
        """清空结果缓存"""
        self._results.clear()
