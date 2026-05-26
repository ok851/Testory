#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""UAT 浏览器扩展安装助手（Chrome / Edge 未打包扩展）。"""

from __future__ import annotations

import argparse
import os
import shutil
import sys


def project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def default_install_dir() -> str:
    local = os.path.join(
        os.environ.get("LOCALAPPDATA", ""),
        "NewUITestPlatform",
        "extensions",
        "chrome",
    )
    if local:
        return local
    return os.path.join(
        os.environ.get("ProgramFiles", r"C:\Program Files"),
        "NewUITestPlatform",
        "extensions",
        "chrome",
    )


def prepare_extension(target_dir: str) -> int:
    src = os.path.join(project_root(), "browser_extension", "chrome")
    if not os.path.isdir(src):
        print(f"ERROR: extension source missing: {src}", file=sys.stderr)
        return 1
    os.makedirs(target_dir, exist_ok=True)
    for name in os.listdir(src):
        sp = os.path.join(src, name)
        dp = os.path.join(target_dir, name)
        if os.path.isdir(sp):
            if os.path.exists(dp):
                shutil.rmtree(dp)
            shutil.copytree(sp, dp)
        else:
            shutil.copy2(sp, dp)
    print(f"OK: extension copied to {target_dir}")
    print("Manual step: open edge://extensions or chrome://extensions")
    print("Enable Developer mode -> Load unpacked -> select the folder above")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Install UAT web capture extension")
    parser.add_argument("--prepare", action="store_true", help="Copy extension to install dir")
    parser.add_argument("--silent", action="store_true", help="Minimal output")
    parser.add_argument("--target", default="", help="Override install directory")
    args = parser.parse_args()
    target = (args.target or "").strip() or default_install_dir()
    if args.prepare or not args.silent:
        return prepare_extension(target)
    return prepare_extension(target)


if __name__ == "__main__":
    raise SystemExit(main())
