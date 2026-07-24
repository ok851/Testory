# -*- coding: utf-8 -*-
"""批量执行：用例角色排序、登录步骤跳过、运行时变量。"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional, Tuple


_NEGATIVE_LOGIN_CASE_KEYWORDS = (
    "交叉",
    "错误",
    "失败",
    "无效",
    "不正确",
    "留空",
    "为空",
    "空账号",
    "空密码",
    "错误密码",
    "错密码",
    "wrong",
    "invalid",
    "negative",
    "未登录",
)


def login_failure_expected_for_case(
    case_name: str = "",
    step_descriptions: Optional[List[str]] = None,
) -> bool:
    """负向登录用例：故意错填/交叉/空值，登录后仍停在登录页属预期，不应在点击步失败。"""
    blob = f"{case_name or ''} " + " ".join(step_descriptions or [])
    if not blob.strip():
        return False
    if any(k in blob for k in _NEGATIVE_LOGIN_CASE_KEYWORDS):
        return True
    for desc in step_descriptions or []:
        d = (desc or "").strip()
        if not d:
            continue
        if re.search(r"账号框.*密码|密码框.*账号", d):
            return True
        if re.search(r"账号.*输入.*密码|密码.*输入.*账号", d):
            return True
    return False


_ASSERT_COMPARE_ALIASES = {
    "equals": "text_equals",
    "contains": "text_contains",
    "regex": "text_regex",
    "text": "text_contains",
    "visible": "element_visible",
    "exist": "element_exists",
    "exists": "element_exists",
    "vision": "vision_contains",
    "vision_assert": "vision_contains",
}

_TEXT_EQUALS_TYPES = frozenset(
    {"text_equals", "page_text_equals", "equals"}
)
_TEXT_CONTAINS_TYPES = frozenset(
    {"text_contains", "page_text_contains", "contains"}
)


def page_text_has_exact_snippet(page_text: str, expected: str) -> bool:
    """
    文本相等（整页/无 selector）：页面中须存在与预期完全一致的可见片段（整页或独立一行），
    不允许「预期是更长文案的真子串」式误通过（如 预期「请输入您的」≠ 页面「请输入您的密码」）。
    """
    exp = (expected or "").strip()
    if not exp:
        return True
    blob = (page_text or "").replace("\u00a0", " ")
    if blob.strip() == exp:
        return True
    for line in re.split(r"[\r\n]+", blob):
        if line.strip() == exp:
            return True
    return False


def page_text_assert_matches(page_text: str, expected: str, compare_type: str = "") -> bool:
    """整页文本断言是否通过（equals / contains / regex）。"""
    ct = (compare_type or "").strip().lower()
    exp = (expected or "").strip()
    if not exp:
        return True
    blob = (page_text or "").replace("\u00a0", " ")
    if ct in _TEXT_EQUALS_TYPES:
        return page_text_has_exact_snippet(blob, exp)
    if ct in ("text_regex", "page_text_regex"):
        try:
            return bool(re.search(exp, blob))
        except re.error:
            return False
    if "|" in exp:
        try:
            return bool(re.search(exp, blob))
        except re.error:
            pass
        for part in (p.strip() for p in exp.split("|") if p.strip()):
            if part in blob:
                return True
        return False
    return exp in blob


def normalize_assert_compare_type(
    raw: Optional[str],
    *,
    selector_value: str = "",
    input_value: str = "",
) -> str:
    """将 AI/旧库中的 compare_type 别名规范为平台支持的断言类型。"""
    ct = (raw or "").strip().lower()
    if not ct:
        ct = "text_equals"
    elif ct in _ASSERT_COMPARE_ALIASES:
        ct = _ASSERT_COMPARE_ALIASES[ct]
    sv = (selector_value or "").strip()
    iv = (input_value or "").strip()
    if ct in ("text_equals", "text_contains", "text_regex") and not sv and iv:
        return {
            "text_equals": "page_text_equals",
            "text_contains": "page_text_contains",
            "text_regex": "page_text_regex",
        }.get(ct, "page_text_contains")
    return ct


def normalize_step_assert_fields(step: Dict[str, Any]) -> None:
    """就地修正 assert 步骤的 compare_type（执行前/落库前均可调用）。"""
    if not isinstance(step, dict):
        return
    if (step.get("action") or "").strip().lower() != "assert":
        return
    from ai_step_normalization import repair_single_assert_step_inplace

    repair_single_assert_step_inplace(step)
    sv = str(step.get("selector_value") or "")
    iv = str(step.get("input_value") or "")
    ct = normalize_assert_compare_type(
        step.get("compare_type"),
        selector_value=sv,
        input_value=iv,
    )
    # 登录成功类断言：AI 常写长 XPath 指向菜单/欢迎语，改为整页文本更稳
    if (
        iv
        and sv.startswith("//")
        and ct in ("text_equals", "text_contains")
        and "message" not in sv.lower()
        and "error" not in sv.lower()
    ):
        ct = "page_text_contains"
    step["compare_type"] = ct


def prepare_steps_for_execution(
    steps: List[Dict[str, Any]],
    case_url: str = "",
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """执行前：LIVE 探测修复选择器 + assert 字段归一化。"""
    from ai_page_probe import runtime_repair_steps_with_live_probe

    repaired, warns = runtime_repair_steps_with_live_probe(steps or [], case_url)
    for st in repaired:
        normalize_step_assert_fields(st)
    return repaired, warns


def _case_role(case: Dict[str, Any]) -> str:
    role = (case.get("case_role") or "").strip().lower()
    if role in ("login_feature", "business", "auth_fixture"):
        return role
    desc = (case.get("description") or "")
    if "[role:login_feature]" in desc:
        return "login_feature"
    if "[role:auth_fixture]" in desc:
        return "auth_fixture"
    return "business"


def reorder_case_ids_for_batch(case_ids: List[int], db: Any) -> List[int]:
    """
    批量执行排序：
    - 含 login_feature 时：先跑登录功能用例（各自需干净会话），再 auth_fixture，再 business；
    - 否则：auth_fixture 置顶（仅一条），其余保持相对顺序。
    """
    login_features: List[int] = []
    fixtures: List[int] = []
    business: List[int] = []
    others: List[int] = []
    fixture_seen = False
    for cid in case_ids:
        case = db.get_test_case_v2(cid)
        if not case:
            others.append(cid)
            continue
        role = _case_role(case)
        if role == "login_feature":
            login_features.append(cid)
        elif role == "auth_fixture" and not fixture_seen:
            fixtures.append(cid)
            fixture_seen = True
        elif role == "business":
            business.append(cid)
        else:
            others.append(cid)
    if login_features:
        return login_features + fixtures + business + others
    return fixtures + business + others


def _looks_like_login_block(steps: List[Dict[str, Any]], max_steps: int = 6) -> int:
    """
    返回可跳过的前置登录步数（0 表示不跳过）。
    启发式：开头 navigate + 若干 input/click，且描述含登录/账号/密码等。
    """
    if not steps:
        return 0
    login_kw = ("登录", "login", "账号", "密码", "username", "password", "sign in", "signin")
    n = 0
    for i, st in enumerate(steps[:max_steps]):
        if not isinstance(st, dict):
            break
        action = (st.get("action") or "").strip().lower()
        desc = (st.get("description") or "").lower()
        if action == "navigate":
            n = i + 1
            continue
        if action in ("input", "fill") and any(k in desc for k in login_kw):
            n = i + 1
            continue
        if action == "click" and i < 5 and n > 0:
            n = i + 1
            continue
        break
    if n >= 2:
        return n
    return 0


def maybe_strip_duplicate_login_steps(
    execution_steps: List[Dict[str, Any]],
    *,
    case_role: str,
    session_ready: bool,
    skip_enabled: bool,
) -> Tuple[List[Dict[str, Any]], int]:
    """business 用例在已有会话时跳过开头登录块。返回 (steps, skipped_count)。"""
    if not skip_enabled or not session_ready:
        return execution_steps, 0
    if case_role != "business":
        return execution_steps, 0
    cut = _looks_like_login_block(execution_steps)
    if cut <= 0:
        return execution_steps, 0
    return execution_steps[cut:], cut


def merge_runtime_from_project(db: Any, project_id: Optional[int], runtime: Dict[str, str]) -> None:
    """将项目级变量并入运行时池（不覆盖已有键）。"""
    if not project_id:
        return
    try:
        for v in db.get_variables(scope="project", project_id=project_id):
            name = (v.get("name") or "").strip()
            if name and name not in runtime:
                runtime[name] = str(v.get("value") or "")
    except Exception:
        pass


def resolve_execution_steps_variables(
    steps: List[Dict[str, Any]],
    db: Any,
    project_id: Optional[int],
    case_id: int,
    runtime: Dict[str, str],
) -> None:
    """解析批量执行步骤中的 {{var}} 占位符。"""
    for st in steps:
        if not isinstance(st, dict):
            continue
        for key in ("url", "text", "batch_text", "key", "input_value"):
            if key in st and st[key]:
                st[key] = db.resolve_variables(
                    str(st[key]),
                    project_id=project_id,
                    case_id=case_id,
                    runtime_overlay=runtime,
                )
        if st.get("description"):
            st["description"] = db.resolve_variables(
                str(st["description"]),
                project_id=project_id,
                case_id=case_id,
                runtime_overlay=runtime,
            )


def mark_session_ready_after_case(
    case_info: Dict[str, Any],
    case_status: str,
    runtime_vars: Dict[str, str],
) -> bool:
    """登录功能或 fixture 用例成功后标记会话可用。"""
    if case_status != "success":
        return False
    role = _case_role(case_info)
    if role in ("login_feature", "auth_fixture"):
        runtime_vars["session_ready"] = "1"
        return True
    if runtime_vars.get("auth_token"):
        runtime_vars["session_ready"] = "1"
        return True
    return bool(runtime_vars.get("session_ready") == "1")


def summarize_batch_case_error(
    case_results: List[Dict[str, Any]],
    *,
    total_steps: int = 0,
    steps_completed: int = 0,
) -> str:
    """从步骤结果中提取可读的失败原因（写入 run_history.error）。"""
    if int(total_steps or 0) <= 0:
        return "用例无有效步骤（空用例不得判定为成功）"

    if not case_results:
        if total_steps > 0 and steps_completed < total_steps:
            return (
                f"用例未完成：已执行 {steps_completed}/{total_steps} 个步骤"
                "（可能被并发任务打断或浏览器被关闭，请避免同时运行多个用例）"
            )
        return "用例未产生任何步骤结果"

    for r in case_results:
        if not isinstance(r, dict):
            continue
        st = _norm_exec_status(r.get("status"))
        if st not in ("error", "stopped", "failed", "fail", "skipped", "warning"):
            continue
        step = r.get("step") if isinstance(r.get("step"), dict) else {}
        if st == "skipped" and _step_allows_skip(step, r):
            continue
        action = step.get("action") or r.get("step") or "unknown"
        desc = (step.get("description") or "").strip()
        err = (r.get("error") or st).strip()
        parts = [f"步骤动作: {action}"]
        if desc:
            parts.append(f"描述: {desc}")
        if st == "skipped":
            parts.append(f"结果: 已跳过（未允许 skip，按失败计）: {err}")
        elif st == "warning":
            parts.append(f"结果: 警告（门禁不通过）: {err}")
        else:
            parts.append(f"错误: {err}")
        return " | ".join(parts)

    if total_steps > 0 and steps_completed < total_steps:
        ok = sum(
            1
            for r in case_results
            if isinstance(r, dict) and _norm_exec_status(r.get("status")) == "success"
        )
        return (
            f"用例提前结束：成功步骤 {ok} 个，计划 {total_steps} 个，"
            f"实际执行 {steps_completed} 个（可能被并发任务打断）"
        )
    return "用例执行失败（未捕获到具体错误信息）"


def record_cases_run_rejected(
    db: Any,
    case_ids: List[int],
    reason: str,
    *,
    duration: float = 0.0,
) -> None:
    """并发/锁冲突导致未真正执行时，仍写入运行历史便于追溯。"""
    msg = (reason or "用例未执行").strip()
    dur = round(max(0.0, float(duration or 0.0)), 2)
    for cid in case_ids:
        try:
            db.create_run_history(int(cid), "error", dur, msg, "", "")
        except Exception:
            pass


def batch_worker_timeout_seconds(case_ids: List[int], db: Any = None) -> int:
    """整批 worker 超时：按用例数与步骤数估算，可通过环境变量覆盖。"""
    per_case = int(os.environ.get("UAT_BATCH_TIMEOUT_PER_CASE_SEC", "180") or 180)
    minimum = int(os.environ.get("UAT_BATCH_TIMEOUT_MIN_SEC", "300") or 300)
    maximum = int(os.environ.get("UAT_BATCH_TIMEOUT_MAX_SEC", "7200") or 7200)
    per_step = int(os.environ.get("UAT_BATCH_TIMEOUT_PER_STEP_SEC", "15") or 15)
    n = max(1, len(case_ids))
    step_bonus = 0
    if db:
        for cid in case_ids:
            try:
                steps = db.get_case_steps(int(cid), page=1, page_size=9999) or []
                step_bonus += len(steps) * per_step
            except Exception:
                step_bonus += per_case // 2
    total = n * per_case + step_bonus
    return min(maximum, max(minimum, total))


def finalize_batch_timeout_results(
    db: Any,
    case_ids: List[int],
    snapshot: Dict[str, Any],
    err_str: str,
) -> Dict[str, Any]:
    """Worker 超时后保留已执行用例结果，仅为未跑完的用例补录历史。"""
    snap = dict(snapshot or {})
    case_results: List[Dict[str, Any]] = list(snap.get("case_results") or [])
    finished_ids = set()
    for row in case_results:
        if not isinstance(row, dict):
            continue
        try:
            finished_ids.add(int(row.get("case_id")))
        except (TypeError, ValueError):
            pass
    msg = f"批量执行中断: {(err_str or '执行超时').strip()}"
    failed = int(snap.get("failed_cases") or 0)
    success = int(snap.get("successful_cases") or 0)
    for cid in case_ids:
        try:
            mid = int(cid)
        except (TypeError, ValueError):
            continue
        if mid in finished_ids:
            continue
        try:
            db.create_run_history(mid, "error", 0.0, msg, "", "")
        except Exception:
            pass
        info = db.get_test_case_v2(mid) if db else None
        case_results.append(
            {
                "case_id": mid,
                "case_name": (info or {}).get("name") or "未知",
                "status": "error",
                "error": msg,
            }
        )
        failed += 1
    snap["case_results"] = case_results
    snap["total_cases"] = len(case_ids)
    snap["successful_cases"] = success
    snap["failed_cases"] = failed
    snap["error"] = err_str or "执行超时"
    return snap


def build_batch_lock_fail_results(
    db: Any,
    case_ids: List[int],
    reason: str,
) -> Dict[str, Any]:
    """批量执行未能启动（锁/异常）时：写历史并返回标准 results 结构。"""
    msg = (reason or "批量执行未启动").strip()
    record_cases_run_rejected(db, case_ids, msg)
    case_results = []
    for cid in case_ids:
        info = db.get_test_case_v2(int(cid)) if db else None
        case_results.append(
            {
                "case_id": int(cid),
                "case_name": (info or {}).get("name") or "未知",
                "status": "error",
                "error": msg,
            }
        )
    n = len(case_ids)
    return {
        "total_cases": n,
        "successful_cases": 0,
        "failed_cases": n,
        "case_results": case_results,
        "error": msg,
    }


def _norm_exec_status(value: Any) -> str:
    return (str(value or "")).strip().lower()


def _step_allows_skip(step: Dict[str, Any], result_row: Optional[Dict[str, Any]] = None) -> bool:
    """显式允许跳过的步骤不计入门禁失败（allow_skip / optional / skip_ok）。"""
    for src in (step, result_row):
        if not isinstance(src, dict):
            continue
        for key in ("allow_skip", "optional", "skip_ok"):
            val = src.get(key)
            if val is True or str(val).strip().lower() in ("1", "true", "yes", "y"):
                return True
    return False


def step_allows_skip(step: Dict[str, Any], result_row: Optional[Dict[str, Any]] = None) -> bool:
    """公开别名：步骤是否显式允许跳过。"""
    return _step_allows_skip(step, result_row)


# 需要非空期望值的断言类型（element_exists / element_visible / vision 用 description 除外）
_ASSERT_TYPES_REQUIRE_EXPECTED = frozenset(
    {
        "url_equals",
        "url_contains",
        "text_equals",
        "text_contains",
        "text_regex",
        "page_text_equals",
        "page_text_contains",
        "page_text_regex",
    }
)


def assert_empty_expected_error(compare_type: Any, expected: Any) -> Optional[str]:
    """空期望不得假绿：返回错误文案；不需要期望或期望非空则返回 None。"""
    ctype = (str(compare_type or "")).strip().lower()
    if ctype not in _ASSERT_TYPES_REQUIRE_EXPECTED:
        return None
    if (str(expected or "")).strip():
        return None
    return f"assert 步骤缺少预期值（compare_type={ctype}）"


def is_execution_gate_success(status: Any) -> bool:
    """CI/调度门禁：仅 success 为通过（见 docs/EXECUTION_RELIABILITY_STANDARD.md）。"""
    return _norm_exec_status(status) == "success"


def count_batch_gate_failures(case_results: List[Dict[str, Any]]) -> int:
    """批次用例结果中未通过门禁的数量（含 warning/stopped/error/skipped 等）。"""
    n = 0
    for r in case_results or []:
        if not isinstance(r, dict) or not is_execution_gate_success(r.get("status")):
            n += 1
    return n


def evaluate_batch_case_status(
    case_results: List[Dict[str, Any]],
    *,
    total_steps: int,
    steps_completed: int,
) -> str:
    """判定批量单用例最终状态：success / error / stopped / warning。

    企业流水线门禁：
    - 仅 ``success`` 视为通过；
    - ``stopped`` 保留；
    - 硬失败 / 未允许的 ``skipped`` / 未知状态 → ``error``；
    - 仅有 ``warning``、无硬失败 → ``warning``（展示用；调度/CI 仍不得当成功）；
    - ``total_steps <= 0`` → ``error``（空用例不得绿灯）。
    """
    if int(total_steps or 0) <= 0:
        return "error"

    # 声称有步骤却无任何结果行 → 不可信，不得绿灯
    if not case_results:
        return "error"

    if any(
        isinstance(r, dict) and _norm_exec_status(r.get("status")) == "stopped"
        for r in case_results
    ):
        return "stopped"

    hard_fail = False
    soft_warn = False
    for r in case_results:
        if not isinstance(r, dict):
            hard_fail = True
            continue
        st = _norm_exec_status(r.get("status"))
        if st in ("error", "failed", "fail"):
            hard_fail = True
            continue
        if st == "warning":
            soft_warn = True
            continue
        if st == "skipped":
            step = r.get("step") if isinstance(r.get("step"), dict) else {}
            if _step_allows_skip(step, r):
                continue
            hard_fail = True
            continue
        if st in ("success", "ok", "passed"):
            continue
        # 空 status 或未知状态：保守失败，避免假绿
        hard_fail = True

    if hard_fail:
        return "error"
    if int(steps_completed or 0) < int(total_steps or 0):
        return "error"
    if soft_warn:
        return "warning"
    return "success"
