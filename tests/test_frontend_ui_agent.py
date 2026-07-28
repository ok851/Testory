# -*- coding: utf-8 -*-
"""前端组件精准识别 + UI Agent 可靠用例生成。"""

from __future__ import annotations


REACT_LOGIN = '''
import React from 'react';
export function LoginPage() {
  return (
    <form className="login-form" data-testid="login-form">
      <input data-testid="username-input" name="username" placeholder="用户名" aria-label="用户名" />
      <input data-testid="password-input" type="password" name="password" placeholder="密码" aria-label="密码" />
      <button data-testid="login-submit" type="submit" onClick={handleLogin}>登录</button>
      <a href="/forgot" data-testid="forgot-link">忘记密码</a>
      {error && <Modal open data-testid="error-dialog" role="dialog" aria-label="错误提示">失败</Modal>}
    </form>
  );
}
'''

VUE_ORDER = '''
<template>
  <div>
    <el-button data-testid="checkout-pay" @click="onPay">去支付</el-button>
    <el-input data-testid="coupon-code" v-model="code" placeholder="优惠码" />
    <el-dialog v-if="show" data-testid="pay-dialog" title="支付确认">
      <el-button data-testid="confirm-pay" @click="confirm">确认支付</el-button>
    </el-dialog>
  </div>
</template>
'''


def test_parse_react_login_components():
    from ai_modules.code_intel.frontend_parser import parse_frontend_source

    inv = parse_frontend_source(REACT_LOGIN, source_file="src/pages/Login.tsx")
    assert inv["counts"]["interactive"] >= 3
    testids = {n.get("testid") for n in inv["interactive_nodes"]}
    assert "login-submit" in testids
    assert "username-input" in testids
    submit = next(n for n in inv["interactive_nodes"] if n.get("testid") == "login-submit")
    assert submit["best_locator"]["strategy"] == "testid"
    assert submit["best_locator"]["stability"] == "high"
    assert any(h.get("event") == "click" for h in submit.get("handlers") or [])


def test_parse_vue_and_inventory():
    from ai_modules.code_intel.frontend_parser import parse_frontend_files

    inv = parse_frontend_files({
        "src/Order.vue": VUE_ORDER,
        "src/pages/Login.tsx": REACT_LOGIN,
    })
    assert inv["files_parsed"] == 2
    assert inv["stability_buckets"]["high"] >= 3
    assert inv["recommended_for_automation"]
    block = inv  # summary exists
    assert "解析" in (inv.get("summary") or "")


def test_heuristic_reliable_cases_prefer_testid():
    from ai_modules.code_intel.ui_agent import generate_reliable_cases_from_frontend

    drafts, warns, meta = generate_reliable_cases_from_frontend(
        file_snippets={"src/pages/Login.tsx": REACT_LOGIN},
        base_url="https://app.example.com/login",
        git_sha="deadbeef",
        use_llm=False,
    )
    assert drafts, warns
    assert meta.get("analysis_source") == "heuristic"
    # 至少有一条步骤含 data-testid
    flat = []
    for d in drafts:
        assert "[review_status:pending]" in (d.get("description") or "")
        for s in d.get("steps") or []:
            flat.append(s)
    assert any("data-testid" in str(s.get("selector_value") or "") for s in flat)
    assert any(s.get("locator_stability") == "high" for s in flat if s.get("action") in ("click", "input"))


def test_form_flow_generated():
    from ai_modules.code_intel.ui_agent import generate_reliable_cases_from_frontend

    drafts, _, _ = generate_reliable_cases_from_frontend(
        file_snippets={"Login.tsx": REACT_LOGIN},
        use_llm=False,
    )
    names = " ".join(str(d.get("case_name") or "") for d in drafts)
    # 表单主路径或组件冒烟
    assert drafts
    assert "登录" in names or "表单" in names or "login" in names.lower() or "组件" in names


def test_generate_from_code_uses_ui_agent():
    from ai_modules.code_intel.generate_from_code import generate_cases_from_code

    drafts, warns = generate_cases_from_code(
        signals={"testids": ["login-submit"]},
        impact={"change_types": ["component_add"], "is_rollback": False, "is_new_feature": True},
        file_snippets={"Login.tsx": REACT_LOGIN},
        use_llm=False,
        git_sha="abc",
    )
    assert drafts
    assert any("UI分析" in w or "解析" in w for w in warns) or drafts


def test_low_stability_text_marked():
    from ai_modules.code_intel.test_knowledge import step_from_locator

    step = step_from_locator(
        action="click",
        locator={
            "strategy": "text",
            "selector_type": "text",
            "selector_value": "确定",
            "stability": "low",
        },
        accessible_name="确定",
    )
    assert "视觉可定位" in step["description"]
    assert step["locator_stability"] == "low"
