# -*- coding: utf-8 -*-
"""回归：通用桌面原语 / 去应用死模板 / 观察校验。"""
from __future__ import annotations

import json
import unittest
from unittest.mock import patch


class TestNoWeChatDeadTemplateInOuterFC(unittest.TestCase):
    def test_default_desktop_fc_has_windows_tools(self):
        """桌面默认走外层 windows_*（OpenClaw 式），不再只剩 hermes_execute。"""
        import os
        from modules.ai.ai_chat_tool_loop import chat_tool_schemas, _should_enable_desktop_windows_tools

        os.environ.pop("PLATFORM_OUTER_DESKTOP_TOOLS", None)
        self.assertTrue(_should_enable_desktop_windows_tools("desktop"))
        names = [
            s["function"]["name"]
            for s in chat_tool_schemas(platform_type="desktop", allow_hermes=True)
        ]
        self.assertNotIn("wechat_send_message", names)
        self.assertIn("windows_focus_app", names)
        # 桌面精简 profile：外层只留 windows_* + 观察
        self.assertNotIn("hermes_execute", names)

    def test_outer_desktop_tools_can_disable(self):
        import os
        from modules.ai.ai_chat_tool_loop import chat_tool_schemas, _should_enable_desktop_windows_tools

        os.environ["PLATFORM_OUTER_DESKTOP_TOOLS"] = "0"
        try:
            self.assertFalse(_should_enable_desktop_windows_tools("desktop"))
            names = [
                s["function"]["name"]
                for s in chat_tool_schemas(
                    platform_type="desktop",
                    allow_hermes=True,
                    allow_desktop_windows_tools=False,
                )
            ]
        finally:
            os.environ.pop("PLATFORM_OUTER_DESKTOP_TOOLS", None)
        self.assertNotIn("windows_focus_app", names)
        self.assertIn("hermes_execute", names)

    def test_outer_desktop_tools_opt_in(self):
        import os
        from modules.ai.ai_chat_tool_loop import chat_tool_schemas

        os.environ["PLATFORM_OUTER_DESKTOP_TOOLS"] = "1"
        try:
            names = [
                s["function"]["name"]
                for s in chat_tool_schemas(
                    platform_type="desktop",
                    allow_hermes=False,
                    allow_desktop_windows_tools=True,
                )
            ]
        finally:
            os.environ.pop("PLATFORM_OUTER_DESKTOP_TOOLS", None)
        self.assertNotIn("wechat_send_message", names)
        self.assertIn("windows_focus_app", names)
        self.assertIn("windows_type_text", names)

    def test_wechat_send_message_deprecated(self):
        from modules.desktop.windows_desktop_tools import wechat_send_message

        r = wechat_send_message("张三", "你好")
        self.assertFalse(r.get("success"))
        self.assertTrue(r.get("deprecated"))
        self.assertIn("死模板", r.get("error") or "")


class TestCaptureAfterSemantics(unittest.TestCase):
    def test_type_fails_when_screen_unchanged(self):
        from modules.desktop.windows_desktop_tools import windows_type_text, set_desktop_target, clear_desktop_target

        set_desktop_target(hwnd=12345, label="记事本", title="无标题 - 记事本")
        try:
            with patch(
                "modules.desktop.windows_desktop_tools.begin_desktop_action_frame",
                return_value={
                    "ok": True,
                    "hwnd": 12345,
                    "frame_id": "f1",
                    "before_hash": "samehash",
                },
            ), patch(
                "modules.desktop.windows_desktop_tools._reclick_armed_search_if_needed",
                return_value={"ok": False, "skipped": True},
            ), patch(
                "modules.desktop.windows_desktop_tools._type_observe_act_verify",
                return_value={
                    "ok": False,
                    "verified": False,
                    "error": "OCR 未在画面上看到输入内容",
                    "delivery": {"ok": True, "via": "sendinput_fallback"},
                    "ocr_check": {"ok": False, "error": "OCR 未在画面上看到输入内容"},
                    "attempts": [],
                },
            ):
                r = windows_type_text("hello", require_change=True)
            self.assertFalse(r.get("success"))
            self.assertFalse(r.get("verified"))
            blob = (r.get("error") or "") + (r.get("reply") or "")
            self.assertNotIn("已输入", blob)
            steps = r.get("steps_done") or []
            self.assertTrue(
                "type_ocr_miss" in steps or "type_attempted" in steps or "type_verify_loop" in steps,
                steps,
            )
            self.assertTrue(r.get("flow_halt"))
        finally:
            clear_desktop_target()

    def test_press_returns_capture_after(self):
        from modules.desktop.windows_desktop_tools import windows_press_key, set_desktop_target, clear_desktop_target

        set_desktop_target(hwnd=99, label="app")
        try:
            with patch(
                "modules.desktop.windows_desktop_tools.begin_desktop_action_frame",
                return_value={
                    "ok": True,
                    "hwnd": 99,
                    "frame_id": "f2",
                    "before_hash": "h1",
                },
            ), patch(
                "modules.desktop.windows_desktop_tools._capture_target_hash", side_effect=["h1", "h2"]
            ), patch(
                "modules.desktop.desktop_input.deliver_keys_to_hwnd",
                return_value={"ok": True, "via": "postmessage", "keys": ["enter"]},
            ):
                r = windows_press_key("Enter", require_change=False)
            self.assertTrue(r.get("success"))
            self.assertIn("capture_after", r)
            self.assertIn("delivery", r)
        finally:
            clear_desktop_target()


