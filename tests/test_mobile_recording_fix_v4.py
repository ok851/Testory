# -*- coding: utf-8 -*-
"""
APK录制/回放 v4 修复单元测试

覆盖6个核心修复场景：
1. _select_primary_node() 智能节点选择
2. _validate_coord_node_consistency() 坐标一致性校验
3. normalize_assistant_event() viewport_coord 无条件写入
4. _resolve_tap_coords() 多级fallback
5. _pick_replay_strategy() 策略选择
6. 向后兼容旧格式步骤
"""
from __future__ import annotations

import json
import sys
import os
import unittest

# 确保可以导入项目模块
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from modules.mobile.mobile_assistant_events import (
    _select_primary_node,
    _validate_coord_node_consistency,
    normalize_assistant_event,
    suggest_locator_from_node,
    _bounds_center,
)

# 导入 plugin_rpc 的函数（需要处理可能的导入依赖）
_resolve_tap_coords = None
_pick_replay_strategy = None
_step_mobile_spec = None

try:
    from mobile_automation_gateway.plugin_rpc import (
        _resolve_tap_coords,
        _pick_replay_strategy,
        _step_mobile_spec,
    )
except ImportError as e:
    # 如果直接导入失败，尝试添加路径后再导
    import importlib.util
    rpc_path = os.path.join(os.path.dirname(__file__), "..", "mobile_automation_gateway", "plugin_rpc.py")
    if os.path.exists(rpc_path):
        spec = importlib.util.spec_from_file_location("plugin_rpc", rpc_path)
        if spec and spec.loader:
            mod = importlib.util.module_from_spec(spec)
            try:
                spec.loader.exec_module(mod)
                _resolve_tap_coords = getattr(mod, "_resolve_tap_coords", None)
                _pick_replay_strategy = getattr(mod, "_pick_replay_strategy", None)
                _step_mobile_spec = getattr(mod, "_step_mobile_spec", None)
            except Exception:
                pass
    if not _step_mobile_spec:
        print(f"[WARN] Cannot import plugin_rpc: {e}")


class TestSelectPrimaryNode(unittest.TestCase):
    """Fix 1.1: 测试智能节点选择逻辑"""

    def test_node_with_text_uses_node(self):
        """node 有 text → 使用 node"""
        node = {"text": "登录按钮"}
        op_node = {"text": "ScrollView"}
        event = {}
        chosen, reason = _select_primary_node(node, op_node, event)
        self.assertEqual(chosen, node)
        self.assertIn("text", reason)

    def test_empty_node_fallback_to_op_node(self):
        """node 为空但有有效 op_node → 降级到 op_node"""
        node = {}
        op_node = {"text": "确定", "resource_id": "com.app:id/ok_btn"}
        chosen, reason = _select_primary_node(node, op_node, event={})
        self.assertEqual(chosen, op_node)
        self.assertEqual(reason, "fallback_to_op_node")

    def test_node_with_resource_id_uses_node(self):
        """node 有 resource_id 但无 text → 使用 node"""
        node = {"resource_id": "com.app:id/login_btn"}
        op_node = {"text": "父容器"}
        chosen, reason = _select_primary_node(node, op_node, event={})
        self.assertEqual(chosen, node)
        self.assertIn("resource_id", reason)

    def test_both_empty_returns_original(self):
        """两者都空 → 返回原始空 node"""
        node = {}
        op_node = {}
        chosen, reason = _select_primary_node(node, op_node, event={})
        self.assertEqual(chosen, {})
        self.assertEqual(reason, "no_valid_node")

    def test_node_with_content_desc_uses_node(self):
        """node 有 content_desc → 使用 node"""
        node = {"content_desc": "设置图标"}
        op_node = {"text": "其他"}
        chosen, reason = _select_primary_node(node, op_node, event={})
        self.assertEqual(chosen, node)
        self.assertIn("content_desc", reason)


