# -*- coding: utf-8 -*-
"""批量执行：用例角色排序、登录步骤跳过、运行时变量。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


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
    """auth_fixture 排到最前（仅第一条 fixture），其余保持相对顺序。"""
    fixtures: List[int] = []
    others: List[int] = []
    fixture_seen = False
    for cid in case_ids:
        case = db.get_test_case_v2(cid)
        if not case:
            others.append(cid)
            continue
        if _case_role(case) == "auth_fixture" and not fixture_seen:
            fixtures.append(cid)
            fixture_seen = True
        else:
            others.append(cid)
    return fixtures + others


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
