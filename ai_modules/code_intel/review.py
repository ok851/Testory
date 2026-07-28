# -*- coding: utf-8 -*-
"""用例审核状态：pending / active / rejected；CI 默认排除未激活。"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set

REVIEW_PENDING = "pending"
REVIEW_ACTIVE = "active"
REVIEW_REJECTED = "rejected"
VALID = {REVIEW_PENDING, REVIEW_ACTIVE, REVIEW_REJECTED}

_DESC_RE = re.compile(r"\[review_status:(pending|active|rejected)\]", re.I)


def normalize_review_status(raw: Any, description: str = "") -> str:
    s = str(raw or "").strip().lower()
    if s in VALID:
        return s
    m = _DESC_RE.search(description or "")
    if m:
        return m.group(1).lower()
    # 兼容旧草稿标记
    desc = description or ""
    if "[review_status:pending]" in desc or "[待审核]" in desc:
        return REVIEW_PENDING
    return REVIEW_ACTIVE


def ensure_pending_description(description: str, git_sha: str = "") -> str:
    desc = (description or "").strip()
    if "[review_status:pending]" not in desc:
        desc = "[待审核][由代码自动生成][review_status:pending] " + desc
    if git_sha and f"source_commit={git_sha}" not in desc:
        desc = (desc + f" source_commit={git_sha}")[:4000]
    return desc[:4000]


def mark_description_status(description: str, status: str) -> str:
    st = normalize_review_status(status)
    desc = _DESC_RE.sub(f"[review_status:{st}]", description or "")
    if f"[review_status:{st}]" not in desc:
        desc = f"[review_status:{st}] " + desc
    if st == REVIEW_PENDING and "[待审核]" not in desc:
        desc = "[待审核] " + desc
    if st == REVIEW_ACTIVE:
        desc = desc.replace("[待审核]", "[已激活]")
    if st == REVIEW_REJECTED and "[已拒绝]" not in desc:
        desc = "[已拒绝] " + desc
    return desc[:4000]


def case_is_ci_eligible(case: Dict[str, Any], *, include_pending: bool = False) -> bool:
    st = normalize_review_status(case.get("review_status"), str(case.get("description") or ""))
    if st == REVIEW_REJECTED:
        return False
    if st == REVIEW_PENDING:
        return bool(include_pending)
    return True


def filter_ci_case_ids(
    db: Any,
    case_ids: List[int],
    *,
    include_pending: bool = False,
) -> Dict[str, Any]:
    """过滤不可进门禁的用例；返回 kept/skipped。"""
    kept: List[int] = []
    skipped: List[Dict[str, Any]] = []
    for cid in case_ids:
        try:
            case = db.get_test_case_v2(int(cid))
        except Exception:
            case = None
        if not case:
            skipped.append({"case_id": cid, "reason": "not_found"})
            continue
        st = normalize_review_status(case.get("review_status"), str(case.get("description") or ""))
        # get_test_case_v2 可能尚无 review_status 字段
        if "review_status" not in case:
            try:
                # 尝试从 DB 扩展字段读取
                st = normalize_review_status(None, str(case.get("description") or ""))
            except Exception:
                st = REVIEW_ACTIVE
        fake = {**case, "review_status": st}
        if case_is_ci_eligible(fake, include_pending=include_pending):
            kept.append(int(cid))
        else:
            skipped.append({"case_id": int(cid), "reason": f"review_status={st}"})
    return {"kept": kept, "skipped": skipped}


def list_pending_cases(db: Any, project_id: int) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for ct in ("ui", "api"):
        try:
            cases = db.get_project_cases(int(project_id), case_type=ct) or []
        except Exception:
            cases = []
        for c in cases:
            st = normalize_review_status(c.get("review_status"), str(c.get("description") or ""))
            if st == REVIEW_PENDING:
                item = dict(c)
                item["review_status"] = st
                out.append(item)
    # 去重
    seen: Set[int] = set()
    uniq: List[Dict[str, Any]] = []
    for c in out:
        cid = c.get("id")
        if cid is None or int(cid) in seen:
            continue
        seen.add(int(cid))
        uniq.append(c)
    return uniq
