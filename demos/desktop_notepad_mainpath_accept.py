# -*- coding: utf-8 -*-
"""真机 Desktop 主路径验收（记事本）。

用法：
  python demos/desktop_notepad_mainpath_accept.py

退出码：0 成功；1 失败（诚实失败，含 error_code）；2 预检未通过。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def main() -> int:
    from ai_modules.execute.desktop_preflight import (
        build_notepad_mainpath_plan,
        check_desktop_preflight,
    )
    from ai_modules.execute.orchestrator import execute_cross_end_plan
    from desktop_run_context import reset_desktop_run_context

    pre = check_desktop_preflight()
    print(json.dumps({"preflight": pre}, ensure_ascii=False, indent=2))
    if not pre.get("ok"):
        print("ACCEPT: SKIP/FAIL preflight → DESKTOP_NO_SESSION", flush=True)
        return 2

    reset_desktop_run_context()
    plan = build_notepad_mainpath_plan()
    result = execute_cross_end_plan(
        plan,
        user_id="desktop-accept",
        project_id=None,
        record_history=True,
    )
    summary = {
        "success": result.get("success"),
        "gate_passed": result.get("gate_passed"),
        "error": result.get("error"),
        "error_code": result.get("error_code"),
        "stage_results": [
            {
                "stage_id": s.get("stage_id") or s.get("id"),
                "ok_assert": s.get("ok_assert"),
                "error": s.get("error"),
                "error_code": s.get("error_code"),
                "steps_executed": s.get("steps_executed"),
            }
            for s in (result.get("stage_results") or [])
        ],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if result.get("success") and result.get("gate_passed") is not False:
        print("ACCEPT: PASS desktop notepad mainpath", flush=True)
        return 0
    print("ACCEPT: FAIL (honest)", flush=True)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
