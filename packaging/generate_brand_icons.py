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

    # 紫青色渐变背景（与 testory-mark.svg 一致）
    gradient = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    grad_draw = ImageDraw.Draw(gradient)
    for y in range(size):
        t = y / max(1, size - 1)
        # 从 #6366f1 (紫) 到 #06b6d4 (青)，中间经 #8b5cf6 (紫)
        if t < 0.48:
            tt = t / 0.48
            r = _lerp(99, 139, tt)
            g = _lerp(102, 92, tt)
            b = _lerp(241, 246, tt)
        else:
            tt = (t - 0.48) / 0.52
            r = _lerp(139, 6, tt)
            g = _lerp(92, 182, tt)
            b = _lerp(246, 212, tt)
        grad_draw.line([(0, y), (size, y)], fill=(r, g, b, 255))

    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (margin, margin, size - margin, size - margin),
        radius=radius,
        fill=255,
    )
    img.paste(gradient, mask=mask)
    draw = ImageDraw.Draw(img)

    # 白色 T 字母
    t_w = size * 0.44
    t_h = size * 0.44
    t_x = cx - t_w // 2
    t_y = cy - t_h // 2
    draw.rectangle((t_x, t_y, t_x + t_w, t_y + t_h * 0.16), fill=(255, 255, 255, 255))
    draw.rectangle((cx - t_w * 0.11, t_y, cx + t_w * 0.11, t_y + t_h), fill=(255, 255, 255, 255))

    # 右下角 AI 绿点
    badge_r = size // 9
    bx = size - margin - badge_r - size // 24
    by = size - margin - badge_r - size // 24
    draw.ellipse(
        (bx - badge_r, by - badge_r, bx + badge_r, by + badge_r),
        fill=(52, 211, 153, 255),
        outline=(110, 231, 183, 180),
        width=max(1, size // 180),
    )
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
