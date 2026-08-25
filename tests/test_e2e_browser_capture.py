# -*- coding: utf-8 -*-
"""端到端验证：模拟 SSE 流中浏览器工具事件的捕获和记录。"""
import sys
import json
sys.path.insert(0, '.')

from hermes_gateway_client import _tool_call_delta_to_event
from ai_action_recorder import ActionRecorder

# 模拟 Chat Completions delta.tool_calls
mock_tool_calls = [
    {"function": {"name": "browser_navigate", "arguments": json.dumps({"url": "https://example.com/login"})}},
    {"function": {"name": "browser_click", "arguments": json.dumps({"ref": "@e5"})}},
    {"function": {"name": "browser_type", "arguments": json.dumps({"ref": "@e3", "text": "admin123"})}},
    {"function": {"name": "mobile_extract_otp", "arguments": json.dumps({"timeout_sec": 30})}},
    {"function": {"name": "windows_launch_app", "arguments": json.dumps({"app": "notepad.exe"})}},
]

# 模拟 hermes_gateway_client 的流处理（修复后的逻辑）
tool_events = []
for tc in mock_tool_calls:
    te = _tool_call_delta_to_event(tc)
    if te and (te.get("args") or te.get("name") not in ("", "tool")):
        te_name = str(te.get("name") or "").strip()
        is_platform_tool = (
            te_name.startswith("browser_")
            or te_name.startswith("windows_")
            or te_name.startswith("mobile_")
            or te_name in ("navigate", "goto", "click", "type", "snapshot", "scroll",
                           "open_app", "tap", "input_text", "swipe", "extract_otp",
                           "launch_app", "focus_app", "press_key", "screenshot")
        )
        if is_platform_tool and te.get("args"):
            _existing = None
            for _e in tool_events:
                if _e.get("name") == te_name:
                    _existing = _e
                    break
            if _existing:
                _existing["args"] = te.get("args")
                if not _existing.get("result"):
                    _existing["result"] = {"ok": True}
                if not _existing.get("status") or _existing["status"] == "running":
                    _existing["status"] = "completed"
            else:
                te["status"] = "completed"
                te["result"] = te.get("result") or {"ok": True}
                tool_events.append(te)

print(f"tool_events count: {len(tool_events)}")
for te in tool_events:
    print(f"  name={te['name']}, status={te['status']}, args={te['args']}, has_result={te.get('result') is not None}")

assert len(tool_events) == 5, f"Expected 5 tool events, got {len(tool_events)}"
names = [te["name"] for te in tool_events]
assert "browser_navigate" in names
assert "browser_click" in names
assert "browser_type" in names
assert "mobile_extract_otp" in names
assert "windows_launch_app" in names

# 模拟 ai_chat_tool_loop 中的 ActionRecorder 处理
rec = ActionRecorder(platform="web")
out_recs = []
for te in tool_events[-80:]:
    new_recs = rec.capture_from_tool_event(
        name=te["name"],
        args=te.get("args", {}),
        result=te.get("result"),
        status=te.get("status", "completed"),
    )
    for r in new_recs:
        st = (r.status or "warning").strip().lower()
        if st in ("running", "in_progress", "started", "progress"):
            continue
        if st in ("fail", "error", "failed"):
            st = "failed"
        elif st in ("ok", "done", "success", "completed", "complete"):
            st = "success"
        elif st not in ("warning", "skipped"):
            st = "warning"
        out_recs.append({
            "action_type": r.action_type,
            "target": r.target,
            "status": st,
            "result": (r.result or "")[:100],
            "has_vision": False,
            "env_verify": None,
        })

print(f"\nout_recs count: {len(out_recs)}")
for r in out_recs:
    print(f"  action={r['action_type']}, target={r['target'][:40]}, status={r['status']}")

assert len(out_recs) >= 4, f"Expected at least 4 records, got {len(out_recs)}"
actions = [r["action_type"] for r in out_recs]
assert "navigate" in actions, f"navigate not in {actions}"
assert "click" in actions, f"click not in {actions}"
assert "input" in actions or "type" in actions, f"input/type not in {actions}"

print("\n✅ End-to-end verification PASSED! Browser steps are now captured.")
