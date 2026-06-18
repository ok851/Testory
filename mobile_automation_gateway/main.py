# -*- coding: utf-8 -*-
"""
Testory 移动端 Agent Gateway（FastAPI）。

环境变量：
  MOBILE_AGENT_GATEWAY_SECRET  与平台共用，必填
  MOBILE_AGENT_GATE_PORT       默认 8777
  MOBILE_AGENT_WS_PATH         默认 /internal/events
"""
from __future__ import annotations

import asyncio
import base64
import os
import secrets
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from mobile_assistant_bundles import (
    assistant_installed_on_device,
    install_testory_assistant,
    resolve_assistant_apk_path,
)
from mobile_device_manager import (
    adb_disconnect_device,
    capture_screenshot_png,
    check_mobile_health,
    get_connected_udid,
    get_device_info,
    list_emulators,
    list_usb_devices,
    pick_default_device,
    pick_default_emulator,
    set_connected_udid,
    wireless_pair_and_connect,
)
from mobile_automation_gateway import plugin_rpc
from mobile_automation_gateway import recording as rec_mod
from mobile_automation_gateway import replay as replay_mod

app = FastAPI(title="Testory Mobile Agent Gateway")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _secret_ok(request: Request) -> bool:
    expected = (os.environ.get("MOBILE_AGENT_GATEWAY_SECRET") or "").strip()
    if not expected:
        return False
    got = (request.headers.get("X-Mobile-Agent-Secret") or "").strip()
    return secrets.compare_digest(got, expected)


def _ws_secret_ok(websocket: WebSocket) -> bool:
    expected = (os.environ.get("MOBILE_AGENT_GATEWAY_SECRET") or "").strip()
    if not expected:
        return False
    got = (websocket.headers.get("x-mobile-agent-secret") or "").strip()
    if not got:
        got = (websocket.query_params.get("secret") or "").strip()
    return secrets.compare_digest(got, expected)


def _require_auth(request: Request) -> None:
    if not _secret_ok(request):
        raise HTTPException(401, "unauthorized")


@app.on_event("startup")
async def _on_startup() -> None:
    rec_mod.set_ws_loop(asyncio.get_event_loop())


_GATEWAY_BUILD = os.environ.get("MOBILE_GATEWAY_BUILD") or "20260616-no-auto-install"


@app.get("/health")
async def health() -> Dict[str, Any]:
    return {
        "success": True,
        "service": "mobile-agent-gateway",
        "build": _GATEWAY_BUILD,
        "auto_install_on_connect": False,
    }


@app.post("/internal/devices/scan")
async def devices_scan(request: Request) -> Dict[str, Any]:
    _require_auth(request)
    devices = list_usb_devices()
    emulators = list_emulators()
    return {
        "success": True,
        "devices": devices,
        "emulators": emulators,
        "connected_udid": get_connected_udid() or "",
    }


@app.post("/internal/devices/connect")
async def devices_connect(request: Request) -> Dict[str, Any]:
    _require_auth(request)
    body = await request.json()
    udid = (body.get("udid") or "").strip()
    wireless = body.get("wireless") or {}
    if wireless.get("host") or body.get("wireless_host"):
        host = (wireless.get("host") or body.get("wireless_host") or "").strip()
        port = int(wireless.get("port") or body.get("wireless_port") or 0)
        code = (wireless.get("pair_code") or body.get("pairing_code") or "").strip()
        ok, msg, resolved, phase = wireless_pair_and_connect(host, port, code)
        if not ok:
            return {"success": False, "error": msg, "phase": phase}
        udid = resolved or udid
    if not udid:
        dev = pick_default_device() or pick_default_emulator()
        if not dev:
            return {"success": False, "error": "未发现已授权设备"}
        udid = dev.get("udid") or ""
    set_connected_udid(udid)
    info = get_device_info(udid)
    assistant_status: Dict[str, Any] = {}
    try:
        from mobile_assistant_bundles import get_assistant_device_status

        assistant_status = get_assistant_device_status(udid)
    except Exception:
        pass
    plugin_installed = bool(assistant_status.get("assistant_installed")) or assistant_installed_on_device(udid)
    plugin_ok = False
    plugin_msg = ""
    if plugin_installed:
        plugin_ok, plugin_msg = plugin_rpc.ensure_plugin_tunnel(udid)
    out = {
        "success": True,
        "udid": udid,
        "device": info,
        "plugin_installed": plugin_installed,
        "plugin_ready": plugin_ok,
        "plugin_message": plugin_msg,
        **assistant_status,
    }
    if assistant_status.get("assistant_needs_install"):
        ver = assistant_status.get("assistant_version_on_device") or 0
        exp = assistant_status.get("assistant_version_name_expected") or ""
        out["assistant_install_hint"] = (
            f"设备助手版本过旧或未安装（当前 versionCode={ver}）。"
            f"请点击「安装插件」手动安装 v{exp}，连接设备不会自动推送 APK。"
        )
    return out


@app.post("/internal/devices/disconnect")
async def devices_disconnect(request: Request) -> Dict[str, Any]:
    _require_auth(request)
    body = await request.json()
    udid = (body.get("udid") or get_connected_udid() or "").strip()
    if udid:
        plugin_rpc.clear_forward(udid)
        adb_disconnect_device(udid)
    set_connected_udid(None)
    return {"success": True, "message": "已断开"}


