# -*- coding: utf-8 -*-
"""Desktop 主路径预检：环境不可用时诚实失败（DESKTOP_NO_SESSION），禁止假绿。"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, Optional


def check_desktop_preflight(*, timeout_sec: float = 1.5) -> Dict[str, Any]:
    """返回 {ok, mode, detail, error_code?, error?}。

    - DESKTOP_PREFLIGHT=0：跳过（仅供单测 mock 步骤路径）
    - gateway / remote：须配置 URL+密钥且 /health（或等价）可达
    - inprocess：须 Windows；非 Windows 一律 DESKTOP_NO_SESSION
    """
    out: Dict[str, Any] = {
        "ok": False,
        "mode": "unknown",
        "detail": "",
        "platform": sys.platform,
    }
    if os.environ.get("DESKTOP_PREFLIGHT", "1").strip().lower() in (
        "0",
        "false",
        "no",
        "off",
    ):
        out["ok"] = True
        out["mode"] = "skipped"
        out["detail"] = "DESKTOP_PREFLIGHT=0"
        return out

    try:
        from desktop_env_config import desktop_execution_mode

        mode = (desktop_execution_mode() or "inprocess").strip().lower()
    except Exception:
        mode = (os.environ.get("DESKTOP_EXECUTION_MODE") or "inprocess").strip().lower()
    out["mode"] = mode

    if mode in ("gateway", "remote"):
        try:
            from desktop_agent_client import desktop_agent_enabled, desktop_agent_json

            if not desktop_agent_enabled():
                out["error_code"] = "DESKTOP_NO_SESSION"
                out["error"] = (
                    "Desktop Gateway 未配置（需 DESKTOP_AGENT_GATEWAY_URL 与 "
                    "DESKTOP_AGENT_GATEWAY_SECRET）"
                )
                out["detail"] = "gateway_not_configured"
                return out
            payload, err = desktop_agent_json("GET", "/health", timeout_sec=timeout_sec)
            if err and payload is None:
                out["error_code"] = "DESKTOP_NO_SESSION"
                out["error"] = f"Desktop Gateway 不可达: {err}"
                out["detail"] = f"gateway_unreachable:{err[:80]}"
                return out

            # remote：额外农场调度门禁（默认 auto）
            if mode == "remote":
                try:
                    from ai_modules.execute.farm_dispatch_gate import check_farm_dispatch_gate

                    farm_gate = check_farm_dispatch_gate()
                    out["farm_dispatch"] = {
                        "ok": bool(farm_gate.get("ok")),
                        "gate_mode": farm_gate.get("gate_mode"),
                        "detail": farm_gate.get("detail"),
                        "failed_checks": farm_gate.get("failed_checks"),
                        "skipped": bool(farm_gate.get("skipped")),
                    }
                    if not farm_gate.get("ok"):
                        out["ok"] = False
                        out["error_code"] = farm_gate.get("error_code") or "FARM_DISPATCH_NOT_READY"
                        out["error"] = farm_gate.get("error") or "农场调度未就绪"
                        out["detail"] = f"farm_gate:{farm_gate.get('detail') or 'fail'}"
                        return out
                except Exception as farm_exc:
                    out["ok"] = False
                    out["error_code"] = "FARM_DISPATCH_NOT_READY"
                    out["error"] = f"农场调度门禁异常: {farm_exc}"
                    out["detail"] = "farm_gate_exception"
                    out["farm_dispatch"] = {"ok": False, "error": str(farm_exc)[:120]}
                    return out

            out["ok"] = True
            out["detail"] = "gateway_ok" if not err else f"gateway_probe:{err[:60]}"
            return out
        except Exception as exc:
            out["error_code"] = "DESKTOP_NO_SESSION"
            out["error"] = f"Desktop Gateway 预检异常: {exc}"
            out["detail"] = str(exc)[:120]
            return out

    # inprocess / 默认
    if sys.platform != "win32":
        out["error_code"] = "DESKTOP_NO_SESSION"
        out["error"] = "桌面自动化仅支持 Windows（当前非 win32，不得假绿）"
        out["detail"] = f"platform={sys.platform}"
        return out

    out["ok"] = True
    out["detail"] = "inprocess_win32"
    try:
        from desktop_runtime import desktop_runtime_available

        if desktop_runtime_available():
            out["detail"] = "inprocess_win32+vision"
        else:
            out["detail"] = "inprocess_win32_uia_only"
    except Exception:
        pass
    return out


def build_notepad_mainpath_plan(
    *,
    project_id: Optional[Any] = None,
    plan_id: str = "desktop-notepad-mainpath",
) -> Dict[str, Any]:
    """企业可演示的 Windows 桌面主路径：启动记事本 → 等待 → 附着 → 输入 → 校验窗口。

    真机执行依赖预检通过；离线 CI 应期望 DESKTOP_NO_SESSION 或 mock 步骤。
    Win11 标题常见为 Notepad；正则同时覆盖中英文。
    """
    notepad_re = r"(?i).*(Notepad|记事本).*"
    stages = [
        {
            "id": "stage-desktop-notepad",
            "layer": "desktop",
            "name": "记事本主路径",
            "steps": [
                {
                    "action": "launch_app",
                    "automation_layer": "desktop",
                    "input_value": "notepad.exe",
                    "description": "启动 Windows 记事本",
                },
                {
                    "action": "wait",
                    "automation_layer": "desktop",
                    "input_value": "1.5",
                    "description": "等待窗口出现",
                },
                {
                    "action": "attach_window",
                    "automation_layer": "desktop",
                    "desktop_spec": {"window_title_re": notepad_re},
                    "description": "附着 Notepad / 记事本窗口",
                },
                {
                    "action": "input",
                    "automation_layer": "desktop",
                    "input_value": "Testory desktop mainpath OK",
                    "description": "输入主路径探针文本",
                },
                {
                    "action": "verify",
                    "automation_layer": "desktop",
                    "selector_type": "window",
                    "desktop_spec": {"window_title_re": notepad_re},
                    "selector_value": "Notepad",
                    "description": "校验记事本窗口仍在",
                },
            ],
        },
    ]
    plan: Dict[str, Any] = {
        "plan_id": plan_id,
        "scenario": "Windows 桌面主路径（记事本 launch→attach→input→verify）",
        "stages": stages,
        "meta": {
            "template": "desktop_notepad_mainpath",
            "honesty": "warning/未预检不得绿；Gateway 不可达 → DESKTOP_NO_SESSION",
        },
    }
    if project_id is not None and str(project_id).strip() != "":
        try:
            plan["project_id"] = int(project_id)
        except (TypeError, ValueError):
            plan["project_id"] = project_id
    return plan
