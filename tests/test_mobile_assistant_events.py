# -*- coding: utf-8 -*-
"""
移动端助手事件归一化测试（v3）。

覆盖：
  - 滑动事件正确生成 swipe 步骤（含起止坐标百分比）
  - 点击事件优先使用 text/content_desc 而非 resource_id
  - open_app 事件正确携带应用名
  - 空白区域点击使用坐标定位
  - 元素描述优先级验证
"""
import json

from mobile_assistant_events import normalize_assistant_event, suggest_locator_from_node


# ============================================================
# 基础事件类型测试
# ============================================================


def test_normalize_includes_context_package():
    step = normalize_assistant_event({
        "type": "click",
        "package": "com.example.app",
        "bounds": [10, 20, 30, 40],
        "node": {"text": "OK"},
    })
    spec = step["mobile_spec"]
    assert spec["context_package"] == "com.example.app"
    assert "app_package" not in spec
    assert step["action"] == "tap"


def test_normalize_open_app():
    step = normalize_assistant_event({
        "type": "open_app",
        "package": "com.example.calc",
        "description": "打开计算器",
    })
    assert step["action"] == "open_app"
    assert step["input_value"] == "com.example.calc"


def test_normalize_open_app_with_label():
    """open_app 携带 app_label 时，description 应使用友好应用名。"""
    step = normalize_assistant_event({
        "type": "open_app",
        "package": "com.android.settings",
        "app_label": "设置",
    })
    assert step["action"] == "open_app"
    assert step["description"] == "打开应用[设置]"
    assert step["input_value"] == "com.android.settings"


def test_normalize_open_app_no_label():
    """open_app 无 app_label 时，用包名生成描述。"""
    step = normalize_assistant_event({
        "type": "open_app",
        "package": "com.example.foo",
    })
    assert step["action"] == "open_app"
    assert step["description"] == "打开应用[com.example.foo]"


# ============================================================
# 滑动事件测试
# ============================================================


def test_normalize_swipe_uses_scroll_delta():
    step = normalize_assistant_event({
        "type": "swipe",
        "bounds": [100, 200, 300, 400],
        "scroll_delta_x": 0,
        "scroll_delta_y": -120,
    })
    assert step["action"] == "swipe"
    spec = step["mobile_spec"]
    cx, cy = 200, 300
    assert spec["x1"] == cx
    assert spec["y1"] == cy - (-120)
    assert spec["x2"] == cx
    assert spec["y2"] == cy + (-120)


def test_normalize_swipe_from_payload_endpoints():
    step = normalize_assistant_event({
        "type": "swipe",
        "x1": 10,
        "y1": 20,
        "x2": 10,
        "y2": 500,
    })
    spec = step["mobile_spec"]
    assert spec["x1"] == 10
    assert spec["y2"] == 500


def test_normalize_swipe_description_format():
    """滑动步骤描述应包含起止坐标。"""
    step = normalize_assistant_event({
        "type": "swipe",
        "x1": 200,
        "y1": 300,
        "x2": 200,
        "y2": 800,
    })
    assert step["action"] == "swipe"
    assert "200" in step["description"]
    assert "800" in step["description"]
    assert "→" in step["description"]


def test_normalize_swipe_with_percent_coords():
    """滑动步骤应包含百分比坐标以支持跨分辨率回放。"""
    step = normalize_assistant_event({
        "type": "swipe",
        "x1": 100,
        "y1": 200,
        "x2": 100,
        "y2": 800,
    }, screen_width=1080, screen_height=1920)
    spec = step["mobile_spec"]
    assert "rx1" in spec
    assert "ry1" in spec
    assert "rx2" in spec
    assert "ry2" in spec
    # rx1 ≈ 100/1080 ≈ 0.0926
    assert 0.09 <= spec["rx1"] <= 0.10


def test_normalize_swipe_no_selector():
    """滑动步骤不应携带 selector_type/value。"""
    step = normalize_assistant_event({
        "type": "swipe",
        "x1": 100,
        "y1": 200,
        "x2": 100,
        "y2": 800,
    })
    assert step["selector_type"] == ""
    assert step["selector_value"] == ""


# ============================================================
# 点击事件 — 文本优先测试
# ============================================================


