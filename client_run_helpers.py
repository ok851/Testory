# -*- coding: utf-8 -*-
"""客户端模式下的用例加载与运行结果同步。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from client_config_store import is_setup_complete
from deployment_config import is_client_mode


def should_use_team_server_data() -> bool:
    return is_client_mode() and is_setup_complete()


def load_case_and_steps(case_id: int, db) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    if should_use_team_server_data():
        from team_server_client import TeamServerError, request_json

        try:
            case_data, _ = request_json("GET", f"/api/cases/{case_id}")
            steps_data, _ = request_json("GET", f"/api/cases/{case_id}/steps")
            case = case_data.get("case") or case_data
            steps = steps_data.get("steps") or steps_data.get("data") or []
            if isinstance(steps, dict):
                steps = steps.get("steps") or []
            return case, steps
        except TeamServerError:
            return db.get_test_case_v2(case_id), db.get_case_steps(case_id)
    return db.get_test_case_v2(case_id), db.get_case_steps(case_id)


def sync_run_to_team_server(
    case_id: int,
    status: str,
    duration: float,
    error: str = "",
    extracted_text: str = "",
    expected_text: str = "",
    step_results: Optional[List[Dict[str, Any]]] = None,
    screenshots: Optional[List[str]] = None,
) -> Optional[int]:
    if not should_use_team_server_data():
        return None
    try:
        from team_server_client import report_run_result

        resp = report_run_result(
            case_id,
            status,
            duration,
            error=error,
            extracted_text=extracted_text,
            expected_text=expected_text,
            step_results=step_results,
            screenshots=screenshots,
        )
        return resp.get("run_history_id")
    except Exception:
        return None
