#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CI 辅助：打包变更前端源码为 /api/ci/code-change 请求 JSON（含 file_snippets）。

用法（在仓库根目录）:
  python scripts/build_code_change_payload.py \\
    --project-id 1 --git-sha \"$CI_COMMIT_SHA\" \\
    --out payload.json

  curl -X POST \"$URL/api/ci/code-change\" -H \"Authorization: Bearer $TOKEN\" \\
    -H \"Content-Type: application/json\" -d @payload.json
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai_modules.code_intel.snippet_pack import build_code_change_body  # noqa: E402


def _git_diff_names(before: str, after: str = "HEAD") -> list:
    try:
        out = subprocess.check_output(
            ["git", "diff", "--name-only", before, after],
            cwd=str(ROOT),
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


def _git_diff_text(before: str, after: str = "HEAD", limit: int = 180000) -> str:
    try:
        out = subprocess.check_output(
            ["git", "diff", before, after],
            cwd=str(ROOT),
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""
    return out[:limit]


def main() -> int:
    ap = argparse.ArgumentParser(description="Build Testory code-change payload with file_snippets")
    ap.add_argument("--project-id", type=int, default=int(os.environ.get("PROJECT_ID") or "1"))
    ap.add_argument("--git-sha", default=os.environ.get("CI_COMMIT_SHA") or "")
    ap.add_argument("--branch", default=os.environ.get("CI_COMMIT_REF_NAME") or "")
    ap.add_argument("--repo", default=os.environ.get("CI_PROJECT_PATH") or "")
    ap.add_argument("--before", default=os.environ.get("CI_COMMIT_BEFORE_SHA") or "HEAD~1")
    ap.add_argument("--after", default="HEAD")
    ap.add_argument("--generate-drafts", action="store_true")
    ap.add_argument("--no-snippets", action="store_true")
    ap.add_argument("--out", default="code-change-payload.json")
    ap.add_argument("--repo-root", default=str(ROOT))
    args = ap.parse_args()

    files = _git_diff_names(args.before, args.after)
    diff = _git_diff_text(args.before, args.after)
    body = build_code_change_body(
        project_id=args.project_id,
        git_sha=args.git_sha,
        branch=args.branch,
        repo=args.repo,
        changed_files=files,
        diff=diff,
        mr_description=os.environ.get("CI_COMMIT_TITLE") or os.environ.get("CI_COMMIT_MESSAGE") or "",
        repo_root=args.repo_root,
        include_snippets=not args.no_snippets,
        generate_drafts=bool(args.generate_drafts),
        analyze_only=not bool(args.generate_drafts),
        trigger_source=os.environ.get("TRIGGER_SOURCE") or "gitlab",
    )
    out_path = Path(args.out)
    out_path.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {out_path} files={len(files)} snippets={len(body.get('file_snippets') or {})}")
    for w in body.get("_snippet_warnings") or []:
        print(f"warn: {w}")
    # 不把内部警告字段留给 API
    if "_snippet_warnings" in body:
        del body["_snippet_warnings"]
        out_path.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
