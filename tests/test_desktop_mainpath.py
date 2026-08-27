# -*- coding: utf-8 -*-
"""Desktop 主路径：预检诚实失败 + 记事本标准计划。"""

from __future__ import annotations

from unittest.mock import patch

from ai_modules.execute.desktop_preflight import (
    build_notepad_mainpath_plan,
    check_desktop_preflight,
)
from ai_modules.execute.orchestrator import _execute_ui_stage
from ai_modules.plan.context_bus import CrossEndContext


def _ctx():
    return CrossEndContext(plan_id="desk-mp", scenario="mainpath")


def test_notepad_mainpath_plan_has_desktop_stages():
    plan = build_notepad_mainpath_plan(project_id=9)
    assert plan["project_id"] == 9
    assert plan["meta"]["template"] == "desktop_notepad_mainpath"
    assert len(plan["stages"]) == 1
    assert plan["stages"][0]["layer"] == "desktop"
    actions = [st["action"] for s in plan["stages"] for st in s["steps"]]
    assert actions == ["launch_app", "wait", "attach_window", "input", "verify"]
    attach = plan["stages"][0]["steps"][2]
    assert "Notepad" in (attach.get("desktop_spec") or {}).get("window_title_re", "")


def test_preflight_skip_env(monkeypatch):
    monkeypatch.setenv("DESKTOP_PREFLIGHT", "0")
    pre = check_desktop_preflight()
    assert pre["ok"] is True
    assert pre["mode"] == "skipped"


def test_preflight_non_windows_inprocess(monkeypatch):
    monkeypatch.delenv("DESKTOP_PREFLIGHT", raising=False)
    monkeypatch.setenv("DESKTOP_EXECUTION_MODE", "inprocess")
    with patch("ai_modules.execute.desktop_preflight.sys.platform", "linux"):
        with patch(
            "modules.desktop.desktop_env_config.desktop_execution_mode",
            return_value="inprocess",
        ):
            pre = check_desktop_preflight()
    assert pre["ok"] is False
    assert pre["error_code"] == "DESKTOP_NO_SESSION"


def test_preflight_gateway_unreachable(monkeypatch):
    monkeypatch.delenv("DESKTOP_PREFLIGHT", raising=False)
    monkeypatch.setenv("DESKTOP_EXECUTION_MODE", "gateway")
    monkeypatch.setenv("DESKTOP_AGENT_GATEWAY_URL", "http://127.0.0.1:59999")
    monkeypatch.setenv("DESKTOP_AGENT_GATEWAY_SECRET", "test-secret")

    def _fail(*_a, **_k):
        return None, "connection refused"

    with patch("modules.desktop.desktop_env_config.desktop_execution_mode", return_value="gateway"):
        with patch("modules.desktop.desktop_agent_client.desktop_agent_enabled", return_value=True):
            with patch("modules.desktop.desktop_agent_client.desktop_agent_json", _fail):
                pre = check_desktop_preflight()
    assert pre["ok"] is False
    assert pre["error_code"] == "DESKTOP_NO_SESSION"


def test_orchestrator_desktop_stage_fails_preflight():
    def _bad(**_k):
        return {
            "ok": False,
            "mode": "gateway",
            "detail": "down",
            "error_code": "DESKTOP_NO_SESSION",
            "error": "Gateway down",
        }

    stage = {
        "id": "d1",
        "layer": "desktop",
        "steps": [{"action": "launch_app", "input_value": "notepad.exe"}],
    }
    with patch(
        "ai_modules.execute.desktop_preflight.check_desktop_preflight",
        _bad,
    ):
        result, _extracted = _execute_ui_stage(stage, _ctx())
    assert result["ok_assert"] is False
    assert result["error_code"] == "DESKTOP_NO_SESSION"
    assert result.get("desktop_preflight", {}).get("ok") is False


def test_orchestrator_desktop_runs_when_preflight_ok():
    """预检通过后才真正调 sync_desktop_execute_step。"""
    calls = []

    def _ok_pre(**_k):
        return {"ok": True, "mode": "inprocess", "detail": "test"}

    def _step(step):
        calls.append(step.get("action"))
        return {"status": "success", "action": step.get("action")}

    stage = {
        "id": "d2",
        "layer": "desktop",
        "steps": [
            {"action": "launch_app", "input_value": "notepad.exe"},
            {"action": "wait", "input_value": "0.1"},
        ],
    }
    with patch(
        "ai_modules.execute.desktop_preflight.check_desktop_preflight",
        _ok_pre,
    ):
        with patch("modules.desktop.desktop_automation.sync_desktop_execute_step", _step):
            with patch(
                "modules.execution.step_executor.validate_desktop_step_result",
                lambda *_a, **_k: None,
            ):
                result, _ = _execute_ui_stage(stage, _ctx())
    assert result["ok_assert"] is True
    assert calls == ["launch_app", "wait"]
