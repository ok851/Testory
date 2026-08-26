# -*- coding: utf-8 -*-
"""Desktop MCP 入口（Phase 4c）：含 windows_* / get_screen_*。"""
from __future__ import annotations

import json
import sys

from testory_mcp.kit import mcp_kit_for_port
from modules.ai.vision_action_port import DesktopVisionActionPort


def main():
    port = DesktopVisionActionPort()
    _, tools = mcp_kit_for_port(port)
    by_name = {t["name"]: t for t in tools}
    sys.stderr.write("testory-desktop-mcp stdio ready\n")
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
            # 按参数名调用
            if name.endswith("_tap"):
                out = fn(params.get("locate") or "")
            elif name.endswith("_input"):
                out = fn(params.get("locate") or "", params.get("text") or "")
            elif name.endswith("_assert"):
                out = fn(params.get("condition") or "")
            elif name.endswith("_query"):
                out = fn(params.get("prompt") or "")
            elif name.endswith("_run_steps"):
                out = fn(params.get("steps") or [])
            else:
                # windows_* / get_screen_* / screenshot
                try:
                    out = fn(**{k: v for k, v in params.items() if v is not None})
                except TypeError:
                    out = fn()
            sys.stdout.write(json.dumps({"result": out}, ensure_ascii=False) + "\n")
            sys.stdout.flush()
        except Exception as e:
            sys.stdout.write(json.dumps({"error": str(e)}) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
