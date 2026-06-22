# -*- coding: utf-8 -*-
"""Android 设备子命令（内部 / Hermes / CI）。"""
from __future__ import annotations

import argparse
import json
import sys
from typing import List

from testory_cli._gateway import load_steps_file


def _build_mobile_parser(sub: argparse._SubParsersAction) -> None:
    mob = sub.add_parser("mobile", help="Android 设备视觉自动化（udid）")
    msub = mob.add_subparsers(dest="mobile_cmd", required=True)

    def _udid_arg(p: argparse.ArgumentParser) -> None:
        p.add_argument("--udid", default="", help="设备 udid（默认当前已连接设备）")

    p_shot = msub.add_parser("screenshot", help="截取设备画面 PNG")
    _udid_arg(p_shot)
    p_shot.add_argument("-o", "--output", default="mobile_screenshot.png")
    p_shot.set_defaults(handler=_cmd_screenshot)

    p_tap = msub.add_parser("tap", help="智能点击（自然语言）")
    _udid_arg(p_tap)
    p_tap.add_argument("--locate", required=True)
    p_tap.set_defaults(handler=_cmd_tap)

    p_input = msub.add_parser("input", help="智能输入")
    _udid_arg(p_input)
    p_input.add_argument("--locate", required=True)
    p_input.add_argument("--text", required=True)
    p_input.set_defaults(handler=_cmd_input)

    p_assert = msub.add_parser("assert", help="画面断言")
    _udid_arg(p_assert)
    p_assert.add_argument("--condition", required=True)
    p_assert.set_defaults(handler=_cmd_assert)

    p_query = msub.add_parser("query", help="从画面读取信息")
    _udid_arg(p_query)
    p_query.add_argument("--prompt", required=True)
    p_query.set_defaults(handler=_cmd_query)

    p_act = msub.add_parser("act", help="多步自动规划执行")
    _udid_arg(p_act)
    p_act.add_argument("--goal", required=True)
    p_act.set_defaults(handler=_cmd_act)

    p_run = msub.add_parser("run-steps", help="串行执行步骤 JSON")
    _udid_arg(p_run)
    p_run.add_argument("--file", required=True)
    p_run.set_defaults(handler=_cmd_run_steps)

    p_ready = msub.add_parser("readiness", help="视觉自动化就绪检查")
    _udid_arg(p_ready)
    p_ready.set_defaults(handler=_cmd_readiness)


def _resolve_udid(udid: str) -> str:
    u = (udid or "").strip()
    if u:
        return u
    from mobile_device_manager import get_connected_udid

    return (get_connected_udid() or "").strip()


def _port(udid: str):
    from vision_action_port import MobileVisionActionPort

    u = _resolve_udid(udid)
    if not u:
        raise SystemExit("未指定 udid 且无已连接设备")
    return MobileVisionActionPort(u), u


def _normalize_step(raw: dict) -> dict:
    from ai_step_normalization import normalize_ai_step

    raw = dict(raw)
    raw.setdefault("automation_layer", "android")
    return normalize_ai_step(raw)


def _cmd_screenshot(args: argparse.Namespace) -> int:
    port, _ = _port(args.udid)
    try:
        frame = port.capture()
    except Exception as e:
        print(str(e), file=sys.stderr)
        return 1
    with open(args.output, "wb") as f:
        f.write(frame.png_bytes)
    print(args.output)
    return 0


def _cmd_tap(args: argparse.Namespace) -> int:
    port, udid = _port(args.udid)
    result = port.tap(args.locate)
    print(json.dumps({"ok": result.ok, "message": result.message, "udid": udid}, ensure_ascii=False))
    return 0 if result.ok else 1


def _cmd_input(args: argparse.Namespace) -> int:
    port, udid = _port(args.udid)
    result = port.input_text(args.locate, args.text)
    print(json.dumps({"ok": result.ok, "message": result.message, "udid": udid}, ensure_ascii=False))
    return 0 if result.ok else 1


def _cmd_assert(args: argparse.Namespace) -> int:
    port, udid = _port(args.udid)
    result = port.assert_vision(args.condition)
    print(json.dumps({"ok": result.ok, "message": result.message, "udid": udid}, ensure_ascii=False))
    return 0 if result.ok else 1


def _cmd_query(args: argparse.Namespace) -> int:
    port, udid = _port(args.udid)
    text, err = port.query(args.prompt)
    if err or not text:
        print(err or "query failed", file=sys.stderr)
        return 1
    print(json.dumps({"ok": True, "data": text, "udid": udid}, ensure_ascii=False))
    return 0


def _cmd_act(args: argparse.Namespace) -> int:
    from mobile_playground import playground_act

    udid = _resolve_udid(args.udid)
    if not udid:
        print("未指定 udid 且无已连接设备", file=sys.stderr)
        return 1
    out = playground_act(udid, args.goal)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out.get("success") else 1


def _cmd_run_steps(args: argparse.Namespace) -> int:
    port, udid = _port(args.udid)
    try:
        steps: List[dict] = load_steps_file(args.file)
    except (OSError, ValueError, json.JSONDecodeError) as e:
        print(str(e), file=sys.stderr)
        return 1
    norm = [_normalize_step(s) for s in steps]
    results = port.run_steps(norm)
    print(json.dumps({"udid": udid, "results": results}, ensure_ascii=False, indent=2))
    if any(isinstance(r, dict) and r.get("status") == "error" for r in results):
        return 1
    if any(isinstance(r, dict) and r.get("ok") is False for r in results):
        return 1
    return 0


def _cmd_readiness(args: argparse.Namespace) -> int:
    from vision_platform_readiness import check_vision_automation_readiness

    out = check_vision_automation_readiness()
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out.get("ready") else 2
