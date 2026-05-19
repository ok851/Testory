# -*- mode: python ; coding: utf-8 -*-
# onedir 启动器 + 外置 playwright-browsers（先运行 bundle_playwright.ps1）
# pyinstaller packaging/pyinstaller/uat_onedir.spec

import os
from pathlib import Path

block_cipher = None
spec_dir = Path(SPECPATH).resolve()
root = spec_dir.parent.parent
launcher = spec_dir / "uat_onedir_launcher.py"

a = Analysis(
    [str(launcher)],
    pathex=[str(root.parent)],
    binaries=[],
    datas=[],
    hiddenimports=["dotenv"],
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
    [],
    exclude_binaries=True,
    name="uat_platform",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="uat_onedir",
)
