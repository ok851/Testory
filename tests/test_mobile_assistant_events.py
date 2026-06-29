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
