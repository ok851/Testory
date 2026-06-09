"""toast/message 泛化断言修复与 pipe 预期匹配。"""

from ai_page_probe import (
    page_text_matches_assert_expected,
    repair_message_toast_assert_step_inplace,
    split_assert_expected_alternatives,
)


def test_split_assert_alternatives() -> None:
    assert split_assert_expected_alternatives("错误|不正确|失败") == [
        "错误",
        "不正确",
        "失败",
    ]


def test_page_text_matches_pipe_pattern() -> None:
    assert page_text_matches_assert_expected(
        "账号或密码不正确，请重试",
        "错误|不正确|失败",
        "page_text_regex",
    )


def test_repair_toast_xpath_assert() -> None:
    step = {
        "action": "assert",
        "selector_type": "xpath",
        "selector_value": "//*[contains(@class, 'toast') or contains(@class, 'message')]",
        "input_value": "错误|不正确|失败",
        "compare_type": "text_contains",
        "description": "断言错误提示",
    }
    msg = repair_message_toast_assert_step_inplace(step)
    assert msg
    assert step["compare_type"] == "page_text_regex"
    assert step["selector_value"] == ""
    assert step["input_value"] == "错误|不正确|失败"
