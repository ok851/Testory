"""install_paths 解析（保护版安装目录布局）。"""
from pathlib import Path

from modules.core.install_paths import helper_executable, protected_backend_exe, resolve_install_root


def test_resolve_install_root_dev():
    root = resolve_install_root()
    assert (root / "app.py").is_file() or (root / "packaging").is_dir()


def test_protected_backend_path_layout(tmp_path):
    runtime = tmp_path / "runtime" / "testory_app"
    runtime.mkdir(parents=True)
    exe = runtime / "TestoryBackend.exe"
    exe.write_text("", encoding="utf-8")
    import os

    os.environ["TESTORY_INSTALL_ROOT"] = str(tmp_path)
    found = protected_backend_exe()
    assert found is not None
    assert found.name == "TestoryBackend.exe"


def test_helper_executable_layout(tmp_path):
    gw = tmp_path / "runtime" / "TestoryEmbeddedGw" / "TestoryEmbeddedGw.exe"
    gw.parent.mkdir(parents=True)
    gw.write_text("", encoding="utf-8")
    import os

    os.environ["TESTORY_INSTALL_ROOT"] = str(tmp_path)
    assert helper_executable("TestoryEmbeddedGw") == gw
