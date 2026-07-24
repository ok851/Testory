# -*- coding: utf-8 -*-
"""skills 包：bundled Hermes Skills + 附录 B 质量校验。

请使用::

    from skills.skill_quality import validate_gate_skills
    python -m skills.skill_quality
"""

__all__ = ["GATE_SKILLS", "validate_gate_skills", "validate_skill_md"]


def __getattr__(name: str):
    if name in __all__:
        from skills import skill_quality as sq

        return getattr(sq, name)
    raise AttributeError(name)
