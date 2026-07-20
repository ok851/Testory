# -*- coding: utf-8 -*-
"""能力注册表：探测当前可用的手（web/desktop/mobile/api），供预检与 skill 按需加载。"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Tuple


def _flag(name: str, default: str = "1") -> bool:
    return os.environ.get(name, default).strip().lower() not in ("0", "false", "no", "off")


def probe_web() -> Dict[str, Any]:
    ok = False
    detail = "cdp_not_attached"
    try:
        from hermes_config import hermes_cdp_attached

        ok = bool(hermes_cdp_attached())
        detail = "cdp_attached" if ok else "cdp_not_attached"
    except Exception as e:
        detail = str(e)[:120]
    return {"id": "web", "available": ok, "detail": detail, "skills": ["testory-web-browser"]}


def probe_desktop() -> Dict[str, Any]:
    gateway = False
    local = True
    detail_parts: List[str] = []
    try:
        from desktop_agent_client import desktop_agent_enabled, desktop_agent_json

        if desktop_agent_enabled():
            payload, err = desktop_agent_json("GET", "/health", timeout_sec=1.5)
            if err:
                # 部分 gateway 无 /health，仍视为可尝试
                gateway = True
                detail_parts.append(f"gateway_configured:{err[:60]}")
            else:
                gateway = True
                detail_parts.append("gateway_ok")
        else:
            detail_parts.append("gateway_disabled")
    except Exception as e:
        detail_parts.append(str(e)[:80])
    # 本机 DesktopAutomation 始终可作为后备
    available = gateway or local
    return {
        "id": "desktop",
        "available": available,
        "detail": ";".join(detail_parts) or "local_desktop",
        "skills": ["testory-windows-desktop"],
        "gateway": gateway,
    }


def probe_mobile() -> Dict[str, Any]:
    udid = ""
    ok = False
    detail = "no_device"
    try:
        from mobile_device_manager import get_connected_udid

        udid = (get_connected_udid() or "").strip()
        ok = bool(udid)
        detail = f"udid={udid}" if ok else "no_device"
    except Exception as e:
        detail = str(e)[:120]
    return {
        "id": "mobile",
        "available": ok,
        "detail": detail,
        "udid": udid,
        "skills": ["testory-android-mobile"],
    }


def probe_api() -> Dict[str, Any]:
    # 接口执行内核始终在平台进程内可用
    return {
        "id": "api",
        "available": True,
        "detail": "platform_http_runner",
        "skills": ["testory-api-http"],
    }


def probe_hermes() -> Dict[str, Any]:
    ok = False
    detail = "not_configured"
    try:
        from hermes_gateway_client import HermesGatewayClient

        c = HermesGatewayClient()
        if not c.is_configured():
            detail = "not_configured"
        elif c.health_check(timeout_sec=1.0):
            ok = True
            detail = "healthy"
        else:
            detail = "unreachable"
    except Exception as e:
        detail = str(e)[:120]
    return {"id": "hermes", "available": ok, "detail": detail, "skills": []}


def snapshot_capabilities() -> Dict[str, Any]:
    caps = {
        "hermes": probe_hermes(),
        "web": probe_web(),
        "desktop": probe_desktop(),
        "mobile": probe_mobile(),
        "api": probe_api(),
    }
    skills: List[str] = []
    for key in ("web", "desktop", "mobile", "api"):
        c = caps[key]
        if c.get("available"):
            for s in c.get("skills") or []:
                if s not in skills:
                    skills.append(s)
    # auto 时至少给 cross-end 提示（不依赖设备）
    if "testory-cross-end" not in skills and any(
        caps[k].get("available") for k in ("web", "desktop", "mobile", "api")
    ):
        skills.append("testory-cross-end")
    return {"capabilities": caps, "available_skills": skills}


def preflight_for_task(
    message: str = "",
    *,
    require_hermes: bool = True,
) -> Tuple[bool, str, Dict[str, Any]]:
    """
    任务开跑前预检。返回 (ok, user_message, snapshot)。
    不因「未连手机」直接失败（任务可能纯 Web）；仅当 Hermes 不可用且需要自动化时失败。
    """
    snap = snapshot_capabilities()
    caps = snap["capabilities"]
    if require_hermes and not caps["hermes"].get("available"):
        return False, "智能体未就绪。请先在左上角点击「启动」后再执行自动化任务。", snap

    hints: List[str] = []
    t = (message or "").lower()
    # 软提示：用户话里像移动但无设备
    mobile_hints = ("安卓", "android", "手机", "app", "adb")
    if any(h in (message or "") or h in t for h in mobile_hints) and not caps["mobile"].get("available"):
        hints.append("未检测到已连接的 Android 设备；若任务需要移动端，请先用 USB 连接并开启调试。")

    web_hints = ("http://", "https://", "网页", "浏览器", "网站")
    if any(h in (message or "") or h in t for h in web_hints) and not caps["web"].get("available"):
        hints.append("浏览器 CDP 尚未连接；执行时将按需尝试启动本机 Edge/Chrome。")

    msg = "；".join(hints) if hints else ""
    return True, msg, snap


def skills_for_available_caps(snap: Dict[str, Any] = None) -> List[str]:
    if snap is None:
        snap = snapshot_capabilities()
    return list(snap.get("available_skills") or [])
