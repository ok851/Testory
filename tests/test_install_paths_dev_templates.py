# -*- coding: utf-8 -*-
"""源码运行时 Flask 应使用仓库 templates，而非旧安装目录。"""
from __future__ import annotations

import os
from pathlib import Path


def test_resource_root_prefers_repo_when_not_frozen(monkeypatch, tmp_path):
    import install_paths

    repo = Path(install_paths.__file__).resolve().parent
    stale = tmp_path / "stale_install"
    (stale / "templates").mkdir(parents=True)
    (stale / "static").mkdir(parents=True)
    (stale / "templates" / "base.html").write_text("SIDEBAR_MARKER", encoding="utf-8")

    monkeypatch.delenv("TESTORY_INSTALL_ROOT", raising=False)
    monkeypatch.setenv("TESTORY_INSTALL_ROOT", str(stale))
    monkeypatch.setattr(install_paths.sys, "frozen", False, raising=False)

    root = install_paths.resource_root()
    assert root == repo
    text = (root / "templates" / "base.html").read_text(encoding="utf-8")
    assert "testory-sidebar" not in text
    assert "testoryMainNav" in text or "统一顶栏" in text