def test_normalize_click_prefers_text_over_id():
    """点击事件应优先使用 text 作为定位策略，而非 resource_id。"""
    step = normalize_assistant_event({
        "type": "click",
        "bounds": [10, 20, 200, 60],
        "node": {
            "resource_id": "com.android.settings:id/icon_frame",
            "text": "Wi-Fi",
            "content_desc": "",
        },
    })
    assert step["action"] == "tap"
    assert step["selector_type"] == "accessibility_id"
    assert step["selector_value"] == "Wi-Fi"
    assert "Wi-Fi" in step["description"]


def test_normalize_click_falls_back_to_content_desc():
    """无 text 时应使用 content_desc。"""
    step = normalize_assistant_event({
        "type": "click",
        "bounds": [10, 20, 200, 60],
        "node": {
            "resource_id": "com.android.settings:id/icon",
            "content_desc": "设置图标",
        },
    })
    assert step["selector_type"] == "accessibility_id"
    assert step["selector_value"] == "设置图标"
    assert "设置图标" in step["description"]


def test_normalize_click_uses_id_only_as_fallback():
    """text 和 content_desc 都为空时，回退到 resource_id。"""
    step = normalize_assistant_event({
        "type": "click",
        "bounds": [10, 20, 200, 60],
        "node": {
            "resource_id": "com.example:id/button",
        },
    })
    assert step["selector_type"] == "id"
    assert step["selector_value"] == "com.example:id/button"
    # 描述中应显示可读部分
    assert "button" in step["description"]


def test_normalize_click_blank_area_uses_coord():
    """无节点信息时，使用坐标定位。"""
    step = normalize_assistant_event({
        "type": "click",
        "x": 500,
        "y": 600,
    })
    assert step["action"] == "tap"
    assert step["selector_type"] == "viewport_coord"
    coord = json.loads(step["selector_value"])
    assert coord["x"] == 500
    assert coord["y"] == 600


def test_normalize_click_coord_with_screen_size():
    """坐标点击应包含百分比以支持跨分辨率。"""
    step = normalize_assistant_event({
        "type": "click",
        "x": 540,
        "y": 960,
    }, screen_width=1080, screen_height=1920)
    coord = json.loads(step["selector_value"])
    assert "rx" in coord
    assert "ry" in coord
    assert abs(coord["rx"] - 0.5) < 0.01
    assert abs(coord["ry"] - 0.5) < 0.01


# ============================================================
# 定位器优先级独立测试
# ============================================================


def test_suggest_locator_text_first():
    """text 优先于所有其他属性。"""
    node = {
        "resource_id": "com.example:id/icon",
        "text": "Wi-Fi",
        "content_desc": "toggle wifi",
    }
    stype, sval = suggest_locator_from_node(node)
    assert stype == "accessibility_id"
    assert sval == "Wi-Fi"


def test_suggest_locator_content_desc_second():
    """无 text 时 content_desc 次之。"""
    node = {
        "resource_id": "com.example:id/icon",
        "content_desc": "toggle wifi",
    }
    stype, sval = suggest_locator_from_node(node)
    assert stype == "accessibility_id"
    assert sval == "toggle wifi"


def test_suggest_locator_id_third():
    """无 text 和 content_desc 时 resource_id 兜底。"""
    node = {
        "resource_id": "com.example:id/button",
    }
    stype, sval = suggest_locator_from_node(node)
    assert stype == "id"
    assert sval == "com.example:id/button"


def test_suggest_locator_xpath_fourth():
    """无 text/desc/id 时使用 xpath。"""
    node = {
        "xpath": "//android.widget.Button[@text='OK']",
    }
    stype, sval = suggest_locator_from_node(node)
    assert stype == "xpath"
    assert "OK" in sval


# ============================================================
# 输入事件测试
# ============================================================


def test_normalize_input():
    step = normalize_assistant_event({
        "type": "input",
        "text": "hello",
        "node": {"text": "搜索框"},
    })
    assert step["action"] == "input_text"
    assert step["input_value"] == "hello"


# ============================================================
# 长按事件测试
# ============================================================


def test_normalize_long_press():
    step = normalize_assistant_event({
        "type": "long-press",
        "bounds": [10, 20, 200, 60],
        "node": {"text": "选项"},
    })
    assert step["action"] == "long_press"
    assert "选项" in step["description"]


# ============================================================
# 长按坐标测试
# ============================================================


def test_normalize_long_press_coord():
    step = normalize_assistant_event({
        "type": "long-press",
        "x": 300,
        "y": 400,
    })
    assert step["action"] == "long_press"
    assert step["selector_type"] == "viewport_coord"
