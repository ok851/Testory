# -*- coding: utf-8 -*-
"""Phase 0a-2：假绿发射点守卫（空 assert / Hermes 默认失败）。"""

from auth_batch_helpers import assert_empty_expected_error, step_allows_skip
from ai_modules.execute.hermes_stage_executor import _parse_hermes_result


def test_assert_empty_expected_fails_for_text_types():
    assert assert_empty_expected_error("text_contains", "") 
    assert assert_empty_expected_error("url_equals", "   ")
    assert assert_empty_expected_error("page_text_regex", None)
    assert assert_empty_expected_error("text_contains", "ok") is None
    assert assert_empty_expected_error("element_exists", "") is None


def test_step_allows_skip_flags():
    assert step_allows_skip({"allow_skip": True})
    assert step_allows_skip({"optional": "yes"})
    assert step_allows_skip({}, {"skip_ok": 1})
    assert not step_allows_skip({"action": "navigate"})


def test_hermes_parse_default_fail_without_result_ok():
    r = _parse_hermes_result("已完成点击登录按钮")
    assert r["ok_assert"] is False
    assert "默认失败" in (r.get("error") or "")


def test_hermes_parse_explicit_ok():
    r = _parse_hermes_result("done\n[RESULT] ok 登录成功")
    assert r["ok_assert"] is True


def test_hermes_parse_explicit_fail():
    r = _parse_hermes_result("boom\n[RESULT] fail 找不到按钮")
    assert r["ok_assert"] is False


def test_hermes_parse_json_ok_true():
    r = _parse_hermes_result('{"ok": true, "result": "x"}')
    assert r["ok_assert"] is True


def test_hermes_parse_json_missing_ok():
    r = _parse_hermes_result('{"result": "vague"}')
    assert r["ok_assert"] is False


def test_hermes_parse_empty():
    r = _parse_hermes_result("")
    assert r["ok_assert"] is False
