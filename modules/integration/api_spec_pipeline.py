"""
Postman 风格 api_spec 扩展：前置请求链、前置/后置脚本（Duktape JS）、变量提取与写回用例变量。
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from modules.integration.api_http_helper import (
    execute_api_spec_sync,
    get_json_path_value,
    substitute_env_placeholders,
)


def apply_variable_extracts(
    rules: Any,
    http_out: Dict[str, Any],
    runtime: Dict[str, str],
) -> None:
    """按规则从响应中提取值写入 runtime（字符串，供 {{var}} 使用）。"""
    if not rules:
        return
    if isinstance(rules, str):
        try:
            rules = json.loads(rules)
        except json.JSONDecodeError:
            return
    if not isinstance(rules, list):
        return
    rj = http_out.get("response_json")
    rt = http_out.get("response_text") or ""
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        name = (rule.get("name") or "").strip()
        if not name:
            continue
        val: Any = None
        jp = (rule.get("json_path") or "").strip()
        if jp:
            val = get_json_path_value(rj, jp)
        else:
            rx = (rule.get("regex") or "").strip()
            if rx:
                try:
                    grp = int(rule.get("group", 1))
                except (TypeError, ValueError):
                    grp = 1
                m = re.search(rx, rt, re.DOTALL)
                if m:
                    try:
                        val = m.group(grp)
                    except IndexError:
                        val = m.group(0)
        if val is not None:
            runtime[name] = (
                json.dumps(val, ensure_ascii=False)
                if isinstance(val, (dict, list))
                else str(val)
            )


def _dukpy_run_script(
    js: str,
    runtime: Dict[str, str],
    *,
    phase: str,
    last_http: Optional[Dict[str, Any]] = None,
    logs: Optional[List[str]] = None,
) -> Tuple[bool, str]:
    js = (js or "").strip()
    if not js:
        return True, ""
    try:
        import dukpy
    except ImportError:
        return (
            False,
            "前后置脚本需要安装 dukpy（在 requirements.txt 已声明，请执行 pip install dukpy）",
        )

    rt_payload = {str(k): str(v) if v is not None else "" for k, v in runtime.items()}
    rt_json = json.dumps(rt_payload, ensure_ascii=False)

    status = 0
    body_s = ""
    rj_enc = "null"
    if phase == "post" and last_http is not None:
        status = int(last_http.get("status_code") or 0)
        body_s = str(last_http.get("response_text") or "")
        rj = last_http.get("response_json")
        if rj is not None:
            rj_enc = json.dumps(rj, ensure_ascii=False)

    parts: List[str] = [
        "var __rt = " + rt_json + ";",
        "var runtime = {",
        "  get: function(k){",
        "    k = String(k);",
        "    return __rt[k] !== undefined && __rt[k] !== null ? String(__rt[k]) : '';",
        "  },",
        "  set: function(k,v){ __rt[String(k)] = (v === null || v === undefined) ? '' : String(v); }",
        "};",
        "var __logs = [];",
        "function log(){ __logs.push(Array.prototype.join.call(arguments, ' ')); }",
    ]
    if phase == "post" and last_http is not None:
        parts.append(
            "var response = { status: %s, body: %s, json: %s };"
            % (status, json.dumps(body_s, ensure_ascii=False), rj_enc)
        )
    else:
        parts.append("var response = { status: 0, body: '', json: null };")

    parts.append(js)
    parts.append("JSON.stringify({ runtime: __rt, logs: __logs });")
    src = "\n".join(parts)

    try:
        raw_out = dukpy.evaljs(src)
    except Exception as e:
        return False, f"脚本执行错误: {e}"

    try:
        pack = json.loads(raw_out)
    except json.JSONDecodeError:
        return False, "脚本未返回合法结构"

    merged = pack.get("runtime")
    if not isinstance(merged, dict):
        return False, "脚本破坏了 runtime 结构"

    runtime.clear()
    for k, v in merged.items():
        runtime[str(k)] = "" if v is None else str(v)
    if logs is not None:
        for lg in pack.get("logs") or []:
            logs.append(str(lg))
    return True, ""


def _normalize_chain(chain: Any) -> List[Any]:
    if isinstance(chain, str):
        try:
            chain = json.loads(chain)
        except json.JSONDecodeError:
            return []
    if not isinstance(chain, list):
        return []
    return chain


def _persist_rule_names(
    db,
    project_id: Optional[int],
    case_id: Optional[int],
    runtime: Dict[str, str],
    rules: Any,
) -> None:
    if not case_id or not rules:
        return
    if isinstance(rules, str):
        try:
            rules = json.loads(rules)
        except json.JSONDecodeError:
            return
    if not isinstance(rules, list):
        return
    names = {r.get("name") for r in rules if isinstance(r, dict) and r.get("name")}
    for n in names:
        if n in runtime:
            db.upsert_case_scoped_variable(n, runtime[n], project_id, case_id)


def run_api_spec_pipeline(
    spec: Dict[str, Any],
    db,
    project_id: Optional[int],
    case_id: Optional[int],
    browser_cookie_jar=None,
    *,
    persist_extracts: bool = False,
    collect_script_logs: bool = False,
    depth: int = 0,
    runtime: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    执行完整接口步骤：前置脚本 → 前置请求链（可嵌套）→ 主请求 → 后置脚本 → 提取变量。
    persist_extracts: 为 True 时，将本层 extract_variables/extract 中列出的 name 写入用例变量表。
    """
    try:
        return _run_api_spec_pipeline_impl(
            spec,
            db,
            project_id,
            case_id,
            browser_cookie_jar,
            persist_extracts=persist_extracts,
            collect_script_logs=collect_script_logs,
            depth=depth,
            runtime=runtime,
        )
    except Exception as e:
        return {
            "status_code": None,
            "response_text": "",
            "response_json": None,
            "ok_assert": False,
            "assert_message": f"接口流水线异常: {e}",
            "error": str(e),
        }


