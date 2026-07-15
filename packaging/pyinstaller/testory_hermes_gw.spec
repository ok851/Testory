# -*- mode: python ; coding: utf-8 -*-
# → dist/TestoryHermesGw/

import sys
from pathlib import Path

block_cipher = None
spec_dir = Path(SPECPATH).resolve()
root = spec_dir.parent.parent
sys.path.insert(0, str(spec_dir))

entry = spec_dir / "testory_hermes_gw_entry.py"
hidden = [
    "hermes_gateway_client",
    "hermes_config",
    "hermes_service_bootstrap",
    "agent_gateway_client",
    "install_paths",
    "dotenv",
    "requests",
]

a = Analysis(
    [str(entry)],
    pathex=[str(root)],
    binaries=[],
    datas=[],
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tests", "pytest", "numpy", "cv2", "PIL", "mss", "pandas", "scipy", "openpyxl", "reportlab", "docx"],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="TestoryHermesGw",
    debug=False,
    console=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    name="TestoryHermesGw",
)
