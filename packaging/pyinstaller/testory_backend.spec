# -*- mode: python ; coding: utf-8 -*-
# 保护版后端：PyInstaller onedir → dist/testory_app/
# 构建: .\packaging\bundle\build_testory_onedir.ps1

import sys
from pathlib import Path

block_cipher = None
spec_dir = Path(SPECPATH).resolve()
root = spec_dir.parent.parent
sys.path.insert(0, str(spec_dir))
from _spec_common import project_analysis_bundle  # noqa: E402

entry = spec_dir / "testory_backend_entry.py"
icon = spec_dir.parent / "inno" / "testory.ico"
_pyi_datas, _pyi_binaries, _pyi_hidden = project_analysis_bundle(root)

a = Analysis(
    [str(entry), str(root / "app.py")],
    pathex=[str(root)],
    binaries=_pyi_binaries,
    datas=_pyi_datas,
    hiddenimports=_pyi_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tests", "pytest", "packaging.enterprise"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="TestoryBackend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    icon=str(icon) if icon.is_file() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="testory_app",
)
