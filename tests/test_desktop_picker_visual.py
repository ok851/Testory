# -*- coding: utf-8
"""desktop_visual_picker 产物测试。"""

from modules.desktop.desktop_visual_picker import VISUAL_SELECTOR_TYPE, build_visual_recorded_step


def test_visual_step_contract():
    pick = {
        "selector_type": VISUAL_SELECTOR_TYPE,
        "selector_value": (
            '{"template_image_base64":"YWJj","click_offset":{"x":5,"y":6},'
            '"match_threshold":0.8,"match_method":"orb","template_size":{"w":40,"h":40}}'
        ),
        "pick_point": {"x": 100, "y": 200},
        "rectangle": {"left": 80, "top": 180, "right": 120, "bottom": 220},
    }
    step = build_visual_recorded_step(pick, action="double_click")
    assert step["selector_type"] == "visual"
    assert step["action"] == "double_click"
    assert step["automation_layer"] == "desktop"
    assert step["locator_candidates"] == []
    assert "100" in step["description"]
