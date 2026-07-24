# -*- coding: utf-8 -*-
"""Desktop 运行时自愈（Y5 后半）：有限策略、失败不假绿。

当前策略（可扩展）:
- ``attach_window``：过严 ``^标题$`` → 放宽为 contains / best_match
- ``launch_app``：别名重解析（``@erp`` / DESKTOP_APP_ALIASES）

环境变量 ``DESKTOP_RUNTIME_HEAL``（默认 1）。关闭则不做运行时尝试。
成功时在结果写入 ``desktop_heal``；失败仍返回原失败，禁止把 soft-fail 洗成 success。
"""

from __future__ import annotations

import copy
import os
import re
from typing import Any, Callable, Dict, Optional, Tuple

_ANCHOR_RE = re.compile(r"(?is)^\^(?P<body>.+)\$$")
_INLINE_FLAGS_RE = re.compile(r"^\(\?[aiLmsux]+\)")


def _broaden_exact_title_re(tre: str) -> Optional[str]:
    """(?i)^Title$ / ^Title$ → .*Title.*；已是 contains 则不改。"""
    s = (tre or "").strip()
    if not s:
        return None
    s2 = _INLINE_FLAGS_RE.sub("", s).strip()
    m = _ANCHOR_RE.match(s2)
    if not m:
        return None
    body = m.group("body").strip()
    if not body or ".*" in body:
        return None
    return f".*{body}.*"


