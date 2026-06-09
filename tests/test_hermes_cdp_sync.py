"""Hermes CDP 同步与 hermes_execute 条件启用。"""
import importlib
import os
from pathlib import Path


def _reload_loop():
    import ai_chat_tool_loop as m

    return importlib.reload(m)


def test_sync_hermes_cdp_endpoint_writes_env(tmp_path, monkeypatch):
    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("HERMES_CDP_ENDPOINT", raising=False)
    from hermes_config import (
        clear_hermes_cdp_endpoint,
        hermes_cdp_attached,
        hermes_cdp_endpoint_active,
        sync_hermes_cdp_endpoint,
    )

    ws = "ws://127.0.0.1:9222/devtools/browser/abc"
    assert sync_hermes_cdp_endpoint(ws, restart_gateway=False) is True
    assert hermes_cdp_attached()
    assert hermes_cdp_endpoint_active() == ws
    env_path = Path(tmp_path) / "hermes" / ".env"
    assert env_path.is_file()
    assert f"HERMES_CDP_ENDPOINT={ws}" in env_path.read_text(encoding="utf-8")
    clear_hermes_cdp_endpoint(restart_gateway=False)
    assert not hermes_cdp_attached()


def test_hermes_blocked_when_embedded_without_cdp(monkeypatch):
    monkeypatch.setenv("EMBEDDED_BROWSER_GATEWAY_URL", "http://127.0.0.1:8765")
    monkeypatch.setenv("EMBEDDED_BROWSER_GATEWAY_SECRET", "test-secret")
    monkeypatch.delenv("AI_ALLOW_MAIN_PLAYWRIGHT_FALLBACK", raising=False)
    monkeypatch.delenv("HERMES_CDP_ENDPOINT", raising=False)
    import hermes_config as hc

    importlib.reload(hc)
    m = _reload_loop()
    assert m.hermes_execute_allowed() is False
    names = [s["function"]["name"] for s in m.chat_tool_schemas(allow_hermes=False)]
    assert "hermes_execute" not in names
    assert "refine_test_plan" in names


def test_hermes_allowed_when_cdp_attached(monkeypatch):
    monkeypatch.setenv("EMBEDDED_BROWSER_GATEWAY_URL", "http://127.0.0.1:8765")
    monkeypatch.setenv("EMBEDDED_BROWSER_GATEWAY_SECRET", "test-secret")
    monkeypatch.setenv("HERMES_CDP_ENDPOINT", "ws://127.0.0.1:9222/devtools/browser/x")
    monkeypatch.delenv("AI_ALLOW_MAIN_PLAYWRIGHT_FALLBACK", raising=False)
    import hermes_config as hc

    importlib.reload(hc)
    m = _reload_loop()
    assert m.hermes_execute_allowed() is True
    names = [s["function"]["name"] for s in m.chat_tool_schemas(allow_hermes=True)]
    assert "hermes_execute" in names


def test_skill_loop_auto_export_threshold(tmp_path, monkeypatch):
    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HERMES_SKILL_AUTO_EXPORT_AFTER", "2")
    monkeypatch.setenv("HERMES_SKILL_CURATOR_ENABLE", "0")
    from hermes_skill_loop import record_execution_success

    plan = {
        "case_name": "登录",
        "case_url": "https://example.com/login",
        "steps": [{"action": "navigate", "url": "https://example.com/login"}],
    }
    r1 = record_execution_success(plan, case_url=plan["case_url"])
    assert r1["success_count"] == 1
    assert not r1["auto_exported"]
    r2 = record_execution_success(plan, case_url=plan["case_url"])
    assert r2["success_count"] == 2
    assert r2["auto_exported"]
    assert r2["skill"]


def test_llm_readiness_without_ollama(monkeypatch):
    monkeypatch.setenv("UAT_DATA_DIR", str(Path(os.environ.get("TEMP", "/tmp")) / "testory_llm"))
    from ai_llm_readiness import assess_llm_readiness

    out = assess_llm_readiness(local_ai_service=None)
    assert "ready" in out
    assert "mode" in out
    assert "recommendation" in out
