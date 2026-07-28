# -*- coding: utf-8 -*-
"""语义 Diff → ChangeImpactReport（LLM + 启发式降级）。"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from logger import uat_logger

IMPACT_SCHEMA_HINT = """{
  "change_types": ["ui_copy|component_add|component_remove|api_param|flow_logic|style_only|config|test_only|other"],
  "affected_modules": ["登录流程", "订单提交"],
  "risk_level": "low|medium|high",
  "may_break_existing_cases": true,
  "at_risk_case_hints": ["登录按钮文案", "订单提交"],
  "suggested_new_coverage": ["新页面冒烟"],
  "summary": "一句话摘要",
  "is_rollback": false,
  "is_new_feature": false
}"""


def _heuristic_impact(
    signals: Dict[str, Any],
    mr_description: str = "",
) -> Dict[str, Any]:
    files = [str(f).lower() for f in (signals.get("changed_files") or [])]
    change_types: List[str] = []
    modules: List[str] = []
    risk = "low"
    is_new = False

    ui_ext = (".tsx", ".jsx", ".vue", ".css", ".scss", ".html")
    api_hints = signals.get("api_hints") or []
    testids = signals.get("testids") or []
    tokens = signals.get("path_tokens") or []

    only_test = files and all(
        any(x in f for x in ("/test", "__tests__", ".spec.", ".test.", "/docs/"))
        for f in files
    )
    only_style = files and all(
        f.endswith((".css", ".scss", ".less", ".sass")) or "/styles/" in f for f in files
    )

    if only_test:
        change_types.append("test_only")
        risk = "low"
    elif only_style:
        change_types.append("style_only")
        risk = "low"
    else:
        if any(f.endswith(ui_ext) for f in files):
            change_types.append("component_add" if any("new" in t for t in tokens) else "flow_logic")
            risk = "medium"
        if api_hints or any("/api/" in f or f.endswith((".py", ".go", ".java")) for f in files):
            change_types.append("api_param")
            risk = "high" if risk != "high" else risk
        if testids and "ui_copy" not in change_types:
            change_types.append("ui_copy")

    for t in tokens[:12]:
        modules.append(t)
    if signals.get("routes"):
        modules.extend(f"route:{r}" for r in signals["routes"][:5])

    desc = (mr_description or "").lower()
    if any(k in desc for k in ("feat", "feature", "新增", "新功能")):
        is_new = True
        if "component_add" not in change_types:
            change_types.append("component_add")
        risk = "medium" if risk == "low" else risk

    is_rollback = bool(signals.get("looks_like_rollback"))
    if is_rollback:
        change_types = ["other"]
        risk = "medium"

    if not change_types:
        change_types = ["other"]
        risk = "medium" if files else "low"

    may_break = risk in ("medium", "high") and not only_test and not only_style
    summary_bits = [
        f"{len(files)} files",
        f"types={','.join(change_types)}",
        f"risk={risk}",
    ]
    if testids:
        summary_bits.append(f"testids={len(testids)}")

    return {
        "change_types": change_types,
        "affected_modules": modules[:20] or ["unknown"],
        "risk_level": risk,
        "may_break_existing_cases": may_break,
        "at_risk_case_hints": list(tokens)[:15] + list(testids)[:10],
        "suggested_new_coverage": (
            [f"覆盖新增信号: {', '.join(testids[:5])}"] if is_new and testids
            else (["新增功能冒烟"] if is_new else [])
        ),
        "summary": "; ".join(summary_bits),
        "is_rollback": is_rollback,
        "is_new_feature": is_new,
        "analysis_source": "heuristic",
    }


def _build_llm_prompt(
    *,
    diff: str,
    changed_files: List[str],
    signals: Dict[str, Any],
    mr_description: str,
) -> str:
    files_txt = "\n".join(changed_files[:80]) or "(none)"
    diff_cap = (diff or "")[:24000]
    sig_json = json.dumps(
        {
            "testids": signals.get("testids"),
            "aria_labels": signals.get("aria_labels"),
            "routes": signals.get("routes"),
            "api_hints": signals.get("api_hints"),
            "path_tokens": signals.get("path_tokens"),
            "frameworks": signals.get("frameworks"),
            "looks_like_rollback": signals.get("looks_like_rollback"),
        },
        ensure_ascii=False,
    )
    return (
        "你是测试影响分析助手。根据代码变更，输出 JSON（不要 markdown）。\n"
        f"Schema:\n{IMPACT_SCHEMA_HINT}\n\n"
        "规则：\n"
        "- risk_level 仅 low|medium|high\n"
        "- 不要虚构未出现的模块名；可基于路径与 testid 推断\n"
        "- 文案/选择器变更 → may_break_existing_cases=true\n"
        "- 纯样式/纯测试文件 → risk 偏低\n"
        "- 回滚 commit → is_rollback=true，不要建议生成新用例\n\n"
        f"MR/提交说明:\n{(mr_description or '')[:4000]}\n\n"
        f"变更文件:\n{files_txt}\n\n"
        f"已提取信号:\n{sig_json}\n\n"
        f"Diff(截断):\n{diff_cap}\n"
    )


def _normalize_impact(raw: Dict[str, Any], fallback: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(fallback)
    if not isinstance(raw, dict):
        return out

    types = raw.get("change_types")
    if isinstance(types, list) and types:
        out["change_types"] = [str(t)[:64] for t in types[:12]]

    mods = raw.get("affected_modules")
    if isinstance(mods, list) and mods:
        out["affected_modules"] = [str(m)[:80] for m in mods[:20]]

    risk = str(raw.get("risk_level") or out.get("risk_level") or "medium").lower()
    if risk not in ("low", "medium", "high"):
        risk = "medium"
    out["risk_level"] = risk

    if "may_break_existing_cases" in raw:
        out["may_break_existing_cases"] = bool(raw.get("may_break_existing_cases"))

    hints = raw.get("at_risk_case_hints")
    if isinstance(hints, list):
        out["at_risk_case_hints"] = [str(h)[:120] for h in hints[:30]]

    sug = raw.get("suggested_new_coverage")
    if isinstance(sug, list):
        out["suggested_new_coverage"] = [str(s)[:200] for s in sug[:20]]

    if raw.get("summary"):
        out["summary"] = str(raw.get("summary"))[:500]
    out["is_rollback"] = bool(raw.get("is_rollback", out.get("is_rollback")))
    out["is_new_feature"] = bool(raw.get("is_new_feature", out.get("is_new_feature")))
    out["analysis_source"] = "llm"
    return out


def build_change_impact_report(
    *,
    diff: str = "",
    changed_files: Optional[List[str]] = None,
    file_snippets: Optional[Dict[str, Any]] = None,
    mr_description: str = "",
    signals: Optional[Dict[str, Any]] = None,
    profile: Optional[Dict[str, Any]] = None,
    use_llm: bool = True,
) -> Dict[str, Any]:
    from ai_modules.code_intel.signals import extract_ui_signals

    sig = signals or extract_ui_signals(
        diff=diff,
        changed_files=changed_files,
        file_snippets=file_snippets,
    )
    files = list(changed_files or sig.get("changed_files") or [])
    heuristic = _heuristic_impact(sig, mr_description=mr_description)
    heuristic["signals"] = {
        "testids": sig.get("testids") or [],
        "aria_labels": sig.get("aria_labels") or [],
        "routes": sig.get("routes") or [],
        "api_hints": sig.get("api_hints") or [],
        "path_tokens": sig.get("path_tokens") or [],
        "frameworks": sig.get("frameworks") or [],
        "signal_counts": sig.get("signal_counts") or {},
    }

    if not use_llm:
        return heuristic

    if not (diff or files or mr_description):
        heuristic["summary"] = "无变更内容可分析"
        return heuristic

    try:
        from ai_selector_recovery import _extract_json_obj
        from ai_local_inference import local_ai_service
        from ai_multi_provider import dispatch_chat
        from ai_modules.code_intel.policy import llm_timeout_s
        import concurrent.futures

        prompt = _build_llm_prompt(
            diff=diff or "",
            changed_files=files,
            signals=sig,
            mr_description=mr_description or "",
        )
        timeout = llm_timeout_s()

        def _call():
            return dispatch_chat(prompt, profile, local_ai_service)

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(_call)
            try:
                raw = fut.result(timeout=timeout)
            except concurrent.futures.TimeoutError:
                heuristic["warnings"] = [f"LLM 超时({timeout}s)，已使用启发式"]
                return heuristic

        data = _extract_json_obj(raw)
        if isinstance(data, dict):
            return _normalize_impact(data, heuristic)
        heuristic["warnings"] = ["LLM 输出无法解析，已使用启发式"]
    except Exception as e:
        uat_logger.warning("[CODE_INTEL] impact LLM failed: %s", e)
        heuristic["warnings"] = [f"LLM 不可用，已使用启发式: {str(e)[:160]}"]

    return heuristic
