# -*- coding: utf-8 -*-
"""packaging.launch_checks 布局与进程内导入提示。"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from packaging.launch_checks import (
    check_current_process_imports,
    check_layout,
)


def test_check_layout_flags_missing_backend(tmp_path: Path) -> None:
    (tmp_path / "packaging").mkdir()
    (tmp_path / "packaging" / "uat_desktop.py").write_text("# stub", encoding="utf-8")
    (tmp_path / "templates").mkdir()
    (tmp_path / "static").mkdir()
    (tmp_path / "static" / "desktop").mkdir(parents=True)
    (tmp_path / "static" / "desktop" / "shell_boot.html").write_text("", encoding="utf-8")
    (tmp_path / "Testory.exe").write_bytes(b"")
    (tmp_path / "playwright-browsers").mkdir()
    (tmp_path / "redist" / "webview2").mkdir(parents=True)
    (tmp_path / "redist" / "webview2" / "MicrosoftEdgeWebview2Setup.exe").write_bytes(b"")

    errs = check_layout(tmp_path)
    assert any("TestoryBackend" in e or "app.py" in e for e in errs)
    assert any(".venv" in e for e in errs)


def test_check_current_process_rejects_testory_shell_at_root(tmp_path: Path) -> None:
    shell = tmp_path / "TestoryShell.exe"
    shell.write_bytes(b"")
    with patch.object(sys, "executable", str(shell)):
        errs = check_current_process_imports(tmp_path)
    assert errs
    assert "TestoryShell" in errs[0]
