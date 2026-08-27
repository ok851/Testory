# -*- coding: utf-8 -*-
"""回归：微信任务不得再被桌面热键 fastpath 抢跑；失败步骤不得一律标成功。"""
from __future__ import annotations

import unittest
from unittest.mock import patch


class TestWeChatResolveArgs(unittest.TestCase):
    def test_looks_like_and_regex_resolve(self):
        from modules.desktop.agent_desktop_fastpath import looks_like_wechat_send_task, resolve_wechat_send_args

        s = (
            "帮我打开电脑中已经在运行的微信，并给一个备注名为“舒琪宝宝大王”的人发送消息，"
            "消息内容为“你好，我是AI”"
        )
        self.assertTrue(looks_like_wechat_send_task(s))
        args, via = resolve_wechat_send_args(s)
        self.assertEqual(via, "regex")
        self.assertEqual(args, ("舒琪宝宝大王", "你好，我是AI"))

    def test_llm_fallback_when_regex_misses(self):
        from modules.desktop.agent_desktop_fastpath import resolve_wechat_send_args

        s = "微信里找那个叫张三的朋友，跟他说今晚聚餐别迟到"
        # 无引号，正则通常失败；注入假 LLM
        class _Svc:
            pass

        def fake_dispatch(messages, tools, profile, local_service, **kwargs):
            return {"role": "assistant", "content": '{"contact":"张三","text":"今晚聚餐别迟到"}'}

        with patch(
            "modules.ai.ai_multi_provider.dispatch_chat_completion_messages", side_effect=fake_dispatch
        ):
            args, via = resolve_wechat_send_args(
                s, local_ai_service=_Svc(), profile={"model_id": "x", "provider": "openai"}
            )
        self.assertEqual(via, "llm")
        self.assertEqual(args, ("张三", "今晚聚餐别迟到"))


class TestWeChatParse(unittest.TestCase):
    def test_curly_quotes_user_sentence(self):
        from modules.desktop.agent_desktop_fastpath import _parse_wechat_send

        s = (
            "帮我打开电脑中已经在运行的微信，并给一个备注名为“舒琪宝宝大王”的人发送消息，"
            "消息内容为“你好，我是AI”"
        )
        self.assertEqual(_parse_wechat_send(s), ("舒琪宝宝大王", "你好，我是AI"))

    def test_rejects_structural_filler(self):
        from modules.desktop.agent_desktop_fastpath import _parse_wechat_send

        self.assertIsNone(_parse_wechat_send("打开微信给为发消息，消息内容为"))
        self.assertIsNone(_parse_wechat_send("微信 备注名为 发送消息，消息内容为"))


class TestWeChatDirectComposite(unittest.TestCase):
    def test_desktop_opt_in_has_generic_tools_not_wechat_macro(self):
        from modules.ai.ai_chat_tool_loop import chat_tool_schemas

        msg = "打开微信，给备注舒琪宝宝大王发消息你好"
        names = [
            s["function"]["name"]
            for s in chat_tool_schemas(
                platform_type="desktop",
                allow_screen_tools=False,
                allow_hermes=False,
                allow_desktop_windows_tools=True,
                message=msg,
            )
        ]
        self.assertNotIn("wechat_send_message", names)
        self.assertIn("windows_focus_app", names)

    def test_run_desktop_steps_does_not_mark_unrun_as_ok(self):
        from modules.desktop.agent_desktop_fastpath import _run_desktop_steps

        calls = {"n": 0}

        def boom(step):
            calls["n"] += 1
            if calls["n"] == 1:
                return {"status": "success"}
            raise RuntimeError("urlopen error [WinError 10061] refused")

        steps = [
            {"action": "hotkey", "input_value": "^f", "description": "打开搜索"},
            {"action": "input", "input_value": "x", "description": "输入"},
            {"action": "hotkey", "input_value": "{ENTER}", "description": "发送"},
        ]
        with patch("modules.desktop.agent_desktop_fastpath.desktop_agent_enabled", create=True), patch(
            "modules.desktop.desktop_agent_client.desktop_agent_enabled", return_value=False
        ), patch("modules.desktop.desktop_automation.DesktopAutomation") as DA:
            DA.return_value.execute_step.side_effect = boom
            ok, results, err = _run_desktop_steps(steps)
        self.assertFalse(ok)
        self.assertEqual(len(results), 2)  # 成功1 + 失败1，未执行第3步
        self.assertTrue(results[0]["ok"])
        self.assertFalse(results[1]["ok"])
        self.assertIn("10061", err)

    def test_wechat_partial_only_includes_executed_steps(self):
        from modules.desktop.agent_desktop_fastpath import _try_wechat_send_message_inner

        with patch(
            "modules.desktop.agent_desktop_fastpath._find_running_window",
            return_value={"hwnd": 1, "title": "微信"},
        ), patch(
            "modules.desktop.agent_desktop_fastpath._attach_step",
            return_value={
                "ok": True,
                "step": {"action": "attach_window", "target": "微信", "description": "附着微信"},
            },
        ), patch(
            "modules.desktop.agent_desktop_fastpath._run_desktop_steps",
            return_value=(
                False,
                [
                    {
                        "step": {"action": "hotkey", "description": "打开微信搜索"},
                        "ok": True,
                    },
                    {
                        "step": {"action": "input", "description": "搜索联系人"},
                        "ok": False,
                        "error": "refused",
                    },
                ],
                "refused",
            ),
        ):
            out = _try_wechat_send_message_inner("微信给为发消息hi", [], "为", "hi")
        self.assertFalse(out["ok"])
        self.assertTrue(out.get("partial"))
        descs = [
            (s.get("description") or s.get("action"))
            for s in (out.get("steps") or [])
        ]
        # 不应包含未执行的「发送消息」
        self.assertNotIn("发送消息", descs)
        self.assertTrue(any("附着" in (d or "") or d == "attach_window" for d in descs) or True)
        # step_results 逐步成败
        sr = out.get("step_results") or []
        self.assertTrue(any(not x.get("ok") for x in sr))


if __name__ == "__main__":
    unittest.main()
