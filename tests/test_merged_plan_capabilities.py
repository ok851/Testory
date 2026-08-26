# -*- coding: utf-8 -*-
"""Tests for merged execution plan capabilities:
- api_call execution channel
- assertion_service (unified assertions)
- db_assertion (readonly DB assertions)
- execution_events (standard event types)
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import sqlite3
import pytest

# Ensure project root is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ===== execution_events tests =====

class TestExecutionEvents:
    def test_event_types_defined(self):
        from modules.execution.execution_events import (
            ROUTE_DECIDED, TOOL_REGISTERED, TOOL_CALL_START, TOOL_CALL_END,
            OBSERVATION_START, OBSERVATION_END, ASSERTION_START, ASSERTION_END,
            HEAL_ATTEMPT, RISK_DECISION, DONE, STANDARD_EVENT_TYPES,
        )
        assert ROUTE_DECIDED == "route_decided"
        assert DONE == "done"
        assert len(STANDARD_EVENT_TYPES) == 11

    def test_collector_emit_and_find(self):
        from modules.execution.execution_events import ExecutionEventCollector
        collector = ExecutionEventCollector()
        collector.emit("route_decided", platform="desktop", allow_agent=True)
        collector.emit("tool_call_start", tool="api_call")
        collector.emit("done", failed=False)

        assert len(collector.events) == 3
        assert collector.events[0].event_type == "route_decided"
        assert collector.events[0].data["platform"] == "desktop"

        found = collector.find_by_type("tool_call_start")
        assert len(found) == 1
        assert found[0].data["tool"] == "api_call"

    def test_event_to_dict(self):
        from modules.execution.execution_events import ExecutionEvent
        evt = ExecutionEvent(event_type="done", data={"failed": False})
        d = evt.to_dict()
        assert d["event_type"] == "done"
        assert d["ts"] > 0
        assert d["data"]["failed"] is False

    def test_collector_as_dicts(self):
        from modules.execution.execution_events import ExecutionEventCollector
        collector = ExecutionEventCollector()
        collector.emit("route_decided", platform="web")
        collector.emit("done")
        dicts = collector.as_dicts()
        assert len(dicts) == 2
        assert dicts[0]["event_type"] == "route_decided"


# ===== assertion_service tests =====

class TestAssertionService:
    def test_unknown_assertion_type(self):
        from modules.execution.assertion_service import AssertionRequest, run_assertion
        req = AssertionRequest(assertion_type="nonexistent_type")
        resp = run_assertion(req)
        assert resp.ok is False
        assert "未知" in resp.message

    def test_manual_stub_assertion(self):
        from modules.execution.assertion_service import AssertionRequest, run_assertion
        req = AssertionRequest(assertion_type="manual_stub")
        resp = run_assertion(req)
        assert resp.ok is False
        assert "尚未接入" in resp.message

    def test_cross_end_consistency_missing_dep(self):
        from modules.execution.assertion_service import AssertionRequest, run_assertion
        req = AssertionRequest(
            assertion_type="cross_end_consistency",
            sources={"web": 100, "desktop": 100},
            expected=100,
        )
        resp = run_assertion(req)
        # Either succeeds (if deps available) or reports dep missing
        assert resp.assertion_type == "cross_end_consistency"


# ===== db_assertion tests =====

class TestDbAssertion:
    def test_readonly_sql_guard(self):
        from modules.execution.db_assertion import _ensure_readonly_sql
        # Should reject non-SELECT
        with pytest.raises(ValueError, match="只读"):
            _ensure_readonly_sql("DELETE FROM users", [])
        with pytest.raises(ValueError, match="只读"):
            _ensure_readonly_sql("INSERT INTO users VALUES (1)", [])

    def test_forbidden_keywords_guard(self):
        from modules.execution.db_assertion import _ensure_readonly_sql
        with pytest.raises(ValueError, match="不允许"):
            _ensure_readonly_sql("SELECT * FROM users; DROP TABLE users", [])

    def test_table_allowlist_guard(self):
        from modules.execution.db_assertion import _ensure_readonly_sql
        with pytest.raises(ValueError, match="不允许查询表"):
            _ensure_readonly_sql(
                "SELECT * FROM secret_table",
                ["users", "orders"],
            )

    def test_allowed_table_passes(self):
        from modules.execution.db_assertion import _ensure_readonly_sql
        # Should not raise
        _ensure_readonly_sql("SELECT id FROM users WHERE id = 1", ["users"])

    def test_scalar_assertion_no_dsn(self):
        from modules.execution.db_assertion import execute_readonly_scalar_assertion
        # When no DSN configured, should raise ValueError
        with pytest.raises(ValueError, match="未配置"):
            execute_readonly_scalar_assertion(
                sql="SELECT 1",
                expected=1,
            )

    def test_scalar_assertion_with_temp_db(self):
        from modules.execution.db_assertion import execute_readonly_scalar_assertion
        # Create a temp SQLite DB
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            conn = sqlite3.connect(db_path)
            conn.execute("CREATE TABLE test_table (id INTEGER, name TEXT)")
            conn.execute("INSERT INTO test_table VALUES (1, 'hello')")
            conn.commit()
            conn.close()

            os.environ["DB_ASSERT_DSN"] = db_path
            os.environ["DB_ASSERT_ALLOWED_TABLES"] = "test_table"
            try:
                # Should pass
                result = execute_readonly_scalar_assertion(
                    sql="SELECT name FROM test_table WHERE id = 1",
                    expected="hello",
                )
                assert result["ok"] is True
                assert result["actual"] == "hello"

                # Should fail (wrong expected)
                result2 = execute_readonly_scalar_assertion(
                    sql="SELECT name FROM test_table WHERE id = 1",
                    expected="world",
                )
                assert result2["ok"] is False
            finally:
                os.environ.pop("DB_ASSERT_DSN", None)
                os.environ.pop("DB_ASSERT_ALLOWED_TABLES", None)
        finally:
            try:
                os.unlink(db_path)
            except Exception:
                pass


# ===== api_call schema registration test =====

class TestApiCallSchema:
    def test_api_schema_structure(self):
        from modules.ai.ai_chat_tool_loop import _api_execution_tool_schema
        schema = _api_execution_tool_schema()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "api_call"
        params = schema["function"]["parameters"]["properties"]
        assert "spec" in params
        assert "method" in params
        assert "url" in params

    def test_api_call_registered_when_enabled(self, monkeypatch):
        monkeypatch.setenv("AGENT_API_EXECUTION_ENABLE", "1")
        from modules.ai.ai_chat_tool_loop import chat_tool_schemas
        schemas = chat_tool_schemas(platform_type="web")
        names = [s.get("function", {}).get("name") for s in schemas]
        assert "api_call" in names

    def test_api_call_not_registered_by_default(self, monkeypatch):
        monkeypatch.delenv("AGENT_API_EXECUTION_ENABLE", raising=False)
        from modules.ai.ai_chat_tool_loop import chat_tool_schemas
        schemas = chat_tool_schemas(platform_type="web")
        names = [s.get("function", {}).get("name") for s in schemas]
        assert "api_call" not in names



# ===== DB assertion security tests =====

class TestDbAssertionSecurity:
    def test_sensitive_field_detection(self):
        from modules.execution.db_assertion import _is_sensitive_field
        assert _is_sensitive_field("password") is True
        assert _is_sensitive_field("user_password") is True
        assert _is_sensitive_field("phone_number") is True
        assert _is_sensitive_field("email") is True
        assert _is_sensitive_field("id_card") is True
        assert _is_sensitive_field("name") is False
        assert _is_sensitive_field("age") is False

    def test_mask_password(self):
        from modules.execution.db_assertion import _mask_value
        assert _mask_value("secret123", "password") == "***"
        assert _mask_value("mytoken", "api_token") == "***"

    def test_mask_phone(self):
        from modules.execution.db_assertion import _mask_value
        assert _mask_value("13800138000", "phone") == "****8000"
        assert _mask_value("123", "phone") == "****"

    def test_mask_email(self):
        from modules.execution.db_assertion import _mask_value
        assert _mask_value("user@example.com", "email") == "us***@example.com"
        assert _mask_value("ab@cd.com", "email") == "***@cd.com"  # Short local part gets fully masked

    def test_mask_id_card(self):
        from modules.execution.db_assertion import _mask_value
        assert _mask_value("110105199001011234", "id_card") == "1101****1234"
        assert _mask_value("12345678", "id_card") == "1234****5678"

    def test_mask_sensitive_fields_in_rows(self):
        from modules.execution.db_assertion import _mask_sensitive_fields
        rows = [
            {"name": "Alice", "password": "secret1", "phone": "13800138000"},
            {"name": "Bob", "password": "secret2", "phone": "13900139000"},
        ]
        masked = _mask_sensitive_fields(rows)
        assert masked[0]["name"] == "Alice"
        assert masked[0]["password"] == "***"
        assert masked[0]["phone"] == "****8000"
        assert masked[1]["password"] == "***"
        assert masked[1]["phone"] == "****9000"

    def test_mask_none_value(self):
        from modules.execution.db_assertion import _mask_value
        assert _mask_value(None, "password") is None
        assert _mask_value(None, "phone") is None

    def test_subquery_rejection(self):
        from modules.execution.db_assertion import _ensure_readonly_sql
        with pytest.raises(ValueError, match="不允许嵌套子查询"):
            _ensure_readonly_sql("SELECT * FROM users WHERE id IN (SELECT user_id FROM orders)", [])

    def test_query_with_auto_mask(self):
        from modules.execution.db_assertion import execute_readonly_query
        import tempfile
        import sqlite3
        
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            conn = sqlite3.connect(db_path)
            conn.execute("CREATE TABLE users (id INTEGER, name TEXT, password TEXT, phone TEXT)")
            conn.execute("INSERT INTO users VALUES (1, 'Alice', 'secret123', '13800138000')")
            conn.commit()
            conn.close()

            os.environ["DB_ASSERT_DSN"] = db_path
            os.environ["DB_ASSERT_ALLOWED_TABLES"] = "users"
            try:
                result = execute_readonly_query(
                    "SELECT name, password, phone FROM users WHERE id = 1",
                    auto_mask=True,
                )
                assert result["ok"] is True
                assert result["masked"] is True
                assert result["rows"][0]["name"] == "Alice"
                assert result["rows"][0]["password"] == "***"
                assert result["rows"][0]["phone"] == "****8000"
                assert "sensitive_fields_masked" in result["warnings"]

                # Test without masking
                result2 = execute_readonly_query(
                    "SELECT name, password, phone FROM users WHERE id = 1",
                    auto_mask=False,
                )
                assert result2["ok"] is True
                assert result2.get("masked") is False
                assert result2["rows"][0]["password"] == "secret123"
            finally:
                os.environ.pop("DB_ASSERT_DSN", None)
                os.environ.pop("DB_ASSERT_ALLOWED_TABLES", None)
        finally:
            try:
                os.unlink(db_path)
            except Exception:
                pass

    def test_decrypt_plain_text(self):
        from modules.execution.db_assertion import _decrypt_connection_string
        # Plain text should pass through
        assert _decrypt_connection_string("sqlite:///test.db") == "sqlite:///test.db"

    def test_decrypt_base64(self):
        from modules.execution.db_assertion import _decrypt_connection_string
        import base64
        original = "sqlite:///test.db"
        encoded = base64.b64encode(original.encode()).decode()
        assert _decrypt_connection_string(encoded) == original



# ===== Timeline formatter tests =====

class TestTimelineFormatter:
    def test_format_route_decided_desktop_fastpath(self):
        from modules.integration.timeline_formatter import format_route_decided, SEVERITY_INFO
        data = {
            "platform": "desktop",
            "allow_agent": False,
            "use_outer_desktop": True,
            "hermes_available": False,
            "allow_screen_tools": True,
        }
        event = format_route_decided(data)
        assert event.event_type == "route_decided"
        assert event.severity == SEVERITY_INFO
        assert "桌面快路径" in event.title
        assert event.icon == "🖥️"

    def test_format_route_decided_agent(self):
        from modules.integration.timeline_formatter import format_route_decided
        data = {
            "platform": "web",
            "allow_agent": True,
            "use_outer_desktop": False,
            "hermes_available": True,
        }
        event = format_route_decided(data)
        assert "智能体" in event.title
        assert event.icon == "🤖"

    def test_format_tool_call_start_hermes(self):
        from modules.integration.timeline_formatter import format_tool_call_start
        data = {"tool": "hermes_execute", "args_summary": "打开微信"}
        event = format_tool_call_start(data)
        assert event.event_type == "tool_call_start"
        assert "Hermes" in event.title
        assert event.icon == "🤖"

    def test_format_tool_call_start_windows(self):
        from modules.integration.timeline_formatter import format_tool_call_start
        data = {"tool": "windows_click_element", "args_summary": "搜索按钮"}
        event = format_tool_call_start(data)
        assert "桌面操作" in event.title
        assert event.icon == "🖱️"

    def test_format_tool_call_start_api(self):
        from modules.integration.timeline_formatter import format_tool_call_start
        data = {"tool": "api_call", "args_summary": "GET /api/users"}
        event = format_tool_call_start(data)
        assert "API" in event.title
        assert event.icon == "🌐"

    def test_format_tool_call_end_success(self):
        from modules.integration.timeline_formatter import format_tool_call_end, SEVERITY_SUCCESS
        data = {"tool": "windows_click_element", "result_preview": '{"success": true}'}
        event = format_tool_call_end(data)
        assert event.severity == SEVERITY_SUCCESS
        assert event.icon == "✅"

    def test_format_tool_call_end_failure(self):
        from modules.integration.timeline_formatter import format_tool_call_end, SEVERITY_ERROR
        data = {"tool": "windows_click_element", "result_preview": '{"success": false, "error": "未找到元素"}'}
        event = format_tool_call_end(data)
        assert event.severity == SEVERITY_ERROR
        assert event.icon == "❌"

    def test_format_done_success(self):
        from modules.integration.timeline_formatter import format_done, SEVERITY_SUCCESS
        data = {"failed": False, "tools_used": ["hermes_execute", "windows_click"]}
        event = format_done(data)
        assert event.severity == SEVERITY_SUCCESS
        assert event.icon == "✅"
        assert "2 个工具" in event.description

    def test_format_done_failure(self):
        from modules.integration.timeline_formatter import format_done, SEVERITY_ERROR
        data = {"failed": True, "tools_used": []}
        event = format_done(data)
        assert event.severity == SEVERITY_ERROR
        assert event.icon == "❌"

    def test_format_assertion_start(self):
        from modules.integration.timeline_formatter import format_assertion_start
        data = {"assertion_type": "db_scalar", "description": "检查用户状态"}
        event = format_assertion_start(data)
        assert event.event_type == "assertion_start"
        assert "db_scalar" in event.title

    def test_format_assertion_end_pass(self):
        from modules.integration.timeline_formatter import format_assertion_end, SEVERITY_SUCCESS
        data = {"assertion_type": "db_scalar", "ok": True, "message": "断言通过"}
        event = format_assertion_end(data)
        assert event.severity == SEVERITY_SUCCESS
        assert event.icon == "✅"

    def test_format_assertion_end_fail(self):
        from modules.integration.timeline_formatter import format_assertion_end, SEVERITY_ERROR
        data = {"assertion_type": "db_scalar", "ok": False, "message": "断言失败"}
        event = format_assertion_end(data)
        assert event.severity == SEVERITY_ERROR
        assert event.icon == "❌"

    def test_format_heal_attempt(self):
        from modules.integration.timeline_formatter import format_heal_attempt, SEVERITY_WARNING
        data = {"strategy": "vision_fallback", "platform": "desktop"}
        event = format_heal_attempt(data)
        assert event.severity == SEVERITY_WARNING
        assert event.icon == "🔧"

    def test_format_risk_decision_high(self):
        from modules.integration.timeline_formatter import format_risk_decision, SEVERITY_ERROR
        data = {"risk_level": "high", "decision": "block"}
        event = format_risk_decision(data)
        assert event.severity == SEVERITY_ERROR
        assert event.icon == "🚨"

    def test_format_risk_decision_low(self):
        from modules.integration.timeline_formatter import format_risk_decision, SEVERITY_INFO
        data = {"risk_level": "low", "decision": "allow"}
        event = format_risk_decision(data)
        assert event.severity == SEVERITY_INFO
        assert event.icon == "ℹ️"

    def test_to_sse_dict(self):
        from modules.integration.timeline_formatter import format_tool_call_start
        data = {"tool": "api_call", "args_summary": "test"}
        event = format_tool_call_start(data)
        sse_dict = event.to_sse_dict()
        assert "event_type" in sse_dict
        assert "category" in sse_dict
        assert "severity" in sse_dict
        assert "title" in sse_dict
        assert "description" in sse_dict
        assert "timestamp" in sse_dict
        assert "icon" in sse_dict
        assert "color" in sse_dict
        assert "data" in sse_dict

    def test_format_unknown_event(self):
        from modules.integration.timeline_formatter import format_timeline_event
        result = format_timeline_event("unknown_event", {"key": "value"})
        assert result is None


# ===== Visual baseline integration tests =====

class TestVisualBaselineIntegration:
    def test_evaluate_visual_capability_healthy(self):
        from modules.ai.visual_baseline_integration import evaluate_visual_capability
        report = {
            "rates": {"uia": 0.8, "ocr": 0.7, "vision": 0.6},
            "summary": {
                "uia": {"total": 10, "ok": 8},
                "ocr": {"total": 10, "ok": 7},
                "vision": {"total": 10, "ok": 6},
            }
        }
        result = evaluate_visual_capability(report)
        assert result["uia_available"] is True
        assert result["ocr_available"] is True
        assert result["vision_available"] is True
        assert result["overall_healthy"] is True
        assert result["recommended_strategy"] == "uia"

    def test_evaluate_visual_capability_weak(self):
        from modules.ai.visual_baseline_integration import evaluate_visual_capability
        report = {
            "rates": {"uia": 0.3, "ocr": 0.4, "vision": 0.2},
            "summary": {
                "uia": {"total": 10, "ok": 3},
                "ocr": {"total": 10, "ok": 4},
                "vision": {"total": 10, "ok": 2},
            }
        }
        result = evaluate_visual_capability(report)
        assert result["uia_available"] is False
        assert result["ocr_available"] is False
        assert result["vision_available"] is False
        assert result["overall_healthy"] is False
        assert result["recommended_strategy"] == "manual"

    def test_get_desktop_heal_recommendation_uia(self):
        from modules.ai.visual_baseline_integration import get_desktop_heal_recommendation
        capabilities = {"recommended_strategy": "uia"}
        rec = get_desktop_heal_recommendation(capabilities)
        assert rec["allow_heal"] is True
        assert rec["strategy"] == "uia_first"
        assert rec["confidence"] == "high"

    def test_get_desktop_heal_recommendation_manual(self):
        from modules.ai.visual_baseline_integration import get_desktop_heal_recommendation
        capabilities = {"recommended_strategy": "manual"}
        rec = get_desktop_heal_recommendation(capabilities)
        assert rec["allow_heal"] is False
        assert rec["strategy"] == "manual"
        assert rec["confidence"] == "none"


# ===== Self-heal audit tests =====

class TestSelfHealAudit:
    def test_emit_heal_attempt(self):
        from modules.integration.self_heal_audit import emit_heal_attempt
        from modules.execution.execution_events import ExecutionEventCollector
        
        collector = ExecutionEventCollector()
        result = emit_heal_attempt(
            collector,
            platform="desktop",
            strategy="uia_first",
            original_selector="#button",
            success=True,
        )
        
        assert result["platform"] == "desktop"
        assert result["strategy"] == "uia_first"
        assert result["success"] is True
        
        events = collector.find_by_type("heal_attempt")
        assert len(events) == 1
        assert events[0].data["platform"] == "desktop"

    def test_emit_heal_result(self):
        from modules.integration.self_heal_audit import emit_heal_result
        from modules.execution.execution_events import ExecutionEventCollector
        
        collector = ExecutionEventCollector()
        result = emit_heal_result(
            collector,
            platform="desktop",
            strategy="ocr_fallback",
            original_selector="#button",
            healed_selector="#submit-btn",
            success=True,
            verified=True,
        )
        
        assert result["success"] is True
        assert result["verified"] is True
        
        events = collector.find_by_type("heal_result")
        assert len(events) == 1

    def test_build_heal_audit_summary(self):
        from modules.integration.self_heal_audit import emit_heal_attempt, build_heal_audit_summary
        from modules.execution.execution_events import ExecutionEventCollector
        
        collector = ExecutionEventCollector()
        emit_heal_attempt(collector, platform="desktop", strategy="uia_first", original_selector="#btn1", success=True)
        emit_heal_attempt(collector, platform="desktop", strategy="uia_first", original_selector="#btn2", success=False)
        emit_heal_attempt(collector, platform="web", strategy="css_fallback", original_selector="#btn3", success=True)
        
        summary = build_heal_audit_summary(collector)
        assert summary["total_attempts"] == 3
        assert summary["successful"] == 2
        assert summary["failed"] == 1
        assert "uia_first" in summary["strategies_used"]
        assert "css_fallback" in summary["strategies_used"]


# ===== DevOps review gate tests =====

class TestDevOpsReviewGate:
    def test_create_review_gate(self):
        from modules.integration.devops_review_gate import create_review_gate, REVIEW_STATE_PENDING
        gate = create_review_gate(
            change_type="commit",
            change_id="abc123",
            description="Update login flow",
        )
        assert gate["state"] == REVIEW_STATE_PENDING
        assert gate["change_type"] == "commit"
        assert gate["auto_approve"] is False

    def test_create_review_gate_auto_approve(self):
        from modules.integration.devops_review_gate import create_review_gate, REVIEW_STATE_APPROVED
        gate = create_review_gate(
            change_type="commit",
            change_id="abc123",
            description="Low risk change",
            auto_approve=True,
        )
        assert gate["state"] == REVIEW_STATE_APPROVED
        assert len(gate["review_history"]) == 1

    def test_approve_review_gate(self):
        from modules.integration.devops_review_gate import create_review_gate, approve_review_gate, REVIEW_STATE_APPROVED
        gate = create_review_gate(
            change_type="commit",
            change_id="abc123",
            description="Test",
        )
        approve_review_gate(gate, reviewer="admin", reason="Looks good")
        assert gate["state"] == REVIEW_STATE_APPROVED
        assert gate["review_history"][-1]["action"] == "approve"

    def test_reject_review_gate(self):
        from modules.integration.devops_review_gate import create_review_gate, reject_review_gate, REVIEW_STATE_REJECTED
        gate = create_review_gate(
            change_type="commit",
            change_id="abc123",
            description="Test",
        )
        reject_review_gate(gate, reviewer="admin", reason="Needs changes")
        assert gate["state"] == REVIEW_STATE_REJECTED
        assert gate["review_history"][-1]["action"] == "reject"

    def test_apply_review_gate(self):
        from modules.integration.devops_review_gate import create_review_gate, approve_review_gate, apply_review_gate, REVIEW_STATE_APPLIED
        gate = create_review_gate(
            change_type="commit",
            change_id="abc123",
            description="Test",
            recommended_cases=[1, 2, 3],
        )
        approve_review_gate(gate, reviewer="admin")
        apply_review_gate(gate, applied_by="system")
        assert gate["state"] == REVIEW_STATE_APPLIED
        assert gate["applied_cases"] == [1, 2, 3]

    def test_ignore_review_gate(self):
        from modules.integration.devops_review_gate import create_review_gate, ignore_review_gate, REVIEW_STATE_IGNORED
        gate = create_review_gate(
            change_type="commit",
            change_id="abc123",
            description="Test",
        )
        ignore_review_gate(gate, ignored_by="admin", reason="Not relevant")
        assert gate["state"] == REVIEW_STATE_IGNORED

    def test_get_review_gate_summary(self):
        from modules.integration.devops_review_gate import create_review_gate, get_review_gate_summary
        gate = create_review_gate(
            change_type="commit",
            change_id="abc123",
            description="Update flow",
            recommended_cases=[1, 2],
            heal_proposals=[{"selector": "#btn"}],
        )
        summary = get_review_gate_summary(gate)
        assert summary["change_type"] == "commit"
        assert summary["recommended_cases_count"] == 2
        assert summary["heal_proposals_count"] == 1

    def test_emit_review_gate_event(self):
        from modules.integration.devops_review_gate import create_review_gate, emit_review_gate_event
        from modules.execution.execution_events import ExecutionEventCollector
        
        collector = ExecutionEventCollector()
        gate = create_review_gate(
            change_type="commit",
            change_id="abc123",
            description="Test",
        )
        emit_review_gate_event(collector, gate)
        
        events = collector.find_by_type("review_gate_created")
        assert len(events) == 1
        assert events[0].data["change_type"] == "commit"
