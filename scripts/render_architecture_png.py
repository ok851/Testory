# -*- coding: utf-8 -*-
"""将 docs/assets/architecture_local_16x9.svg 导出为 1920×1080 PNG。"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SVG = ROOT / "docs" / "assets" / "architecture_local_16x9.svg"
PNG = ROOT / "docs" / "assets" / "architecture_local_16x9.png"
W, H = 1920, 1080


def _via_cairosvg() -> bool:
    try:
        import cairosvg  # type: ignore
    except ImportError:
        return False
    cairosvg.svg2png(url=str(SVG), write_to=str(PNG), output_width=W, output_height=H)
    return True


def _via_inkscape() -> bool:
    for cmd in ("inkscape", "inkscape.com"):
        try:
            subprocess.run(
                [
                    cmd,
                    str(SVG),
                    f"--export-filename={PNG}",
                    f"--export-width={W}",
                    f"--export-height={H}",
                ],
                check=True,
                capture_output=True,
            )
            return PNG.is_file()
        except (FileNotFoundError, subprocess.CalledProcessError):
            continue
    return False


def main() -> int:
    if not SVG.is_file():
        print(f"缺少 SVG: {SVG}", file=sys.stderr)
        return 1
    PNG.parent.mkdir(parents=True, exist_ok=True)
    if _via_cairosvg() or _via_inkscape():
        print(f"已生成: {PNG}")
        return 0
    print(
        "无法导出 PNG。请任选其一：\n"
        "  pip install cairosvg && python scripts/render_architecture_png.py\n"
        "  或安装 Inkscape 并加入 PATH\n"
        "  或在浏览器中打开 SVG 另存为 PNG（1920×1080）",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
