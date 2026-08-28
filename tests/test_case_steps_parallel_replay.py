# -*- coding: utf-8 -*-
"""阶段5：回放并行 + 自愈 —— 回放端按 stage_info 重建跨端并行 stage。

验证：
- partition_steps_by_stage 无 stage_info → []（旧用例零影响，回归红线）
- 有 stage_info → 有序单元（serial + stage）保持原始顺序
- stage 重建 schema 正确（id/branches/layer/device_id/allow_partial/timeout_sec）
- PC 分支含 web 步骤 → 降级串行（playwright async 上下文无法进 ThreadPool）
- mobile 分支无 selector → uia_anchor bounds 中心降级 coordinate + degraded
- _execute_case_steps 并行路径：mock execute_cross_end_parallel_stage →
  分组执行 / 结果展开 / 失败终止后续单元 / allow_partial 部分失败不终止（warning 防假绿）
- _try_self_heal_step：失败步骤按 candidates 回退重定位，命中后 update_test_step 回写
"""
import asyncio
import json
from unittest import mock

from modules.web.playwright_automation import (
    PlaywrightAutomation,
    partition_steps_by_stage,
)


def _mk_step(
    action="click",
    layer="desktop",
    desc="",
    order=1,
    stage=None,
    branch=None,
    device_id="",
    selector=("css", "#btn"),
    uia_anchor=None,
    step_id=None,
):
    """构造 DB 形态 execution step（stage_info/uia_anchor 为 JSON 串，模拟落库读回）。"""
    s = {
        "id": step_id,
        "case_id": 1,
        "action": action,
        "selector_type": selector[0],
        "selector_value": selector[1],
        "input_value": "",
        "description": desc,
        "step_order": order,
        "automation_layer": layer,
        "mobile_spec": {},
        "desktop_spec": {},
        "cross_end_spec": "",
    }
    if device_id:
        s["device_id"] = device_id
    if stage is not None:
        # stage_info.layer 对齐录制端 _group_steps_into_stages：pc 分支 desktop / mobile 分支 mobile
        si_layer = "mobile" if branch == "mobile" else layer
        s["stage_info"] = json.dumps({
            "stage_id": stage,
            "branch": branch,
            "layer": si_layer,
            "device_id": device_id,
            "allow_partial": False,
            "timeout_sec": 600,
        })
    if uia_anchor is not None:
        s["uia_anchor"] = json.dumps(uia_anchor) if not isinstance(uia_anchor, str) else uia_anchor
    return s


def _fake_automation():
    """PlaywrightAutomation 实例（跳过 __init__，方法仍绑定；测试无需浏览器）。"""
    return PlaywrightAutomation.__new__(PlaywrightAutomation)


# ── 1. 无 stage_info → 原串行路径（回归红线） ──────────────────────────

def test_no_stage_info_returns_empty_units():
    steps = [
        _mk_step(action="navigate", layer="web", desc="nav", order=1),
        _mk_step(action="click", layer="web", desc="click", order=2),
    ]
    assert partition_steps_by_stage(steps) == []


def test_empty_input_returns_empty():
    assert partition_steps_by_stage([]) == []
    assert partition_steps_by_stage(None) == []


# ── 2. 有 stage_info → 有序单元 ─────────────────────────────────────────

def test_mixed_units_keep_original_order():
    steps = [
        _mk_step(action="navigate", layer="web", desc="nav", order=1),  # 无 stage：串行
        _mk_step(action="click", layer="desktop", desc="pc1", order=2, stage="stage_1", branch="pc"),
        _mk_step(action="tap", layer="android", desc="mob1", order=3, stage="stage_1", branch="mobile", device_id="emulator-5554"),
        _mk_step(action="tap", layer="android", desc="mob2", order=4, stage="stage_2", branch="mobile", device_id="emulator-5554"),
        _mk_step(action="click", layer="desktop", desc="pc3", order=5, stage="stage_2", branch="pc"),
    ]
    units = partition_steps_by_stage(steps)
    assert len(units) == 3
    assert units[0]["type"] == "serial"
    assert [s["description"] for s in units[0]["steps"]] == ["nav"]
    assert units[1]["type"] == "stage"
    assert units[1]["stage"]["id"] == "stage_1"
    assert [s["description"] for s in units[1]["stage"]["branches"][0]["steps"]] == ["pc1"]
    assert [s["description"] for s in units[1]["stage"]["branches"][1]["steps"]] == ["mob1"]
    assert units[2]["type"] == "stage"
    assert units[2]["stage"]["id"] == "stage_2"


