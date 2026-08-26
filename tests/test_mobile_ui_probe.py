# -*- coding: utf-8 -*-
"""mobile_ui_probe 单元测试。"""

from modules.mobile.mobile_ui_probe import find_node_at_point, suggest_locator_from_node


_SAMPLE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<hierarchy>
  <node bounds="[0,0][1080,1920]" class="android.widget.FrameLayout">
    <node bounds="[100,200][300,280]" class="android.widget.Button"
          resource-id="com.demo:id/login" text="登录" clickable="true" />
  </node>
</hierarchy>
"""


def test_find_node_at_point_smallest():
    node = find_node_at_point(_SAMPLE_XML, 150, 240)
    assert node is not None
    assert node.get("resource_id") == "com.demo:id/login"
    assert node.get("clickable") is True


def test_suggest_locator_resource_id():
    node = find_node_at_point(_SAMPLE_XML, 150, 240)
    loc = suggest_locator_from_node(node)
    assert loc["strategy"] == "id"
    assert loc["selector_value"] == "com.demo:id/login"
