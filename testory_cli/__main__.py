# -*- coding: utf-8 -*-
"""
Testory CLI — 对标 Midscene Instant Action CLI，供 Hermes / Cursor / CI 使用。

示例：
  python -m testory_cli web readiness --session-id <sid>
  python -m testory_cli web screenshot --session-id <sid> -o shot.png
  python -m testory_cli web tap --session-id <sid> --locate "登录按钮"
  python -m testory_cli web run-steps --session-id <sid> --file steps.json
  python -m testory_cli mobile tap --udid emulator-5554 --locate "登录按钮"
  python -m testory_cli mobile query --prompt "当前用户 ID"
"""
from __future__ import annotations

import argparse
import sys

from testory_cli.web import _build_web_parser
from testory_cli.mobile import _build_mobile_parser


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="testory_cli",
        description="Testory 内部自动化 CLI（开发者/CI；终端用户请使用 AI 测试页）",
    )
    sub = parser.add_subparsers(dest="platform", required=True)
    _build_web_parser(sub)
    _build_mobile_parser(sub)
    args = parser.parse_args(argv)
    if args.platform == "web":
        return args.handler(args)
    if args.platform == "mobile":
        return args.handler(args)
    print("未知平台", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
