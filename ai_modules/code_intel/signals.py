# -*- coding: utf-8 -*-
"""从 diff / 文件片段轻量提取 testid、aria-label、路径信号（不做完整 AST）。"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set

_TESTID_RE = re.compile(
    r"""(?:data-testid|data-test-id|data-cy|data-qa)\s*=\s*['"]([^'"]+)['"]""",
    re.I,
)
_ARIA_RE = re.compile(r"""aria-label\s*=\s*['"]([^'"]+)['"]""", re.I)
_ROUTE_RE = re.compile(
    r"""(?:path|route|to)\s*[:=]\s*['"](/[^'"]{1,120})['"]""",
    re.I,
)
_API_RE = re.compile(
    r"""(?:fetch|axios|request)\s*\(\s*['"`]([^'"`]{3,200})['"`]"""
    r"""|(?:url|endpoint)\s*[:=]\s*['"`](/api/[^'"`]{1,200})['"`]""",
    re.I,
)


def _as_text_blob(
    diff: str,
    file_snippets: Dict[str, Any],
    changed_files: List[str],
) -> str:
    parts: List[str] = []
    if diff:
        parts.append(str(diff)[:120_000])
    if isinstance(file_snippets, dict):
        for path, content in list(file_snippets.items())[:40]:
            parts.append(f"\n=== FILE:{path} ===\n{str(content)[:8000]}")
    if changed_files:
        parts.append("\nCHANGED:\n" + "\n".join(str(p) for p in changed_files[:200]))
    return "\n".join(parts)


def extract_ui_signals(
    *,
    diff: str = "",
    changed_files: Optional[List[str]] = None,
    file_snippets: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """返回结构化信号，供影响分析与用例生成使用。"""
    files = [str(f) for f in (changed_files or []) if f]
    snippets = file_snippets if isinstance(file_snippets, dict) else {}
    blob = _as_text_blob(diff or "", snippets, files)

    testids = sorted(set(_TESTID_RE.findall(blob)))[:80]
    aria_labels = sorted(set(_ARIA_RE.findall(blob)))[:80]
    routes = sorted(set(_ROUTE_RE.findall(blob)))[:40]
    apis: List[str] = []
    for m in _API_RE.finditer(blob):
        g = m.group(1) or m.group(2) or ""
        if g:
            apis.append(g.strip())
    apis = sorted(set(apis))[:40]

    path_tokens = _path_tokens(files)
    frameworks = _detect_frameworks(files, blob)
    looks_like_rollback = bool(
        re.search(r"\b(revert|rollback|回滚)\b", blob, re.I)
        or re.search(r"^Revert\s+", (diff or "")[:500], re.M)
    )

    return {
        "testids": testids,
        "aria_labels": aria_labels,
        "routes": routes,
        "api_hints": apis,
        "changed_files": files[:200],
        "path_tokens": sorted(path_tokens)[:80],
        "frameworks": frameworks,
        "looks_like_rollback": looks_like_rollback,
        "signal_counts": {
            "testids": len(testids),
            "aria_labels": len(aria_labels),
            "routes": len(routes),
            "apis": len(apis),
            "files": len(files),
        },
    }


def _path_tokens(files: List[str]) -> Set[str]:
    tokens: Set[str] = set()
    stop = {
        "src", "app", "components", "pages", "views", "lib", "utils", "hooks",
        "store", "api", "assets", "styles", "public", "index", "main", "ts", "tsx",
        "js", "jsx", "vue", "css", "scss", "less", "json", "md", "test", "spec",
        "tests", "__tests__", "node_modules",
    }
    for f in files:
        norm = f.replace("\\", "/").lower()
        for part in re.split(r"[/_.\-]+", norm):
            p = part.strip()
            if len(p) < 2 or p in stop or p.isdigit():
                continue
            tokens.add(p)
    return tokens


def _detect_frameworks(files: List[str], blob: str) -> List[str]:
    found: Set[str] = set()
    joined = " ".join(files).lower() + "\n" + blob[:5000].lower()
    if any(x in joined for x in (".tsx", ".jsx", "react", "next/")):
        found.add("react")
    if any(x in joined for x in (".vue", "vuex", "nuxt")):
        found.add("vue")
    if "angular" in joined:
        found.add("angular")
    if any(x in joined for x in ("playwright", "cypress", "jest")):
        found.add("test_tooling")
    return sorted(found)
