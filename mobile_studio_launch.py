# -*- coding: utf-8 -*-
"""移动端测试页：一键启动模拟器 + 投屏连接。"""

from __future__ import annotations

from typing import Any, Callable, Dict, Tuple

ProgressCallback = Callable[[int, str], None]


def launch_emulator_studio(
    preset_id: str,
    *,
    port: int = 5554,
    gpu: str = "host",
    no_window: bool = True,
    force_restart: bool = False,
    progress_cb: ProgressCallback = None,
) -> Tuple[bool, str, Dict[str, Any]]:
    """
    一键流程：检查环境 → 创建 AVD（若缺）→ 启动/复用模拟器 → 等待系统就绪。
    """
    from mobile_emulator_manager import (
        emulator_status,
        ensure_emulator_for_preset,
        frame_preset_for_model,
        get_preset_by_id,
        provision_avd_for_preset,
        wait_emulator_mirror_ready,
        _serial_for_port,
    )

    preset = get_preset_by_id(preset_id)
    if not preset:
        return False, f"未知设备型号：{preset_id}", {}

    label = preset.get("label") or preset_id
    if progress_cb:
        progress_cb(5, f"检查 {label} 环境…")

    st = emulator_status()
    if not st.get("emulator_available"):
        msg = (st.get("emulator_message") or "模拟器 SDK 不可用").strip()
        hint = (st.get("setup_hint") or "").strip()
        if hint:
            msg = msg + "\n" + hint
        return False, msg, {}

    if progress_cb:
        progress_cb(10, "准备虚拟手机…")
    ok_avd, avd_name, avd_msg = provision_avd_for_preset(preset_id)
    if not ok_avd:
        if progress_cb:
            progress_cb(12, "正在修复 SDK 环境…")
        try:
            from mobile_emulator_sdk_bundles import ensure_emulator_sdk_ready

            repair = ensure_emulator_sdk_ready()
            if not repair.get("success"):
                err = repair.get("error") or avd_msg or "环境修复失败"
                return False, err, {}
            ok_avd, avd_name, avd_msg = provision_avd_for_preset(preset_id)
        except Exception as exc:
            return False, avd_msg or str(exc), {}
        if not ok_avd:
            return False, avd_msg or "创建虚拟手机失败", {}

    if progress_cb:
        progress_cb(15, "正在启动模拟器…")
    ok, msg, meta = ensure_emulator_for_preset(
        preset_id,
        port=port,
        gpu=gpu,
        no_window=no_window,
        force_restart=force_restart,
        progress_cb=progress_cb,
    )
    if not ok:
        return False, msg, meta or {}

    meta = dict(meta or {})
    serial = (meta.get("serial") or _serial_for_port(port)).strip()
    meta["serial"] = serial
    meta["preset_id"] = preset_id
    meta["frame_preset_id"] = frame_preset_for_model(preset_id)

    if progress_cb:
        progress_cb(92, "等待 Android 就绪…")
    ready, ready_msg = wait_emulator_mirror_ready(serial, timeout=90 if not meta.get("reused") else 45)
    if not ready:
        return False, ready_msg, meta

    if progress_cb:
        progress_cb(96, "正在连接投屏…")
    return True, msg, meta


def finish_studio_connect(
    udid: str,
    *,
    frame_preset: str = "generic_19_9",
    try_appium: bool = False,
    client_host: str = "",
) -> Dict[str, Any]:
    """启动/复用模拟器完成后：建立投屏会话并返回前端所需 payload。"""
    from mobile_device_manager import capture_screenshot_png, get_device_info, list_user_apps, set_connected_udid
    from mobile_device_profiles import get_frame_preset
    from mobile_emulator_manager import wait_emulator_mirror_ready
    from mobile_env_config import resolve_mirror_backend, scrcpy_bridge_url
    from mobile_mirror import start_scrcpy_mirror

    udid = (udid or "").strip()
    if not udid:
        raise RuntimeError("缺少设备 serial")

    if udid.startswith("emulator-"):
        ready, ready_msg = wait_emulator_mirror_ready(udid, timeout=30)
        if not ready:
            raise RuntimeError(ready_msg or "模拟器尚未就绪")

    set_connected_udid(udid)
    backend = resolve_mirror_backend(udid)
    mirror_fallback_reason = ""

    if backend == "scrcpy_ws":
        from mobile_scrcpy_bridge import bridge_health

        health = bridge_health()
        if not health.get("scrcpy_server_ready"):
            backend = "screencap"
            mirror_fallback_reason = "未找到 scrcpy-server，请在插件市场安装「scrcpy 高帧率投屏」"
    elif udid.startswith("emulator-"):
        probe = capture_screenshot_png(udid)
        if not probe:
            raise RuntimeError("模拟器已连接但无法获取画面，请等待 10 秒后重试或点「停止」后重新启动")

    mirror = start_scrcpy_mirror(udid)
    session_id = mirror.get("session_id") or ""

    payload: Dict[str, Any] = {
        "udid": udid,
        "session_id": session_id,
        "mirror_backend": backend,
        "mirror_frame_url": f"/api/mobile/mirror/frame?session_id={session_id}&udid={udid}",
        "mirror_fallback_reason": mirror_fallback_reason or None,
        "is_emulator": udid.startswith("emulator-"),
        "scrcpy_started": mirror.get("scrcpy_started"),
    }
    if backend == "scrcpy_ws":
        payload["mirror_stream_url"] = f"/api/mobile/mirror/scrcpy-stream?serial={udid}"
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
