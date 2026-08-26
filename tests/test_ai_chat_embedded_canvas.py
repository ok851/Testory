"""AI 优化工具循环：画布运行时 CDP 未 attach 时不应暴露 hermes_execute。"""
import importlib
import os


def _reload_loop():
    from modules.ai import ai_chat_tool_loop as m

    return importlib.reload(m)


def test_hermes_blocked_when_embedded_gateway_configured(monkeypatch):
    monkeypatch.setenv("EMBEDDED_BROWSER_GATEWAY_URL", "http://127.0.0.1:8765")
    monkeypatch.setenv("EMBEDDED_BROWSER_GATEWAY_SECRET", "test-secret")
    monkeypatch.delenv("AI_ALLOW_MAIN_PLAYWRIGHT_FALLBACK", raising=False)
    monkeypatch.delenv("HERMES_CDP_ENDPOINT", raising=False)
    from modules.hermes import hermes_config as hc

    importlib.reload(hc)
    m = _reload_loop()
    assert m.hermes_execute_allowed() is False
    assert m.openclaw_execute_allowed() is False
    names = [s["function"]["name"] for s in m.chat_tool_schemas(allow_hermes=False)]
    assert "hermes_execute" not in names
    assert "refine_test_plan" in names


def test_hermes_allowed_when_fallback_enabled(monkeypatch):
    monkeypatch.setenv("EMBEDDED_BROWSER_GATEWAY_URL", "http://127.0.0.1:8765")
    monkeypatch.setenv("EMBEDDED_BROWSER_GATEWAY_SECRET", "secret")
    monkeypatch.setenv("AI_ALLOW_MAIN_PLAYWRIGHT_FALLBACK", "1")
    m = _reload_loop()
    assert m.hermes_execute_allowed() is True


def test_hermes_allowed_for_desktop_when_agent_configured(monkeypatch):
    monkeypatch.setenv("EMBEDDED_BROWSER_GATEWAY_URL", "http://127.0.0.1:8765")
    monkeypatch.setenv("EMBEDDED_BROWSER_GATEWAY_SECRET", "test-secret")
    monkeypatch.delenv("AI_ALLOW_MAIN_PLAYWRIGHT_FALLBACK", raising=False)
    monkeypatch.setenv("HERMES_GATEWAY_URL", "http://127.0.0.1:8642")
    monkeypatch.setenv("HERMES_API_SERVER_KEY", "k")
    m = _reload_loop()
    assert m.hermes_execute_allowed(platform_type="desktop") is True
    assert m.hermes_execute_allowed(platform_type="web") is False


def test_embedded_playwright_headless_defaults_true(monkeypatch):
    monkeypatch.delenv("EMBEDDED_BROWSER_HEADLESS", raising=False)
    monkeypatch.setenv("PLAYWRIGHT_HEADLESS", "0")
    import browser_runtime.main as gw

    gw = importlib.reload(gw)
    assert gw._embedded_playwright_headless() is True


def test_hermes_gateway_client_not_configured_without_key(monkeypatch):
    monkeypatch.delenv("HERMES_API_SERVER_KEY", raising=False)
    monkeypatch.setenv("HERMES_GATEWAY_URL", "http://127.0.0.1:8642")
    from modules.hermes.hermes_gateway_client import HermesGatewayClient

    assert HermesGatewayClient().is_configured() is False


def test_hermes_skills_export_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path))
    from modules.hermes.ai_hermes_skills import apply_skill_to_plan, export_plan_to_skill, list_skills

    plan = {
        "case_name": "登录流程",
        "case_url": "https://example.com/login",
        "steps": [{"action": "navigate", "url": "https://example.com/login"}],
    }
    export_plan_to_skill(plan, skill_name="登录流程", module_hint="login-flow")
    skills = list_skills()
    assert len(skills) == 1
    merged, warnings = apply_skill_to_plan(skills[0]["id"], base_plan={})
    assert len(merged.get("steps") or []) == 1
    assert warnings
