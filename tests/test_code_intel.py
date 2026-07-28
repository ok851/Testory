# -*- coding: utf-8 -*-
"""代码变更感知：信号提取、影响分析、用例匹配、草稿生成、webhook。"""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest


def test_extract_ui_signals_testids_and_routes():
    from ai_modules.code_intel.signals import extract_ui_signals

    diff = '''
+++ b/src/pages/Login.tsx
+ <button data-testid="login-submit" aria-label="登录">登录</button>
+ path: "/login"
+ fetch("/api/auth/login")
'''
    sig = extract_ui_signals(
        diff=diff,
        changed_files=["src/pages/Login.tsx", "src/components/OrderForm.vue"],
    )
    assert "login-submit" in sig["testids"]
    assert "登录" in sig["aria_labels"]
    assert "/login" in sig["routes"]
    assert any("/api/auth/login" in a for a in sig["api_hints"])
    assert "login" in sig["path_tokens"] or "orderform" in sig["path_tokens"]
    assert "react" in sig["frameworks"] or "vue" in sig["frameworks"]


def test_heuristic_impact_and_match(tmp_path, monkeypatch):
    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path))
    from ai_modules.code_intel.signals import extract_ui_signals
    from ai_modules.code_intel.impact import build_change_impact_report
    from ai_modules.code_intel.match_cases import match_cases_to_impact

    files = ["src/pages/Login.tsx"]
    diff = '+ <button data-testid="login-submit">Sign in</button>\n'
    sig = extract_ui_signals(diff=diff, changed_files=files)
    impact = build_change_impact_report(
        diff=diff,
        changed_files=files,
        signals=sig,
        mr_description="feat: update login button",
        use_llm=False,
    )
    assert impact["risk_level"] in ("low", "medium", "high")
    assert "signals" in impact
    assert impact["analysis_source"] == "heuristic"

    cases = [
        {
            "id": 11,
            "name": "登录成功",
            "description": "login-submit 登录按钮",
            "precondition": "",
            "expected_result": "进入首页",
            "url": "/login",
            "unit_name": "登录",
        },
        {
            "id": 22,
            "name": "无关订单列表",
            "description": "查看订单",
            "precondition": "",
            "expected_result": "列表展示",
            "url": "/orders",
            "unit_name": "订单",
        },
    ]
    matched = match_cases_to_impact(cases, impact, min_score=1.5)
    assert 11 in matched["recommended_case_ids"]
    assert 22 not in matched["recommended_case_ids"] or matched["matches"][0]["case_id"] == 11


def test_generate_cases_from_code_heuristic_pending():
    from ai_modules.code_intel.generate_from_code import generate_cases_from_code

    signals = {
        "testids": ["checkout-pay"],
        "aria_labels": [],
        "routes": ["/checkout"],
        "api_hints": [],
        "path_tokens": ["checkout"],
    }
    impact = {
        "change_types": ["component_add"],
        "is_rollback": False,
        "is_new_feature": True,
        "suggested_new_coverage": ["支付冒烟"],
        "may_break_existing_cases": True,
    }
    drafts, warns = generate_cases_from_code(
        signals=signals,
        impact=impact,
        git_sha="abc123deadbeef",
        use_llm=False,
    )
    assert drafts
    assert "[review_status:pending]" in drafts[0]["description"]
    assert "source_commit=abc123deadbeef" in drafts[0]["description"]
    assert any(
        s.get("selector_value") == '[data-testid="checkout-pay"]'
        for s in drafts[0]["steps"]
        if isinstance(s, dict)
    )


def test_rollback_skips_generation():
    from ai_modules.code_intel.generate_from_code import generate_cases_from_code

    drafts, warns = generate_cases_from_code(
        signals={"testids": ["x"]},
        impact={"is_rollback": True, "change_types": ["other"]},
        use_llm=False,
    )
    assert drafts == []
    assert any("回滚" in w for w in warns)


