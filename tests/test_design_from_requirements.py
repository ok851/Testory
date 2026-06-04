# -*- coding: utf-8 -*-
"""AI 用例设计模块单元测试（不调用 LLM）。"""

from ai_modules.generate.design_from_requirements import (
    _normalize_case_role,
    _normalize_draft,
    enforce_base_url_on_draft,
)
from auth_batch_helpers import maybe_strip_duplicate_login_steps, reorder_case_ids_for_batch


def test_enforce_base_url():
    d = {
        "case_url": "http://admin.example.com",
        "steps": [{"action": "navigate", "input_value": "http://other.com"}],
    }
    enforce_base_url_on_draft(d, "http://192.168.5.77:8088/")
    assert d["case_url"] == "http://192.168.5.77:8088/"
    assert d["steps"][0]["input_value"] == "http://192.168.5.77:8088/"


def test_normalize_case_role():
    assert _normalize_case_role("login_feature") == "login_feature"
    assert _normalize_case_role("fixture") == "auth_fixture"
    assert _normalize_case_role("") == "business"


def test_normalize_draft_web():
    raw = {
        "case_name": "登录-正确密码",
        "case_role": "login_feature",
        "design_method": "等价类",
        "steps": [{"action": "navigate", "input_value": ""}],
    }
    d = _normalize_draft(raw, "web", "http://192.168.5.77:8088/")
    assert d["case_role"] == "login_feature"
    assert d["steps"][0]["input_value"] == "http://192.168.5.77:8088/"


def test_skip_duplicate_login():
    steps = [
        {"action": "navigate", "description": "打开登录页"},
        {"action": "input", "description": "输入账号"},
        {"action": "input", "description": "输入密码"},
        {"action": "click", "description": "点击登录"},
        {"action": "click", "description": "进入订单"},
    ]
    out, n = maybe_strip_duplicate_login_steps(
        steps, case_role="business", session_ready=True, skip_enabled=True
    )
    assert n >= 2
    assert len(out) < len(steps)


class _FakeDb:
    def get_test_case_v2(self, cid):
        roles = {1: {"case_role": "auth_fixture"}, 2: {"case_role": "business"}}
        return roles.get(cid, {"case_role": "business"})


def test_reorder_fixture_first():
    order = reorder_case_ids_for_batch([2, 1], _FakeDb())
    assert order[0] == 1
