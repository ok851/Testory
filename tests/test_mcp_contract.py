# -*- coding: utf-8 -*-
"""R12：MCP 契约与 Desktop 适配器样例。"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def test_desktop_tool_descriptors_nonempty():
    from testory_mcp.schemas import desktop_tool_descriptors, jsonrpc_tools_list_result

    tools = desktop_tool_descriptors()
    assert len(tools) >= 8
    names = {t["name"] for t in tools}
    assert "windows_focus_app" in names
    assert "get_screen_text" in names
    for t in tools:
        assert "handler" not in t
        assert t.get("name")
    listed = jsonrpc_tools_list_result(tools)
    assert listed["tools"]
    assert listed["tools"][0]["inputSchema"]["type"] == "object"


def test_stdio_envelopes():
    from testory_mcp.schemas import stdio_request, stdio_response_err, stdio_response_ok

    assert stdio_request("windows_wait", {"duration_ms": "100"}) == {
        "tool": "windows_wait",
        "params": {"duration_ms": "100"},
    }
    assert stdio_response_ok({"ok": True})["result"]["ok"] is True
    assert "error" in stdio_response_err("boom")


def test_mcp_adapter_sample_offline(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "goai_mcp_sample",
        _ROOT / "demos" / "goai-mcp-adapter" / "run_sample.py",
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)

    out = mod.run(out_dir=tmp_path / "mcp", do_list=True, do_call=True)
    assert out["ok"] is True
    assert out["tool_count"] >= 8
    assert out["demo_calls"] >= 2
    assert (tmp_path / "mcp" / "tools.json").is_file()
    assert (tmp_path / "mcp" / "demo_calls.json").is_file()
    calls = out["payload"]["demo_calls"]
    assert all(c.get("ok") is False for c in calls)