# ── 3. stage 重建 schema ────────────────────────────────────────────────

def test_rebuilt_stage_schema():
    steps = [
        _mk_step(action="click", layer="desktop", desc="pc1", order=1, stage="stage_1", branch="pc"),
        _mk_step(action="tap", layer="android", desc="mob1", order=2, stage="stage_1", branch="mobile", device_id="emulator-5554"),
    ]
    units = partition_steps_by_stage(steps)
    assert len(units) == 1 and units[0]["type"] == "stage"
    stage = units[0]["stage"]
    assert stage["id"] == "stage_1"
    assert stage["cross_end_parallel"] is True
    assert stage["allow_partial"] is False
    assert stage["timeout_sec"] == 600.0
    assert [b["name"] for b in stage["branches"]] == ["pc", "mobile"]
    assert [b["layer"] for b in stage["branches"]] == ["desktop", "mobile"]
    assert stage["branches"][1]["device_id"] == "emulator-5554"


# ── 4. PC 分支含 web 步骤 → 降级串行 ────────────────────────────────────

def test_web_step_in_pc_branch_degrades_to_serial():
    steps = [
        _mk_step(action="click", layer="web", desc="web1", order=1, stage="stage_1", branch="pc"),
        _mk_step(action="tap", layer="android", desc="mob1", order=2, stage="stage_1", branch="mobile"),
    ]
    units = partition_steps_by_stage(steps)
    # PC 分支是 web 层（依赖 playwright async 上下文）→ 整个 stage 降级串行
    assert len(units) == 1
    assert units[0]["type"] == "serial"
    assert [s["description"] for s in units[0]["steps"]] == ["web1", "mob1"]


def test_single_branch_stage_degrades_to_serial():
    # 奇数尾段（仅 pc 分支）无法并行 → 降级串行，按 step_order 保持顺序
    steps = [
        _mk_step(action="click", layer="desktop", desc="pc3", order=5, stage="stage_2", branch="pc"),
        _mk_step(action="click", layer="desktop", desc="pc4", order=6, stage="stage_2", branch="pc"),
    ]
    units = partition_steps_by_stage(steps)
    assert len(units) == 1
    assert units[0]["type"] == "serial"
    assert [s["description"] for s in units[0]["steps"]] == ["pc3", "pc4"]


# ── 5. mobile 分支无 selector → coordinate 降级 ─────────────────────────

def test_mobile_branch_coordinate_degrade():
    ua = {
        "layer": "android",
        "candidates": [{"type": "text", "value": "登录", "score": 0.85}],
        "node": {
            "bounds": [100, 200, 300, 400],
            "text": "登录",
            "class": "android.widget.Button",
        },
    }
    steps = [
        _mk_step(action="click", layer="desktop", desc="pc1", order=1, stage="stage_1", branch="pc"),
        _mk_step(action="tap", layer="android", desc="mob1", order=2, stage="stage_1", branch="mobile",
                 selector=("", ""), uia_anchor=ua),
    ]
    units = partition_steps_by_stage(steps)
    assert len(units) == 1 and units[0]["type"] == "stage"
    mob_branch = units[0]["stage"]["branches"][1]
    mob_step = mob_branch["steps"][0]
    assert mob_step["selector_type"] == "coordinate"
    assert mob_step["selector_value"] == "200,300"  # bounds 中心 (100+300)/2, (200+400)/2
    assert mob_step.get("degraded") is True


# ── 6. _execute_case_steps 并行路径（mock scheduler） ────────────────────

