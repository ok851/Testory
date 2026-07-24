# -*- coding: utf-8 -*-
"""跨端 / 阶段结果 Schema（Z3）：Mock(simulate) 与真机共用同一契约。

阶段结果最小字段（LINKAGE Done 契约）::

    {
      "ok_assert": bool,
      "error_code": str|null,
      "error_message": str|null,   # 与历史字段 error 双写
      "extracted": dict,
      "warnings": list,
      "evidence": list
    }

跨端总结果额外要求 ``success`` / ``gate_passed`` / ``stage_results``。
归一化**不删**既有扩展字段（risk_*、hitl_events、simulate 等）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

STAGE_SCHEMA = "testory.stage_result/v1"
CROSS_END_SCHEMA = "testory.cross_end_result/v1"

STAGE_REQUIRED = (
    "ok_assert",
    "error_code",
    "error_message",
    "extracted",
    "warnings",
    "evidence",
)

CROSS_END_REQUIRED = (
    "success",
    "gate_passed",
    "stage_results",
    "schema",
)


def normalize_stage_result(raw: Any) -> Dict[str, Any]:
    """将任意阶段结果归一为 Mock/真机共用 Schema。"""
    if not isinstance(raw, dict):
        return {
            "ok_assert": False,
            "error_code": "INVALID_STAGE_RESULT",
            "error_message": "stage_result 必须为 object",
            "error": "stage_result 必须为 object",
            "extracted": {},
            "warnings": [],
            "evidence": [],
            "schema": STAGE_SCHEMA,
        }

    out = dict(raw)
    ok = out.get("ok_assert")
    if ok is None:
        out["ok_assert"] = False
    else:
        out["ok_assert"] = bool(ok)

    err_msg = out.get("error_message")
    err = out.get("error")
    if err_msg is None or str(err_msg).strip() == "":
        if err is not None and str(err).strip() != "":
            out["error_message"] = str(err)
        else:
            out["error_message"] = None
    else:
        out["error_message"] = str(err_msg)
        if err is None or str(err).strip() == "":
            out["error"] = out["error_message"]

    if "error_code" not in out:
        out["error_code"] = None
    if out.get("ok_assert") is False:
        if not out.get("error_code") and (out.get("error_message") or out.get("error")):
            out["error_code"] = "STAGE_FAILED"

    extracted = out.get("extracted")
    out["extracted"] = dict(extracted) if isinstance(extracted, dict) else {}

    warnings = out.get("warnings")
    if isinstance(warnings, list):
        out["warnings"] = list(warnings)
    elif warnings:
        out["warnings"] = [warnings]
    else:
        out["warnings"] = []

    evidence = out.get("evidence")
    if isinstance(evidence, list):
        out["evidence"] = list(evidence)
    elif evidence:
        out["evidence"] = [evidence]
    else:
        out["evidence"] = []

    shot = out.get("screenshot_path") or out.get("screenshot")
    if shot:
        path = str(shot)
        if not any(
            isinstance(e, dict) and str(e.get("path") or "") == path for e in out["evidence"]
        ):
            out["evidence"].append({"type": "screenshot", "path": path})

    out["schema"] = STAGE_SCHEMA
    return out


def normalize_cross_end_result(raw: Any) -> Dict[str, Any]:
    """跨端执行总结果归一（simulate / live 同形）。"""
    if not isinstance(raw, dict):
        return {
            "success": False,
            "gate_passed": False,
            "stage_results": [],
            "variables": {},
            "error": "cross_end_result 必须为 object",
            "error_code": "INVALID_CROSS_END_RESULT",
            "error_message": "cross_end_result 必须为 object",
            "schema": CROSS_END_SCHEMA,
        }

    out = dict(raw)
    stages_in = out.get("stage_results")
    if not isinstance(stages_in, list):
        stages_in = []
    out["stage_results"] = [normalize_stage_result(s) for s in stages_in]

    if "success" not in out:
        out["success"] = False
    else:
        out["success"] = bool(out.get("success"))

    if "gate_passed" not in out:
        # 兼容仅有 success 的旧 mock
        out["gate_passed"] = bool(out.get("success"))
    else:
        out["gate_passed"] = bool(out.get("gate_passed"))

    if not isinstance(out.get("variables"), dict):
        # orchestrator summary 可能用 context_variables
        alt = out.get("context_variables")
        out["variables"] = dict(alt) if isinstance(alt, dict) else {}

    err = out.get("error")
    if out.get("error_message") in (None, "") and err:
        out["error_message"] = str(err)
    elif out.get("error_message") and not err:
        out["error"] = out["error_message"]

    out["schema"] = CROSS_END_SCHEMA
    return out


def validate_stage_result(sr: Any) -> Tuple[bool, List[str]]:
    """校验阶段结果是否满足最小契约。"""
    errors: List[str] = []
    if not isinstance(sr, dict):
        return False, ["not_object"]
    for key in STAGE_REQUIRED:
        if key not in sr:
            errors.append(f"missing:{key}")
    if "ok_assert" in sr and not isinstance(sr.get("ok_assert"), bool):
        errors.append("ok_assert_not_bool")
    if "extracted" in sr and not isinstance(sr.get("extracted"), dict):
        errors.append("extracted_not_object")
    if "warnings" in sr and not isinstance(sr.get("warnings"), list):
        errors.append("warnings_not_list")
    if "evidence" in sr and not isinstance(sr.get("evidence"), list):
        errors.append("evidence_not_list")
    return (len(errors) == 0), errors


def validate_cross_end_result(result: Any) -> Tuple[bool, List[str]]:
    errors: List[str] = []
    if not isinstance(result, dict):
        return False, ["not_object"]
    for key in CROSS_END_REQUIRED:
        if key not in result:
            errors.append(f"missing:{key}")
    stages = result.get("stage_results")
    if not isinstance(stages, list):
        errors.append("stage_results_not_list")
    else:
        for i, sr in enumerate(stages):
            ok, errs = validate_stage_result(sr)
            if not ok:
                errors.append(f"stage[{i}]:{','.join(errs)}")
    return (len(errors) == 0), errors


def mock_and_live_share_schema(
    mock_result: Dict[str, Any],
    live_like_result: Dict[str, Any],
) -> Tuple[bool, List[str]]:
    """Z3 门禁：两边归一化后均通过 validate，且 schema 常量一致。"""
    a = normalize_cross_end_result(mock_result)
    b = normalize_cross_end_result(live_like_result)
    ok_a, err_a = validate_cross_end_result(a)
    ok_b, err_b = validate_cross_end_result(b)
    errors: List[str] = []
    if not ok_a:
        errors.extend([f"mock:{e}" for e in err_a])
    if not ok_b:
        errors.extend([f"live:{e}" for e in err_b])
    if a.get("schema") != b.get("schema"):
        errors.append("schema_mismatch")
    if a.get("schema") != CROSS_END_SCHEMA:
        errors.append("unexpected_schema")
    return (len(errors) == 0), errors
