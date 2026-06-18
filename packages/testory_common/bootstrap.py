# -*- coding: utf-8 -*-
"""Add monorepo packages + load .env without polluting sys.path with repo root."""
from __future__ import annotations

import sys
from pathlib import Path


def bootstrap_project(*, project_dir: Path, repo_levels_up: int | None = None) -> Path:
    """Return repo root; ensure project_dir + packages/ are importable."""
    project_dir = project_dir.resolve()
    standalone = (project_dir / "testory_common").is_dir()
    if repo_levels_up is None:
        repo_levels_up = 0 if standalone else 2

    repo_root = project_dir
    for _ in range(repo_levels_up):
        repo_root = repo_root.parent

    project_s = str(project_dir)
    if project_s not in sys.path:
        sys.path.insert(0, project_s)

    pkg_roots: list[Path] = []
    if standalone:
        pkg_roots.append(project_dir)
    packages = repo_root / "packages"
    if packages.is_dir():
        pkg_roots.append(packages)

    for pkg_root in pkg_roots:
        if not (pkg_root / "testory_common").is_dir():
            continue
        pkg_s = str(pkg_root)
        if pkg_s not in sys.path:
            insert_at = 1 if sys.path and sys.path[0] == project_s else 0
            sys.path.insert(insert_at, pkg_s)

    try:
        from dotenv import load_dotenv

        env_file = repo_root / ".env"
        if env_file.is_file():
            load_dotenv(env_file, encoding="utf-8-sig")
        local_env = project_dir / ".env"
        if local_env.is_file():
            load_dotenv(local_env, encoding="utf-8-sig", override=True)
    except ImportError:
        pass
    return repo_root