class TestSkillHintsNoWeChatTemplate(unittest.TestCase):
    def test_desktop_hint_mentions_gateways_not_wechat_macro(self):
        from modules.hermes.hermes_skill_hints import desktop_gateway_auth_hint, build_explore_instruction

        hint = desktop_gateway_auth_hint()
        self.assertIn("8642", hint)
        self.assertIn("8766", hint)
        self.assertNotIn("attach_window\",\"target\":\"微信", hint)
        instr = build_explore_instruction("打开记事本输入 hello", {"platform": "desktop"})
        self.assertIn("observe", instr.lower() or "observe→act")
        self.assertIn("skill_view", instr)


class TestExecuteDesktopNlNoAutoWechat(unittest.TestCase):
    def test_skips_wechat_macro_by_default(self):
        from modules.desktop.agent_desktop_fastpath import execute_desktop_nl

        with patch(
            "modules.desktop.agent_desktop_fastpath._try_wechat_send_message",
            return_value={"ok": True, "reply": "should not run"},
        ) as wx, patch(
            "modules.desktop.agent_desktop_fastpath.resolve_desktop_launch_target",
            return_value=None,
        ), patch(
            "modules.desktop.agent_desktop_fastpath._find_running_window",
            return_value=None,
        ):
            # may fall through to DesktopAgent or fail — but must not call wechat macro
            try:
                execute_desktop_nl("打开微信给张三发消息你好")
            except Exception:
                pass
            wx.assert_not_called()


class TestMcpHasNoWechatTool(unittest.TestCase):
    def test_mcp_windows_tools(self):
        from testory_mcp.kit import mcp_windows_desktop_tools

        names = [t["name"] for t in mcp_windows_desktop_tools()]
        self.assertNotIn("wechat_send_message", names)
        self.assertIn("windows_focus_app", names)
        self.assertIn("get_screen_text", names)


class TestHermesStreamNoRerun(unittest.TestCase):
    def test_empty_stream_does_not_call_nonstream(self):
        from modules.hermes.hermes_gateway_client import HermesGatewayClient

        client = HermesGatewayClient()
        client.base_url = "http://127.0.0.1:9"
        client.token = "t"

        class _Resp:
            ok = True

            def iter_lines(self, decode_unicode=True):
                yield "data: {\"choices\":[{\"delta\":{}}]}"
                yield "data: [DONE]"

        calls = {"n": 0}

        def boom(*a, **k):
            calls["n"] += 1
            raise AssertionError("must not re-run non-stream execute")

        with patch("modules.hermes.hermes_gateway_client.requests.post", return_value=_Resp()), patch.object(
            client, "execute_user_instruction", side_effect=boom
        ):
            events = list(client.execute_user_instruction_stream("打开记事本"))
        kinds = [k for k, _ in events]
        self.assertIn("result", kinds)
        self.assertEqual(calls["n"], 0)
        result = next(p for k, p in events if k == "result")
        self.assertIn("stream_empty", (result.get("content") or ""))

    def test_parses_hermes_tool_progress_sse(self):
        from modules.hermes.hermes_gateway_client import HermesGatewayClient

        client = HermesGatewayClient()
        client.base_url = "http://127.0.0.1:9"
        client.token = "t"

        class _Resp:
            ok = True

            def iter_lines(self, decode_unicode=True):
                yield "event: hermes.tool.progress"
                yield 'data: {"name":"computer_use","status":"running","arguments":{"action":"capture"}}'
                yield ""
                yield 'data: {"choices":[{"delta":{"content":"done"}}]}'
                yield "data: [DONE]"

        with patch("modules.hermes.hermes_gateway_client.requests.post", return_value=_Resp()):
            events = list(client.execute_user_instruction_stream("打开记事本输入hello"))
        kinds = [k for k, _ in events]
        self.assertIn("tool", kinds)
        tool = next(p for k, p in events if k == "tool")
        self.assertEqual(tool.get("name"), "computer_use")
        result = next(p for k, p in events if k == "result")
        self.assertIn("done", result.get("content") or "")