@app.post("/internal/plugin/install")
async def plugin_install(request: Request) -> Dict[str, Any]:
    _require_auth(request)
    body = await request.json()
    udid = (body.get("udid") or get_connected_udid() or "").strip()
    if not udid:
        return {"success": False, "error": "请先连接设备"}
    result = install_testory_assistant(udid, launch_app=False)
    if not result.get("success"):
        return result
    ok, msg = plugin_rpc.ensure_plugin_tunnel(udid)
    result["plugin_tunnel"] = ok
    result["plugin_tunnel_message"] = msg
    return result


@app.post("/internal/recording/start")
async def recording_start(request: Request) -> Dict[str, Any]:
    _require_auth(request)
    body = await request.json()
    udid = (body.get("udid") or get_connected_udid() or "").strip()
    screenshot = bool(body.get("screenshot_per_step", True))
    return rec_mod.start_recording_session(udid, screenshot_per_step=screenshot)


@app.post("/internal/recording/stop")
async def recording_stop(request: Request) -> Dict[str, Any]:
    _require_auth(request)
    body = await request.json()
    udid = (body.get("udid") or get_connected_udid() or "").strip()
    return rec_mod.stop_recording_session(udid)


@app.get("/internal/recording/status")
async def recording_status(request: Request, udid: str = "") -> Dict[str, Any]:
    _require_auth(request)
    return {"success": True, **rec_mod.recording_status(udid or get_connected_udid() or "")}


@app.post("/internal/replay/run")
async def replay_run(request: Request) -> Dict[str, Any]:
    _require_auth(request)
    body = await request.json()
    udid = (body.get("udid") or get_connected_udid() or "").strip()
    steps = body.get("steps") or []
    from_index = int(body.get("from_index") or 0)
    if not isinstance(steps, list):
        return {"success": False, "error": "steps 须为数组"}
    return replay_mod.run_steps(udid, steps, from_index=from_index)


@app.post("/internal/replay/step")
async def replay_step(request: Request) -> Dict[str, Any]:
    _require_auth(request)
    body = await request.json()
    udid = (body.get("udid") or get_connected_udid() or "").strip()
    step = body.get("step") or {}
    idx = int(body.get("step_index") or 0)
    result = replay_mod.execute_step(udid, step, step_index=idx)
    ok = result.get("status") != "error"
    return {"success": ok, "result": result}


@app.post("/internal/inspect/page-source")
async def inspect_page_source(request: Request) -> Dict[str, Any]:
    _require_auth(request)
    body = await request.json()
    udid = (body.get("udid") or get_connected_udid() or "").strip()
    ok, msg = plugin_rpc.ensure_plugin_tunnel(udid)
    if not ok:
        return {"success": False, "error": msg}
    try:
        tree = plugin_rpc.get_page_source(udid)
        return {"success": True, "tree": tree}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@app.post("/internal/inspect/screenshot")
async def inspect_screenshot(request: Request) -> Dict[str, Any]:
    _require_auth(request)
    body = await request.json()
    udid = (body.get("udid") or get_connected_udid() or "").strip()
    use_plugin = body.get("use_plugin", True)
    try:
        if use_plugin:
            ok, msg = plugin_rpc.ensure_plugin_tunnel(udid)
            if ok:
                data, fmt = plugin_rpc.take_screenshot(udid)
                return {
                    "success": True,
                    "format": fmt,
                    "image_base64": base64.b64encode(data).decode("ascii"),
                    "source": "plugin",
                }
        data = capture_screenshot_png(udid)
        return {
            "success": True,
            "format": "png",
            "image_base64": base64.b64encode(data).decode("ascii"),
            "source": "adb",
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@app.post("/internal/plugin/status")
async def plugin_status(request: Request) -> Dict[str, Any]:
    _require_auth(request)
    body = await request.json()
    udid = (body.get("udid") or get_connected_udid() or "").strip()
    installed = assistant_installed_on_device(udid) if udid else False
    status = plugin_rpc.plugin_status(udid) if udid and installed else {}
    reachable = bool(status.get("accessibility_enabled")) and bool(status.get("server_running"))
    if udid and installed:
        ping_ok, ping_msg = plugin_rpc.ping_plugin(udid)
        reachable = ping_ok
        if not ping_ok:
            status["ping_error"] = ping_msg
    return {
        "success": True,
        "udid": udid,
        "plugin_installed": installed,
        "plugin_ready": reachable,
        "assistant_installed": installed,
        "assistant_connected": reachable,
        "status": status,
        "apk_available": resolve_assistant_apk_path() is not None,
    }


@app.post("/internal/health/detail")
async def health_detail(request: Request) -> Dict[str, Any]:
    _require_auth(request)
    data = check_mobile_health()
    data["agent"] = "mobile_automation_gateway"
    data["plugin_driver"] = True
    return {"success": True, **data}


@app.websocket("/internal/events")
async def ws_events(websocket: WebSocket) -> None:
    if not _ws_secret_ok(websocket):
        await websocket.close(code=4401)
        return
    await websocket.accept()
    rec_mod.register_ws(websocket)
    try:
        await websocket.send_json({"type": "connected", "payload": {"message": "mobile agent events"}})
        while True:
            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
            except asyncio.TimeoutError:
                await websocket.send_json({"type": "ping", "payload": {}})
    except WebSocketDisconnect:
        pass
    finally:
        rec_mod.unregister_ws(websocket)


def run_gateway() -> None:
    import uvicorn

    port = int(os.environ.get("MOBILE_AGENT_GATE_PORT", "8777"))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")


if __name__ == "__main__":
    run_gateway()
