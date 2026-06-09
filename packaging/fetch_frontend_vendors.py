# -*- coding: utf-8 -*-
"""下载前端 vendor 资源到 static/vendor/（离线安装包必需）。"""
from __future__ import annotations

import hashlib
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "static" / "vendor"

ASSETS = [
    (
        "tailwindcss/tailwind.min.js",
        "https://cdn.tailwindcss.com/3.4.16",
    ),
    (
        "fontawesome/css/all.min.css",
        "https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css",
    ),
    (
        "fontawesome/webfonts/fa-solid-900.woff2",
        "https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/webfonts/fa-solid-900.woff2",
    ),
    (
        "fontawesome/webfonts/fa-regular-400.woff2",
        "https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/webfonts/fa-regular-400.woff2",
    ),
    (
        "fontawesome/webfonts/fa-brands-400.woff2",
        "https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/webfonts/fa-brands-400.woff2",
    ),
    (
        "sweetalert2/sweetalert2.min.js",
        "https://cdn.jsdelivr.net/npm/sweetalert2@11/dist/sweetalert2.min.js",
    ),
    (
        "sweetalert2/sweetalert2.min.css",
        "https://cdn.jsdelivr.net/npm/sweetalert2@11/dist/sweetalert2.min.css",
    ),
    (
        "chart.js/chart.umd.min.js",
        "https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js",
    ),
]


def _fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "TestoryVendorFetch/1.0"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read()


def _patch_fontawesome_css(css_path: Path) -> None:
    text = css_path.read_text(encoding="utf-8")
    text = text.replace("/static/vendor/fontawesome/webfonts/", "../webfonts/")
    css_path.write_text(text, encoding="utf-8")


def main() -> int:
    VENDOR.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, str] = {}
    for rel, url in ASSETS:
        dest = VENDOR / rel.replace("/", "\\") if "\\" in rel else VENDOR / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        print(f"fetch {url}")
        data = _fetch(url)
        dest.write_bytes(data)
        manifest[rel] = hashlib.sha256(data).hexdigest()
        if rel.endswith("all.min.css"):
            _patch_fontawesome_css(dest)
    manifest_path = VENDOR / "manifest.json"
    import json

    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"wrote {len(ASSETS)} assets under {VENDOR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