class TestActionRecorderNoFakeInputOk(unittest.TestCase):
    def test_json_ok_false_yields_no_records(self):
        from modules.ai.ai_action_recorder import ActionRecorder

        rec = ActionRecorder()
        out = rec.capture_from_hermes_result(
            '{"ok":false,"stream_empty_text":true,"error":"empty"}'
        )
        self.assertEqual(out, [])

    def test_prose_with_type_does_not_invent_input_ok(self):
        from modules.ai.ai_action_recorder import ActionRecorder

        rec = ActionRecorder()
        prose = '我将 type "ok" 到记事本\n输入完成 status success'
        out = rec.capture_from_hermes_result(prose)
        self.assertEqual(out, [])

    def test_tool_event_records_real_name(self):
        from modules.ai.ai_action_recorder import ActionRecorder

        rec = ActionRecorder()
        out = rec.capture_from_tool_event(
            name="computer_use",
            args={"action": "type", "text": "hello"},
            result={"ok": True, "verified": True, "effect": "confirmed"},
            status="completed",
        )
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].status, "success")
        self.assertNotEqual(out[0].target.lower(), "ok")
        self.assertIn("computer", out[0].action_type.lower() + out[0].raw_text.lower())

    def test_completed_without_result_is_warning_not_success(self):
        from modules.ai.ai_action_recorder import ActionRecorder

        rec = ActionRecorder()
        out = rec.capture_from_tool_event(
            name="skill_view",
            args={"name": "computer-use"},
            result=None,
            status="completed",
        )
        self.assertEqual(out[0].status, "warning")


class TestStreamEmptyRetryGate(unittest.TestCase):
    def test_stream_empty_blocks_second_hermes_execute(self):
        from modules.ai.ai_chat_tool_loop import (
            _hermes_retry_blocked,
            _result_is_stream_empty,
            _hermes_retry_blocked_payload,
        )

        text = json.dumps(
            {"ok": False, "stream_empty_text": True, "error": "空流"},
            ensure_ascii=False,
        )
        self.assertTrue(_result_is_stream_empty(text))
        meta = {"hermes_stream_blocked": True, "hermes_stream_error": "空流"}
        self.assertTrue(_hermes_retry_blocked(meta))
        payload = _hermes_retry_blocked_payload(meta)
        self.assertIn("禁止再次", payload)


class TestThinOuterDesktopPrompt(unittest.TestCase):
    def test_desktop_prompt_uses_windows_tools_directly(self):
        from modules.ai.ai_chat_tool_loop import _build_system_prompt

        sp = _build_system_prompt(
            project_name="t",
            current_plan={},
            page_snapshot="",
            dom_pack="",
            memory_context="",
            interaction_note="",
            test_scope="",
            platform_type="desktop",
        )
        self.assertIn("windows_", sp)
        self.assertIn("流程闸", sp)
        self.assertIn("禁止", sp)


class TestOcrNeedleShortMatch(unittest.TestCase):
    def test_short_needle_not_matched_by_superset_fragment(self):
        from modules.desktop.windows_desktop_tools import verify_screen_contains

        with patch(
            "modules.desktop.windows_desktop_tools.observe_screen_texts",
            return_value={"ok": True, "texts": ["行为", "设置"]},
        ):
            r = verify_screen_contains(["为"], min_hits=1)
        self.assertFalse(r.get("ok"))


if __name__ == "__main__":
    unittest.main()
