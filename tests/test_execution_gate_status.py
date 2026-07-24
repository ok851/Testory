# -*- coding: utf-8 -*-
"""企业流水线门禁：evaluate_batch_case_status / is_execution_gate_success 回归。"""

from auth_batch_helpers import (
    count_batch_gate_failures,
    evaluate_batch_case_status,
    is_execution_gate_success,
    summarize_batch_case_error,
)


def test_all_success():
    steps = [{"status": "success"}, {"status": "success"}]
    assert evaluate_batch_case_status(steps, total_steps=2, steps_completed=2) == "success"
    assert is_execution_gate_success("success")


def test_hard_error():
    steps = [{"status": "success"}, {"status": "error", "error": "boom", "step": {"action": "click"}}]
    assert evaluate_batch_case_status(steps, total_steps=2, steps_completed=2) == "error"
    assert not is_execution_gate_success("error")


def test_failed_alias():
    steps = [{"status": "Failed"}]
    assert evaluate_batch_case_status(steps, total_steps=1, steps_completed=1) == "error"


def test_stopped():
    steps = [{"status": "success"}, {"status": "stopped"}]
    assert evaluate_batch_case_status(steps, total_steps=2, steps_completed=1) == "stopped"


def test_incomplete_steps():
    steps = [{"status": "success"}]
    assert evaluate_batch_case_status(steps, total_steps=3, steps_completed=1) == "error"


def test_empty_plan_is_error():
    assert evaluate_batch_case_status([], total_steps=0, steps_completed=0) == "error"


def test_no_results_but_planned_is_error():
    assert evaluate_batch_case_status([], total_steps=2, steps_completed=0) == "error"


def test_skipped_without_allow_is_error():
    steps = [
        {"status": "success"},
        {"status": "skipped", "step": {"action": "navigate", "url": ""}},
    ]
    assert evaluate_batch_case_status(steps, total_steps=2, steps_completed=2) == "error"
    msg = summarize_batch_case_error(steps, total_steps=2, steps_completed=2)
    assert "跳过" in msg or "skipped" in msg.lower()


def test_skipped_with_allow_skip_ok():
    steps = [
        {"status": "success"},
        {
            "status": "skipped",
            "step": {"action": "navigate", "allow_skip": True},
        },
    ]
    assert evaluate_batch_case_status(steps, total_steps=2, steps_completed=2) == "success"


def test_skipped_optional_flag_on_result_row():
    steps = [{"status": "skipped", "optional": True, "step": {"action": "wait"}}]
    assert evaluate_batch_case_status(steps, total_steps=1, steps_completed=1) == "success"


def test_warning_only_is_warning_not_success():
    steps = [{"status": "success"}, {"status": "warning", "error": "soft"}]
    assert evaluate_batch_case_status(steps, total_steps=2, steps_completed=2) == "warning"
    assert not is_execution_gate_success("warning")


def test_warning_plus_error_is_error():
    steps = [{"status": "warning"}, {"status": "error"}]
    assert evaluate_batch_case_status(steps, total_steps=2, steps_completed=2) == "error"


def test_unknown_status_is_error():
    steps = [{"status": "weird"}]
    assert evaluate_batch_case_status(steps, total_steps=1, steps_completed=1) == "error"


def test_empty_status_is_error():
    steps = [{"status": ""}]
    assert evaluate_batch_case_status(steps, total_steps=1, steps_completed=1) == "error"


def test_ok_passed_aliases_count_as_success_steps():
    steps = [{"status": "ok"}, {"status": "passed"}]
    assert evaluate_batch_case_status(steps, total_steps=2, steps_completed=2) == "success"


def test_count_batch_gate_failures():
    rows = [
        {"status": "success"},
        {"status": "warning"},
        {"status": "error"},
        {"status": "stopped"},
    ]
    assert count_batch_gate_failures(rows) == 3
    assert count_batch_gate_failures([{"status": "success"}]) == 0


def test_summarize_empty_case():
    assert "无有效步骤" in summarize_batch_case_error([], total_steps=0, steps_completed=0)