class TestValidateCoordNodeConsistency(unittest.TestCase):
    """Fix 1.2: 测试坐标与定位符一致性校验"""

    def test_coord_inside_bounds(self):
        """坐标在 bounds 内 → 一致"""
        node = {"bounds": [100, 200, 300, 400]}
        ok, reason, bounds = _validate_coord_node_consistency(200, 300, node)
        self.assertTrue(ok)
        self.assertEqual(reason, "within_bounds")
        self.assertIsNotNone(bounds)

    def test_coord_outside_bounds(self):
        """坐标在 bounds 外 → 不一致"""
        node = {"bounds": [100, 200, 300, 400]}
        ok, reason, bounds = _validate_coord_node_consistency(500, 600, node)
        self.assertFalse(ok)
        self.assertIn("outside", reason)

    def test_no_bounds_always_consistent(self):
        """无 bounds 信息 → 默认一致"""
        node = {}
        ok, reason, bounds = _validate_coord_node_consistency(500, 600, node)
        self.assertTrue(ok)
        self.assertEqual(reason, "no_bounds_to_check")

    def test_margin_tolerance(self):
        """边界值有15%容差"""
        node = {"bounds": [100, 200, 300, 400]}
        # 刚好在边界外一点但仍在容差范围内
        ok, _, _ = _validate_coord_node_consistency(95, 195, node)  # 左上角外5px
        self.assertTrue(ok)  # 宽度200 * 15% = 30 > 5


class TestViewportCoordWrite(unittest.TestCase):
    """Fix 1.3: 测试 viewport_coord 无条件写入"""

    def test_touch_coord_written_even_at_zero_x(self):
        """event.x=0 时 viewport_coord 也应写入（点击屏幕左边缘）"""
        event = {
            "type": "click",
            "node": {"text": "按钮A"},
            "x": 0,
            "y": 300,
            "operation_node": {},
        }
        result = normalize_assistant_event(event, screen_width=1080, screen_height=1920)
        vc = result.get("mobile_spec", {}).get("viewport_coord")
        self.assertIsNotNone(vc, "viewport_coord 应该被写入")
        self.assertEqual(vc["x"], 0)  # x=0 是合法的左边缘坐标
        self.assertEqual(vc["y"], 300)

    def test_viewport_coord_has_source_field(self):
        """viewport_coord 应包含 source 来源标记"""
        event = {
            "type": "click",
            "node": {"text": "按钮"},
            "x": 100,
            "y": 200,
        }
        result = normalize_assistant_event(event, screen_width=1080, screen_height=1920)
        vc = result.get("mobile_spec", {}).get("viewport_coord")
        self.assertIsNotNone(vc)
        self.assertIn("source", vc)
        self.assertEqual(vc["source"], "event_touch")

    def test_relative_coords_calculated(self):
        """相对坐标 rx/ry 应被计算"""
        event = {
            "type": "click",
            "node": {"text": "中心"},
            "x": 540,
            "y": 960,
        }
        result = normalize_assistant_event(event, screen_width=1080, screen_height=1920)
        vc = result.get("mobile_spec", {}).get("viewport_coord")
        self.assertIsNotNone(vc)
        self.assertAlmostEqual(vc["rx"], 0.5, places=3)  # 540/1080
        self.assertAlmostEqual(vc["ry"], 0.5, places=3)  # 960/1920

    def test_diagnostic_field_present(self):
        """_diagnostic 字段应存在于 mobile_spec 中"""
        event = {
            "type": "click",
            "node": {"text": "测试"},
            "operation_node": {"text": "容器"},
            "x": 100,
            "y": 200,
        }
        result = normalize_assistant_event(event)
        diag = result.get("mobile_spec", {}).get("_diagnostic")
        self.assertIsInstance(diag, dict)
        self.assertIn("coord_source", diag)
        self.assertIn("node_select_reason", diag)
        self.assertIn("is_coord_consistent", diag)