def _parallel_result(branch_results):
    return {
        "ok_assert": all(b["ok"] for b in branch_results),
        "error": None,
        "elapsed_ms": 10,
        "stage_id": "stage_1",
        "branch_results": branch_results,
    }


async def _run_case(automation, steps):
    return await automation._execute_case_steps(steps)


def test_execute_case_steps_parallel_and_result_expand():
    steps = [
        _mk_step(action="click", layer="desktop", desc="pc1", order=1, stage="stage_1", branch="pc"),
        _mk_step(action="tap", layer="android", desc="mob1", order=2, stage="stage_1", branch="mobile"),
    ]
    automation = _fake_automation()
    automation._execution_context = None

    def _fake_stage(stage, **kw):
        assert stage["id"] == "stage_1"
        assert len(stage["branches"]) == 2
        return (
            _parallel_result([
                {"branch": "pc", "ok": True, "steps_executed": 1,
                 "step_results": [{"status": "success", "step": "click"}]},
                {"branch": "mobile", "ok": True, "steps_executed": 1,
                 "result_payload": {"results": [{"status": "success", "step": "tap"}]}},
            ]),
            {},
        )

    with mock.patch(
        "ai_modules.execute.multi_device_scheduler.execute_cross_end_parallel_stage",
        side_effect=_fake_stage,
    ) as m:
        out = asyncio.run(_run_case(automation, steps))

    m.assert_called_once()
    assert out["all_steps_done"] is True
    assert out["steps_completed"] == 2
    statuses = [r["status"] for r in out["step_results"]]
    assert statuses == ["success", "success"]  # mobile 分支结果从 result_payload.results 展开
    assert {r.get("branch") for r in out["step_results"]} == {"pc", "mobile"}


def test_stage_failure_terminates_following_units():
    steps = [
        _mk_step(action="click", layer="desktop", desc="pc1", order=1, stage="stage_1", branch="pc"),
        _mk_step(action="tap", layer="android", desc="mob1", order=2, stage="stage_1", branch="mobile"),
        _mk_step(action="navigate", layer="web", desc="nav", order=3),  # 后续串行单元
    ]

    def _fake_stage(stage, **kw):
        return (
            _parallel_result([
                {"branch": "pc", "ok": False, "steps_executed": 1,
                 "step_results": [{"status": "error", "step": "click", "error": "boom"}]},
                {"branch": "mobile", "ok": True, "steps_executed": 1,
                 "result_payload": {"results": [{"status": "success", "step": "tap"}]}},
            ]),
            {},
        )

    automation = _fake_automation()
    automation._execution_context = None
    with mock.patch(
        "ai_modules.execute.multi_device_scheduler.execute_cross_end_parallel_stage",
        side_effect=_fake_stage,
    ):
        out = asyncio.run(_run_case(automation, steps))

    # stage 失败 → 终止后续串行单元（nav 未被标记完成）
    assert out["all_steps_done"] is False
    assert any(r["status"] == "error" for r in out["step_results"])
    assert not any(r.get("step") == "navigate" for r in out["step_results"])


