# -*- coding: utf-8 -*-
"""remote 模式农场调度门禁：跨端 Desktop 执行前检查。

环境变量 ``DESKTOP_FARM_DISPATCH_GATE``：
- ``auto``（默认）：已登记农场节点时要求 ``dispatch_ready``；无节点时仅要求 Gateway URL+SECRET 可解析
- ``1``/``true``：始终要求完整 ``dispatch_ready``（含在线节点）
- ``0``/``false``：跳过本门禁（仍走原有 Gateway health 预检）

诚实：未就绪 → ``FARM_DISPATCH_NOT_READY``，不得假绿。
"""

from __future__ import annotations

import os
from typing import Any, Dict, List


def _truthy_gate(raw: str) -> str:
    v = (raw or "auto").strip().lower()
    if v in ("0", "false", "no", "off"):
        return "off"
    if v in ("1", "true", "yes", "on", "force"):
        return "force"
    return "auto"


def check_farm_dispatch_gate() -> Dict[str, Any]:
    """返回 {ok, error_code?, error?, detail, readiness?, failed_checks?, case_pass_claimed}。"""
    mode = _truthy_gate(os.environ.get("DESKTOP_FARM_DISPATCH_GATE", "auto"))
    out: Dict[str, Any] = {
        "ok": False,
        "gate_mode": mode,
        "detail": "",
        "case_pass_claimed": False,
    }
    if mode == "off":
        out["ok"] = True
        out["skipped"] = True
        out["detail"] = "DESKTOP_FARM_DISPATCH_GATE=0"
        return out

    try:
        from ai_modules.enterprise.execution_farm import dispatch_readiness
        from ai_modules.enterprise.gateway_resolve import resolve_desktop_gateway
    except Exception as e:
        out["error_code"] = "FARM_DISPATCH_NOT_READY"
        out["error"] = f"无法加载农场调度检查: {e}"
        out["detail"] = "import_failed"
        return out

    readiness = dispatch_readiness()
    out["readiness"] = {
        "dispatch_ready": bool(readiness.get("dispatch_ready")),
        "checks": readiness.get("checks") or [],
        "disclaimer": readiness.get("disclaimer"),
    }
    node_count = int((readiness.get("summary") or {}).get("node_count") or 0)
    resolved = resolve_desktop_gateway()

    if mode == "auto" and node_count == 0:
        if not resolved.get("enabled"):
            out["error_code"] = "FARM_DISPATCH_NOT_READY"
            out["error"] = (
                "remote 模式未配置 Desktop Gateway（URL+SECRET）；"
                "可登记农场节点并 probe，或设置 DESKTOP_AGENT_GATEWAY_* / DESKTOP_FARM_GATEWAY=1"
            )
            out["detail"] = "no_farm_nodes_gateway_disabled"
            out["failed_checks"] = ["gateway_enabled"]
            return out
        out["ok"] = True
        out["detail"] = "auto_no_nodes_gateway_enabled"
        return out

    # force，或 auto 且已有农场节点 → 要求完整 dispatch_ready
    if readiness.get("dispatch_ready"):
        out["ok"] = True
        out["detail"] = "dispatch_ready"
        return out

    failed: List[str] = [
        str(c.get("id"))
        for c in (readiness.get("checks") or [])
        if isinstance(c, dict) and not c.get("ok")
    ]
    out["error_code"] = "FARM_DISPATCH_NOT_READY"
    out["error"] = (
        "remote 农场调度未就绪: "
        + (", ".join(failed) if failed else "dispatch_ready=false")
        + "。请探测在线节点、设置 SECRET，或 DESKTOP_FARM_DISPATCH_GATE=0 仅跳过农场门禁（仍须 Gateway 可达）。"
    )
    out["detail"] = "dispatch_not_ready"
    out["failed_checks"] = failed
    return out