def desktop_runtime_heal_enabled() -> bool:
    raw = (os.environ.get("DESKTOP_RUNTIME_HEAL") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _spec_of(step: Dict[str, Any]) -> Dict[str, Any]:
    raw = step.get("desktop_spec")
    if isinstance(raw, dict):
        return dict(raw)
    return {}


def _is_desktop_success(result: Any, action: str) -> bool:
    """与跨端闸门一致：仅 success/ok/passed；warning 不算成功。"""
    if not isinstance(result, dict):
        return False
    st = str(result.get("status") or "").strip().lower()
    if st not in ("success", "ok", "passed"):
        return False
    act = (action or "").strip().lower()
    if act in ("click", "dblclick", "double_click", "right_click", "hover", "drag"):
        if not result.get("verified") or not result.get("pointer_executed"):
            return False
    return True


def propose_healed_desktop_step(
    step: Dict[str, Any],
    *,
    failed_result: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    """提出一步自愈候选；无策略时返回 (None, meta)。"""
    if not isinstance(step, dict):
        return None, {"reason": "invalid_step"}
    action = str(step.get("action") or "").strip().lower()
    spec = _spec_of(step)
    meta: Dict[str, Any] = {"action": action, "strategies": []}

    if action == "attach_window":
        tre = str(spec.get("window_title_re") or spec.get("title_re") or "").strip()
        broadened = _broaden_exact_title_re(tre) if tre else None
        if broadened:
            new_step = copy.deepcopy(step)
            new_spec = _spec_of(new_step)
            new_spec["window_title_re"] = broadened
            new_spec["best_match"] = True
            new_spec["_heal_from"] = tre
            new_step["desktop_spec"] = new_spec
            meta["strategies"].append("broaden_window_title_re")
            meta["from_title_re"] = tre
            meta["to_title_re"] = broadened
            return new_step, meta

        # 有精确 window_title 无 re 时补 contains re
        wt = str(spec.get("window_title") or "").strip()
        if wt and not tre:
            new_step = copy.deepcopy(step)
            new_spec = _spec_of(new_step)
            new_spec["window_title_re"] = f".*{re.escape(wt)}.*"
            new_spec["best_match"] = True
            new_step["desktop_spec"] = new_spec
            meta["strategies"].append("title_to_title_re")
            return new_step, meta

        desc = str(step.get("description") or "").strip()
        if desc and not tre and not wt:
            # 弱策略：从描述猜标题关键词（仅当完全无窗口条件）
            hint = desc[:48]
            new_step = copy.deepcopy(step)
            new_spec = _spec_of(new_step)
            new_spec["title_contains"] = hint
            new_spec["window_title_re"] = f".*{re.escape(hint)}.*"
            new_spec["best_match"] = True
            new_step["desktop_spec"] = new_spec
            meta["strategies"].append("description_title_hint")
            return new_step, meta

        return None, {**meta, "reason": "no_attach_strategy"}

    if action == "launch_app":
        alias = str(spec.get("alias") or step.get("input_value") or "").strip()
        if alias.startswith("@") or (
            alias and "/" not in alias and "\\" not in alias and not alias.lower().endswith(".exe")
        ):
            try:
                from desktop_env_config import resolve_launch_spec

                launch = resolve_launch_spec(alias if alias.startswith("@") else f"@{alias.lstrip('@')}")
            except Exception:
                launch = None
            if launch and launch.get("path"):
                new_step = copy.deepcopy(step)
                new_spec = _spec_of(new_step)
                new_spec["path"] = launch["path"]
                if launch.get("args"):
                    new_spec["args"] = list(launch["args"])
                new_spec["alias"] = launch.get("alias") or alias.lstrip("@")
                new_step["desktop_spec"] = new_spec
                new_step["input_value"] = f"@{new_spec['alias']}"
                meta["strategies"].append("reresolve_alias")
                meta["resolved_path"] = launch["path"]
                # 仅当 path 相对原步骤有变化才算有效自愈
                old_path = str(spec.get("path") or spec.get("exe") or "").strip()
                if old_path and old_path == launch["path"] and not failed_result:
                    return None, {**meta, "reason": "alias_unchanged"}
                if old_path == launch["path"] and isinstance(failed_result, dict):
                    # 失败后同 path：仍允许再试一次（进程未起完），标 retry_same_path
                    meta["strategies"].append("retry_same_launch")
                return new_step, meta
        return None, {**meta, "reason": "no_launch_strategy"}

    return None, {**meta, "reason": "unsupported_action"}


def run_desktop_step_with_optional_heal(
    step: Dict[str, Any],
    *,
    execute_fn: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """执行 Desktop 步骤；失败时按策略至多自愈重试 1 次。

    Returns:
        (result, heal_meta)
    """
    from desktop_automation import sync_desktop_execute_step

    exec_fn = execute_fn or sync_desktop_execute_step
    action = str((step or {}).get("action") or "")
    first = exec_fn(step)
    if not isinstance(first, dict):
        first = {"status": "failed", "error": "桌面步骤返回非 dict"}

    if _is_desktop_success(first, action):
        return first, {"heal_attempted": False, "heal_succeeded": False, "reason": "first_ok"}

    if not desktop_runtime_heal_enabled():
        meta = {"heal_attempted": False, "heal_succeeded": False, "reason": "disabled"}
        out = dict(first)
        out["desktop_heal"] = meta
        return out, meta

    healed_step, proposal = propose_healed_desktop_step(step, failed_result=first)
    if not healed_step:
        meta = {
            "heal_attempted": False,
            "heal_succeeded": False,
            "reason": proposal.get("reason") or "no_strategy",
            "proposal": proposal,
        }
        out = dict(first)
        out["desktop_heal"] = meta
        return out, meta

    second = exec_fn(healed_step)
    if not isinstance(second, dict):
        second = {"status": "failed", "error": "自愈重试返回非 dict"}

    ok = _is_desktop_success(second, action)
    meta = {
        "heal_attempted": True,
        "heal_succeeded": bool(ok),
        "proposal": proposal,
        "strategies": proposal.get("strategies") or [],
    }
    if ok:
        out = dict(second)
        out["desktop_heal"] = meta
        # 证据：供 stage evidence 归一化
        out.setdefault("heal_strategy", ",".join(meta["strategies"]))
        return out, meta

    # 自愈仍失败：返回自愈后的失败信息（更贴近最终状态），不洗绿
    out = dict(second)
    out["desktop_heal"] = meta
    if not out.get("error") and first.get("error"):
        out["error"] = first.get("error")
    return out, meta
