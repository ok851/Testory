# -*- coding: utf-8 -*-
"""从需求/结构化场景生成 API 用例（MVP）。"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple


def generate_api_cases_from_document(doc_text: str, project_name: str = "") -> Dict[str, Any]:
    """解析文档文本并返回 API 用例结构建议（不落库）。"""
    text = (doc_text or "").strip()
    if not text:
        return {
            "success": False,
            "error": "文档内容为空",
            "cases": [],
            "project_name": project_name,
        }
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()][:40]
    cases = []
    for i, ln in enumerate(lines[:10], start=1):
        cases.append({
            "name": f"API-场景-{i}",
            "description": ln[:500],
            "steps": [
                {
                    "action": "api_request",
                    "api_spec": {
                        "method": "GET",
                        "url": "https://api.example.com/endpoint",
                        "description": ln[:300],
                    },
                }
            ],
        })
    return {
        "success": True,
        "cases": cases,
        "project_name": project_name,
        "source_length": len(text),
        "hint": "请根据实际 OpenAPI 在接口测试模块调整 URL 与断言；或使用 /api/ai/import/api-spec 导入规范。",
    }


def _scenario_parts(sc: Dict[str, Any]) -> Tuple[str, str, str, str]:
    sid = str(sc.get("id") or "").strip() or "auto"
    title = str(sc.get("title") or "").strip() or f"场景 {sid}"
    hs = sc.get("high_level_steps") or []
    if isinstance(hs, str):
        hs = [hs]
    hs_t = "\n".join(str(x) for x in hs if str(x).strip())
    desc = f"[REQ-API:{sid}] {title}"
    return sid, title, hs_t or title, desc


def batch_api_cases_from_scenarios(
    db: Any,
    *,
    project_id: int,
    scenarios: List[Dict[str, Any]],
    max_scenarios: int = 20,
    user_id: int = 0,
) -> Dict[str, Any]:
    """为每个场景创建 API 用例，含占位 api_request 步骤（可后续在接口测试中编辑）。"""
    created_ids: List[int] = []
    warnings: List[str] = []

    try:
        from modules.auth.license_manager import license_manager, LicenseType

        license_info = license_manager.get_current_license()
        limits = license_manager.get_limits()
    except Exception:
        license_info = None
        limits = {"max_cases_per_project": -1}

    for sc in scenarios[:max_scenarios]:
        if not isinstance(sc, dict):
            continue
        sid, title, summary, desc = _scenario_parts(sc)
        if limits and limits.get("max_cases_per_project", -1) != -1:
            if db.get_project_case_count(project_id) >= limits["max_cases_per_project"]:
                warnings.append(f"已达项目用例上限，停止在 {len(created_ids)} 条")
                break

        if license_info and getattr(license_info, "license_type", None) == LicenseType.FREE.value:
            db.increment_created_cases(user_id)

        case_id = db.create_test_case_v2(
            project_id,
            (f"API-{title}")[:200],
            "",
            desc[:3900],
            "",
            summary[:2000],
            case_type="api",
        )
        api_spec = {
            "method": "GET",
            "url": "",
            "headers": {},
            "body": None,
            "assertions": [{"type": "status_code", "expected": 200}],
            "description": summary[:800],
            "scenario_id": sid,
        }
        db.create_test_step(
            case_id=case_id,
            action="api_request",
            selector_type="",
            selector_value="",
            input_value="",
            description=f"场景 {sid}：请在接口测试中填写实际 URL 与断言",
            step_order=1,
            api_spec=json.dumps(api_spec, ensure_ascii=False),
        )
        created_ids.append(case_id)
        warnings.append(
            f"用例 {case_id}（{title}）已创建占位接口步骤，请在「接口测试」中完善 URL 与 api_spec。"
        )

    return {
        "success": True,
        "created_case_ids": created_ids,
        "count": len(created_ids),
        "warnings": warnings,
        "platform": "api",
    }
