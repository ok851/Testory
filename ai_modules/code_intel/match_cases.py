# -*- coding: utf-8 -*-
"""ChangeImpactReport ↔ 用例库匹配 → recommended / at_risk case ids。"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set, Tuple


def _tokenize(text: str) -> Set[str]:
    raw = (text or "").lower()
    parts = re.split(r"[^\w\u4e00-\u9fff]+", raw)
    stop = {
        "the", "and", "for", "with", "from", "test", "case", "step", "ui", "api",
        "web", "login", "测试", "用例", "步骤", "功能", "页面",
    }
    out: Set[str] = set()
    for p in parts:
        t = p.strip()
        if len(t) < 2 or t in stop:
            continue
        out.add(t)
    return out


def _case_blob(case: Dict[str, Any]) -> str:
    bits = [
        str(case.get("name") or ""),
        str(case.get("description") or ""),
        str(case.get("precondition") or ""),
        str(case.get("expected_result") or ""),
        str(case.get("url") or ""),
        str(case.get("unit_name") or ""),
        str(case.get("case_role") or ""),
    ]
    return " ".join(bits)


def score_case_against_impact(
    case: Dict[str, Any],
    impact: Dict[str, Any],
) -> Tuple[float, List[str]]:
    """返回 (score, reasons)。score 越高越相关。"""
    reasons: List[str] = []
    score = 0.0
    blob = _case_blob(case).lower()
    case_tokens = _tokenize(blob)

    hints = [str(h).lower() for h in (impact.get("at_risk_case_hints") or [])]
    modules = [str(m).lower() for m in (impact.get("affected_modules") or [])]
    signals = impact.get("signals") if isinstance(impact.get("signals"), dict) else {}
    testids = [str(t).lower() for t in (signals.get("testids") or [])]
    path_tokens = [str(t).lower() for t in (signals.get("path_tokens") or [])]
    routes = [str(r).lower() for r in (signals.get("routes") or [])]

    for h in hints:
        if h and h in blob:
            score += 3.0
            reasons.append(f"hint:{h[:40]}")
        elif h:
            ht = _tokenize(h)
            inter = ht & case_tokens
            if inter:
                score += 1.5 * min(3, len(inter))
                reasons.append(f"hint_token:{','.join(list(inter)[:3])}")

    for m in modules:
        if m.startswith("route:"):
            continue
        if m and m in blob:
            score += 2.0
            reasons.append(f"module:{m[:40]}")
        else:
            mt = _tokenize(m)
            inter = mt & case_tokens
            if inter:
                score += 1.0 * min(2, len(inter))

    for tid in testids:
        if tid and tid in blob:
            score += 4.0
            reasons.append(f"testid:{tid[:40]}")

    for pt in path_tokens:
        if pt in case_tokens or (pt and pt in blob):
            score += 1.2
            reasons.append(f"path:{pt}")

    for r in routes:
        if r and r in blob:
            score += 2.5
            reasons.append(f"route:{r}")

    # 风险加权：高风险时略抬低阈值敏感度（分数本身不变，由调用方阈值控制）
    return score, reasons[:12]


def match_cases_to_impact(
    cases: List[Dict[str, Any]],
    impact: Dict[str, Any],
    *,
    min_score: float = 2.0,
    max_recommend: int = 40,
    use_embeddings: bool = True,
) -> Dict[str, Any]:
    """匹配用例，产出 recommended_case_ids / at_risk_case_ids / matches。"""
    embed_boosts = _embedding_boosts(cases, impact) if use_embeddings else {}

    scored: List[Dict[str, Any]] = []
    for case in cases or []:
        if not isinstance(case, dict):
            continue
        cid = case.get("id")
        if cid is None:
            continue
        # pending/rejected 仍可出现在 at_risk 提示，但不进推荐执行默认列表
        from ai_modules.code_intel.review import normalize_review_status, REVIEW_REJECTED

        rev = normalize_review_status(case.get("review_status"), str(case.get("description") or ""))
        if rev == REVIEW_REJECTED:
            continue

        sc, reasons = score_case_against_impact(case, impact)
        eb = float(embed_boosts.get(int(cid), 0.0))
        if eb > 0:
            sc += eb
            reasons.append(f"embed:+{eb:.2f}")
        if sc < min_score:
            continue
        scored.append({
            "case_id": int(cid),
            "case_name": case.get("name") or "",
            "score": round(sc, 2),
            "reasons": reasons,
            "review_status": rev,
            "at_risk": bool(impact.get("may_break_existing_cases")) and sc >= min_score + 1.0,
        })

    scored.sort(key=lambda x: (-float(x["score"]), int(x["case_id"])))
    top = scored[: max(1, min(int(max_recommend or 40), 100))]

    # 推荐执行：默认仅 active（pending 可在 matches 中看到）
    recommended = [
        m["case_id"] for m in top
        if m.get("review_status") != "pending"
    ]
    if not recommended:
        # 若全是 pending，仍返回匹配供人审，但不自动跑
        recommended = []
    at_risk = [m["case_id"] for m in top if m.get("at_risk")]

    note = ""
    if impact.get("is_rollback"):
        note = (
            "检测到回滚：建议恢复该 commit 对应的历史用例版本，不生成新用例；"
            "已保留 at_risk 推荐供回归确认"
        )
        return {
            "recommended_case_ids": recommended or [m["case_id"] for m in top],
            "at_risk_case_ids": at_risk or [m["case_id"] for m in top][:10],
            "matches": top,
            "note": note,
            "embedding_used": bool(embed_boosts),
        }

    return {
        "recommended_case_ids": recommended,
        "at_risk_case_ids": at_risk,
        "matches": top,
        "note": note,
        "embedding_used": bool(embed_boosts),
    }


def _embedding_boosts(
    cases: List[Dict[str, Any]],
    impact: Dict[str, Any],
) -> Dict[int, float]:
    """LOCAL_MEMORY_ENABLE 时用余弦相似度加权；失败则空。"""
    try:
        from ai_memory_store import memory_enabled, embed_text, _cosine
    except Exception:
        return {}
    if not memory_enabled():
        return {}

    query_bits = [
        " ".join(str(x) for x in (impact.get("affected_modules") or [])),
        " ".join(str(x) for x in (impact.get("at_risk_case_hints") or [])),
        str(impact.get("summary") or ""),
    ]
    signals = impact.get("signals") if isinstance(impact.get("signals"), dict) else {}
    query_bits.append(" ".join(str(t) for t in (signals.get("path_tokens") or [])[:20]))
    query_bits.append(" ".join(str(t) for t in (signals.get("testids") or [])[:20]))
    query = " ".join(query_bits).strip()
    if len(query) < 8:
        return {}

    try:
        qvec = embed_text(query[:2000])
    except Exception:
        return {}

    boosts: Dict[int, float] = {}
    for case in (cases or [])[:80]:
        if not isinstance(case, dict) or case.get("id") is None:
            continue
        blob = _case_blob(case)[:1500]
        if len(blob) < 8:
            continue
        try:
            cvec = embed_text(blob)
            sim = _cosine(qvec, cvec)
        except Exception:
            continue
        if sim >= 0.55:
            # 映射到约 0.5–3.0 的加分
            boosts[int(case["id"])] = round(min(3.0, (sim - 0.5) * 6.0), 2)
    return boosts


def load_project_cases_for_match(db: Any, project_id: int) -> List[Dict[str, Any]]:
    """拉取项目 UI+API 用例（匹配用）。"""
    out: List[Dict[str, Any]] = []
    try:
        out.extend(db.get_project_cases(int(project_id), case_type="ui") or [])
    except Exception:
        pass
    try:
        out.extend(db.get_project_cases(int(project_id), case_type="api") or [])
    except Exception:
        pass
    # 去重
    seen: Set[int] = set()
    uniq: List[Dict[str, Any]] = []
    for c in out:
        cid = c.get("id")
        if cid is None or int(cid) in seen:
            continue
        seen.add(int(cid))
        # 补齐 review_status
        try:
            from ai_modules.code_intel.review import normalize_review_status
            c = dict(c)
            c["review_status"] = normalize_review_status(
                c.get("review_status"), str(c.get("description") or "")
            )
        except Exception:
            pass
        uniq.append(c)
    return uniq
