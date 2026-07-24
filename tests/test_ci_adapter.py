# -*- coding: utf-8 -*-
"""Phase 0c：CI 门禁映射与 JUnit 导出。"""

from ci_adapter import (
    aggregate_run_status,
    build_junit_xml,
    build_run_record_from_batch,
    extract_case_rows,
    get_run,
    junit_counts,
    normalize_ci_case_status,
)


def test_normalize_only_success_is_passed():
    assert normalize_ci_case_status("success") == "passed"
    assert normalize_ci_case_status("warning") == "failed"
    assert normalize_ci_case_status("skipped") == "failed"
    assert normalize_ci_case_status("stopped") == "failed"
    assert normalize_ci_case_status("error") == "error"


def test_junit_failures_match_gate():
    rows = extract_case_rows({
        "case_results": [
            {"case_id": 1, "case_name": "ok", "status": "success", "elapsed_ms": 100},
            {"case_id": 2, "case_name": "warn", "status": "warning", "error": "soft"},
            {"case_id": 3, "case_name": "bad", "status": "error", "error": "boom"},
        ]
    })
    xml = build_junit_xml(rows, suite_name="T", build_id="B1")
    counts = junit_counts(xml)
    assert counts["tests"] == 3
    assert counts["failures"] == 1  # warning
    assert counts["errors"] == 1    # error
    assert counts["skipped"] == 0
    assert "warn" in xml and "bad" in xml


def test_empty_cases_junit_is_red():
    xml = build_junit_xml([], suite_name="empty")
    counts = junit_counts(xml)
    assert counts["tests"] == 1
    assert counts["errors"] == 1
    assert aggregate_run_status([]) == "failed"


def test_build_and_get_run(tmp_path, monkeypatch):
    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path))
    batch = {
        "case_results": [
            {"case_id": 10, "case_name": "A", "status": "success"},
            {"case_id": 11, "case_name": "B", "status": "warning", "error": "w"},
        ]
    }
    rec = build_run_record_from_batch(
        batch,
        project_id=7,
        case_ids=[10, 11],
        trigger_source="jenkins",
        build_id="42",
        git_sha="abc",
    )
    assert rec["status"] == "failed"
    assert rec["gate_passed"] is False
    assert rec["success"] is False
    assert rec["failed"] == 1
    assert rec["passed"] == 1
    assert rec["build_id"] == "42"
    assert "junit.xml" in rec["junit_url"]

    got = get_run(rec["run_id"])
    assert got is not None
    assert got["build_id"] == "42"
    counts = junit_counts(got["junit_xml"])
    assert counts["failures"] == 1


def test_all_success_run_green(tmp_path, monkeypatch):
    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path))
    rec = build_run_record_from_batch(
        {
            "case_results": [
                {"case_id": 1, "case_name": "a", "status": "success"},
                {"case_id": 2, "case_name": "b", "status": "success"},
            ]
        },
        build_id="99",
    )
    assert rec["status"] == "success"
    assert rec["gate_passed"] is True
    assert junit_counts(rec["junit_xml"])["failures"] == 0
    assert junit_counts(rec["junit_xml"])["errors"] == 0


def test_queued_finalize_and_terminal(tmp_path, monkeypatch):
    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path))
    from ci_adapter import (
        create_queued_run,
        finalize_run_from_batch,
        is_terminal_status,
        mark_run_running,
    )

    q = create_queued_run(case_ids=[1, 2], build_id="b9", callback_url="https://example.test/hook")
    assert q["status"] == "queued"
    assert is_terminal_status(q["status"]) is False
    mark_run_running(q["run_id"])
    fin = finalize_run_from_batch(
        q["run_id"],
        {
            "case_results": [
                {"case_id": 1, "case_name": "a", "status": "success"},
                {"case_id": 2, "case_name": "b", "status": "success"},
            ]
        },
    )
    assert fin["status"] == "success"
    assert fin["gate_passed"] is True
    assert is_terminal_status(fin["status"]) is True
    assert fin.get("finished_at")


def test_webhook_payload_and_delivery(tmp_path, monkeypatch):
    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path))
    from ci_adapter import (
        build_callback_payload,
        build_run_record_from_batch,
        deliver_run_callback,
        post_ci_webhook,
    )

    rec = build_run_record_from_batch(
        {"case_results": [{"case_id": 1, "case_name": "a", "status": "error", "error": "x"}]},
        build_id="77",
        trigger_source="jenkins",
    )
    payload = build_callback_payload(rec)
    assert payload["build_id"] == "77"
    assert payload["success"] is False
    assert "junit_xml" not in payload

    # invalid scheme
    bad = post_ci_webhook("ftp://nope", rec)
    assert bad["ok"] is False

    calls = {}

    class _Resp:
        status_code = 204

    def _fake_post(url, json=None, headers=None, timeout=None):
        calls["url"] = url
        calls["json"] = json
        return _Resp()

    monkeypatch.setattr("requests.post", _fake_post)
    from ci_adapter import update_run_fields

    update_run_fields(rec["run_id"], callback_url="https://hooks.example/ci")
    deliver_run_callback(rec["run_id"])
    assert calls["url"] == "https://hooks.example/ci"
    assert calls["json"]["run_id"] == rec["run_id"]
    from ci_adapter import get_run

    assert get_run(rec["run_id"])["callback_status"] == "ok"


def test_webhook_http_error_marks_failed(tmp_path, monkeypatch):
    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path))
    from ci_adapter import build_run_record_from_batch, deliver_run_callback, update_run_fields

    rec = build_run_record_from_batch(
        {"case_results": [{"case_id": 1, "case_name": "a", "status": "success"}]},
    )
    update_run_fields(rec["run_id"], callback_url="https://hooks.example/ci")

    class _Resp:
        status_code = 500

    monkeypatch.setattr("requests.post", lambda *a, **k: _Resp())
    deliver_run_callback(rec["run_id"])
    from ci_adapter import get_run

    assert get_run(rec["run_id"])["callback_status"] == "failed"
