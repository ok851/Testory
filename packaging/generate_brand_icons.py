# -*- coding: utf-8 -*-
"""生成 Testory 应用图标（透明圆角，科技感）。"""
from __future__ import annotations

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "packaging" / "inno" / "testory.ico"
OUT_APP_ICO = ROOT / "static" / "brand" / "app.ico"
OUT_PNG = ROOT / "static" / "brand" / "app-icon.png"
SVG_SRC = ROOT / "static" / "brand" / "testory-mark.svg"

ICO_SIZES = (256, 128, 64, 48, 32, 16)


def _lerp(a: int, b: int, t: float) -> int:
    return int(a + (b - a) * t)


def render_testory_icon(size: int = 512):
    """优先从 testory-mark.svg 栅格化，失败则回退程序化绘制。"""
    if SVG_SRC.is_file():
        try:
            import io

            import cairosvg
            from PIL import Image

            png = cairosvg.svg2png(url=str(SVG_SRC), output_width=size, output_height=size)
            return Image.open(io.BytesIO(png)).convert("RGBA")
        except Exception:
            pass
    return _render_testory_icon_program(size)


def _render_testory_icon_program(size: int = 512):
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    cx, cy = size // 2, size // 2
    margin = max(6, size // 18)
    radius = size // 4

    # 深色科技底 + 霓虹描边
    for y in range(size):
        t = y / max(1, size - 1)
        c1 = (_lerp(12, 4, t), _lerp(18, 8, t), _lerp(42, 28, t), 255)
        for x in range(size):
            tx = x / max(1, size - 1)
            blend = (t * 0.55 + tx * 0.45)
            col = (
                _lerp(15, 8, blend),
                _lerp(23, 12, blend),
                _lerp(56, 36, blend),
                255,
            )
            img.putpixel((x, y), col)

    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (margin, margin, size - margin, size - margin),
        radius=radius,
        fill=255,
    )
    bg = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    bg.paste(img, mask=mask)
    img = bg
    draw = ImageDraw.Draw(img)

    box = (margin, margin, size - margin, size - margin)
    draw.rounded_rectangle(box, radius=radius, outline=(56, 189, 248, 180), width=max(2, size // 128))

    # 六边形智能网络
    hex_r = size * 0.22
    hex_pts = []
    for i in range(6):
        ang = math.radians(60 * i - 30)
        hex_pts.append((cx + hex_r * math.cos(ang), cy + hex_r * math.sin(ang)))
    draw.polygon(hex_pts, outline=(99, 102, 241, 120), width=max(2, size // 160))
    for i, (x, y) in enumerate(hex_pts):
        draw.ellipse(
            (x - size * 0.018, y - size * 0.018, x + size * 0.018, y + size * 0.018),
            fill=(34, 211, 238, 220),
        )
        nxt = hex_pts[(i + 1) % 6]
        draw.line([(x, y), nxt], fill=(129, 140, 248, 90), width=max(1, size // 200))

    # 中心 T
    font_size = int(size * 0.34)
    try:
        font = ImageFont.truetype("segoeuib.ttf", font_size)
    except OSError:
        try:
            font = ImageFont.truetype("segoeui.ttf", font_size)
        except OSError:
            font = ImageFont.load_default()
    letter = "T"
    bbox = draw.textbbox((0, 0), letter, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    tx = cx - tw // 2 - bbox[0]
    ty = cy - th // 2 - bbox[1]
    draw.text((tx + 1, ty + 2), letter, fill=(0, 0, 0, 120), font=font)
    draw.text((tx, ty), letter, fill=(240, 249, 255, 255), font=font)

    # 右下角 AI 节点
    badge_r = size // 9
    bx = size - margin - badge_r - size // 24
    by = size - margin - badge_r - size // 24
    draw.ellipse(
        (bx - badge_r, by - badge_r, bx + badge_r, by + badge_r),
        fill=(16, 185, 129, 255),
        outline=(110, 231, 183, 200),
        width=max(1, size // 180),
    )
    dot = badge_r * 0.35
    draw.ellipse((bx - dot, by - dot, bx + dot, by + dot), fill=(255, 255, 255, 240))
    return img


def save_icons() -> None:
    from PIL import Image

    base = render_testory_icon(512)
    OUT_PNG.parent.mkdir(parents=True, exist_ok=True)
    base.save(OUT_PNG, format="PNG")

    # 为每个尺寸生成独立图像，确保 ICO 包含所有尺寸
    OUT.parent.mkdir(parents=True, exist_ok=True)
    images = []
    for size in ICO_SIZES:
        img = base.resize((size, size), Image.Resampling.LANCZOS)
        # 确保是 RGBA 模式（支持透明度）
        if img.mode != "RGBA":
            img = img.convert("RGBA")
        images.append(img)

    # 保存为多尺寸 ICO，第一个图像作为默认
    images[0].save(
        OUT,
        format="ICO",
        append_images=images[1:],
        sizes=[(s, s) for s in ICO_SIZES],
    )

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
