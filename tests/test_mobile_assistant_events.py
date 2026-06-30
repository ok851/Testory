# -*- coding: utf-8 -*-
from mobile_assistant_events import normalize_assistant_event


def test_normalize_includes_context_package():
    step = normalize_assistant_event({
        "type": "click",
        "package": "com.example.app",
        "bounds": [10, 20, 30, 40],
        "node": {"text": "OK"},
    })
    spec = step["mobile_spec"]
    assert spec["context_package"] == "com.example.app"
    assert "app_package" not in spec
    assert step["action"] == "tap"


def test_normalize_open_app():
    step = normalize_assistant_event({
        "type": "open_app",
        "package": "com.example.calc",
        "description": "打开计算器",
    })
    assert step["action"] == "open_app"
    assert step["input_value"] == "com.example.calc"


def test_normalize_swipe_uses_scroll_delta():
    step = normalize_assistant_event({
        "type": "swipe",
        "bounds": [100, 200, 300, 400],
        "scroll_delta_x": 0,
        "scroll_delta_y": -120,
    })
    assert step["action"] == "swipe"
    spec = step["mobile_spec"]
    cx, cy = 200, 300
    assert spec["x1"] == cx
    assert spec["y1"] == cy - (-120)
    assert spec["x2"] == cx
    assert spec["y2"] == cy + (-120)


def test_normalize_swipe_from_payload_endpoints():
    step = normalize_assistant_event({
        "type": "swipe",
        "x1": 10,
        "y1": 20,
        "x2": 10,
        "y2": 500,
    })
    spec = step["mobile_spec"]
    assert spec["x1"] == 10
    assert spec["y2"] == 500
