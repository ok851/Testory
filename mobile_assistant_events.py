# -*- coding: utf-8 -*-
"""
Testory 移动端助手事件 → 用例步骤归一化（v3）。

v3 修改要点：
  1. 定位器优先级调整为 text > content_desc > 可读 resource_id > xpath > class
     原缺陷：resource_id 优先导致步骤显示 "com.android.settings:id/icon_frame"。
     Design inspired by mobile-automation-guide: 元素定位应以人类可读标识为首选。
  2. 点击描述优先使用 text/content_desc，坐标仅作兜底。
  3. open_app 事件自动获取应用名并写入 description。
  4. 滑动步骤确保携带起止坐标百分比，提升跨分辨率回放稳定性。
  5. 空白区域点击与元素点击分类更明确。
  6. 添加常见包名到应用名的映射表，作为 PackageManager 失败时的回退。
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional, Tuple

# 常见应用包名到友好名称的映射（设备端 PackageManager 失败时的回退）。
# Design inspired by mobile-automation-guide: 应用操作应记录人类可读标识。
COMMON_APP_LABELS: Dict[str, str] = {
    "com.tencent.mm": "微信",
    "com.tencent.mobileqq": "QQ",
    "com.eg.android.AlipayGphone": "支付宝",
    "com.taobao.taobao": "淘宝",
    "com.jingdong.app.mall": "京东",
    "com.ss.android.ugc.aweme": "抖音",
    "com.sina.weibo": "微博",
    "com.tencent.qqlive": "腾讯视频",
    "com.youku.phone": "优酷",
    "com.netease.cloudmusic": "网易云音乐",
    "com.tencent.qqmusic": "QQ音乐",
    "com.baidu.BaiduMap": "百度地图",
    "com.autonavi.minimap": "高德地图",
    "com.didi.passenger": "滴滴出行",
    "com.meituan": "美团",
    "me.ele": "饿了么",
    "com.xunmeng.pinduoduo": "拼多多",
    "com.android.settings": "设置",
    "com.android.camera": "相机",
    "com.android.gallery3d": "相册",
    "com.android.contacts": "联系人",
    "com.android.mms": "信息",
    "com.android.browser": "浏览器",
    "com.android.calendar": "日历",
    "com.android.deskclock": "时钟",
    "com.android.calculator2": "计算器",
    "com.android.email": "邮件",
    # vivo 常见应用
    "com.vivo.health": "vivo健康",
    "com.vivo.weather": "天气",
    "com.vivo.gallery": "相册",
    "com.vivo.browser": "浏览器",
    "com.vivo.calculator": "计算器",
    "com.vivo.timer": "闹钟",
    "com.vivo.note": "便签",
    "com.vivo.music": "音乐",
    "com.vivo.vivokaraoke": "唱K",
    "com.vivo.video": "视频",
    "com.vivo.appstore": "应用商店",
    "com.vivo.space": "i管家",
    "com.bbk.calendar": "日历",
    "com.bbk.cloud": "云服务",
    "com.bbk.theme": "主题",
    "com.bbk.SuperPowerSave": "超级省电",
    "com.iqoo.weather": "天气",
    # 小米常见应用
    "com.miui.calculator": "计算器",
    "com.miui.weather2": "天气",
    "com.miui.notes": "便签",
    "com.miui.gallery": "相册",
    "com.miui.player": "音乐",
    "com.miui.video": "视频",
    "com.miui.compass": "指南针",
    # 华为常见应用
    "com.huawei.camera": "相机",
    "com.huawei.gallery": "图库",
    "com.huawei.music": "音乐",
    "com.huawei.video": "视频",
    "com.huawei.calculator": "计算器",
    "com.huawei.notepad": "备忘录",
    # OPPO 常见应用
    "com.coloros.gallery3d": "相册",
    "com.coloros.calculator": "计算器",
    "com.coloros.weather2": "天气",
    "com.heytap.market": "应用商店",
}


def _resolve_app_label(pkg: str, app_label: str) -> str:
    """
    解析应用友好名称。
    优先使用设备端注入的 app_label，若为空或等于包名则查映射表。
    Design inspired by mobile-automation-guide: 应用操作应记录人类可读标识。
    """
    if app_label and app_label != pkg:
        return app_label
    mapped = COMMON_APP_LABELS.get(pkg)
    return mapped or pkg


def _apply_swipe_spec(
    event: Dict[str, Any],
    mobile_spec: Dict[str, Any],
    cx: int,
    cy: int,
) -> None:
    """Mirror StepNormalizer.applySwipeSpec — use absolute endpoints or scroll deltas."""
    # 优先使用绝对起止坐标（TouchGestureClassifier 产出）
    try:
        x1 = event.get("x1")
        x2 = event.get("x2")
        if x1 is not None and x2 is not None:
            mobile_spec.update({
                "x1": int(x1),
                "y1": int(event.get("y1") or event.get("from_y") or cy),
                "x2": int(x2),
                "y2": int(event.get("y2") or event.get("to_y") or cy),
            })
            return
    except (TypeError, ValueError):
        pass

    dx = int(event.get("scroll_delta_x") or 0)
    dy = int(event.get("scroll_delta_y") or 0)
    dist = 400
    if dx or dy:
        mobile_spec.update({
            "x1": cx - dx,
            "y1": cy - dy,
            "x2": cx + dx,
            "y2": cy + dy,
        })
        return
    if cx or cy:
        mobile_spec.update({
            "x1": cx,
            "y1": cy + dist // 2,
            "x2": cx,
            "y2": cy - dist // 2,
        })
        return
    mobile_spec.update({"x1": 540, "y1": 1600, "x2": 540, "y2": 800})


def _bounds_center(bounds: Any) -> Tuple[int, int]:
    if isinstance(bounds, (list, tuple)) and len(bounds) >= 4:
        l, t, r, b = [int(x) for x in bounds[:4]]
        return (l + r) // 2, (t + b) // 2
    if isinstance(bounds, dict):
        l = int(bounds.get("left") or bounds.get("l") or 0)
        t = int(bounds.get("top") or bounds.get("t") or 0)
        r = int(bounds.get("right") or bounds.get("r") or l)
        b = int(bounds.get("bottom") or bounds.get("b") or t)
        return (l + r) // 2, (t + b) // 2
    return 0, 0


def suggest_locator_from_node(node: Dict[str, Any]) -> Tuple[str, str]:
    """
    元素定位策略选择器（v3 优先级）。

    原优先级（有缺陷）：id > accessibility_id > xpath > text > class
    ——resource_id 优先导致步骤显示 "com.android.settings:id/icon_frame"。

    新优先级：text > content_desc > 可读 resource_id > xpath > class
    Design inspired by mobile-automation-guide: 人类可读标识应作为首选定位策略。
    """
    # 优先级 1：text（最易读，如 "Wi-Fi"）
    text = (node.get("text") or "").strip()
    if text:
        return "accessibility_id", text

    # 优先级 2：content_desc（无障碍描述，如 "设置图标"）
    desc = (node.get("content_desc") or node.get("content-desc") or "").strip()
    if desc:
        return "accessibility_id", desc

    # 优先级 3：resource_id（使用可读的短 ID，而非完整的包名路径）
    # 原缺陷：使用完整 rid，导致步骤显示 "com.android.settings:id/icon_frame"。
    # 新逻辑：提取 "/" 后的可读部分，如 "icon_frame"。
    # Design inspired by mobile-automation-guide: 定位符应尽可能人类可读。
    rid = (node.get("resource_id") or node.get("resource-id") or "").strip()
    if rid:
        # 提取可读部分：去掉包名前缀，取 "/" 后的 ID 部分
        short_id = rid.rsplit("/", 1)[-1] if "/" in rid else rid
        return "id", short_id

    # 优先级 4：xpath
    xpath = (node.get("xpath") or "").strip()
    if xpath:
        return "xpath", xpath

    # 优先级 5：class
    cls = (node.get("class") or node.get("class_name") or "").strip()
    if cls:
        return "android_uiautomator", f'new UiSelector().className("{cls}")'

    return "accessibility_id", ""


def _build_click_description(
    event: Dict[str, Any],
    node: Dict[str, Any],
    op_node: Dict[str, Any],
    cx: int,
    cy: int,
) -> str:
    """
    构建点击步骤的人类可读描述。
    优先级：event.description > node.text > op_node.text > content_desc > 可读 resource_id > 坐标。
    Design inspired by mobile-automation-guide: 步骤描述应以用户可见文本为核心。
    """
    desc = (event.get("description") or "").strip()
    if desc:
        return desc

    # 优先检查 operation_node（通常携带更丰富的 UI 信息）
    for nd in (op_node, node):
        if not nd:
            continue
        text = (nd.get("text") or "").strip()
        if text:
            return f"点击「{text[:20]}」"
        content_desc = (nd.get("content_desc") or nd.get("content-desc") or "").strip()
        if content_desc:
            return f"点击「{content_desc[:20]}」"
        rid = (nd.get("resource_id") or nd.get("resource-id") or "").strip()
        if rid:
            short_id = rid.rsplit("/", 1)[-1] if "/" in rid else rid
            return f"点击 {short_id[:24]}"

    # 坐标兜底
    if cx > 0 or cy > 0:
        return f"点击 ({cx},{cy})"
    return "点击"


def normalize_assistant_event(
    event: Dict[str, Any],
    screen_width: int = 0,
    screen_height: int = 0,
) -> Dict[str, Any]:
    """
    将助手 WebSocket 事件转为步骤字段草案（v3）。

    支持 type: click | scroll | swipe | capture | input | dialog | long-press | open_app
    """
    etype = (event.get("type") or event.get("action") or "").strip().lower()
    node = event.get("node") if isinstance(event.get("node"), dict) else {}
    op_node = event.get("operation_node") if isinstance(event.get("operation_node"), dict) else {}
    if not node and op_node:
        node = op_node
    bounds = event.get("bounds") or node.get("bounds") or op_node.get("bounds")
    cx, cy = _bounds_center(bounds)
    if event.get("x") is not None and event.get("y") is not None:
        try:
            cx, cy = int(event["x"]), int(event["y"])
        except (TypeError, ValueError):
            pass

    # ---- open_app: 自动获取应用名 ----
    # 原缺陷：open_app 步骤 description 仅为 "打开应用"，不含应用名。
    # 新逻辑：使用 _resolve_app_label 解析友好应用名（设备端 app_label + 映射表回退）。
    # Design inspired by mobile-automation-guide: 应用操作应记录人类可读标识。
    if etype == "open_app":
        pkg = (event.get("package") or event.get("app_package") or "").strip()
        app_label = (event.get("app_label") or "").strip()
        # 使用增强的应用名解析：设备端 app_label → 映射表 → 包名
        resolved_label = _resolve_app_label(pkg, app_label)
        description = event.get("description") or ""
        if not description:
            if resolved_label:
                description = f"打开应用[{resolved_label}]"
            else:
                description = "打开应用"
        open_app_spec: Dict[str, Any] = {"source": "assistant"}
        if pkg:
            open_app_spec["app_package"] = pkg
            open_app_spec["appPackage"] = pkg
        return {
            "action": "open_app",
            "selector_type": "",
            "selector_value": "",
            "input_value": pkg,
            "description": description,
            "automation_layer": "android",
            "mobile_spec": open_app_spec,
        }

    stype, sval = suggest_locator_from_node(node)
    if event.get("selector_type"):
        stype = str(event.get("selector_type"))
    if event.get("selector_value"):
        sval = str(event.get("selector_value"))

    mobile_spec: Dict[str, Any] = {"source": "assistant"}

    pkg = (event.get("package") or event.get("app_package") or "").strip()
    if pkg and pkg != "com.testory.assistant":
        mobile_spec["context_package"] = pkg

    if bounds is not None:
        mobile_spec["bounds"] = bounds

    # 节点内相对坐标（跨分辨率回放）
    if event.get("node_rx") is not None and event.get("node_ry") is not None:
        try:
            mobile_spec["node_rx"] = float(event["node_rx"])
            mobile_spec["node_ry"] = float(event["node_ry"])
        except (TypeError, ValueError):
            pass

    if event.get("action_duration_ms") is not None:
        mobile_spec["action_duration_ms"] = event["action_duration_ms"]

    if isinstance(event.get("operation_node"), dict):
        mobile_spec["operation_node"] = event["operation_node"]

    if cx != 0 or cy != 0:
        mobile_spec["viewport_coord"] = {"x": cx, "y": cy}
        if screen_width > 0 and screen_height > 0:
            mobile_spec["viewport_coord"]["rx"] = round(cx / screen_width, 4)
            mobile_spec["viewport_coord"]["ry"] = round(cy / screen_height, 4)

    if screen_width > 0:
        mobile_spec["screen_width"] = screen_width
    if screen_height > 0:
        mobile_spec["screen_height"] = screen_height

    if etype in ("press_home", "home"):
        return {
            "action": "press_home",
            "selector_type": "",
            "selector_value": "",
            "description": event.get("description") or "返回桌面",
            "automation_layer": "android",
            "mobile_spec": mobile_spec,
        }

    if etype in ("press_back", "back"):
        return {
            "action": "press_back",
            "selector_type": "",
            "selector_value": "",
            "description": event.get("description") or "返回上一页",
            "automation_layer": "android",
            "mobile_spec": mobile_spec,
        }

    if etype in ("click", "tap", "view_clicked", "capture", "long-press"):
        action = "tap" if etype != "long-press" else "long_press"

        has_element = bool(
            node.get("resource_id")
            or node.get("text")
            or node.get("content_desc")
            or node.get("xpath")
            or node.get("class_name")
        )

        # 使用增强版描述生成
        description = _build_click_description(event, node, op_node, cx, cy)

        # 定位策略选择
        if has_element:
            stype, sval = suggest_locator_from_node(node)
            if not sval and (cx or cy):
                stype = "viewport_coord"
                coord_dict = {"x": cx, "y": cy}
                if screen_width > 0 and screen_height > 0:
                    coord_dict["rx"] = round(cx / screen_width, 4)
                    coord_dict["ry"] = round(cy / screen_height, 4)
                sval = json.dumps(coord_dict, ensure_ascii=False)
        else:
            if cx or cy:
                stype = "viewport_coord"
                coord_dict = {"x": cx, "y": cy}
                if screen_width > 0 and screen_height > 0:
                    coord_dict["rx"] = round(cx / screen_width, 4)
                    coord_dict["ry"] = round(cy / screen_height, 4)
                sval = json.dumps(coord_dict, ensure_ascii=False)
            elif bounds is not None:
                bcx, bcy = _bounds_center(bounds)
                if bcx or bcy:
                    stype = "viewport_coord"
                    coord_dict = {"x": bcx, "y": bcy}
                    if screen_width > 0 and screen_height > 0:
                        coord_dict["rx"] = round(bcx / screen_width, 4)
                        coord_dict["ry"] = round(bcy / screen_height, 4)
                    sval = json.dumps(coord_dict, ensure_ascii=False)
                    cx, cy = bcx, bcy

        return {
            "action": action,
            "selector_type": stype,
            "selector_value": sval,
            "description": str(description),
            "automation_layer": "android",
            "mobile_spec": mobile_spec,
        }

    if etype in ("scroll", "swipe", "view_scrolled"):
        _apply_swipe_spec(event, mobile_spec, cx, cy)
        x1 = int(mobile_spec.get("x1") or 0)
        y1 = int(mobile_spec.get("y1") or 0)
        x2 = int(mobile_spec.get("x2") or 0)
        y2 = int(mobile_spec.get("y2") or 0)
        if screen_width > 0 and screen_height > 0:
            mobile_spec.update({
                "rx1": round(x1 / screen_width, 4),
                "ry1": round(y1 / screen_height, 4),
                "rx2": round(x2 / screen_width, 4),
                "ry2": round(y2 / screen_height, 4),
            })
        return {
            "action": "swipe",
            "selector_type": "",
            "selector_value": "",
            "description": event.get("description") or f"滑动 ({x1},{y1})→({x2},{y2})",
            "automation_layer": "android",
            "mobile_spec": mobile_spec,
        }

    if etype in ("input", "text_changed", "type"):
        text = str(event.get("text") or event.get("input_value") or "")
        return {
            "action": "input_text",
            "selector_type": stype,
            "selector_value": sval,
            "input_value": text,
            "description": event.get("description") or f"输入 {text[:24]}",
            "automation_layer": "android",
            "mobile_spec": mobile_spec,
        }

    # 默认：未知事件类型，尝试映射为点击
    if cx or cy:
        coord_dict = {"x": cx, "y": cy}
        if screen_width > 0 and screen_height > 0:
            coord_dict["rx"] = round(cx / screen_width, 4)
            coord_dict["ry"] = round(cy / screen_height, 4)
        return {
            "action": "tap",
            "selector_type": "viewport_coord",
            "selector_value": json.dumps(coord_dict, ensure_ascii=False),
            "description": event.get("description") or f"操作 ({cx},{cy})",
            "automation_layer": "android",
            "mobile_spec": mobile_spec,
        }

    return {
        "action": "tap",
        "selector_type": stype,
        "selector_value": sval,
        "description": event.get("description") or "未知操作",
        "automation_layer": "android",
        "mobile_spec": mobile_spec,
    }
