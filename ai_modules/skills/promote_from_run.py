# -*- coding: utf-8 -*-
"""Skill 沉淀（Phase B）：从成功的跨端 / AgentTeams 运行生成可复用 Skill 草稿。

诚实约束：仅当 run 终态 success 才允许沉淀；失败 run 拒绝并返回明确错误码。
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def _data_root() -> Path:
    import os

    env = (os.environ.get("UAT_DATA_DIR") or "").strip()
    if env:
        root = Path(env).expanduser().resolve()
    else:
        root = Path(__file__).resolve().parents[2] / "data"
    d = root / "skills_promoted"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _slug(name: str) -> str:
    s = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", (name or "skill").strip(), flags=re.UNICODE)
    s = re.sub(r"-+", "-", s).strip("-").lower()
    return (s or "skill")[:64]


def promote_plan_to_skill_draft(
    plan: Dict[str, Any],
    *,
    skill_name: str = "",
    source: str = "cross_end",
    run_id: str = "",
    evidence_level: str = "",
    force: bool = False,
    success: bool = True,
) -> Tuple[Optional[Path], Dict[str, Any]]:
    """将 CrossEnd plan 沉淀为 SKILL.md 草稿。

    ``success=False`` 且非 ``force`` 时拒绝（防失败轨迹当绿沉淀）。
    """
    if not success and not force:
        return None, {
            "ok": False,
            "error_code": "PROMOTE_REQUIRES_SUCCESS",
            "error": "仅成功运行可沉淀为 Skill；失败轨迹请先修复再沉淀（或显式 force=true 仅作草稿）",
        }
    if not isinstance(plan, dict) or not (plan.get("stages") or plan.get("steps")):
        return None, {
            "ok": False,
            "error_code": "PROMOTE_EMPTY_PLAN",
            "error": "plan.stages / plan.steps 为空，无法沉淀",
        }

    name = (skill_name or plan.get("scenario") or plan.get("plan_id") or "promoted-flow").strip()
    slug = _slug(name)
    ts = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    out_dir = _data_root() / f"{slug}-{ts}"
    out_dir.mkdir(parents=True, exist_ok=True)

    stages = plan.get("stages") if isinstance(plan.get("stages"), list) else []
    steps = plan.get("steps") if isinstance(plan.get("steps"), list) else []
    body_lines: List[str] = [
        f"# {name}",
        "",
        f"> 由 Testory 自动沉淀（source={source}）· 草稿，须人工审阅后纳入 bundled Skills。",
        f"> run_id={run_id or '—'} · evidence_level={evidence_level or '—'} · success={success}",
        "",
        "## 场景",
        "",
        str(plan.get("scenario") or plan.get("goal") or name),
        "",
        "## 阶段 / 步骤摘要",
        "",
    ]
    if stages:
        for i, st in enumerate(stages, 1):
            if not isinstance(st, dict):
                continue
            body_lines.append(
                f"{i}. **{st.get('id') or st.get('stage_id') or f'stage-{i}'}** "
                f"({st.get('layer') or st.get('automation_layer') or '?'}) "
                f"{st.get('label') or st.get('name') or ''}".rstrip()
            )
            for step in st.get("steps") or []:
                if isinstance(step, dict):
                    body_lines.append(
                        f"   - `{step.get('action') or '?'}` "
                        f"{(step.get('description') or '')[:80]}"
                    )
    elif steps:
        for i, step in enumerate(steps, 1):
            if isinstance(step, dict):
                body_lines.append(
                    f"{i}. `{step.get('action') or '?'}` {(step.get('description') or '')[:100]}"
                )

    body_lines.extend(
        [
            "",
            "## 诚实约束",
            "",
            "- 本 Skill 草稿不保证可直接在无环境节点绿跑",
            "- 桌面/HITL/Risk 步骤须具备真实会话；失败不得假绿",
            "",
            "## 原始 plan（JSON）",
            "",
            "```json",
            json.dumps(plan, ensure_ascii=False, indent=2)[:12000],
            "```",
            "",
        ]
    )
    skill_md = out_dir / "SKILL.md"
    skill_md.write_text("\n".join(body_lines), encoding="utf-8")
    meta = {
        "ok": True,
        "skill_name": name,
        "slug": slug,
        "path": str(skill_md),
        "dir": str(out_dir),
        "source": source,
        "run_id": run_id,
        "success": success,
        "draft": True,
    }
    (out_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return skill_md, meta


def promote_agent_run(
    state: Any,
    *,
    skill_name: str = "",
    force: bool = False,
) -> Tuple[Optional[Path], Dict[str, Any]]:
    """从 TestRunState 沉淀。"""
    status = str(getattr(state, "status", "") or "")
    success = status == "success"
    plan = getattr(state, "plan", None) if isinstance(getattr(state, "plan", None), dict) else {}
    report = getattr(state, "report", None) if isinstance(getattr(state, "report", None), dict) else {}
    return promote_plan_to_skill_draft(
        plan,
        skill_name=skill_name or str(getattr(state, "goal", "") or ""),
        source="agent_teams",
        run_id=str(getattr(state, "run_id", "") or ""),
        evidence_level=str(report.get("evidence_level") or ""),
        force=force,
        success=success,
    )


def list_promoted_skills(limit: int = 50) -> List[Dict[str, Any]]:
    root = _data_root()
    rows: List[Dict[str, Any]] = []
    for meta_path in sorted(root.glob("*/meta.json"), reverse=True):
        try:
            rows.append(json.loads(meta_path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
        if len(rows) >= max(1, min(int(limit or 50), 200)):
            break
    return rows