class TestResolveTapCoords(unittest.TestCase):
    """Fix 2.1: 测试回放坐标多级 fallback"""

    def setUp(self):
        if _resolve_tap_coords is None:
            self.skipTest("plugin_rpc not available")

    def test_level1_absolute_coords(self):
        """Level 1: viewport_coord 绝对坐标"""
        step = {"description": "test"}
        spec = {"viewport_coord": {"x": 500, "y": 800}}
        x, y = _resolve_tap_coords(step, spec)
        self.assertEqual((x, y), (500, 800))

    def test_level2_relative_coords(self):
        """Level 2: 相对坐标换算"""
        step = {"description": "test"}
        spec = {
            "viewport_coord": {"rx": 0.4630, "ry": 0.4167},
            "screen_width": 1080,
            "screen_height": 1920,
        }
        x, y = _resolve_tap_coords(step, spec)
        self.assertGreater(x, 0)
        self.assertGreater(y, 0)
        self.assertAlmostEqual(x, round(0.4630 * 1080), delta=2)
        self.assertAlmostEqual(y, round(0.4167 * 1920), delta=2)

    def test_level3_top_level_fields(self):
        """Level 3: spec 顶层直写字段"""
        step = {"description": "test"}
        spec = {"x": 300, "y": 500}
        x, y = _resolve_tap_coords(step, spec)
        self.assertEqual((x, y), (300, 500))

    def test_level4_selector_value_json(self):
        """Level 4: selector_value JSON 解析（旧格式兼容）"""
        step = {
            "selector_type": "viewport_coord",
            "selector_value": json.dumps({"x": 200, "y": 400}),
            "description": "old_format_test",
        }
        spec = {}
        x, y = _resolve_tap_coords(step, spec)
        self.assertEqual((x, y), (200, 400))

    def test_all_levels_fail_returns_zero_zero(self):
        """所有级别都失败 → 返回(0,0)并记录警告"""
        step = {"description": "no_coords_anywhere"}
        spec = {}  # 完全空的 spec
        x, y = _resolve_tap_coords(step, spec)
        self.assertEqual((x, y), (0, 0))

    def test_level5_node_relative_coords(self):
        """Level 5: 节点内相对坐标 (SoloPi 风格)"""
        step = {"description": "solo_pi_style"}
        spec = {
            "bounds": [100, 200, 500, 800],
            "node_rx": 0.5,
            "node_ry": 0.5,
        }
        x, y = _resolve_tap_coords(step, spec)
        # center of [100,200,500,800] = (300, 500)
        self.assertEqual((x, y), (300, 500))


class TestPickReplayStrategy(unittest.TestCase):
    """Fix 2.2: 测试回放策略选择"""

    def setUp(self):
        if _pick_replay_strategy is None:
            self.skipTest("plugin_rpc not available")

    def test_stable_id_selector_primary(self):
        """id 类型 selector → selector_primary"""
        s = _pick_replay_strategy("id", "login_btn", 100, 200)
        self.assertEqual(s, "selector_primary")

    def test_text_selector_with_coord_is_coord_primary(self):
        """text 类型且有坐标 → coord_primary"""
        s = _pick_replay_strategy("accessibility_id", "确定", 500, 800)
        self.assertEqual(s, "coord_primary")

    def test_no_selector_but_has_coord(self):
        """无 selector 但有坐标 → coord_only"""
        s = _pick_replay_strategy("", "", 500, 800)
        self.assertEqual(s, "coord_only")

    def test_xpath_with_coord_is_hybrid(self):
        """xpath 类型 + 有坐标 → hybrid"""
        s = _pick_replay_strategy("xpath", "//button", 100, 200)
        self.assertEqual(s, "hybrid")

    def test_default_hybrid(self):
        """默认情况 → hybrid"""
        s = _pick_replay_strategy("android_uiautomator", 'new UiSelector()', 0, 0)
        self.assertEqual(s, "hybrid")


