# -*- coding: utf-8 -*-
from modules.auth.auth_batch_helpers import (
    normalize_assert_compare_type,
    page_text_assert_matches,
    page_text_has_exact_snippet,
)


def test_exact_snippet_rejects_partial_prefix():
    page = "登录\n账号\n密码\n请输入您的密码\n登录"
    assert not page_text_has_exact_snippet(page, "请输入您的")
    assert page_text_has_exact_snippet(page, "请输入您的密码")


def test_page_text_equals_via_normalize_empty_selector():
    ct = normalize_assert_compare_type(
        "text_equals",
        selector_value="",
        input_value="请输入您的",
    )
    assert ct == "page_text_equals"


def test_page_text_assert_equals_vs_contains():
    page = "登录\n请输入您的密码"
    assert not page_text_assert_matches(page, "请输入您的", "page_text_equals")
    assert page_text_assert_matches(page, "请输入您的", "page_text_contains")
    assert page_text_assert_matches(page, "请输入您的密码", "page_text_equals")


def test_normalize_empty_defaults_to_page_text_equals_without_selector():
    assert normalize_assert_compare_type("", selector_value="", input_value="x") == "page_text_equals"
