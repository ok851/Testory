# -*- coding: utf-8 -*-
"""Web / Android / Desktop UI 功能用例生成（封装 ai_local_inference）。"""

from __future__ import annotations

from typing import Any, Dict, Optional


def generate_functional_case(
    goal: str,
    project_name: str = "",
    *,
    platform_type: str = "web",
    model: str = "",
    profile: Optional[Dict[str, Any]] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    from modules.ai.ai_local_inference import local_ai_service

    return local_ai_service.generate_case_and_steps(
        goal,
        project_name,
        model=model,
        profile=profile,
        platform_type=platform_type,
        **kwargs,
    )