def _run_api_spec_pipeline_impl(
    spec: Dict[str, Any],
    db,
    project_id: Optional[int],
    case_id: Optional[int],
    browser_cookie_jar=None,
    *,
    persist_extracts: bool = False,
    collect_script_logs: bool = False,
    depth: int = 0,
    runtime: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    if depth > 12:
        return {
            "status_code": None,
            "response_text": "",
            "response_json": None,
            "ok_assert": False,
            "assert_message": "前置请求链嵌套超过 12 层",
            "error": "前置请求链嵌套过深",
        }

    if runtime is None:
        runtime = {}
    script_logs: List[str] = []

    def resolve(s: str) -> str:
        return substitute_env_placeholders(
            db.resolve_variables(
                str(s),
                project_id=project_id,
                case_id=case_id,
                runtime_overlay=runtime,
            )
        )

    prescript = (spec.get("prescript") or spec.get("pre_request_script") or "").strip()
    if prescript:
        ok, err = _dukpy_run_script(
            prescript,
            runtime,
            phase="pre",
            last_http=None,
            logs=script_logs if collect_script_logs else None,
        )
        if not ok:
            return {
                "status_code": None,
                "response_text": "",
                "response_json": None,
                "ok_assert": False,
                "assert_message": err,
                "error": err,
                "script_logs": script_logs if collect_script_logs else None,
            }

    chain = _normalize_chain(spec.get("prerequest_chain"))

    for idx, item in enumerate(chain):
        if not isinstance(item, dict):
            continue
        sub = item.get("spec") or item.get("api_spec")
        if sub is None and "method" in item and "url" in item:
            sub = item
        if not isinstance(sub, dict):
            continue

        sub_exec = dict(sub)
        # 前置链子请求仅用于准备数据：去掉易误拷的 JSON 断言；HTTP 按「任意 2xx」判定（与 Postman 前置习惯一致）
        sub_exec.pop("json_path", None)
        sub_exec.pop("expected_json_value", None)
        sub_exec.pop("expected_status", None)
        sub_exec["accept_2xx_for_status"] = True

        sub_out = _run_api_spec_pipeline_impl(
            sub_exec,
            db,
            project_id,
            case_id,
            browser_cookie_jar,
            persist_extracts=False,
            collect_script_logs=collect_script_logs,
            depth=depth + 1,
            runtime=runtime,
        )
        if not sub_out.get("ok_assert") and not item.get("continue_on_error"):
            sub_out = dict(sub_out)
            sub_out["pipeline_error"] = f"前置请求 {idx + 1} 失败"
            sub_out["failed_prereq_index"] = idx
            if collect_script_logs:
                sub_out["script_logs"] = script_logs
            return sub_out

        apply_variable_extracts(
            item.get("extract") or item.get("extract_variables"),
            sub_out,
            runtime,
        )
        if item.get("persist_extracts_to_case"):
            _persist_rule_names(
                db,
                project_id,
                case_id,
                runtime,
                item.get("extract") or item.get("extract_variables"),
            )

    main_out = execute_api_spec_sync(spec, resolve, browser_cookie_jar)

    postscript = (spec.get("postscript") or spec.get("post_request_script") or "").strip()
    if postscript:
        ok, err = _dukpy_run_script(
            postscript,
            runtime,
            phase="post",
            last_http=main_out,
            logs=script_logs if collect_script_logs else None,
        )
        if not ok:
            return {
                "status_code": main_out.get("status_code"),
                "response_text": main_out.get("response_text"),
                "response_json": main_out.get("response_json"),
                "ok_assert": False,
                "assert_message": err,
                "error": err,
                "script_logs": script_logs if collect_script_logs else None,
            }

    apply_variable_extracts(
        spec.get("extract") or spec.get("extract_variables"),
        main_out,
        runtime,
    )

    do_persist = persist_extracts or bool(spec.get("persist_extracts_to_case"))
    if do_persist and case_id:
        _persist_rule_names(
            db,
            project_id,
            case_id,
            runtime,
            spec.get("extract_variables") or spec.get("extract"),
        )

    if collect_script_logs:
        main_out = dict(main_out)
        main_out["script_logs"] = script_logs
    return main_out
