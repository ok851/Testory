# -*- coding: utf-8 -*-
"""CI 构建差分包并输出 manifest 片段。"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from update_patch import build_patch


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--old", required=True, help="上一版本安装包或 release.zip")
    ap.add_argument("--new", required=True, help="新版本安装包")
    ap.add_argument("--out-patch", required=True, help="输出 .bsdiff 路径")
    ap.add_argument("--base-version", required=True)
    ap.add_argument("--new-version", required=True)
    ap.add_argument("--patch-url", default="", help="上传 CDN 后的 HTTPS URL")
    ap.add_argument("--emit-json", default="", help="写出 manifest 差分字段 JSON")
    args = ap.parse_args()
    old_p, new_p, patch_p = Path(args.old), Path(args.new), Path(args.out_patch)
    if not old_p.is_file() or not new_p.is_file():
        print("old/new 文件不存在", file=sys.stderr)
        return 1
    build_patch(old_p, new_p, patch_p)
    fragment = {
        "version": args.new_version,
        "base_version": args.base_version,
        "patch_url": args.patch_url,
        "patch_sha256": sha256_file(patch_p),
        "patch_cache_basename": old_p.name,
        "patch_size_bytes": patch_p.stat().st_size,
    }
    print(json.dumps(fragment, ensure_ascii=False, indent=2))
    if args.emit_json:
        Path(args.emit_json).write_text(
            json.dumps(fragment, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
