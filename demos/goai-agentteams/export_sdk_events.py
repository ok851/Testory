# -*- coding: utf-8 -*-
"""导出本地 AgentTeams 运行为 SDK 风格事件（官方 SDK 可选）。

用法（仓库根）:
  python demos/goai-agentteams/export_sdk_events.py
  python demos/goai-agentteams/export_sdk_events.py --from-sample
  python demos/goai-agentteams/export_sdk_events.py --run-id <id>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_DEMO = Path(__file__).resolve().parent
_SAMPLE_STATE = _DEMO / "samples" / "output" / "test_run_state.json"
_DEFAULT_OUT = _ROOT / "artifacts" / "goai-agentteams" / "sdk_bridge"


def _state_from_sample() -> object:
    from ai_modules.agent_teams.test_run_state import TestRunState

    raw = json.loads(_SAMPLE_STATE.read_text(encoding="utf-8"))
    # 样例可能是 dict；用 create + 回填关键字段
    st = TestRunState.create(goal=str(raw.get("goal") or "sample"))
    if raw.get("run_id"):
        st.run_id = str(raw["run_id"])
    st.status = str(raw.get("status") or st.status)
    st.events = list(raw.get("events") or [])
    st.plan = raw.get("plan") if isinstance(raw.get("plan"), dict) else st.plan
    st.report = raw.get("report") if isinstance(raw.get("report"), dict) else st.report
    if not st.events:
        for role in ("Planner", "RiskAdvisor", "DesktopExecutor", "WebApiExecutor", "Verifier"):
            st.emit(agent=role, kind="note", message=f"sample timeline {role}")
    return st


def _state_synthetic() -> object:
    from ai_modules.agent_teams.test_run_state import TestRunState

    st = TestRunState.create(goal="sdk-bridge-export-demo")
    for role, kind in (
        ("Planner", "dispatch"),
        ("RiskAdvisor", "note"),
        ("DesktopExecutor", "dispatch"),
        ("WebApiExecutor", "complete"),
        ("Verifier", "complete"),
    ):
        st.emit(agent=role, kind=kind, message=f"{role} {kind}")
    st.set_status("failed")  # 演示导出不假绿
    st.report = {"passed": False, "reason": "demo export — not a green claim"}
    return st


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Export AgentTeams SDK-style events")
    p.add_argument("--from-sample", action="store_true", help="从 samples/output 状态导出")
    p.add_argument("--run-id", default="", help="从 data/agent_team_runs 加载")
    p.add_argument("--out", type=Path, default=_DEFAULT_OUT)
    args = p.parse_args(argv)

    if args.run_id:
        from ai_modules.agent_teams.test_run_state import load_run

        st = load_run(args.run_id.strip())
        if not st:
            print(json.dumps({"ok": False, "error": "run 不存在"}, ensure_ascii=False))
            return 1
    elif args.from_sample and _SAMPLE_STATE.is_file():
        st = _state_from_sample()
    else:
        st = _state_synthetic()

    from ai_modules.agent_teams.sdk_bridge import export_sdk_events_bundle

    out = export_sdk_events_bundle(st, out_dir=args.out)
    printable = {
        "ok": out.get("ok"),
        "paths": out.get("paths"),
        "event_count": len((out.get("payload") or {}).get("events") or []),
        "sdk_available": (out.get("payload") or {}).get("sdk_available"),
        "status": (out.get("payload") or {}).get("status"),
        "case_pass_claimed": (out.get("payload") or {}).get("case_pass_claimed"),
    }
    print(json.dumps(printable, ensure_ascii=False, indent=2))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
