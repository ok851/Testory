# -*- coding: utf-8 -*-
"""客户向审计交付包。"""

from __future__ import annotations

import json
import os
import tempfile
import zipfile
from pathlib import Path

from database import Database
from ai_modules.execute.cross_end_run_audit import build_audit_record, persist_to_database
from ai_modules.execute.customer_audit_pack import build_customer_audit_pack


def test_customer_audit_pack_indexes_failures_without_greenwash():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    Database._schema_initialized = False
    try:
        db = Database(path)
        pid = db.create_project("cust-audit", "")
        cid = db.create_test_case_v2(pid, "ok-case", case_type="ui", platform="web")
        db.create_run_history(
            cid, "success", 1.0, "", "", "", test_type="web", project_id=pid
        )
        audit = build_audit_record(
            {
                "success": False,
                "error_code": "RISK_APPROVAL_REQUIRED",
                "gate_passed": False,
                "stage_results": [
                    {
                        "stage_id": "r",
                        "ok_assert": False,
                        "risk_level": "L2",
                        "risk_decision": "require_approval",
                        "risk_approval_id": "a1",
                        "risk_events": [{"kind": "require"}],
                        "error_code": "RISK_APPROVAL_REQUIRED",
                    }
                ],
            },
            plan={"scenario": "l2-block"},
            project_id=pid,
        )
        persist_to_database(audit, db=db)

        out = Path(tempfile.mkdtemp()) / "pack"
        exported = build_customer_audit_pack(
            project_id=pid,
            db=db,
            out_dir=out,
            make_zip=True,
            scan_limit=50,
            embed_limit=5,
        )
        assert exported["ok"] is True
        assert exported["indexed_runs"] >= 2
        man = exported["manifest"]
        assert man["honesty"]["no_false_green"] is True
        assert man["counts"]["failed_or_other"] >= 1
        assert man["counts"]["gate_blocked"] >= 1

        pack = Path(exported["pack_dir"])
        assert (pack / "CUSTOMER_README.md").is_file()
        assert (pack / "index.json").is_file()
        assert (pack / "governance.json").is_file()
        index = json.loads((pack / "index.json").read_text(encoding="utf-8"))
        assert any(r.get("gate_blocked") for r in index["runs"])
        assert any(not r.get("passed") for r in index["runs"])

        zpath = Path(exported["zip_path"])
        assert zpath.is_file()
        with zipfile.ZipFile(zpath) as zf:
            names = zf.namelist()
            assert "manifest.json" in names
            assert "CUSTOMER_README.md" in names
            assert "governance.json" in names
            assert exported["embedded_traces"] >= 1
            assert any(n.startswith("runs/") for n in names)
    finally:
        Database._schema_initialized = False
        try:
            os.remove(path)
        except OSError:
            pass
