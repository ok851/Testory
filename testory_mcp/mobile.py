# -*- coding: utf-8 -*-
"""Mobile MCP 入口（Phase 4c）。"""
from __future__ import annotations

import os
import sys

from testory_mcp.web import _run_stdio_json


def main():
    os.environ.setdefault("TESTORY_MCP_PLATFORM", "android")
    from modules.ai.vision_action_port import MobileVisionActionPort

    udid = (os.environ.get("TESTORY_MCP_UDID") or "").strip()
    if not udid:
        from modules.mobile.mobile_device_manager import get_connected_udid

        udid = (get_connected_udid() or "").strip()
    if not udid:
        raise SystemExit("请设置 TESTORY_MCP_UDID 或先连接设备")
    port = MobileVisionActionPort(udid)
    import json
    from testory_mcp.kit import mcp_kit_for_port

    _, tools = mcp_kit_for_port(port)
    by_name = {t["name"]: t for t in tools}
    sys.stderr.write(f"testory-mobile-mcp stdio ready udid={udid}\n")
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


if __name__ == "__main__":
    main()