def test_task_store_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path))
    from ai_modules.code_intel.pipeline import enqueue_code_change, process_code_change
    from ai_modules.code_intel.task_store import find_by_git_sha, get_task

    payload = {
        "project_id": None,
        "git_sha": "sha-idem-001",
        "changed_files": ["src/Login.tsx"],
        "diff": '+ data-testid="login-btn"\n',
        "mr_description": "fix login",
        "analyze_only": True,
        "generate_drafts": False,
        "trigger_run": False,
    }
    v1 = enqueue_code_change(payload, use_llm=False, background=False)
    assert v1.get("task_id")
    assert get_task(v1["task_id"])["status"] == "done"

    v2 = enqueue_code_change(payload, use_llm=False, background=False)
    assert v2.get("idempotent_hit") is True
    assert v2["task_id"] == v1["task_id"]
    assert find_by_git_sha("sha-idem-001") is not None


def test_pipeline_match_with_fake_db(tmp_path, monkeypatch):
    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path))
    from ai_modules.code_intel.pipeline import enqueue_code_change

    class FakeDB:
        def get_project_cases(self, project_id, case_type="ui"):
            if case_type != "ui":
                return []
            return [
                {
                    "id": 101,
                    "name": "登录流程",
                    "description": "login-btn 登录",
                    "precondition": "",
                    "expected_result": "ok",
                    "url": "/login",
                    "unit_name": "auth",
                }
            ]

    view = enqueue_code_change(
        {
            "project_id": 1,
            "git_sha": "sha-pipe-002",
            "changed_files": ["src/pages/Login.tsx"],
            "diff": '+ <button data-testid="login-btn">登录</button>\n',
            "mr_description": "update login",
            "analyze_only": True,
        },
        db_factory=lambda: FakeDB(),
        use_llm=False,
        background=False,
    )
    assert view["status"] == "done"
    assert 101 in (view.get("recommended_case_ids") or [])
    assert view.get("impact", {}).get("risk_level")


def test_github_webhook_signature():
    from ai_modules.code_intel.webhooks import (
        normalize_github_event,
        parse_webhook,
        verify_github_signature,
    )

    secret = "whsec_test"
    body = json.dumps({
        "ref": "refs/heads/main",
        "after": "deadbeef",
        "repository": {"full_name": "acme/app"},
        "commits": [{
            "message": "fix ui",
            "added": [],
            "modified": ["src/App.tsx"],
            "removed": [],
        }],
    }).encode("utf-8")
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert verify_github_signature(body, sig, secret)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("TESTORY_GITHUB_WEBHOOK_SECRET", secret)
    try:
        norm, err, code = parse_webhook(
            provider="github",
            headers={"X-Hub-Signature-256": sig, "X-GitHub-Event": "push"},
            body=body,
        )
        assert err is None
        assert code == 200
        assert norm["git_sha"] == "deadbeef"
        assert "src/App.tsx" in norm["changed_files"]
    finally:
        monkeypatch.undo()

    n = normalize_github_event("pull_request", {
        "action": "opened",
        "repository": {"full_name": "acme/app"},
        "pull_request": {
            "title": "PR",
            "body": "desc",
            "html_url": "https://example/pr/1",
            "head": {"ref": "feat", "sha": "abc"},
        },
        "number": 1,
    })
    assert n and n["git_sha"] == "abc"


def test_heal_proposals_from_failed_run():
    from ai_modules.code_intel.heal_bridge import (
        apply_heal_proposal_noop,
        build_heal_proposals_from_run,
        mark_cases_at_risk_meta,
    )

    ci_run = {
        "cases": [
            {
                "case_id": 7,
                "case_name": "登录",
                "ci_status": "failed",
                "gate_passed": False,
                "error": "locator not found",
            },
            {
                "case_id": 8,
                "case_name": "其它",
                "ci_status": "passed",
                "gate_passed": True,
            },
        ]
    }
    props = build_heal_proposals_from_run(
        task_id="cc-1",
        git_sha="sha",
        at_risk_case_ids=[7, 8],
        ci_run=ci_run,
        db=None,
    )
    assert len(props) == 1
    assert props[0]["case_id"] == 7
    assert props[0]["status"] == "pending_review"
    assert props[0]["applied"] is False
    ack = apply_heal_proposal_noop(props[0])
    assert ack["applied"] is False
    meta = mark_cases_at_risk_meta({"risk_level": "high", "may_break_existing_cases": True}, [7])
    assert meta["auto_write_forbidden"] is True
