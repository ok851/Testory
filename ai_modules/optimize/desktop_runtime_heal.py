# -*- coding: utf-8 -*-
"""Desktop 运行时自愈（Y5）：有限策略、失败不假绿。

当前策略（可扩展）:
- ``attach_window``：过严 ``^标题$`` → 放宽为 contains / best_match
- ``launch_app``：别名重解析（``@erp`` / DESKTOP_APP_ALIASES）
- ``click`` / ``input`` 等：有限 UIA 选择器放宽
  （去 automation_id 保 name、name equals→contains、清空过严 parent_chain、缩短 uia_path）

环境变量 ``DESKTOP_RUNTIME_HEAL``（默认 1）。关闭则不做运行时尝试。
成功时在结果写入 ``desktop_heal``；失败仍返回原失败，禁止把 soft-fail 洗成 success。
**禁止宣传「通用 UIA / Desktop 已自愈」。**
"""

from __future__ import annotations

import copy
import json
import os
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

_ANCHOR_RE = re.compile(r"(?is)^\^(?P<body>.+)\$$")
_INLINE_FLAGS_RE = re.compile(r"^\(\?[aiLmsux]+\)")

_POINTER_ACTIONS = frozenset({
    "click",
    "dblclick",
    "double_click",
    "right_click",
    "hover",
    "drag",
    "input",
    "type",
    "assert",
    "verify",
})


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


def _loads_jsonish(raw: Any) -> Any:
    if isinstance(raw, (dict, list)):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None
    return None


