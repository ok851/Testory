"""用例 YAML 导出预览（Phase 5，仅供审阅，非执行格式）。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def _yaml_quote(s: str) -> str:
    t = (s or "").replace("\n", " ").strip()
    if not t:
        return '""'
    if any(c in t for c in ':"\'\n#'):
        return '"' + t.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return t


def case_to_yaml_preview(
    case: Dict[str, Any],
    steps: List[Dict[str, Any]],
    *,
    unit_name: str = "",
) -> str:
    """生成可读 YAML 预览（Midscene task 风格，中文描述优先）。"""
    lines: List[str] = [
        "# Testory 用例预览（YAML 导出，仅供人工审阅）",
        f"name: {_yaml_quote(case.get('name') or '未命名用例')}",
        f"project_id: {case.get('project_id') or ''}",
    ]
    if unit_name:
        lines.append(f"unit: {_yaml_quote(unit_name)}")
    if case.get("url"):
        lines.append(f"url: {_yaml_quote(case.get('url') or '')}")
    if case.get("precondition"):
        lines.append(f"precondition: {_yaml_quote(case.get('precondition') or '')}")
    if case.get("expected_result"):
        lines.append(f"expected: {_yaml_quote(case.get('expected_result') or '')}")
    lines.append("tasks:")
    lines.append("  - name: 主流程")
    lines.append("    steps:")
    for st in steps or []:
        if not isinstance(st, dict):
            continue
        desc = (st.get("description") or "").strip()
        action = (st.get("action") or "").strip()
        label = desc or action or "步骤"
        lines.append(f"      - {_yaml_quote(label)}")
        if action and action not in ("click", "input", "navigate"):
            lines.append(f"        # action: {action}")
        sv = (st.get("selector_value") or "").strip()
        if sv and len(sv) < 120:
            lines.append(f"        # selector: {sv}")
    return "\n".join(lines) + "\n"


def build_case_yaml_preview(db, case_id: int) -> Optional[str]:
    case = db.get_test_case_v2(case_id)
    if not case:
        return None
    steps = db.get_case_steps(case_id, page=1, page_size=9999) or []
    unit_name = ""
    uid = case.get("unit_id")
    if uid:
        unit = db.get_test_unit(int(uid))
        if unit:
            unit_name = unit.get("name") or ""
    return case_to_yaml_preview(case, steps, unit_name=unit_name)


def build_project_yaml_preview(db, project_id: int, *, unit_id: Optional[Any] = None) -> Optional[str]:
    """合并项目下（可选按单元筛选）全部用例为一份 YAML 预览。"""
    cases = db.get_project_cases(project_id, unit_id=unit_id)
    if not cases:
        return None
    parts: List[str] = [
        "# Testory 项目用例 YAML 预览（仅供人工审阅）",
        f"project_id: {project_id}",
    ]
    if unit_id:
        parts.append(f"unit_filter: {unit_id}")
    parts.append("cases:")
    for case in cases:
        cid = case.get("id")
        if not cid:
            continue
        steps = db.get_case_steps(cid, page=1, page_size=9999) or []
        unit_name = case.get("unit_name") or ""
        block = case_to_yaml_preview(case, steps, unit_name=unit_name)
        indented = "\n".join("  " + line if line.strip() else line for line in block.splitlines())
        parts.append(f"  - case_id: {cid}")
        parts.append(indented)
    return "\n".join(parts) + "\n"
