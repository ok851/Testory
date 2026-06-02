# -*- coding: utf-8 -*-
"""Android Appium 执行封装。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def run_mobile_case_steps(
    steps: List[Dict[str, Any]],
    *,
    capabilities: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    from mobile_executor import get_mobile_executor

    executor = get_mobile_executor()
    if capabilities:
        executor.connect(capabilities)
    try:
        return executor.execute_steps(steps)
    finally:
        executor.disconnect()
