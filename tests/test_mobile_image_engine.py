# -*- coding: utf-8 -*-
"""mobile_image_engine 单元测试。"""

import base64
import json

import pytest

from mobile_automation import parse_tap_coordinates
from mobile_image_engine import build_visual_template_json


def _tiny_png_b64() -> str:
    # 1x1 PNG
    raw = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    )
    return base64.b64encode(raw).decode("ascii")


@pytest.mark.skipif(
    __import__("importlib").util.find_spec("cv2") is None,
    reason="opencv-python not installed",
)
def test_build_visual_template_json():
    png = base64.b64decode(_tiny_png_b64())
    out = build_visual_template_json(png, 0, 0, half_size=1)
    obj = json.loads(out)
    assert "png_b64" in obj
    assert obj.get("anchor_x") == 0


def test_parse_tap_coordinates_from_mobile_spec():
    step = {
        "strategy": "viewport_coord",
        "selector_value": "10,20",
        "mobile_spec": {"tap_x": 10, "tap_y": 20},
    }
    assert parse_tap_coordinates(step) == (10, 20)