class TestBackwardCompatibility(unittest.TestCase):
    """向后兼容性测试：确保旧格式步骤仍可正确解析"""

    def test_old_step_without_mobile_spec(self):
        """无 mobile_spec 的旧步骤不应崩溃"""
        step = {
            "action": "tap",
            "selector_type": "",
            "selector_value": "",
            "description": "old_step",
        }
        if _step_mobile_spec is None:
            self.skipTest("_step_mobile_spec not available")
        spec = _step_mobile_spec(step)
        self.assertIsInstance(spec, dict)
        self.assertEqual(len(spec), 0)  # 空dict

    def test_old_step_string_mobile_spec(self):
        """mobile_spec 为字符串格式的旧步骤"""
        step = {
            "action": "tap",
            "mobile_spec": '{"x": 100, "y": 200}',
            "description": "string_spec",
        }
        if _step_mobile_spec is None:
            self.skipTest("_step_mobile_spec not available")
        spec = _step_mobile_spec(step)
        self.assertEqual(spec.get("x"), 100)
        self.assertEqual(spec.get("y"), 200)

    def test_normalize_without_screen_size(self):
        """不提供屏幕尺寸时不应崩溃"""
        event = {
            "type": "click",
            "node": {"text": "按钮"},
            "x": 100,
            "y": 200,
        }
        result = normalize_assistant_event(event)  # 无 screen_width/height
        self.assertEqual(result["action"], "tap")
        vc = result.get("mobile_spec", {}).get("viewport_coord")
        if vc:
            self.assertEqual(vc["x"], 100)
            self.assertNotIn("rx", vc)  # 无屏幕尺寸时不写相对坐标


class TestNormalizeAssistantEventV4(unittest.TestCase):
    """综合测试：normalize_assistant_event v4 行为验证"""

    def test_click_records_correct_element_not_operation_node(self):
        """点击事件应记录正确的元素而非 operation_node"""
        event = {
            "type": "click",
            "node": {"text": "登录按钮", "bounds": [480, 280, 560, 320]},
            "operation_node": {"text": "ScrollView", "bounds": [0, 200, 1080, 1500]},
            "x": 520,
            "y": 300,
        }
        result = normalize_assistant_event(event, screen_width=1080, screen_height=1920)

        # 应使用 node.text 而非 op_node.text
        self.assertIn("登录按钮", result["description"])
        self.assertNotIn("ScrollView", result["description"])

        # 坐标应来自 event.x/y
        vc = result["mobile_spec"]["viewport_coord"]
        self.assertEqual((vc["x"], vc["y"]), (520, 300))

        # 诊断信息应标记使用了哪个节点
        diag = result["mobile_spec"]["_diagnostic"]
        self.assertEqual(diag["primary_node_text"], "登录按钮")
        self.assertEqual(diag["op_node_text"], "ScrollView")

    def test_open_app_preserves_diagnostic(self):
        """open_app 事件也应有诊断信息"""
        event = {
            "type": "open_app",
            "package": "com.tencent.mm",
            "app_label": "微信",
            "node": {},
            "operation_node": {},
        }
        result = normalize_assistant_event(event)
        self.assertEqual(result["action"], "open_app")
        self.assertIn("微信", result["description"])
        diag = result["mobile_spec"].get("_diagnostic")
        self.assertIsInstance(diag, dict)

    def test_swipe_preserves_coordinates(self):
        """滑动事件的坐标应正确保存"""
        event = {
            "type": "swipe",
            "x1": 540, "y1": 1600,
            "x2": 540, "y2": 800,
        }
        result = normalize_assistant_event(event, screen_width=1080, screen_height=1920)
        self.assertEqual(result["action"], "swipe")
        spec = result["mobile_spec"]
        self.assertEqual(spec["x1"], 540)
        self.assertEqual(spec["y1"], 1600)
        self.assertEqual(spec["x2"], 540)
        self.assertEqual(spec["y2"], 800)
        # 应有相对坐标
        self.assertAlmostEqual(spec["rx1"], 0.5, places=3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
