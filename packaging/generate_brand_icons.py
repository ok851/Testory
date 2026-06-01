# -*- coding: utf-8 -*-
"""生成 Testory 应用图标（透明圆角，无黑边）。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "packaging" / "inno" / "testory.ico"
OUT_APP_ICO = ROOT / "static" / "brand" / "app.ico"
OUT_PNG = ROOT / "static" / "brand" / "app-icon.png"

ICO_SIZES = (256, 128, 64, 48, 32, 16)


def _lerp(a: int, b: int, t: float) -> int:
    return int(a + (b - a) * t)


def render_testory_icon(size: int = 512):
    from PIL import Image, ImageDraw, ImageFont

    margin = max(4, size // 32)
    radius = size // 5
    box = (margin, margin, size - margin, size - margin)

    grad = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    for y in range(size):
        t = y / max(1, size - 1)
        row_color = (_lerp(109, 6, t), _lerp(40, 182, t), _lerp(217, 212, t), 255)
        for x in range(size):
            tx = x / max(1, size - 1)
            blend = (t + tx) / 2
            grad.putpixel(
                (x, y),
                (_lerp(109, 6, blend), _lerp(40, 182, blend), _lerp(217, 212, blend), 255),
            )

    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(box, radius=radius, fill=255)

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    img.paste(grad, mask=mask)
    draw = ImageDraw.Draw(img)

    font_size = int(size * 0.46)
    try:
        font = ImageFont.truetype("segoeui.ttf", font_size)
    except OSError:
        try:
            font = ImageFont.truetype("arial.ttf", font_size)
        except OSError:
            font = ImageFont.load_default()

    letter = "T"
    bbox = draw.textbbox((0, 0), letter, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    tx = (size - tw) // 2 - bbox[0]
    ty = (size - th) // 2 - bbox[1] - size // 40
    draw.text((tx + 2, ty + 3), letter, fill=(0, 0, 0, 80), font=font)
    draw.text((tx, ty), letter, fill=(255, 255, 255, 255), font=font)

    badge_r = size // 7
    bx = size - margin - badge_r - size // 28
    by = size - margin - badge_r - size // 28
    draw.ellipse(
        (bx - badge_r, by - badge_r, bx + badge_r, by + badge_r),
        fill=(34, 197, 94, 255),
    )
    check_w = badge_r
    draw.line(
        [(bx - check_w // 2, by), (bx - check_w // 6, by + check_w // 2), (bx + check_w // 2, by - check_w // 3)],
        fill=(255, 255, 255, 255),
        width=max(2, size // 64),
    )
    return img


def save_icons() -> None:
    from PIL import Image

    base = render_testory_icon(512)
    OUT_PNG.parent.mkdir(parents=True, exist_ok=True)
    base.save(OUT_PNG, format="PNG")

    ico_base = base.resize((256, 256), Image.Resampling.LANCZOS)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    ico_base.save(OUT, format="ICO", sizes=[(s, s) for s in ICO_SIZES])

    import shutil

    shutil.copy2(OUT, OUT_APP_ICO)
    size_kb = OUT.stat().st_size / 1024
    print(f"Wrote {OUT_PNG}")
    print(f"Wrote {OUT} ({size_kb:.1f} KB, sizes={ICO_SIZES})")
    print(f"Copied {OUT_APP_ICO}")


def main() -> int:
    try:
        save_icons()
    except ImportError as e:
        raise SystemExit("需要 Pillow：pip install Pillow") from e
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
