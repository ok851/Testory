"""平台语义修复：URL 断言、过宽 CSS 标签检测。"""

from modules.ai.ai_step_normalization import is_overly_broad_css_selector, repair_raw_ai_steps_for_platform


def test_is_overly_broad_css_selector() -> None:
    assert is_overly_broad_css_selector("button") is True
    assert is_overly_broad_css_selector("#su") is False
    assert is_overly_broad_css_selector("input.search") is False


def test_repair_url_assert_from_text_equals_plus_css() -> None:
    steps = [
        {
            "action": "assert",
            "selector_type": "css",
            "selector_value": "a.title-content.c-link",
            "input_value": "wd=%E8%87%AA%E5%B8%A6%E5%8C%96%E6%B5%8B%E8%AF%95",
            "compare_type": "text_equals",
            "description": "验证搜索结果页URL包含搜索关键词",
        }
    ]
    w = repair_raw_ai_steps_for_platform(steps)
    assert steps[0]["compare_type"] == "url_contains"
    assert steps[0]["selector_value"] == ""
    assert steps[0]["selector_type"] == ""
    assert steps[0]["input_value"] == "自带化测试"
    assert w


def test_repair_clears_selector_on_url_compare() -> None:
    steps = [
        {
            "action": "assert",
            "selector_type": "css",
            "selector_value": "#foo",
            "input_value": "https://x/",
            "compare_type": "url_contains",
            "description": "x",
        }
    ]
    repair_raw_ai_steps_for_platform(steps)
    assert steps[0]["selector_value"] == ""


def test_repair_url_assert_from_description_at_runtime():
    from modules.auth.auth_batch_helpers import normalize_step_assert_fields

    step = {
        "action": "assert",
        "compare_type": "text_equals",
        "selector_type": "",
        "selector_value": "",
        "input_value": "/phone/",
        "description": "断言URL包含系统路径",
    }
    normalize_step_assert_fields(step)
    assert step["compare_type"] == "url_contains"
    assert step["selector_value"] == ""
    assert step["input_value"] == "/phone/"


def test_repair_assert_no_selector_to_page_text() -> None:
    steps = [
        {
            "action": "assert",
            "compare_type": "text_contains",
            "input_value": "支付成功",
            "description": "结果页提示",
        }
    ]
    w = repair_raw_ai_steps_for_platform(steps)
    assert steps[0]["compare_type"] == "page_text_contains"
    assert w