def _selector_from_step(step: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """从 selector_value / locator_candidates 提取 UIA selector 字典。"""
    sv = _loads_jsonish(step.get("selector_value"))
    if isinstance(sv, dict):
        snap = sv.get("element_snapshot") if isinstance(sv.get("element_snapshot"), dict) else sv
        sel = snap.get("selector") if isinstance(snap, dict) else None
        if isinstance(sel, dict) and (
            sel.get("key_candidates") or sel.get("parent_chain") is not None
        ):
            return sel

    cands = step.get("locator_candidates")
    parsed = _loads_jsonish(cands)
    if parsed is None and isinstance(cands, list):
        parsed = cands
    if not isinstance(parsed, list):
        return None
    for cand in parsed:
        if not isinstance(cand, dict):
            continue
        st = str(cand.get("selector_type") or "").strip().lower()
        if st != "uia_path":
            continue
        nodes = _loads_jsonish(cand.get("selector_value"))
        if not isinstance(nodes, list) or not nodes:
            continue
        target = nodes[-1] if isinstance(nodes[-1], dict) else {}
        parent_chain = [dict(n) for n in nodes[:-1] if isinstance(n, dict)]
        keys: List[Dict[str, str]] = []
        aid = str(target.get("automation_id") or "").strip()
        nm = str(target.get("name") or "").strip()
        if aid:
            keys.append({"property": "automation_id", "value": aid, "match": "equals"})
        if nm:
            keys.append({"property": "uia-name", "value": nm, "match": "equals"})
        return {
            "anchor_props": str(target.get("control_type") or "Control").strip() or "Control",
            "key_candidates": keys,
            "parent_chain": parent_chain,
            "_from_uia_path_nodes": [dict(n) for n in nodes if isinstance(n, dict)],
        }
    return None


def _apply_selector_to_step(step: Dict[str, Any], selector: Dict[str, Any]) -> Dict[str, Any]:
    """把放宽后的 selector 写回步骤（优先改 selector_value JSON）。"""
    new_step = copy.deepcopy(step)
    clean_sel = {
        "anchor_props": selector.get("anchor_props") or "Control",
        "key_candidates": list(selector.get("key_candidates") or []),
        "parent_chain": list(selector.get("parent_chain") or []),
    }
    snap = {"selector": clean_sel}

    sv = _loads_jsonish(new_step.get("selector_value"))
    if isinstance(sv, dict):
        if "element_snapshot" in sv or "template_image_base64" in sv or "template_path" in sv:
            sv = dict(sv)
            sv["element_snapshot"] = snap
            new_step["selector_value"] = json.dumps(sv, ensure_ascii=False)
        else:
            merged = dict(sv)
            merged["selector"] = clean_sel
            if "element_snapshot" not in merged:
                merged["element_snapshot"] = snap
            new_step["selector_value"] = json.dumps(merged, ensure_ascii=False)
    else:
        new_step["selector_value"] = json.dumps({"element_snapshot": snap}, ensure_ascii=False)
        new_step["selector_type"] = new_step.get("selector_type") or "uia"

    nodes = selector.get("_from_uia_path_nodes")
    if isinstance(nodes, list) and nodes:
        cands = _loads_jsonish(new_step.get("locator_candidates"))
        if cands is None and isinstance(new_step.get("locator_candidates"), list):
            cands = new_step.get("locator_candidates")
        if isinstance(cands, list):
            out_cands = []
            for cand in cands:
                if not isinstance(cand, dict):
                    continue
                c2 = dict(cand)
                if str(c2.get("selector_type") or "").strip().lower() == "uia_path":
                    c2["selector_value"] = json.dumps(nodes, ensure_ascii=False)
                out_cands.append(c2)
            new_step["locator_candidates"] = json.dumps(out_cands, ensure_ascii=False)

    new_spec = _spec_of(new_step)
    new_spec["_heal_uia"] = True
    new_step["desktop_spec"] = new_spec
    return new_step


def _propose_uia_selector_heal(
    step: Dict[str, Any],
) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    """有限 UIA 放宽：至多提出一种策略。"""
    meta: Dict[str, Any] = {"action": str(step.get("action") or ""), "strategies": []}
    sel = _selector_from_step(step)
    if not sel:
        return None, {**meta, "reason": "no_uia_selector"}

    keys = [dict(k) for k in (sel.get("key_candidates") or []) if isinstance(k, dict)]
    parent = list(sel.get("parent_chain") or [])
    nodes = sel.get("_from_uia_path_nodes")

    # 1) 同时有 automation_id + name → 去掉 automation_id，name 改为 contains
    has_aid = any(
        str(k.get("property") or "").lower() == "automation_id" and str(k.get("value") or "").strip()
        for k in keys
    )
    name_keys = [
        k
        for k in keys
        if str(k.get("property") or "").lower() in ("uia-name", "name")
        and str(k.get("value") or "").strip()
    ]
    if has_aid and name_keys:
        new_keys = []
        for k in keys:
            prop = str(k.get("property") or "").lower()
            if prop == "automation_id":
                continue
            kk = dict(k)
            if prop in ("uia-name", "name"):
                kk["match"] = "contains"
            new_keys.append(kk)
        new_sel = dict(sel)
        new_sel["key_candidates"] = new_keys
        meta["strategies"].append("drop_automation_id_prefer_name")
        return _apply_selector_to_step(step, new_sel), meta

    # 2) name equals → contains
    changed = False
    new_keys = []
    for k in keys:
        kk = dict(k)
        prop = str(kk.get("property") or "").lower()
        match = str(kk.get("match") or "equals").lower()
        if prop in ("uia-name", "name") and match == "equals" and str(kk.get("value") or "").strip():
            kk["match"] = "contains"
            changed = True
        new_keys.append(kk)
    if changed:
        new_sel = dict(sel)
        new_sel["key_candidates"] = new_keys
        meta["strategies"].append("name_match_contains")
        return _apply_selector_to_step(step, new_sel), meta

    # 3) 过长 parent_chain → 清空（避免层级漂移导致假失败）
    if len(parent) >= 2:
        new_sel = dict(sel)
        new_sel["parent_chain"] = []
        meta["strategies"].append("clear_parent_chain")
        return _apply_selector_to_step(step, new_sel), meta

    # 4) uia_path 节点过多 → 只保留末尾 2 个并重建 key
    if isinstance(nodes, list) and len(nodes) >= 3:
        short = [dict(n) for n in nodes[-2:] if isinstance(n, dict)]
        target = short[-1]
        keys2: List[Dict[str, str]] = []
        aid = str(target.get("automation_id") or "").strip()
        nm = str(target.get("name") or "").strip()
        if nm:
            keys2.append({"property": "uia-name", "value": nm, "match": "contains"})
        elif aid:
            keys2.append({"property": "automation_id", "value": aid, "match": "equals"})
        new_sel = {
            "anchor_props": str(target.get("control_type") or sel.get("anchor_props") or "Control"),
            "key_candidates": keys2,
            "parent_chain": [dict(short[0])] if len(short) > 1 else [],
            "_from_uia_path_nodes": short,
        }
        meta["strategies"].append("shorten_uia_path")
        return _apply_selector_to_step(step, new_sel), meta

    return None, {**meta, "reason": "no_uia_strategy"}


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
                from modules.desktop.desktop_env_config import resolve_launch_spec

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
                old_path = str(spec.get("path") or spec.get("exe") or "").strip()
                if old_path and old_path == launch["path"] and not failed_result:
                    return None, {**meta, "reason": "alias_unchanged"}
                if old_path == launch["path"] and isinstance(failed_result, dict):
                    meta["strategies"].append("retry_same_launch")
                return new_step, meta
        return None, {**meta, "reason": "no_launch_strategy"}

    if action in _POINTER_ACTIONS:
        return _propose_uia_selector_heal(step)

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
    from modules.desktop.desktop_automation import sync_desktop_execute_step

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
        out.setdefault("heal_strategy", ",".join(meta["strategies"]))
        return out, meta

    out = dict(second)
    out["desktop_heal"] = meta
    if not out.get("error") and first.get("error"):
        out["error"] = first.get("error")
    return out, meta
