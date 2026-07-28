# -*- coding: utf-8 -*-
"""AgentTeams SDK 映射桥（Phase A 加深）：本地 5 角色 ↔ 官方 SDK 可选适配。

当前默认 ``control_plane=local``，不强制安装官方 SDK。
若环境提供 AgentTeams 客户端，可通过 ``adapt_local_run_to_sdk_events`` 导出事件流。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

# 本地角色 → SDK 语义角色名（文档契约，非运行时强依赖）
ROLE_SDK_MAP = {
    "Planner": "planner",
    "RiskAdvisor": "governance",
    "DesktopExecutor": "desktop_executor",
    "WebApiExecutor": "web_api_executor",
    "Verifier": "verifier",
}

LOCAL_ROLES_MIN = (
    "Planner",
    "RiskAdvisor",
    "DesktopExecutor",
    "WebApiExecutor",
    "Verifier",
)


def sdk_available() -> bool:
    """探测官方 AgentTeams SDK 是否可导入（可选）。"""
    try:
        import importlib

        importlib.import_module("agentteams")
        return True
    except Exception:
        try:
            import importlib

            importlib.import_module("agent_teams_sdk")
            return True
        except Exception:
            return False


def adapt_local_run_to_sdk_events(state: Any) -> Dict[str, Any]:
    """将 TestRunState 事件映射为 SDK 风格事件列表（离线可测）。"""
    events_in = list(getattr(state, "events", None) or [])
    out_events: List[Dict[str, Any]] = []
    for ev in events_in:
        if not isinstance(ev, dict):
            continue
        agent = str(ev.get("agent") or "")
        out_events.append({
            "sdk_role": ROLE_SDK_MAP.get(agent, agent.lower() or "system"),
            "local_agent": agent,
            "kind": ev.get("kind"),
            "message": ev.get("message"),
            "at": ev.get("at"),
            "payload": ev.get("payload") or {},
        })
    return {
        "control_plane": "local",
        "sdk_available": sdk_available(),
        "sdk_mapping": dict(ROLE_SDK_MAP),
        "run_id": getattr(state, "run_id", ""),
        "status": getattr(state, "status", ""),
        "events": out_events,
        "note": (
            "官方 AgentTeams SDK 未接入时使用本地控制面；"
            "本结构用于对齐赛期/企业叙事，不得把单 Hermes 对话成功表述为多 Agent 完成。"
        ),
    }


def assert_five_roles_in_spec(spec: Optional[Dict[str, Any]]) -> List[str]:
    """返回 Spec 中缺失的本地角色 id。"""
    roles = [r.get("id") for r in (spec or {}).get("roles") or [] if isinstance(r, dict)]
    missing = [r for r in LOCAL_ROLES_MIN if r not in roles]
    return missing


def export_sdk_events_bundle(
    state: Any,
    *,
    out_dir: Any = None,
) -> Dict[str, Any]:
    """写出 SDK 风格事件包（离线可交付）；不调用官方 SDK 运行时。"""
    import json
    from pathlib import Path

    payload = adapt_local_run_to_sdk_events(state)
    payload["exported_at"] = __import__("time").strftime("%Y-%m-%dT%H:%M:%SZ", __import__("time").gmtime())
    payload["case_pass_claimed"] = False
    paths: Dict[str, str] = {}
    if out_dir is not None:
        root = Path(out_dir)
        root.mkdir(parents=True, exist_ok=True)
        events_path = root / "sdk_events.json"
        summary = root / "SDK_BRIDGE.md"
        events_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        summary.write_text(
            "\n".join(
                [
                    "# AgentTeams SDK Bridge Export",
                    "",
                    f"- run_id: `{payload.get('run_id')}`",
                    f"- status: `{payload.get('status')}`",
                    f"- sdk_available: **{payload.get('sdk_available')}**",
                    f"- events: **{len(payload.get('events') or [])}**",
                    "",
                    "本地控制面导出；官方 SDK 未安装时仍可对齐事件结构。",
                    "**不得**把本导出当作多 Agent 业务用例已通过的证明。",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        paths = {"sdk_events": str(events_path), "summary": str(summary)}
    return {"ok": True, "payload": payload, "paths": paths}
