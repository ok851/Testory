# -*- coding: utf-8 -*-
"""桌面版启动状态与延迟网关引导。"""
from __future__ import annotations

import os
import threading
import time
from typing import Any, Dict

_STATE_LOCK = threading.Lock()
_STATE: Dict[str, Any] = {
    "phase": "booting",
    "message": "正在启动本地服务…",
    "gateways": "pending",
    "ready": False,
    "started_at": time.time(),
}
_DEFER_THREAD: threading.Thread | None = None


def desktop_lazy_gateway_boot() -> bool:
    raw = (os.environ.get("DESKTOP_LAZY_GATEWAY_BOOT") or "").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    if raw in ("1", "true", "yes", "on"):
        return True
    return os.environ.get("UAT_DESKTOP_MODE", "").strip().lower() in ("1", "true", "yes")


def set_startup_phase(phase: str, message: str, **extra: Any) -> None:
    with _STATE_LOCK:
        _STATE["phase"] = phase
        _STATE["message"] = message
        _STATE.update(extra)


def startup_status_payload() -> Dict[str, Any]:
    with _STATE_LOCK:
        return dict(_STATE)


def mark_app_ready() -> None:
    set_startup_phase("ready", "准备就绪", ready=True)


def _deferred_gateway_worker() -> None:
    set_startup_phase("gateways", "正在初始化 AI 与浏览器组件…", gateways="starting")
    try:
        from embedded_browser_service_bootstrap import bootstrap_embedded_browser_services

        bootstrap_embedded_browser_services()
    except Exception as exc:
        set_startup_phase("gateways", f"浏览器组件初始化异常：{exc}", gateways="error")
    try:
        from hermes_service_bootstrap import bootstrap_hermes_services

        bootstrap_hermes_services()
    except Exception as exc:
        set_startup_phase("gateways", f"Hermes 组件初始化异常：{exc}", gateways="error")
    set_startup_phase("ready", "准备就绪", gateways="done", ready=True)


def schedule_deferred_gateway_boot() -> None:
    global _DEFER_THREAD
    if not desktop_lazy_gateway_boot():
        mark_app_ready()
        return
    if _DEFER_THREAD is not None and _DEFER_THREAD.is_alive():
        return
    _DEFER_THREAD = threading.Thread(target=_deferred_gateway_worker, daemon=True, name="desktop-gateway-boot")
    _DEFER_THREAD.start()


def shutdown_all_services() -> None:
    """终止桌面版拉起的子进程（网关等）。"""
    for mod_name, func_name in (
        ("embedded_browser_service_bootstrap", "stop_embedded_gateway"),
        ("hermes_service_bootstrap", "stop_hermes_gateway"),
        ("desktop_service_bootstrap", "stop_desktop_gateway"),
        ("mobile_service_bootstrap", "stop_mobile_gateway"),
    ):
        try:
            mod = __import__(mod_name, fromlist=[func_name])
            stop = getattr(mod, func_name, None)
            if callable(stop):
                stop()
        except Exception:
            pass
