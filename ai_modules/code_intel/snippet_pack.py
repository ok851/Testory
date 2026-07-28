# -*- coding: utf-8 -*-
"""把变更文件路径打包为 file_snippets，供 UI Agent / code-change 使用。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

_FRONTEND_EXT = {
    ".tsx", ".jsx", ".vue", ".svelte", ".html", ".ts", ".js", ".css", ".scss",
}


def is_frontend_path(path: str) -> bool:
    p = (path or "").replace("\\", "/").lower()
    if any(skip in p for skip in ("/node_modules/", "/dist/", "/build/", ".min.js")):
        return False
    ext = Path(p).suffix
    if ext in _FRONTEND_EXT:
        return True
    return False


def pack_file_snippets(
    changed_files: Iterable[str],
    *,
    repo_root: Optional[str] = None,
    max_files: int = 25,
    max_chars_per_file: int = 8000,
    frontend_only: bool = True,
) -> Tuple[Dict[str, str], List[str]]:
    """
    从工作区读取变更文件内容。
    返回 (snippets, warnings)。
    """
    root = Path(repo_root or os.getcwd()).expanduser().resolve()
    snippets: Dict[str, str] = {}
    warns: List[str] = []
    files = [str(f).replace("\\", "/").lstrip("./") for f in changed_files if f]
    if frontend_only:
        files = [f for f in files if is_frontend_path(f)]
    for rel in files[: max(1, int(max_files))]:
        path = (root / rel).resolve()
        try:
            # 防路径穿越
            path.relative_to(root)
        except ValueError:
            warns.append(f"跳过越界路径: {rel}")
            continue
        if not path.is_file():
            warns.append(f"文件不存在: {rel}")
            continue
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            warns.append(f"读取失败 {rel}: {e}")
            continue
        if len(raw) > max_chars_per_file:
            raw = raw[:max_chars_per_file] + "\n/* …truncated… */\n"
            warns.append(f"截断 {rel} 至 {max_chars_per_file} 字符")
        snippets[rel] = raw
    if not snippets and files:
        warns.append("未能打包任何文件内容")
    return snippets, warns


def build_code_change_body(
    *,
    project_id: Optional[int],
    git_sha: str = "",
    branch: str = "",
    repo: str = "",
    changed_files: Optional[List[str]] = None,
    diff: str = "",
    mr_description: str = "",
    repo_root: Optional[str] = None,
    include_snippets: bool = True,
    generate_drafts: bool = False,
    analyze_only: bool = True,
    trigger_source: str = "ci",
) -> Dict[str, Any]:
    """构造 POST /api/ci/code-change 请求体（含 file_snippets）。"""
    files = list(changed_files or [])
    body: Dict[str, Any] = {
        "project_id": project_id,
        "git_sha": git_sha,
        "branch": branch,
        "repo": repo,
        "changed_files": files,
        "diff": (diff or "")[:200_000],
        "mr_description": (mr_description or "")[:8000],
        "analyze_only": bool(analyze_only),
        "generate_drafts": bool(generate_drafts),
        "trigger_source": trigger_source or "ci",
        "use_llm": True,
        "async": True,
    }
    if include_snippets and files:
        snippets, warns = pack_file_snippets(files, repo_root=repo_root)
        body["file_snippets"] = snippets
        if warns:
            body["_snippet_warnings"] = warns
    return body
