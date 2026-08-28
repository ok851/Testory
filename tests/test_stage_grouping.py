# -*- coding: utf-8 -*-
"""阶段3：Stage 级并行产出 —— 录制期把相邻同端步骤聚合成 PC/手机 branches。

验证：
- [pc,pc,mob,mob,pc] → 2 stages（stage1 双分支并行，尾段 pc 单分支 stage）
- 纯 pc / 纯 mob → stages 空（单端兼容红线，旧用例零影响）
- 产出 stage 可被 scheduler is_cross_end_parallel_stage 识别
- mobile branch.device_id 从录制 serial 透传
- cross_end（extract_otp/api_call）步骤不进分支、不阻断分组
- normalize_ai_step 白名单透传 uia_anchor/verification/device_id（防 L160/171/200 pop 误删）
"""
import json

from modules.ai.ai_action_recorder import ActionRecorder, _group_steps_into_stages, _side_for_layer
from modules.ai.ai_step_normalization import normalize_ai_step


def _step(action, layer, desc="", device_id=""):
    s = {"action": action, "automation_layer": layer, "description": desc}
    if device_id:
        s["device_id"] = device_id
    return s


def test_alternating_runs_produce_two_stages():
    # [pc,pc,mob,mob,pc] → runs=[pc,mob,pc] → stage1 双分支 + 尾段 pc 单分支 stage
    steps = [
        _step("click", "desktop", desc="pc1"),
        _step("click", "desktop", desc="pc2"),
        _step("tap", "android", desc="mob1", device_id="emulator-5554"),
        _step("tap", "android", desc="mob2", device_id="emulator-5554"),
        _step("click", "desktop", desc="pc3"),
    ]
    stages = _group_steps_into_stages(steps)
    assert len(stages) == 2

    s1 = stages[0]
    assert s1["id"] == "stage_1"
    assert s1["cross_end_parallel"] is True
    assert s1["allow_partial"] is False
    assert s1["timeout_sec"] == 600
    assert [b["name"] for b in s1["branches"]] == ["pc", "mobile"]
    assert [b["layer"] for b in s1["branches"]] == ["desktop", "mobile"]
    assert [s["description"] for s in s1["branches"][0]["steps"]] == ["pc1", "pc2"]
    assert [s["description"] for s in s1["branches"][1]["steps"]] == ["mob1", "mob2"]
    assert s1["branches"][1]["device_id"] == "emulator-5554"

    s2 = stages[1]
    assert s2["id"] == "stage_2"
    assert [b["name"] for b in s2["branches"]] == ["pc"]
    assert [s["description"] for s in s2["branches"][0]["steps"]] == ["pc3"]


def test_single_side_never_produces_stages():
    # 纯 pc / 纯 mob → 全局门槛不满足 → stages 空（旧用例零影响）
    assert _group_steps_into_stages(
        [_step("click", "desktop", desc="a"), _step("click", "desktop", desc="b")]
    ) == []
    assert _group_steps_into_stages(
        [_step("tap", "android", desc="a"), _step("tap", "android", desc="b")]
    ) == []
    assert _group_steps_into_stages([]) == []


def test_stages_recognized_by_scheduler():
    from ai_modules.execute.multi_device_scheduler import is_cross_end_parallel_stage

    steps = [
        _step("click", "desktop", desc="pc1"),
        _step("tap", "android", desc="mob1"),
        _step("click", "desktop", desc="pc2"),
    ]
    stages = _group_steps_into_stages(steps)
    assert len(stages) == 2
    for st in stages:
        assert is_cross_end_parallel_stage(st) is True


