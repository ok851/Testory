"""AI 优化工具循环：画布网关启用时不应暴露 openclaw_execute。"""
import importlib
import os


def _reload_loop():
    import ai_chat_tool_loop as m

    return importlib.reload(m)


def test_openclaw_blocked_when_embedded_gateway_configured(monkeypatch):
    monkeypatch.setenv("EMBEDDED_BROWSER_GATEWAY_URL", "http://127.0.0.1:8765")
    monkeypatch.setenv("EMBEDDED_BROWSER_GATEWAY_SECRET", "test-secret")
    monkeypatch.delenv("AI_ALLOW_MAIN_PLAYWRIGHT_FALLBACK", raising=False)
    m = _reload_loop()
    assert m.openclaw_execute_allowed() is False
    names = [s["function"]["name"] for s in m.chat_tool_schemas(allow_openclaw=False)]
    assert "openclaw_execute" not in names
    assert "refine_test_plan" in names


def test_openclaw_allowed_when_fallback_enabled(monkeypatch):
    monkeypatch.setenv("EMBEDDED_BROWSER_GATEWAY_URL", "http://127.0.0.1:8765")
    monkeypatch.setenv("EMBEDDED_BROWSER_GATEWAY_SECRET", "secret")
    monkeypatch.setenv("AI_ALLOW_MAIN_PLAYWRIGHT_FALLBACK", "1")
    m = _reload_loop()
    assert m.openclaw_execute_allowed() is True


def test_embedded_playwright_headless_defaults_true(monkeypatch):
    monkeypatch.delenv("EMBEDDED_BROWSER_HEADLESS", raising=False)
    monkeypatch.setenv("PLAYWRIGHT_HEADLESS", "0")
    import embedded_browser_gateway.main as gw

    gw = importlib.reload(gw)
    assert gw._embedded_playwright_headless() is True