def test_allow_partial_stage_does_not_terminate():
    # 部分分支失败 + allow_partial → stage ok_assert=True → 继续后续单元，warning 防假绿
    steps = [
        _mk_step(action="click", layer="desktop", desc="pc1", order=1, stage="stage_1", branch="pc"),
        _mk_step(action="tap", layer="android", desc="mob1", order=2, stage="stage_1", branch="mobile"),
        _mk_step(action="navigate", layer="web", desc="nav", order=3),
    ]
    # 手动把 stage_1 的 allow_partial 改成 True（stage_info JSON 串重写）
    for s in (steps[0], steps[1]):
        si = json.loads(s["stage_info"])
        si["allow_partial"] = True
        s["stage_info"] = json.dumps(si)

    def _fake_stage(stage, **kw):
        assert stage["allow_partial"] is True
        return (
            {
                "ok_assert": True,
                "partial_success": True,
                "error": "mobile: 设备无响应",
                "failed_branches": ["mobile"],
                "elapsed_ms": 5,
                "stage_id": "stage_1",
                "branch_results": [
                    {"branch": "pc", "ok": True, "steps_executed": 1,
                     "step_results": [{"status": "success", "step": "click"}]},
                    {"branch": "mobile", "ok": False, "steps_executed": 0,
                     "error": "设备无响应", "result_payload": {}},
                ],
            },
            {},
        )

    class _SerialFactory:
        """串行单元（navigate）走 fake 执行器，避免真实 playwright。"""

        async def execute_step_async(self, step, automation):
            return [{"status": "success", "step": step.get("action")}]

    automation = _fake_automation()
    automation._execution_context = None
    with mock.patch(
        "ai_modules.execute.multi_device_scheduler.execute_cross_end_parallel_stage",
        side_effect=_fake_stage,
    ), mock.patch(
        "modules.execution.execution_factory.get_executor_factory",
        return_value=_SerialFactory(),
    ):
        out = asyncio.run(_run_case(automation, steps))

    # 不终止：后续 nav 单元被执行；warning 条目交给下游 evaluate_batch_case_status 防假绿
    assert out["all_steps_done"] is True  # 单元全部执行完，不中断
    assert any(r["status"] == "warning" for r in out["step_results"])
    assert any(r.get("step") == "navigate" for r in out["step_results"])


# ── 7. 自愈：candidates 回退重定位 + DB 回写 ────────────────────────────

class _FakeFactory:
    """mock 执行器：第一个候选失败，第二个候选成功。"""

    def __init__(self):
        self.calls = []

    async def execute_step_async(self, step, automation):
        self.calls.append(step)
        if step.get("selector_type") == "css" and step.get("selector_value") == "#bad":
            return [{"status": "error", "step": step.get("action"), "error": "定位失败"}]
        return [{"status": "success", "step": step.get("action")}]


def test_self_heal_fallback_and_db_writeback():
    ua = {
        "layer": "desktop",
        "candidates": [
            {"type": "css", "value": "#bad", "score": 0.9},
            {"type": "css", "value": "#good", "score": 0.7},
        ],
    }
    step = _mk_step(
        action="click", layer="web", desc="login", order=1,
        selector=("css", "#old"), uia_anchor=ua, step_id=42,
    )
    factory = _FakeFactory()
    automation = _fake_automation()
    written = {}

    def _fake_update(step_id, **kw):
        written["step_id"] = step_id
        written.update(kw)
        return True

    with mock.patch(
        "modules.execution.execution_factory.get_executor_factory",
        return_value=factory,
    ), mock.patch("database.Database.update_test_step", side_effect=_fake_update):
        results = asyncio.run(automation._try_self_heal_step(step))

    assert results is not None
    assert results[0]["status"] == "success"
    # 第一次尝试 #bad 失败，第二次 #good 命中
    assert factory.calls[0]["selector_value"] == "#bad"
    assert factory.calls[1]["selector_value"] == "#good"
    # DB 回写命中项
    assert written["step_id"] == 42
    assert written["selector_type"] == "css"
    assert written["selector_value"] == "#good"


def test_self_heal_no_candidates_returns_none():
    step = _mk_step(action="click", layer="web", desc="login", order=1, step_id=42)
    automation = _fake_automation()
    assert asyncio.run(automation._try_self_heal_step(step)) is None


# ── 8. 学习库 alternates：候选 selector 进入 Hermes 知识库 ───────────────

def test_extract_selectors_learns_uia_candidates():
    from modules.hermes.ai_hermes_skills import extract_selectors_from_plan

    plan = {
        "steps": [
            {
                "action": "click",
                "selector": "#old",
                "selector_type": "css",
                "locator_candidates": [
                    {"selector_value": "#bad", "score": 0.9},
                    "#cand2",
                ],
                "uia_anchor": json.dumps({"candidates": [{"value": "#good", "score": 0.7}]}),
            }
        ]
    }
    items = extract_selectors_from_plan(plan)
    assert len(items) == 1
    alts = items[0]["alternates"]
    assert "#bad" in alts
    assert "#cand2" in alts
    assert "#good" in alts
    assert "#old" not in alts  # 主 selector 不进 alternates（由 learn 侧剔除）
