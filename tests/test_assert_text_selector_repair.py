# -*- coding: utf-8 -*-
from modules.ai.ai_step_normalization import repair_single_assert_step_inplace


def test_repair_text_selector_page_contains_to_page_text():
    step = {
        "action": "assert",
        "selector_type": "text",
        "selector_value": "订单中心",
        "input_value": "订单中心",
        "compare_type": "text_contains",
        "description": "断言当前页面包含订单中心标题",
    }
    warns = repair_single_assert_step_inplace(step)
    assert step["compare_type"] == "page_text_contains"
    assert step["selector_value"] == ""
    assert step["input_value"] == "订单中心"
    assert any("整页可见文本" in w for w in warns)
