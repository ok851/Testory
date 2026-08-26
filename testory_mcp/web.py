# -*- coding: utf-8 -*-
"""
Testory Web MCP（Phase 4c）。

环境：
  TESTORY_MCP_SESSION_ID   内置画布 session_id（必填）
  EMBEDDED_BROWSER_GATEWAY_URL / SECRET  与主站一致

启动：python -m testory_mcp.web
"""
from __future__ import annotations

import json
import os
import sys


def _port():
    from modules.ai.vision_action_port import WebVisionActionPort

    sid = (os.environ.get("TESTORY_MCP_SESSION_ID") or "").strip()
    if not sid:
        raise SystemExit("请设置 TESTORY_MCP_SESSION_ID")
    return WebVisionActionPort(sid)


def _run_stdio_json():
    """无 mcp 包时的极简 stdio 工具服务（每行一个 JSON 请求）。"""
    port = _port()
    _, tools = __import__("testory_mcp.kit", fromlist=["mcp_kit_for_port"]).mcp_kit_for_port(port)
    by_name = {t["name"]: t for t in tools}
    sys.stderr.write("testory-web-mcp stdio ready\n")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            name = req.get("tool") or req.get("name")
            params = req.get("params") or {}
            t = by_name.get(name)
            if not t:
                sys.stdout.write(json.dumps({"error": f"unknown tool {name}"}) + "\n")
                continue
            fn = t["handler"]
            if name.endswith("_tap"):
                out = fn(params.get("locate") or params.get("description") or "")
            elif name.endswith("_input"):
                out = fn(params.get("locate") or "", params.get("text") or "")
            elif name.endswith("_assert"):
                out = fn(params.get("condition") or params.get("description") or "")
            elif name.endswith("_query"):
                out = fn(params.get("prompt") or params.get("question") or "")
            elif name.endswith("_run_steps"):
                out = fn(params.get("steps") or [])
            else:
                out = fn()
            sys.stdout.write(json.dumps({"result": out}, ensure_ascii=False) + "\n")
            sys.stdout.flush()
        except Exception as e:
            sys.stdout.write(json.dumps({"error": str(e)}) + "\n")
            sys.stdout.flush()


def main():
    try:
        import mcp  # noqa: F401

        sys.stderr.write(
            "检测到 mcp 包：完整 MCP 协议服务待与 Cursor 配置联调；当前使用 stdio JSON 模式。\n"
        )
    except ImportError:
        pass
    _run_stdio_json()


if __name__ == "__main__":
    main()
