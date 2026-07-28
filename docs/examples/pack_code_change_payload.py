#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""独立 CI 辅助（可拷贝到业务仓）：打包变更前端源码为 code-change JSON。

不依赖 Testory 源码树；仅需 Python 3.8+ 标准库。

用法（在业务仓根目录）:
  python pack_code_change_payload.py --project-id 1 --out payload.json
  curl -X POST "$UAT_PLATFORM_URL/api/ci/code-change" \\
    -H "Authorization: Bearer $UAT_API_TOKEN" \\
    -H "Content-Type: application/json" -d @payload.json
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

_FRONTEND_EXT = {
    ".tsx", ".jsx", ".vue", ".svelte", ".html", ".ts", ".js", ".css", ".scss",
}


def _is_frontend(path: str) -> bool:
    p = (path or "").replace("\\", "/").lower()
    if any(s in p for s in ("/node_modules/", "/dist/", "/build/", ".min.js")):
        return False
    return Path(p).suffix in _FRONTEND_EXT


def _git(cmd: list, cwd: Path) -> str:
    try:
        return subprocess.check_output(
            cmd, cwd=str(cwd), stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace",
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def pack_snippets(files: list, root: Path, max_files: int = 25, max_chars: int = 8000) -> dict:
    out = {}
    for rel in [f for f in files if _is_frontend(f)][:max_files]:
        path = (root / rel).resolve()
        try:
            path.relative_to(root.resolve())
        except ValueError:
            continue
        if not path.is_file():
            continue
        raw = path.read_text(encoding="utf-8", errors="replace")
        if len(raw) > max_chars:
            raw = raw[:max_chars] + "\n/* …truncated… */\n"
        out[rel.replace("\\", "/")] = raw
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project-id", type=int, default=int(os.environ.get("PROJECT_ID") or "1"))
    ap.add_argument("--git-sha", default=os.environ.get("CI_COMMIT_SHA") or "")
    ap.add_argument("--branch", default=os.environ.get("CI_COMMIT_REF_NAME") or "")
    ap.add_argument("--repo", default=os.environ.get("CI_PROJECT_PATH") or "")
    ap.add_argument("--before", default=os.environ.get("CI_COMMIT_BEFORE_SHA") or "HEAD~1")
    ap.add_argument("--after", default="HEAD")
    ap.add_argument("--generate-drafts", action="store_true")
    ap.add_argument("--no-snippets", action="store_true")
    ap.add_argument("--out", default="code-change-payload.json")
    ap.add_argument("--repo-root", default=".")
    args = ap.parse_args()

    root = Path(args.repo_root).resolve()
    names = [ln.strip() for ln in _git(["git", "diff", "--name-only", args.before, args.after], root).splitlines() if ln.strip()]
    diff = _git(["git", "diff", args.before, args.after], root)[:180000]
    snippets = {} if args.no_snippets else pack_snippets(names, root)
    body = {
        "project_id": args.project_id,
        "git_sha": args.git_sha,
        "branch": args.branch,
        "repo": args.repo,
        "changed_files": names,
        "diff": diff,
        "mr_description": (os.environ.get("CI_COMMIT_TITLE") or "")[:8000],
        "file_snippets": snippets,
        "analyze_only": not bool(args.generate_drafts),
        "generate_drafts": bool(args.generate_drafts),
        "trigger_source": os.environ.get("TRIGGER_SOURCE") or "gitlab",
        "use_llm": True,
        "async": True,
    }
    Path(args.out).write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {args.out} files={len(names)} snippets={len(snippets)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
