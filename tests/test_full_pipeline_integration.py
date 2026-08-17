# -*- coding: utf-8 -*-
"""Integration test for the full merged execution pipeline.

Tests the complete flow from execution events through timeline formatting
to visualization output.
"""

from __future__ import annotations

import json
import os
import sys
import time
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestFullPipelineIntegration:
    """Test complete execution pipeline integration."""
    
    def test_execution_event_flow(self):
        """Test complete execution event flow."""
        from execution_events import ExecutionEventCollector
        from timeline_formatter import format_timeline_event
        from self_heal_audit import emit_heal_attempt, build_heal_audit_summary
        from devops_review_gate import create_review_gate, approve_review_gate, apply_review_gate
        
        # 1. Create collector
        collector = ExecutionEventCollector()
        
        # 2. Emit route decision
        collector.emit(
            "route_decided",
            platform="desktop",
            allow_agent=False,
            use_outer_desktop=True,
        )
        
        # 3. Emit tool calls
        collector.emit("tool_call_start", tool="windows_click_element", args_summary="搜索按钮")
        collector.emit("tool_call_end", tool="windows_click_element", result_preview='{"success": true}')
        
        # 4. Emit assertion
        collector.emit("assertion_start", assertion_type="db_scalar")
        collector.emit("assertion_end", assertion_type="db_scalar", ok=True, message="断言通过")
        
        # 5. Emit heal attempt
        emit_heal_attempt(
            collector,
            platform="desktop",
            strategy="uia_first",
            original_selector="#button",
            success=True,
        )
        
        # 6. Emit done
        collector.emit("done", failed=False, tools_used=["windows_click_element"])
        
        # Verify events
        assert len(collector.events) == 7
        
        # Format all events for timeline
        timeline_events = []
        for event in collector.events:
            formatted = format_timeline_event(event.event_type, event.data)
            if formatted:
                timeline_events.append(formatted)
        
        # Verify timeline events
        assert len(timeline_events) >= 5  # heal_attempt doesn't have formatter
        
        # Check timeline event structure
        for event in timeline_events:
            assert hasattr(event, 'event_type')
            assert hasattr(event, 'category')
            assert hasattr(event, 'severity')
            assert hasattr(event, 'title')
            assert hasattr(event, 'icon')
            assert hasattr(event, 'color')
        
        # Build heal summary
        heal_summary = build_heal_audit_summary(collector)
        assert heal_summary["total_attempts"] == 1
        assert heal_summary["successful"] == 1
        
        # Create review gate
        gate = create_review_gate(
            change_type="commit",
            change_id="test123",
            description="Test change",
            recommended_cases=[1, 2, 3],
        )
        approve_review_gate(gate, reviewer="admin")
        apply_review_gate(gate, applied_by="system")
        
        assert gate["state"] == "applied"
        assert gate["applied_cases"] == [1, 2, 3]
    
    def test_visual_baseline_integration(self):
        """Test visual baseline integration with execution."""
        from visual_baseline_integration import (
            evaluate_visual_capability,
            get_desktop_heal_recommendation,
        )
        
        # Simulate baseline report
        report = {
            "rates": {"uia": 0.85, "ocr": 0.72, "vision": 0.55},
            "summary": {
                "uia": {"total": 20, "ok": 17},
                "ocr": {"total": 20, "ok": 14},
                "vision": {"total": 20, "ok": 11},
            }
        }
        
        capabilities = evaluate_visual_capability(report)
        recommendation = get_desktop_heal_recommendation(capabilities)
        
        assert capabilities["uia_available"] is True
        assert capabilities["ocr_available"] is True
        assert capabilities["vision_available"] is True
        assert recommendation["allow_heal"] is True
        assert recommendation["strategy"] == "uia_first"
    
    def test_db_assertion_with_audit(self):
        """Test DB assertion with audit trail."""
        from execution_events import ExecutionEventCollector
        from assertion_service import AssertionRequest, run_assertion
        import tempfile
        import sqlite3
        
        collector = ExecutionEventCollector()
        
        # Create temp DB
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            conn = sqlite3.connect(db_path)
            conn.execute("CREATE TABLE users (id INTEGER, status TEXT)")
            conn.execute("INSERT INTO users VALUES (1, 'active')")
            conn.commit()
            conn.close()
            
            os.environ["DB_ASSERT_DSN"] = db_path
            os.environ["DB_ASSERT_ALLOWED_TABLES"] = "users"
            try:
                # Emit assertion start
                collector.emit("assertion_start", assertion_type="db_scalar")
                
                # Run assertion
                request = AssertionRequest(
                    assertion_type="db_scalar",
                    expected="active",
                    meta={"sql": "SELECT status FROM users WHERE id = 1"},
                )
                response = run_assertion(request)
                
                # Emit assertion end
                collector.emit(
                    "assertion_end",
                    assertion_type="db_scalar",
                    ok=response.ok,
                    message=response.message,
                )
                
                # Verify
                assert response.ok is True
                assert response.actual == "active"
                
                # Check audit trail
                assertion_starts = collector.find_by_type("assertion_start")
                assertion_ends = collector.find_by_type("assertion_end")
                assert len(assertion_starts) == 1
                assert len(assertion_ends) == 1
                assert assertion_ends[0].data["ok"] is True
            finally:
                os.environ.pop("DB_ASSERT_DSN", None)
                os.environ.pop("DB_ASSERT_ALLOWED_TABLES", None)
        finally:
            try:
                os.unlink(db_path)
            except Exception:
                pass
    
    def test_timeline_sse_format(self):
        """Test timeline SSE format for frontend."""
        from timeline_formatter import format_timeline_event
        
        # Test all event types
        event_types = [
            ("route_decided", {"platform": "desktop", "allow_agent": True, "use_outer_desktop": False}),
            ("tool_call_start", {"tool": "api_call", "args_summary": "GET /api/users"}),
            ("tool_call_end", {"tool": "api_call", "result_preview": '{"ok": true}'}),
            ("done", {"failed": False, "tools_used": ["api_call"]}),
            ("assertion_start", {"assertion_type": "cross_end_consistency"}),
            ("assertion_end", {"assertion_type": "cross_end_consistency", "ok": True}),
            ("heal_attempt", {"strategy": "css_fallback", "platform": "web"}),
            ("risk_decision", {"risk_level": "high", "decision": "block"}),
        ]
        
        for event_type, data in event_types:
            event = format_timeline_event(event_type, data)
            assert event is not None, f"Failed to format {event_type}"
            
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
    
    def test_devops_review_gate_flow(self):
        """Test complete DevOps review gate flow."""
        from devops_review_gate import (
            create_review_gate,
            approve_review_gate,
            reject_review_gate,
            apply_review_gate,
            ignore_review_gate,
            get_review_gate_summary,
        )
        from execution_events import ExecutionEventCollector
        
        collector = ExecutionEventCollector()
        
        # Create gate
        gate = create_review_gate(
            change_type="pull_request",
            change_id="pr-456",
            description="Update authentication flow",
            impact_summary={"affected_modules": ["auth", "user"]},
            recommended_cases=[101, 102, 103],
            heal_proposals=[
                {"selector": "#login-btn", "strategy": "css_fallback"},
            ],
        )
        
        # Verify initial state
        summary = get_review_gate_summary(gate)
        assert summary["state"] == "pending_review"
        assert summary["recommended_cases_count"] == 3
        assert summary["heal_proposals_count"] == 1
        
        # Approve
        approve_review_gate(gate, reviewer="tech-lead", reason="LGTM")
        assert gate["state"] == "approved"
        
        # Apply
        apply_review_gate(gate, applied_by="ci-system", applied_cases=[101, 102])
        assert gate["state"] == "applied"
        assert gate["applied_cases"] == [101, 102]
        
        # Verify history
        assert len(gate["review_history"]) >= 2  # create, approve, apply


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
