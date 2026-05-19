# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 草稿：仅打包启动脚本，浏览器与 Playwright 运行时需外置安装。
# 构建: pyinstaller packaging/uat_platform.spec

import os

block_cipher = None
root = os.path.abspath(os.path.join(SPECPATH, '..'))

a = Analysis(
    [os.path.join(root, 'packaging', 'uat_launcher.py')],
    pathex=[root],
    binaries=[],
    datas=[],
    hiddenimports=['dotenv'],
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
    name='uat_platform',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='uat_platform',
)
