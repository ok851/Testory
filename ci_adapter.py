# -*- coding: utf-8 -*-
"""CI/CD 适配：运行记录、门禁聚合、JUnit XML（Phase 0c）。"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional

from auth_batch_helpers import is_execution_gate_success

_LOCK = threading.RLock()
_RUNS: Dict[str, Dict[str, Any]] = {}


def _data_dir() -> Path:
    env = (os.environ.get("UAT_DATA_DIR") or "").strip()
    if env:
        root = Path(env).expanduser().resolve()
    else:
        root = Path(__file__).resolve().parent / "data"
    d = root / "ci_runs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _persist_path(run_id: str) -> Path:
    safe = "".join(c for c in str(run_id) if c.isalnum() or c in ("-", "_"))[:64]
    return _data_dir() / f"{safe}.json"


def normalize_ci_case_status(status: Any) -> str:
    """映射为 JUnit/门禁用状态：passed | failed | error。

    与执行标准一致：仅 success 为 passed；warning/skipped/stopped 一律 failed（红灯）。
    """
    s = str(status or "").strip().lower()
    if s in ("success", "ok", "passed", "pass"):
        return "passed"
    if s in ("error", "failed", "fail", "exception"):
        return "error"
    # warning / skipped / stopped / unknown → 门禁失败（勿用 skipped 以免 CI 误绿）
    return "failed"


def extract_case_rows(batch_results: Any) -> List[Dict[str, Any]]:
    """从 sync_execute_multiple_test_cases 结果提取用例行。"""
    rows: List[Dict[str, Any]] = []
    if not isinstance(batch_results, dict):
        return rows
    for item in batch_results.get("case_results") or []:
        if not isinstance(item, dict):
            continue
        st = item.get("status")
        # 部分路径把步骤列表塞在 results 里，聚合状态在 status
        rows.append({
            "case_id": item.get("case_id"),
            "case_name": item.get("case_name") or item.get("name") or f"case-{item.get('case_id')}",
            "status": st,
            "ci_status": normalize_ci_case_status(st),
            "error": item.get("error") or item.get("message") or "",
            "elapsed_ms": item.get("elapsed_ms") or item.get("duration_ms") or 0,
            "gate_passed": is_execution_gate_success(st),
        })
    return rows


def aggregate_run_status(case_rows: List[Dict[str, Any]], *, batch_error: str = "") -> str:
    """整次 CI run 终态：success | failed。"""
    if batch_error:
        return "failed"
    if not case_rows:
        return "failed"
    if all(r.get("gate_passed") for r in case_rows):
        return "success"
    return "failed"


def build_junit_xml(
    case_rows: List[Dict[str, Any]],
    *,
    suite_name: str = "Testory",
    build_id: str = "",
) -> str:
    """生成 JUnit XML；failures/errors 与门禁失败数一致。"""
    tests = len(case_rows)
    failures = sum(1 for r in case_rows if r.get("ci_status") == "failed")
    errors = sum(1 for r in case_rows if r.get("ci_status") == "error")
    # 无用例：显式 1 error，避免空 suite 被当成绿
    if tests == 0:
        tests, errors = 1, 1
        case_rows = [{
            "case_id": 0,
            "case_name": "no_cases",
            "ci_status": "error",
            "error": "CI run 没有可执行用例",
            "elapsed_ms": 0,
            "gate_passed": False,
        }]

    suite = ET.Element(
        "testsuite",
        {
            "name": suite_name,
            "tests": str(tests),
            "failures": str(failures),
            "errors": str(errors),
            "skipped": "0",
        },
    )
    if build_id:
        suite.set("build_id", str(build_id))

    for row in case_rows:
        name = str(row.get("case_name") or f"case-{row.get('case_id')}")
        classname = f"testory.case_{row.get('case_id') or 'unknown'}"
        try:
            time_s = float(row.get("elapsed_ms") or 0) / 1000.0
        except (TypeError, ValueError):
            time_s = 0.0
        tc = ET.SubElement(
            suite,
            "testcase",
            {
                "classname": classname,
                "name": name,
                "time": f"{time_s:.3f}",
            },
        )
        ci_st = row.get("ci_status") or normalize_ci_case_status(row.get("status"))
        if ci_st == "passed":
            continue
        msg = str(row.get("error") or f"status={row.get('status')}")
        tag = "error" if ci_st == "error" else "failure"
        node = ET.SubElement(tc, tag, {"message": msg[:500]})
        node.text = msg

    # 声明 + 美化
    rough = ET.tostring(suite, encoding="unicode")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + rough + "\n"


def junit_counts(xml_text: str) -> Dict[str, int]:
    """解析 JUnit 计数（单测用）。"""
    root = ET.fromstring(xml_text)
    if root.tag == "testsuites":
        suite = root.find("testsuite")
        if suite is None:
            suite = root
    else:
        suite = root
    return {
        "tests": int(suite.get("tests") or 0),
        "failures": int(suite.get("failures") or 0),
        "errors": int(suite.get("errors") or 0),
        "skipped": int(suite.get("skipped") or 0),
    }


def new_run_id() -> str:
    return f"ci_{uuid.uuid4().hex[:16]}"


def save_run(record: Dict[str, Any]) -> Dict[str, Any]:
    rid = str(record.get("run_id") or "")
    if not rid:
        raise ValueError("run_id required")
    with _LOCK:
        _RUNS[rid] = dict(record)
        try:
            _persist_path(rid).write_text(
                json.dumps(record, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass
    return record


def get_run(run_id: str) -> Optional[Dict[str, Any]]:
    rid = str(run_id or "").strip()
    if not rid:
        return None
    with _LOCK:
        if rid in _RUNS:
            return dict(_RUNS[rid])
    path = _persist_path(rid)
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                with _LOCK:
                    _RUNS[rid] = data
                return dict(data)
        except (json.JSONDecodeError, OSError):
            return None
    return None


def build_run_record_from_batch(
    batch_results: Dict[str, Any],
    *,
    run_id: str = "",
    project_id: Any = None,
    case_ids: Optional[List[Any]] = None,
    trigger_source: str = "manual",
    build_id: str = "",
    git_sha: str = "",
    branch: str = "",
    suite_name: str = "Testory",
    callback_url: str = "",
) -> Dict[str, Any]:
    """由批量执行结果构造 CI run 记录（含 JUnit）。"""
    rid = run_id or new_run_id()
    prev = get_run(rid) if run_id else None
    rows = extract_case_rows(batch_results if isinstance(batch_results, dict) else {})
    batch_err = ""
    if isinstance(batch_results, dict):
        batch_err = str(batch_results.get("error") or "")
    status = aggregate_run_status(rows, batch_error=batch_err)
    junit = build_junit_xml(rows, suite_name=suite_name, build_id=build_id)
    passed = sum(1 for r in rows if r.get("gate_passed"))
    failed = len(rows) - passed
    if not rows and batch_err:
        failed = 1
    cb = (callback_url or "").strip() or (
        str((prev or {}).get("callback_url") or "").strip()
    )
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    record = {
        "run_id": rid,
        "status": status,
        "gate_passed": status == "success",
        "success": status == "success",  # CI 门禁字段：仅 success 为绿
        "project_id": project_id if project_id is not None else (prev or {}).get("project_id"),
        "case_ids": list(case_ids or (prev or {}).get("case_ids") or []),
        "trigger_source": trigger_source or (prev or {}).get("trigger_source") or "manual",
        "build_id": build_id or (prev or {}).get("build_id") or "",
        "git_sha": git_sha or (prev or {}).get("git_sha") or "",
        "branch": branch or (prev or {}).get("branch") or "",
        "suite_name": suite_name or (prev or {}).get("suite_name") or "Testory",
        "passed": passed,
        "failed": failed,
        "total": len(rows),
        "cases": rows,
        "batch_error": batch_err or None,
        "junit_xml": junit,
        "created_at": (prev or {}).get("created_at") or now,
        "started_at": (prev or {}).get("started_at"),
        "finished_at": now,
        "callback_url": cb,
        "callback_status": (prev or {}).get("callback_status"),
        "poll_url": f"/api/ci/runs/{rid}",
        "junit_url": f"/api/ci/runs/{rid}/junit.xml",
        "report_url": f"/api/ci/runs/{rid}",
        "async": bool((prev or {}).get("async")),
    }
    return save_run(record)


def public_run_view(record: Dict[str, Any]) -> Dict[str, Any]:
    """对外 JSON（不含整份 junit 正文，避免撑爆响应）。"""
    if not isinstance(record, dict):
        return {}
    out = {k: v for k, v in record.items() if k != "junit_xml"}
    out["has_junit"] = bool(record.get("junit_xml"))
    out["terminal"] = is_terminal_status(record.get("status"))
    return out


_TERMINAL = frozenset({"success", "failed", "cancelled", "error"})


def is_terminal_status(status: Any) -> bool:
    return str(status or "").strip().lower() in _TERMINAL


def create_queued_run(
    *,
    project_id: Any = None,
    case_ids: Optional[List[Any]] = None,
    trigger_source: str = "manual",
    build_id: str = "",
    git_sha: str = "",
    branch: str = "",
    suite_name: str = "Testory",
    callback_url: str = "",
) -> Dict[str, Any]:
    """创建 queued 状态的 CI run，供异步执行。"""
    rid = new_run_id()
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    record = {
        "run_id": rid,
        "status": "queued",
        "gate_passed": False,
        "success": False,
        "project_id": project_id,
        "case_ids": list(case_ids or []),
        "trigger_source": trigger_source or "manual",
        "build_id": build_id or "",
        "git_sha": git_sha or "",
        "branch": branch or "",
        "suite_name": suite_name or "Testory",
        "passed": 0,
        "failed": 0,
        "total": len(case_ids or []),
        "cases": [],
        "batch_error": None,
        "junit_xml": "",
        "created_at": now,
        "started_at": None,
        "finished_at": None,
        "callback_url": (callback_url or "").strip(),
        "callback_status": None,
        "poll_url": f"/api/ci/runs/{rid}",
        "junit_url": f"/api/ci/runs/{rid}/junit.xml",
        "report_url": f"/api/ci/runs/{rid}",
        "async": True,
    }
    return save_run(record)


def update_run_fields(run_id: str, **fields: Any) -> Optional[Dict[str, Any]]:
    rec = get_run(run_id)
    if not rec:
        return None
    rec.update(fields)
    return save_run(rec)


def mark_run_running(run_id: str) -> Optional[Dict[str, Any]]:
    return update_run_fields(
        run_id,
        status="running",
        started_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    )


def finalize_run_from_batch(
    run_id: str,
    batch_results: Dict[str, Any],
    *,
    suite_name: str = "",
) -> Optional[Dict[str, Any]]:
    """将已有 queued/running 记录更新为终态 + JUnit。"""
    existing = get_run(run_id)
    if not existing:
        return None
    rows = extract_case_rows(batch_results if isinstance(batch_results, dict) else {})
    batch_err = ""
    if isinstance(batch_results, dict):
        batch_err = str(batch_results.get("error") or "")
    status = aggregate_run_status(rows, batch_error=batch_err)
    name = suite_name or existing.get("suite_name") or "Testory"
    junit = build_junit_xml(
        rows, suite_name=str(name), build_id=str(existing.get("build_id") or "")
    )
    passed = sum(1 for r in rows if r.get("gate_passed"))
    failed = len(rows) - passed
    if not rows and batch_err:
        failed = 1
    existing.update({
        "status": status,
        "gate_passed": status == "success",
        "success": status == "success",
        "passed": passed,
        "failed": failed,
        "total": len(rows),
        "cases": rows,
        "batch_error": batch_err or None,
        "junit_xml": junit,
        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    })
    return save_run(existing)


def build_callback_payload(record: Dict[str, Any]) -> Dict[str, Any]:
    """标准化 CI 结束回调体（不含 Token / junit 全文）。"""
    return {
        "run_id": record.get("run_id"),
        "status": record.get("status"),
        "success": bool(record.get("gate_passed")),
        "gate_passed": bool(record.get("gate_passed")),
        "build_id": record.get("build_id") or None,
        "git_sha": record.get("git_sha") or None,
        "branch": record.get("branch") or None,
        "trigger_source": record.get("trigger_source"),
        "passed": record.get("passed"),
        "failed": record.get("failed"),
        "total": record.get("total"),
        "poll_url": record.get("poll_url"),
        "junit_url": record.get("junit_url"),
        "report_url": record.get("report_url"),
        "finished_at": record.get("finished_at"),
    }


def post_ci_webhook(
    callback_url: str,
    record: Dict[str, Any],
    *,
    timeout_s: float = 10.0,
) -> Dict[str, Any]:
    """POST 回调；失败不抛到执行主路径。返回 {ok, status_code, error}。"""
    url = (callback_url or "").strip()
    result: Dict[str, Any] = {"ok": False, "status_code": None, "error": None}
    if not url:
        result["error"] = "empty_url"
        return result
    if not (url.startswith("http://") or url.startswith("https://")):
        result["error"] = "invalid_url_scheme"
        return result
    payload = build_callback_payload(record)
    try:
        import requests

        resp = requests.post(
            url,
            json=payload,
            headers={"Content-Type": "application/json", "User-Agent": "Testory-CI/0c2"},
            timeout=max(1.0, float(timeout_s)),
        )
        result["status_code"] = resp.status_code
        result["ok"] = 200 <= int(resp.status_code) < 300
        if not result["ok"]:
            result["error"] = f"http_{resp.status_code}"
    except Exception as e:
        result["error"] = str(e)[:200]
    return result


def deliver_run_callback(run_id: str) -> Optional[Dict[str, Any]]:
    """若 run 配置了 callback_url 则投递并写回 callback_status。"""
    rec = get_run(run_id)
    if not rec:
        return None
    url = str(rec.get("callback_url") or "").strip()
    if not url:
        return rec
    if not is_terminal_status(rec.get("status")):
        return rec
    cb = post_ci_webhook(url, rec)
    return update_run_fields(
        run_id,
        callback_status="ok" if cb.get("ok") else "failed",
        callback_detail=cb,
    )
