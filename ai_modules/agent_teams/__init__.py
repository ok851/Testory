# -*- coding: utf-8 -*-
"""多 Agent 协同控制面（Phase A）：TestRunState + Planner/Executor/Verifier。

赛期可对齐 AgentTeams；本包先提供本地可运行骨架，不依赖外部 SDK。
"""

from .test_run_state import TestRunState, load_run, save_run
from .team_runner import run_cross_end_qa_team, load_team_spec

__all__ = [
    "TestRunState",
    "load_run",
    "save_run",
    "run_cross_end_qa_team",
    "load_team_spec",
]
