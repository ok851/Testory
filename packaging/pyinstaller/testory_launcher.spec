# -*- mode: python ; coding: utf-8 -*-
# 构建: python -m PyInstaller packaging/pyinstaller/testory_launcher.spec
# 产物: dist/Testory.exe（复制到 dist/uat_release/Testory.exe 后由 Inno 打包）

import os
from pathlib import Path

block_cipher = None
spec_dir = Path(SPECPATH).resolve()
packaging = spec_dir.parent
root = packaging.parent
launcher = packaging / "testory_exe_launcher.py"
icon = packaging / "inno" / "testory.ico"

a = Analysis(
    [str(launcher)],
    pathex=[str(root)],
    binaries=[],
    datas=[(str(packaging / "testory_runtime.py"), ".")],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="Testory",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(icon) if icon.is_file() else None,
)
