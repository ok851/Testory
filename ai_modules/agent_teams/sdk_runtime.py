# -*- coding: utf-8 -*-
"""官方 AgentTeams SDK 运行时探测（诚实失败）。

未安装 SDK 时返回 ``SDK_NOT_INSTALLED``，可回退本地 bridge 导出；
不得把「探测完成」表述为多 Agent 业务用例已通过。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from .sdk_bridge import adapt_local_run_to_sdk_events, export_sdk_events_bundle, sdk_available


def try_official_sdk_runtime(
    state: Any = None,
    *,
    events_payload: Optional[Dict[str, Any]] = None,
    fallback_export_dir: Any = None,
) -> Dict[str, Any]:
    """尝试接入官方 SDK；不可用时明确失败并可选写出本地 bridge 包。"""
    payload = events_payload
    if payload is None and state is not None:
        payload = adapt_local_run_to_sdk_events(state)

    available = sdk_available()
    if not available:
        fallback = None
        if state is not None and fallback_export_dir is not None:
            fallback = export_sdk_events_bundle(state, out_dir=fallback_export_dir)
        return {
            "ok": False,
            "error_code": "SDK_NOT_INSTALLED",
            "error": (
                "未检测到官方 AgentTeams SDK（agentteams / agent_teams_sdk）；"
                "当前使用本地控制面。安装官方包前不得宣称 SDK 运行时已接入。"
            ),
            "sdk_available": False,
            "used_local_bridge": True,
            "local_events": payload,
            "fallback_export": fallback,
            "case_pass_claimed": False,
            "multi_agent_sdk_runtime_claimed": False,
        }

    # 已安装：尝试常见入口；无稳定 API 时仍诚实失败，避免空成功
    client = None
    err = None
    for mod_name, attr in (
        ("agentteams", "AgentTeamsClient"),
        ("agentteams", "Client"),
        ("agent_teams_sdk", "Client"),
        ("agent_teams_sdk", "AgentTeams"),
    ):
        try:
            import importlib

            mod = importlib.import_module(mod_name)
            client = getattr(mod, attr, None)
            if client is not None:
                break
        except Exception as e:
            err = str(e)[:200]

    if client is None:
        return {
            "ok": False,
            "error_code": "SDK_API_UNSUPPORTED",
            "error": err or "官方包已安装但未找到可调用 Client API",
            "sdk_available": True,
            "used_local_bridge": True,
            "local_events": payload,
            "case_pass_claimed": False,
            "multi_agent_sdk_runtime_claimed": False,
        }

    # 不在无契约文档时盲调 run；仅报告「可导入」
    return {
        "ok": True,
        "sdk_available": True,
        "client_symbol": getattr(client, "__name__", str(client)),
        "note": "官方 Client 符号可导入；完整 runtime 仍需按厂商契约接线，本探针不执行业务用例。",
        "local_events": payload,
        "case_pass_claimed": False,
        "multi_agent_sdk_runtime_claimed": False,
        "disclaimer": "Client 可导入 ≠ 多 Agent 业务用例已在官方 runtime 跑通",
    }
