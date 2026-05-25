# -*- coding: utf-8 -*-
"""desktop_visual_picker 与 visual 步骤产物。"""

from desktop_visual_picker import VISUAL_SELECTOR_TYPE, build_visual_recorded_step


def test_build_visual_recorded_step_fields():
    pick = {
        "selector_type": VISUAL_SELECTOR_TYPE,
        "selector_value": '{"template_image_base64":"abc","click_offset":{"x":1,"y":2}}',
        "pick_point": {"x": 357, "y": 635},
    }
    step = build_visual_recorded_step(pick, action="double_click")
    assert step["automation_layer"] == "desktop"
    assert step["selector_type"] == "visual"
    assert step["action"] == "double_click"
    assert "357" in step["description"]
    assert step["locator_candidates"] == []


def test_desktop_picker_available_uses_runtime(monkeypatch):
    import desktop_picker as dp

    monkeypatch.setattr(dp, "_PICKER_AVAILABLE", True)
    monkeypatch.setattr(
        "desktop_runtime.desktop_runtime_available",
        lambda: True,
    )
    assert dp.desktop_picker_available() is True
