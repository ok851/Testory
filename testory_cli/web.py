# -*- coding: utf-8 -*-
"""Web 画布子命令。"""
from __future__ import annotations

import argparse
import json
import sys
from typing import List

from testory_cli._gateway import gateway_run_steps, gateway_screenshot_png, load_steps_file


def _build_web_parser(sub: argparse._SubParsersAction) -> None:
    web = sub.add_parser("web", help="内置浏览器 / CDP 画布自动化")
    wsub = web.add_subparsers(dest="web_cmd", required=True)

    p_shot = wsub.add_parser("screenshot", help="截取当前视口 PNG")
    p_shot.add_argument("--session-id", required=True)
    p_shot.add_argument("-o", "--output", default="screenshot.png")
    p_shot.set_defaults(handler=_cmd_screenshot)

    p_tap = wsub.add_parser("tap", help="智能点击（自然语言描述元素）")
    p_tap.add_argument("--session-id", required=True)
    p_tap.add_argument("--locate", required=True, help="元素描述，如「登录按钮」")
    p_tap.set_defaults(handler=_cmd_tap)

    p_input = wsub.add_parser("input", help="智能输入")
    p_input.add_argument("--session-id", required=True)
    p_input.add_argument("--locate", required=True)
    p_input.add_argument("--text", required=True)
    p_input.set_defaults(handler=_cmd_input)

    p_run = wsub.add_parser("run-steps", help="串行执行步骤 JSON")
    p_run.add_argument("--session-id", required=True)
    p_run.add_argument("--file", required=True, help="步骤 JSON 文件")
    p_run.set_defaults(handler=_cmd_run_steps)

    p_ready = wsub.add_parser("readiness", help="视觉自动化就绪检查")
    p_ready.add_argument("--session-id", default="")
    p_ready.set_defaults(handler=_cmd_readiness)

    p_assert = wsub.add_parser("assert", help="视觉断言（自然语言条件）")
    p_assert.add_argument("--session-id", required=True)
    p_assert.add_argument("--condition", required=True, help="如「页面显示登录成功」")
    p_assert.set_defaults(handler=_cmd_assert)


def _cmd_screenshot(args: argparse.Namespace) -> int:
    png, err = gateway_screenshot_png(args.session_id)
    if err or not png:
        print(err or "screenshot failed", file=sys.stderr)
        return 1
    with open(args.output, "wb") as f:
        f.write(png)
    print(args.output)
    return 0


def _normalize_step(raw: dict) -> dict:
    from ai_step_normalization import normalize_ai_step

    return normalize_ai_step(raw)


def _cmd_tap(args: argparse.Namespace) -> int:
    step = _normalize_step(
        {
            "action": "ai_tap",
            "description": args.locate,
            "locate_prompt": args.locate,
        }
    )
    return _run_one_step(args.session_id, step)


def _cmd_input(args: argparse.Namespace) -> int:
    step = _normalize_step(
        {
            "action": "ai_input",
            "description": args.locate,
            "locate_prompt": args.locate,
            "input_value": args.text,
        }
    )
    return _run_one_step(args.session_id, step)


def _run_one_step(session_id: str, step: dict) -> int:
    j, err = gateway_run_steps(session_id, [step])
    if err:
        print(err, file=sys.stderr)
        return 1
    rs = (j or {}).get("results") or []
    if rs and not rs[0].get("ok"):
        print(rs[0].get("error") or "step failed", file=sys.stderr)
        return 1
    print(json.dumps({"success": True, "step": step.get("action")}, ensure_ascii=False))
    return 0


def _cmd_run_steps(args: argparse.Namespace) -> int:
    try:
        steps = load_steps_file(args.file)
    except (OSError, ValueError, json.JSONDecodeError) as e:
        print(str(e), file=sys.stderr)
        return 1
    j, err = gateway_run_steps(args.session_id, steps)
    if err:
        print(err, file=sys.stderr)
        return 1
    print(json.dumps(j, ensure_ascii=False, indent=2))
    rs = (j or {}).get("results") or []
    if any(not r.get("ok") for r in rs):
        return 1
    return 0


def _cmd_readiness(args: argparse.Namespace) -> int:
    from vision_platform_readiness import check_vision_automation_readiness

    out = check_vision_automation_readiness(embedded_session_id=(args.session_id or "").strip())
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out.get("ready") else 2


def _cmd_assert(args: argparse.Namespace) -> int:
    from vision_action_port import WebVisionActionPort

    port = WebVisionActionPort(args.session_id)
    result = port.assert_vision(args.condition)
    print(json.dumps({"ok": result.ok, "message": result.message}, ensure_ascii=False))
    return 0 if result.ok else 1
