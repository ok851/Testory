# -*- coding: utf-8 -*-
"""R12：Desktop MCP 适配器离线样例。

用法（仓库根）:
  python demos/goai-mcp-adapter/run_sample.py --list
  python demos/goai-mcp-adapter/run_sample.py --demo-call
  python demos/goai-mcp-adapter/run_sample.py --out artifacts/goai-mcp-adapter
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


class FakeDesktopPort:
    """无真机：capture/tap 一律诚实失败。"""

    platform = "desktop"

    def capture(self) -> Any:
        raise RuntimeError("simulate: no desktop session (FakeDesktopPort)")

    def tap(self, locate: str) -> Any:
        from types import SimpleNamespace

        return SimpleNamespace(ok=False, error=f"simulate: cannot tap {locate!r} without desktop")

    def input_text(self, locate: str, text: str) -> Any:
        from types import SimpleNamespace

        return SimpleNamespace(ok=False, error="simulate: no desktop session")

    def assert_vision(self, condition: str) -> Any:
        from types import SimpleNamespace

        return SimpleNamespace(ok=False, error="simulate: assert skipped — no desktop")


def list_tools() -> List[Dict[str, Any]]:
    from testory_mcp.schemas import desktop_tool_descriptors

    return desktop_tool_descriptors(include_vision_stubs=True)


def demo_calls() -> List[Dict[str, Any]]:
    """演示 stdio 信封 + 假端口调用（未知工具 / 无环境）。"""
    from testory_mcp.kit import mcp_kit_for_port
    from testory_mcp.schemas import stdio_request, stdio_response_err, stdio_response_ok

    rows: List[Dict[str, Any]] = []
    # 1) 未知工具
    req = stdio_request("not_a_real_tool", {})
    rows.append({
        "request": req,
        "response": stdio_response_err("unknown tool not_a_real_tool"),
        "ok": False,
        "case": "unknown_tool",
    })

    # 2) 真工具表 + 假端口：screenshot 应失败
    _desc, tools = mcp_kit_for_port(FakeDesktopPort())
    by_name = {t["name"]: t for t in tools}
    name = "desktop_screenshot"
    t = by_name.get(name)
    req2 = stdio_request(name, {})
    try:
        if not t:
            raise RuntimeError(f"tool missing: {name}")
        result = t["handler"]()
        # handler 可能抛错或返回
        resp = stdio_response_ok(result)
        ok = True
        if isinstance(result, dict) and result.get("ok") is False:
            ok = False
    except Exception as exc:
        resp = stdio_response_err(str(exc))
        ok = False
    rows.append({
        "request": req2,
        "response": resp,
        "ok": ok,
        "case": "no_desktop_session",
    })

    # 诚实：无桌面不得 ok=true
    if any(r.get("case") == "no_desktop_session" and r.get("ok") for r in rows):
        raise SystemExit("honesty violation: FakeDesktopPort call reported ok")

    return rows


def run(*, out_dir: Path | None = None, do_list: bool = True, do_call: bool = True) -> Dict[str, Any]:
    tools = list_tools() if do_list else []
    calls = demo_calls() if do_call else []
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "contract": "docs/goai/MCP_CONTRACT.md",
        "tool_count": len(tools),
        "tools": tools,
        "demo_calls": calls,
        "honesty": {
            "unknown_tool_ok": False,
            "no_desktop_ok": False,
        },
    }
    paths: Dict[str, str] = {}
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
        tools_path = out_dir / "tools.json"
        calls_path = out_dir / "demo_calls.json"
        summary = out_dir / "SUMMARY.md"
        tools_path.write_text(json.dumps(tools, ensure_ascii=False, indent=2), encoding="utf-8")
        calls_path.write_text(json.dumps(calls, ensure_ascii=False, indent=2), encoding="utf-8")
        summary.write_text(
            "\n".join(
                [
                    "# GOAI MCP Adapter Sample",
                    "",
                    f"- tools: **{len(tools)}**",
                    f"- demo_calls: **{len(calls)}**",
                    "- honesty: unknown tool / no desktop → not ok",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        paths = {
            "tools": str(tools_path),
            "demo_calls": str(calls_path),
            "summary": str(summary),
        }
    return {"ok": True, "tool_count": len(tools), "demo_calls": len(calls), "paths": paths, "payload": payload}


def main(argv: List[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="GOAI Desktop MCP adapter sample")
    p.add_argument("--list", action="store_true", help="打印工具 Schema")
    p.add_argument("--demo-call", action="store_true", help="假端口调用演示")
    p.add_argument("--out", type=Path, default=None)
    args = p.parse_args(argv)

    do_list = args.list or (not args.list and not args.demo_call) or args.out is not None
    do_call = args.demo_call or args.out is not None or (not args.list and not args.demo_call)
    # 默认两者都做
    if not args.list and not args.demo_call:
        do_list = do_call = True

    out = args.out
    if out is None and (args.list or args.demo_call):
        out = None
    if args.out is None and not args.list and not args.demo_call:
        out = _ROOT / "artifacts" / "goai-mcp-adapter"

    result = run(out_dir=out, do_list=do_list, do_call=do_call)
    printable = {
        "ok": result["ok"],
        "tool_count": result["tool_count"],
        "demo_calls": result["demo_calls"],
        "paths": result["paths"],
    }
    if args.list and not args.out:
        printable["tools"] = result["payload"]["tools"]
    if args.demo_call and not args.out:
        printable["calls"] = result["payload"]["demo_calls"]
    print(json.dumps(printable, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
