# -*- coding: utf-8 -*-
"""移动端测试页：真机连接与投屏会话建立。"""

from __future__ import annotations

from typing import Any, Dict
from urllib.parse import quote


def finish_studio_connect(
    udid: str,
    *,
    frame_preset: str = "generic_19_9",
    try_appium: bool = False,
    client_host: str = "",
) -> Dict[str, Any]:
    """建立投屏会话并返回前端所需 payload。"""
    from mobile_device_manager import get_device_info, list_user_apps, set_connected_udid
    from mobile_device_profiles import get_frame_preset
    from mobile_env_config import resolve_mirror_backend, scrcpy_bridge_url
    from mobile_mirror import start_scrcpy_mirror

    udid = (udid or "").strip()
    if not udid:
        raise RuntimeError("缺少设备 serial")

    set_connected_udid(udid)
    backend = resolve_mirror_backend(udid)
    mirror_fallback_reason = ""

    if backend == "scrcpy_ws":
        from mobile_scrcpy_bridge import bridge_health, warm_scrcpy_session

        health = bridge_health()
        if not health.get("scrcpy_server_ready"):
            backend = "screencap"
            mirror_fallback_reason = "未找到 scrcpy-server，请在插件市场安装「scrcpy 高帧率投屏」"
        else:
            warm_ok, warm_msg = warm_scrcpy_session(udid, timeout=25.0)
            if not warm_ok:
                backend = "screencap"
                mirror_fallback_reason = warm_msg or "scrcpy 高帧率投屏启动失败"

    mirror = start_scrcpy_mirror(udid)
    session_id = mirror.get("session_id") or ""

    payload: Dict[str, Any] = {
        "udid": udid,
        "session_id": session_id,
        "mirror_backend": backend,
        "mirror_frame_url": f"/api/mobile/mirror/frame?session_id={session_id}&udid={udid}",
        "mirror_fallback_reason": mirror_fallback_reason or None,
        "scrcpy_started": mirror.get("scrcpy_started"),
    }
    if backend == "scrcpy_ws":
        payload["mirror_stream_url"] = (
            f"/api/mobile/mirror/scrcpy-stream?serial={quote(udid, safe='')}"
        )
        payload["mirror_ws_url"] = f"{scrcpy_bridge_url(client_host)}/?serial={udid}"

    device_info = get_device_info(udid)
    apps = list_user_apps(udid, limit=60)
    payload["device"] = device_info
    payload["apps"] = apps
    payload["suggested_app_package"] = device_info.get("foreground_package") or ""
    payload["frame_preset"] = get_frame_preset(frame_preset)

    appium_ok = False
    appium_error = ""
    if try_appium:
        from mobile_env_config import mobile_runtime_available
        from mobile_executor import get_mobile_executor

        if mobile_runtime_available():
            executor = get_mobile_executor()
            ok, appium_msg = executor.check_appium_server()
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
    payload["appium_connected"] = appium_ok
    payload["appium_error"] = appium_error or None
    return payload
