# -*- coding: utf-8 -*-
"""生产闭环增强：repo 映射、审核门禁、策略截断、TTL、指标、heal 钩子。"""

from __future__ import annotations

import json
import time


def test_repo_map_resolve(tmp_path, monkeypatch):
    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path))
    from ai_modules.code_intel.repo_map import (
        delete_mapping,
        list_mappings,
        resolve_project_id,
        upsert_mapping,
    )

    upsert_mapping(repo="github.com/acme/app", project_id=42, default_branch="main")
    hit = resolve_project_id("https://github.com/acme/app.git", "main")
    assert hit and hit["project_id"] == 42
    assert len(list_mappings()) == 1
    assert delete_mapping("acme/app") is True
    assert resolve_project_id("acme/app") is None


def test_policy_clamp_and_rate(tmp_path, monkeypatch):
    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CODE_INTEL_MAX_DIFF_CHARS", "500")
    monkeypatch.setenv("CODE_INTEL_RATE_LIMIT_PER_MIN", "2")
    from ai_modules.code_intel.policy import check_rate_limit, clamp_payload, check_ip_allowed

    out, warns = clamp_payload({"diff": "x" * 2000, "changed_files": ["a"] * 5})
    assert len(out["diff"]) < 2000
    assert any("截断" in w for w in warns)

    assert check_rate_limit("t1")[0] is True
    assert check_rate_limit("t1")[0] is True
    ok, err = check_rate_limit("t1")
    assert ok is False and err

    monkeypatch.setenv("CODE_INTEL_WEBHOOK_IP_ALLOWLIST", "10.0.0.,127.0.0.1")
    assert check_ip_allowed("10.0.0.8")[0] is True
    assert check_ip_allowed("8.8.8.8")[0] is False


def test_review_filter_and_description():
    from ai_modules.code_intel.review import (
        case_is_ci_eligible,
        ensure_pending_description,
        mark_description_status,
        normalize_review_status,
    )

    desc = ensure_pending_description("冒烟", git_sha="abc")
    assert "[review_status:pending]" in desc
    assert normalize_review_status(None, desc) == "pending"
    assert case_is_ci_eligible({"description": desc}, include_pending=False) is False
    assert case_is_ci_eligible({"review_status": "active"}, include_pending=False) is True
    active = mark_description_status(desc, "active")
    assert "[review_status:active]" in active


def test_enqueue_resolves_repo_map(tmp_path, monkeypatch):
    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CODE_INTEL_USE_LLM", "0")
    from ai_modules.code_intel.repo_map import upsert_mapping
    from ai_modules.code_intel.pipeline import enqueue_code_change

    upsert_mapping(repo="acme/shop", project_id=7)

    class FakeDB:
        def get_project_cases(self, project_id, case_type="ui"):
            return [{
                "id": 1,
                "name": "登录",
                "description": "login",
                "review_status": "active",
                "precondition": "",
                "expected_result": "",
                "url": "/login",
                "unit_name": "",
            }] if case_type == "ui" else []

    view = enqueue_code_change(
        {
            "repo": "acme/shop",
            "git_sha": "sha-map-1",
            "changed_files": ["src/pages/Login.tsx"],
            "diff": '+ data-testid="login-btn"\n',
            "mr_description": "fix",
        },
        db_factory=lambda: FakeDB(),
        use_llm=False,
        background=False,
    )
    assert view.get("project_id") == 7 or "project_id=7" in str(view.get("warnings"))
    assert view["status"] == "done"


def test_pending_excluded_from_recommend(tmp_path, monkeypatch):
    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path))
    from ai_modules.code_intel.match_cases import match_cases_to_impact

    impact = {
        "at_risk_case_hints": ["login"],
        "affected_modules": ["login"],
        "may_break_existing_cases": True,
        "signals": {"testids": ["login-btn"], "path_tokens": ["login"], "routes": []},
    }
    cases = [
        {"id": 1, "name": "登录", "description": "login-btn", "review_status": "pending"},
        {"id": 2, "name": "登录2", "description": "login-btn active", "review_status": "active"},
    ]
    m = match_cases_to_impact(cases, impact, min_score=1.0, use_embeddings=False)
    assert 2 in m["recommended_case_ids"]
    assert 1 not in m["recommended_case_ids"]


def test_cleanup_ttl(tmp_path, monkeypatch):
    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path))
    from ai_modules.code_intel import task_store as ts
    import json

    with ts._LOCK:
        ts._CACHE.clear()
    rec = ts.create_queued_task({"git_sha": "old", "changed_files": ["a.py"], "diff": "x"})
    tid = rec["task_id"]
    aged = dict(rec)
    aged["updated_at"] = "2000-01-01T00:00:00Z"
    aged["created_at"] = "2000-01-01T00:00:00Z"
    ts._path(tid).write_text(json.dumps(aged, ensure_ascii=False), encoding="utf-8")
    with ts._LOCK:
        ts._CACHE.clear()
    result = ts.cleanup_expired_tasks(ttl_days=1)
    assert result["removed"] >= 1
    assert all(r["task_id"] != tid for r in ts.list_tasks(limit=50))


def test_metrics_and_heal_hook(tmp_path, monkeypatch):
    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CODE_INTEL_USE_LLM", "0")
    from ai_modules.code_intel.pipeline import enqueue_code_change, on_ci_run_finished
    from ai_modules.code_intel.metrics import collect_metrics
    from ai_modules.code_intel.task_store import update_task, get_task
    from modules.integration.ci_adapter import save_run

    view = enqueue_code_change(
        {
            "project_id": None,
            "git_sha": "sha-met-1",
            "changed_files": ["a.tsx"],
            "diff": "+x",
            "mr_description": "m",
        },
        use_llm=False,
        background=False,
    )
    tid = view["task_id"]
    update_task(tid, ci_run_id="run-heal-1", at_risk_case_ids=[9])
    save_run({
        "run_id": "run-heal-1",
        "status": "failed",
        "gate_passed": False,
        "cases": [{
            "case_id": 9,
            "case_name": "x",
            "ci_status": "failed",
            "gate_passed": False,
            "error": "missing",
        }],
    })
    out = on_ci_run_finished("run-heal-1")
    assert out and out.get("ok")
    assert get_task(tid).get("heal_proposals")
    m = collect_metrics(limit=50)
    assert m["sample_size"] >= 1


def test_mr_window_dedup(tmp_path, monkeypatch):
    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CODE_INTEL_DEDUP_WINDOW_MIN", "30")
    monkeypatch.setenv("CODE_INTEL_USE_LLM", "0")
    from ai_modules.code_intel.pipeline import enqueue_code_change

    p = {
        "mr_key": "https://gitlab/x/merge_requests/12",
        "changed_files": ["a.ts"],
        "diff": "+1",
        "mr_description": "feat",
        "git_sha": "",
    }
    v1 = enqueue_code_change(p, use_llm=False, background=False)
    v2 = enqueue_code_change({**p, "diff": "+2"}, use_llm=False, background=False)
    assert v2.get("idempotent_hit") is True
    assert v2["task_id"] == v1["task_id"]
