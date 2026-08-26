# -*- coding: utf-8 -*-
"""移动端测试页：设备连接（真机 / 第三方模拟器，无投屏）。"""

from __future__ import annotations

from typing import Any, Dict

from modules.mobile.mobile_device_manager import get_device_info, is_emulator_udid, list_user_apps, set_connected_udid


def finish_studio_connect(
    udid: str,
    *,
    frame_preset: str = "generic_19_9",
    try_appium: bool = False,
    client_host: str = "",
) -> Dict[str, Any]:
    """登记当前设备并返回前端所需 payload（不含投屏）。"""
    del client_host, frame_preset  # 保留签名供路由兼容

    udid = (udid or "").strip()
    if not udid:
        raise RuntimeError("缺少设备 serial")

    set_connected_udid(udid)
    device_info = get_device_info(udid)
    apps = list_user_apps(udid, limit=60)

    payload: Dict[str, Any] = {
        "udid": udid,
        "device": device_info,
        "apps": apps,
        "suggested_app_package": device_info.get("foreground_package") or "",
        "is_emulator": is_emulator_udid(udid),
    }

    appium_ok = False
    appium_error = ""
    from modules.mobile.mobile_executor import get_mobile_executor

    executor = get_mobile_executor()
    try:
        executor.bind_device(udid)
    except Exception:
        pass

    if try_appium:
        from modules.mobile.mobile_env_config import mobile_runtime_available

        if mobile_runtime_available():
            ok, appium_msg = executor.check_appium_server(try_auto_start=True)
            if ok:
                caps: Dict[str, Any] = {"udid": udid}
                pkg = device_info.get("foreground_package") or ""
                if pkg:
                    caps["appPackage"] = pkg
                try:
                    executor.connect(caps)
                    appium_ok = True
                except Exception as exc:
                    appium_error = str(exc)
            else:
                appium_error = appium_msg
        else:
            from modules.mobile.mobile_env_config import mobile_runtime_unavailable_reason

            appium_error = mobile_runtime_unavailable_reason() or "Appium 客户端不可用"

    payload["appium_connected"] = appium_ok
    payload["appium_error"] = appium_error or None
    return payload
