# -*- mode: python ; coding: utf-8 -*-
# → dist/TestoryEmbeddedGw/

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

block_cipher = None
spec_dir = Path(SPECPATH).resolve()
root = spec_dir.parent.parent
sys.path.insert(0, str(spec_dir))
from _spec_common import lite_gateway_analysis_bundle  # noqa: E402

entry = spec_dir / "testory_embedded_gw_entry.py"
_gw_datas, _gw_binaries, _gw_hidden = lite_gateway_analysis_bundle(root)
_gw_hidden += list(collect_submodules("embedded_browser_gateway"))

a = Analysis(
    [str(entry)],
    pathex=[str(root)],
    binaries=_gw_binaries,
    datas=_gw_datas,
    hiddenimports=_gw_hidden,
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
    name="TestoryEmbeddedGw",
    debug=False,
    console=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    name="TestoryEmbeddedGw",
)
