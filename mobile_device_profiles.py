# -*- coding: utf-8 -*-
"""虚拟手机外框型号预设（仅影响 UI 展示比例，不改变真机分辨率）。"""

from __future__ import annotations

from typing import Any, Dict, List

# width/height 为外框参考比例；真机分辨率由 adb 实时读取
DEVICE_FRAME_PRESETS: List[Dict[str, Any]] = [
    {
        "id": "pixel_7",
        "label": "Google Pixel 7",
        "brand": "Google",
        "frame_width": 412,
        "frame_height": 915,
        "corner_radius": 28,
        "notch": "punch",
    },
    {
        "id": "samsung_s23",
        "label": "Samsung Galaxy S23",
        "brand": "Samsung",
        "frame_width": 360,
        "frame_height": 780,
        "corner_radius": 32,
        "notch": "punch",
    },
    {
        "id": "xiaomi_14",
        "label": "Xiaomi 14",
        "brand": "Xiaomi",
        "frame_width": 393,
        "frame_height": 873,
        "corner_radius": 24,
        "notch": "punch",
    },
    {
        "id": "huawei_p60",
        "label": "Huawei P60",
        "brand": "Huawei",
        "frame_width": 360,
        "frame_height": 800,
        "corner_radius": 26,
        "notch": "pill",
    },
    {
        "id": "ipad_mini",
        "label": "平板 4:3",
        "brand": "Tablet",
        "frame_width": 768,
        "frame_height": 1024,
        "corner_radius": 18,
        "notch": "none",
    },
    {
        "id": "generic_19_9",
        "label": "通用 19.5:9",
        "brand": "Android",
        "frame_width": 360,
        "frame_height": 780,
        "corner_radius": 20,
        "notch": "none",
    },
]


def get_frame_preset(preset_id: str) -> Dict[str, Any]:
    pid = (preset_id or "generic_19_9").strip()
    for p in DEVICE_FRAME_PRESETS:
        if p.get("id") == pid:
            return dict(p)
    return dict(DEVICE_FRAME_PRESETS[-1])


def list_frame_presets() -> List[Dict[str, Any]]:
    return [dict(p) for p in DEVICE_FRAME_PRESETS]
