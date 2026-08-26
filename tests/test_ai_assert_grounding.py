# -*- coding: utf-8 -*-
from modules.ai.ai_page_probe import (
    apply_ai_assert_grounding_to_plan,
    extract_assert_expected_fragments,
    _ground_single_assert_step,
    _pick_present_fragment,
)


def test_extract_assert_expected_fragments_from_xpath():
    step = {
        "action": "assert",
        "input_value": "true",
        "selector_value": "//div[contains(text(), '密码错误') or contains(text(), '用户名或密码错误')]",
        "compare_type": "text_contains",
    }
    frags = extract_assert_expected_fragments(step)
    assert "密码错误" in frags
    assert "用户名或密码错误" in frags


def test_pick_present_fragment():
    page = "登录失败：账号不能为空，请重新输入"
    assert _pick_present_fragment(page, ["账号不能为空", "密码错误"]) == "账号不能为空"


def test_ground_assert_to_page_text_contains():
    step = {
        "action": "assert",
        "selector_type": "xpath",
        "selector_value": "//div[contains(text(), '欢迎')]",
        "input_value": "欢迎",
        "compare_type": "text_contains",
    }
    page_text = "首页 欢迎 admin 退出"
    grounded, warns = _ground_single_assert_step(
        None,
        step,
        5,
        page_text=page_text,
        page_url="https://example.com/home",
    )
    assert grounded["compare_type"] == "page_text_contains"
    assert grounded["input_value"] == "欢迎"
    assert grounded["selector_value"] == ""
    assert any("page_text_contains" in w for w in warns)


def test_normalize_contains_alias():
    from modules.auth.auth_batch_helpers import normalize_assert_compare_type

    assert normalize_assert_compare_type("contains", selector_value="#x", input_value="err") == "text_contains"
    assert (
        normalize_assert_compare_type("contains", selector_value="", input_value="账号不能为空")
        == "page_text_contains"
    )


def test_ground_wrong_page_text_to_actual_hint():
    """LLM 臆造「密码不能为空」时，应改为页面上真实的「请输入您的密码」。"""
    step = {
        "action": "assert",
        "compare_type": "page_text_contains",
        "selector_type": "",
        "selector_value": "",
        "input_value": "密码不能为空",
        "description": "断言错误提示包含密码相关文案",
    }
    page_text = "登录\n账号\n密码\n请输入您的密码\n登录"
    grounded, warns = _ground_single_assert_step(
        None,
        step,
        5,
        page_text=page_text,
        page_url="https://kol-test.xunku.org/phone/login",
    )
    assert grounded["input_value"] == "请输入您的密码"
    assert any("页面实测修正" in w for w in warns)


def test_apply_ai_assert_grounding_skips_duplicate():
    plan = {
        "case_url": "https://example.com/",
        "steps": [{"action": "wait", "input_value": "1"}],
        "meta": {"assert_grounding_applied": True},
    }
    out, warns = apply_ai_assert_grounding_to_plan(plan, ["keep"])
    assert out is plan
    assert warns == ["keep"]
