# -*- coding: utf-8 -*-
from modules.mobile.mobile_replay_context import (
    infer_prepare_context,
    is_coordinate_step,
    is_skippable_package,
    sanitize_replay_steps,
    should_skip_open_app_step,
)


def test_skip_launcher_open_app():
    step = {
        "action": "open_app",
        "input_value": "com.android.launcher",
        "mobile_spec": {},
    }
    assert should_skip_open_app_step(step)
    assert sanitize_replay_steps([step]) == []


def test_coordinate_tap_not_require_context():
    step = {
        "action": "tap",
        "selector_type": "viewport_coord",
        "selector_value": '{"x":100,"y":200}',
        "mobile_spec": {"context_package": "com.example.app"},
    }
    assert is_coordinate_step(step)
    pkg, required = infer_prepare_context([step])
    assert pkg == "com.example.app"
    assert required is False


def test_real_open_app_not_skipped():
    step = {
        "action": "open_app",
        "input_value": "com.google.android.deskclock",
        "mobile_spec": {},
    }
    assert not should_skip_open_app_step(step)
    pkg, required = infer_prepare_context([step])
    assert pkg == "com.google.android.deskclock"
    assert required is True


def test_is_skippable_launcher_fragment():
    assert is_skippable_package("com.oneplus.launcher")
    assert not is_skippable_package("com.google.android.deskclock")
