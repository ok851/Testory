# -*- coding: utf-8 -*-
"""阶段4：落库 JSON 列扩展 —— test_steps 加 stage_info/uia_anchor/verification/cross_end_spec。

验证：
- create_test_step 新列往返一致（dict → JSON 串落库，读回同串）
- cross_end 层不再强转 web、cross_end_spec 保留（阶段4-前置 bug 修复回归）
- 旧行新列 NULL → 读取返回空串不报错（旧库兼容）
- batch_insert_steps（录制器主路径）透传新字段
"""
import json

import pytest

from database import Database, _TEST_STEPS_SELECT


@pytest.fixture()
def db(tmp_path):
    # 每次测试强制迁移到独立临时库，避免类级 _schema_initialized 复用真实库
    Database._schema_initialized = False
    d = Database(str(tmp_path / "test.db"))
    yield d
    Database._schema_initialized = False


def _mk_project(db):
    conn = db._sqlite_connect()
    cur = conn.execute("INSERT INTO projects (name, description) VALUES (?, ?)", ("测试项目", ""))
    pid = cur.lastrowid
    conn.commit()
    conn.close()
    return pid


def _mk_case(db):
    pid = _mk_project(db)
    cid = db.create_test_case_v2(
        project_id=pid,
        name="跨端联排用例",
        url="",
        description="",
        platform="desktop",
        generated_by_ai=True,
    )
    return cid


def test_create_step_roundtrip_new_columns(db):
    cid = _mk_case(db)
    stage_info = {"stage_id": "stage_1", "branch": "pc", "layer": "desktop", "device_id": "", "allow_partial": False, "timeout_sec": 600}
    uia_anchor = {"layer": "desktop", "candidates": [{"type": "automation_id", "value": "LoginBtn", "score": 0.9}]}
    verification = {"found": True, "matched_via": "automation_id"}
    sid = db.create_test_step(
        cid,
        action="click",
        selector_type="automation_id",
        selector_value="LoginBtn",
        automation_layer="desktop",
        stage_info=stage_info,
        uia_anchor=uia_anchor,
        verification=verification,
    )
    row = db.get_test_step(sid)
    assert row["automation_layer"] == "desktop"
    assert json.loads(row["stage_info"]) == stage_info
    assert json.loads(row["uia_anchor"]) == uia_anchor
    assert json.loads(row["verification"]) == verification


def test_cross_end_layer_and_spec_kept(db):
    # 阶段4-前置 bug：cross_end 层曾被强转 web、cross_end_spec 丢弃
    cid = _mk_case(db)
    spec = {"method": "GET", "url": "https://example.com/api/otp", "timeout_sec": 15}
    sid = db.create_test_step(
        cid,
        action="extract_otp",
        automation_layer="cross_end",
        cross_end_spec=spec,
    )
    row = db.get_test_step(sid)
    assert row["automation_layer"] == "cross_end"
    assert json.loads(row["cross_end_spec"]) == spec


def test_legacy_row_null_columns_readable(db):
    # 旧行新列 NULL → 读取返回空串，不报错
    cid = _mk_case(db)
    conn = db._sqlite_connect()
    conn.execute(
        """INSERT INTO test_steps
           (case_id, action, selector_type, selector_value, input_value, description, step_order)
           VALUES (?, 'click', 'css', '#old', '', '旧步骤', 1)""",
        (cid,),
    )
    conn.commit()
    sid = conn.execute("SELECT id FROM test_steps WHERE case_id = ?", (cid,)).fetchone()["id"]
    conn.close()
    row = db.get_test_step(sid)
    assert row["stage_info"] == ""
    assert row["uia_anchor"] == ""
    assert row["verification"] == ""
    assert row["cross_end_spec"] == ""
    assert row["action"] == "click"


def test_batch_insert_steps_keeps_new_fields(db):
    # 录制器主路径：batch_insert_steps 透传 阶段1/3/4 字段
    cid = _mk_case(db)
    steps = [
        {
            "action": "tap",
            "automation_layer": "android",
            "selector_type": "id",
            "selector_value": "com.example:id/login_btn",
            "description": "手机点登录",
            "device_id": "emulator-5554",
            "uia_anchor": {"layer": "android", "candidates": [{"type": "id", "value": "com.example:id/login_btn"}]},
            "verification": {"found": True, "matched_via": "id"},
            "stage_info": {"stage_id": "stage_1", "branch": "mobile", "layer": "mobile", "device_id": "emulator-5554"},
            "cross_end_spec": "",
        },
        {
            "action": "click",
            "automation_layer": "desktop",
            "selector_type": "automation_id",
            "selector_value": "ConfirmBtn",
            "description": "PC 点确定",
            "stage_info": {"stage_id": "stage_1", "branch": "pc", "layer": "desktop"},
        },
    ]
    ok = db.batch_insert_steps(cid, steps)
    assert ok is True
    rows = db.get_case_steps(cid)
    assert len(rows) == 2
    mob = next(r for r in rows if r["automation_layer"] == "android")
    pc = next(r for r in rows if r["automation_layer"] == "desktop")
    assert json.loads(mob["uia_anchor"])["layer"] == "android"
    assert json.loads(mob["verification"])["found"] is True
    assert json.loads(mob["stage_info"])["branch"] == "mobile"
    assert json.loads(pc["stage_info"])["branch"] == "pc"


def test_select_columns_match_row_dict():
    # _TEST_STEPS_SELECT 与 _row_to_step_dict 索引一致（防列序漂移）
    cols = [c.strip() for c in _TEST_STEPS_SELECT.split(",")]
    assert "stage_info" in cols
    assert "uia_anchor" in cols
    assert "verification" in cols
    assert "cross_end_spec" in cols
    # 行索引：cross_end_spec=23 / stage_info=24 / uia_anchor=25 / verification=26
    assert cols.index("cross_end_spec") == 23
    assert cols.index("stage_info") == 24
    assert cols.index("uia_anchor") == 25
    assert cols.index("verification") == 26
