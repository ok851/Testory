"""
批量/调度执行时的用户与租户上下文（执行线程无 Flask current_user，须在入口解析后显式传入）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

# payload 含 case_id, case_name, project_id, status, error, run_history_id, step_results, execution_time，
# 以及回调合并时的 user_id、tenant_id、trigger、extra。
CaseFailureCallback = Callable[[Dict[str, Any]], None]


@dataclass
class ExecutionContext:
    user_id: Optional[int] = None
    tenant_id: Optional[int] = None
    trigger: str = "unknown"
    on_case_failure: Optional[CaseFailureCallback] = None
    extra: Dict[str, Any] = field(default_factory=dict)
    desktop_agent_session_id: Optional[str] = None
    desktop_machine_id: Optional[int] = None
    # 同一次批量执行内共享（如 auth_token）；优先级高于库内同名项目变量
    runtime_vars: Dict[str, str] = field(default_factory=dict)
    reuse_session: bool = True
    skip_duplicate_login_for_business: bool = True
