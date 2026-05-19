# -*- coding: utf-8 -*-
"""
二进制差分包（bsdiff4）。

构建: python -m packaging.enterprise.build_delta old.exe new.exe patch.bsdiff
应用: python -m packaging.enterprise.update_patch apply base.exe patch.bsdiff out.exe
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _require_bsdiff():
    try:
        import bsdiff4  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "需要 bsdiff4：pip install bsdiff4"
        ) from e
    return bsdiff4


def build_patch(old_path: Path, new_path: Path, patch_path: Path) -> None:
    bsdiff4 = _require_bsdiff()
    patch_path.parent.mkdir(parents=True, exist_ok=True)
    with open(old_path, "rb") as old_f, open(new_path, "rb") as new_f, open(patch_path, "wb") as patch_f:
        bsdiff4.diff(old_f.read(), new_f.read(), patch_f)


def apply_patch(old_path: Path, patch_path: Path, out_path: Path) -> None:
    bsdiff4 = _require_bsdiff()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(old_path, "rb") as old_f, open(patch_path, "rb") as patch_f, open(out_path, "wb") as out_f:
        bsdiff4.patch(old_f.read(), patch_f.read(), out_f)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="UAT 发行包差分")
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="生成差分")
    b.add_argument("old_file")
    b.add_argument("new_file")
    b.add_argument("patch_file")
    a = sub.add_parser("apply", help="应用差分")
    a.add_argument("old_file")
    a.add_argument("patch_file")
    a.add_argument("out_file")
    args = p.parse_args(argv)
    try:
        if args.cmd == "build":
            build_patch(Path(args.old_file), Path(args.new_file), Path(args.patch_file))
            print(f"已生成差分: {args.patch_file}")
        else:
            apply_patch(Path(args.old_file), Path(args.patch_file), Path(args.out_file))
            print(f"已输出: {args.out_file}")
        return 0
    except Exception as e:
        print(f"错误: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
