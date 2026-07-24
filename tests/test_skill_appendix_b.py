# -*- coding: utf-8 -*-
"""R11：Skill 附录 B 质量门禁。"""

from __future__ import annotations

from skills.skill_quality import GATE_SKILLS, validate_gate_skills, validate_skill_md


def test_gate_skills_pass_appendix_b():
    report = validate_gate_skills()
    assert report["checked"] == len(GATE_SKILLS)
    failed = [r for r in report["results"] if not r["ok"]]
    assert not failed, failed


def test_risk_guard_skill_has_l2_default():
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "skills" / "bundled" / "testory-risk-guard" / "SKILL.md"
    r = validate_skill_md(path)
    assert r["ok"], r["errors"]
    text = path.read_text(encoding="utf-8")
    assert "risk_default: L2" in text
    assert "RISK_APPROVAL_REQUIRED" in text