def test_device_id_passthrough_from_serial():
    # 移动步骤 serial 在录制期写入 device_id → mobile branch 携带
    rec = ActionRecorder(platform="desktop")
    rec.capture_from_tool_event(
        name="mobile_tap",
        args={"text": "登录", "serial": "emulator-5554"},
        result={"ok": True, "text": "登录"},
    )
    rec.capture_from_tool_event(
        name="windows_click_element",
        args={"description": "确定"},
        result={"ok": True, "matched": "确定"},
    )
    plan, _warnings = rec.build_normalized_plan(instruction="双端登录")
    stages = plan.get("stages") or []
    assert len(stages) == 1
    mob = [b for b in stages[0]["branches"] if b["name"] == "mobile"]
    assert mob and mob[0]["device_id"] == "emulator-5554"
    # 线性步骤仍完整保留（stage 只是并行 hint，不替换 steps）
    assert len(plan.get("steps") or []) == 2


def test_cross_end_steps_stay_out_of_branches():
    # extract_otp（cross_end）不参与分组：不进分支、不阻断两端聚合
    steps = [
        _step("click", "desktop", desc="pc1"),
        {"action": "extract_otp", "automation_layer": "cross_end", "description": "otp"},
        _step("tap", "android", desc="mob1", device_id="d1"),
        _step("tap", "android", desc="mob2", device_id="d1"),
        _step("click", "desktop", desc="pc2"),
    ]
    stages = _group_steps_into_stages(steps)
    assert len(stages) == 2
    all_branch_steps = [
        s
        for st in stages
        for b in st["branches"]
        for s in b["steps"]
    ]
    assert all(s.get("description") != "otp" for s in all_branch_steps)
    assert [s["description"] for s in all_branch_steps] == ["pc1", "mob1", "mob2", "pc2"]


def test_normalize_keeps_new_fields():
    # normalize_ai_step 白名单透传 uia_anchor/verification/device_id（pytest 固化防误删）
    raw = {
        "action": "tap",
        "automation_layer": "android",
        "selector_value": "com.example:id/login_btn",
        "uia_anchor": {
            "layer": "android",
            "candidates": [{"type": "id", "value": "com.example:id/login_btn", "score": 0.95}],
        },
        "verification": {"found": True, "matched_via": "id"},
        "device_id": "emulator-5554",
    }
    out = normalize_ai_step(raw)
    assert out["device_id"] == "emulator-5554"
    assert isinstance(out["uia_anchor"], str)
    assert json.loads(out["uia_anchor"])["layer"] == "android"
    assert isinstance(out["verification"], str)
    assert json.loads(out["verification"])["found"] is True
    # selector 仍正确保留
    assert out["selector_value"] == "com.example:id/login_btn"


def _loads_jsonish(v):
    """plan 内存中 stage_info 是 dict；落库后读回是 JSON 串。统一解析断言。"""
    return v if isinstance(v, dict) else json.loads(v)


def test_stage_info_expanded_back_onto_steps():
    # stages 展开写回每步 stage_info（随 plan 序列化，落库/回放自动携带）
    rec = ActionRecorder(platform="desktop")
    rec.capture_from_tool_event(
        name="mobile_tap",
        args={"text": "登录", "serial": "emulator-5554"},
        result={"ok": True, "text": "登录"},
    )
    rec.capture_from_tool_event(
        name="windows_click_element",
        args={"description": "确定"},
        result={"ok": True, "matched": "确定"},
    )
    plan, _warnings = rec.build_normalized_plan(instruction="双端登录")
    steps = plan.get("steps") or []
    assert len(steps) == 2
    by_layer = {s["automation_layer"]: s for s in steps}
    mob_si = _loads_jsonish(by_layer["android"]["stage_info"])
    assert mob_si["stage_id"] == "stage_1"
    assert mob_si["branch"] == "mobile"
    assert mob_si["device_id"] == "emulator-5554"
    pc_si = _loads_jsonish(by_layer["desktop"]["stage_info"])
    assert pc_si["stage_id"] == "stage_1"
    assert pc_si["branch"] == "pc"


def test_side_for_layer_mapping():
    assert _side_for_layer("web") == "pc"
    assert _side_for_layer("desktop") == "pc"
    assert _side_for_layer("android") == "mobile"
    assert _side_for_layer("mobile") == "mobile"
    assert _side_for_layer("cross_end") == ""
    assert _side_for_layer("") == ""
