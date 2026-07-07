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


# ========================================================================
# v4 新增：智能节点选择与一致性校验
# ========================================================================

def _point_in_bounds(px: int, py: int, bounds) -> bool:
    """检查点是否在 bounds 矩形内（含5%容差）。"""
    if not isinstance(bounds, (list, tuple)) or len(bounds) < 4:
        return False
    try:
        left, top, right, bottom = [int(v) for v in bounds[:4]]
        w, h = right - left, bottom - top
        if w <= 0 or h <= 0:
            return False
        margin_x = max(w * 0.05, 10)
        margin_y = max(h * 0.05, 10)
        return (left - margin_x <= px <= right + margin_x
                and top - margin_y <= py <= bottom + margin_y)
    except (TypeError, ValueError):
        return False


def _select_primary_node(
    node: Dict[str, Any],
    op_node: Dict[str, Any],
    event: Dict[str, Any],
) -> Tuple[Dict[str, Any], str]:
    """
    智能选择用于提取定位符的主节点（v5增强：坐标优先校验）。

    v5 修复根因：用户点击"SIM卡"却被记录为"通话和短信"。
    原因：operation_node 是设备端选择的父容器，可能包含错误文本。
    
    核心策略变更：
      旧版(v4): 按 text > resource_id > class 优先级选节点
      新版(v5): 先用触摸坐标验证哪个节点真正包含点击点，再结合标识符质量

    Returns:
        (chosen_node, reason)
    """
    # ---- 阶段0: 提取触摸坐标（最高可信度）----
    touch_x = touch_y = 0
    has_touch = False
    if event.get("x") is not None and event.get("y") is not None:
        try:
            touch_x, touch_y = int(event["x"]), int(event["y"])
            has_touch = True
        except (TypeError, ValueError):
            pass

    # ---- 阶段1: 坐标包容性验证（最可靠）----
    # 如果有触摸坐标，检查哪个节点的bounds真正包含该点
    if has_touch and (touch_x != 0 or touch_y != 0):
        node_bounds = node.get("bounds") or node.get("boundingRect")
        op_bounds = op_node.get("bounds") or op_node.get("boundingRect")
        node_contains = _point_in_bounds(touch_x, touch_y, node_bounds)
        op_contains = _point_in_bounds(touch_x, touch_y, op_bounds)

        # 只有node包含点击点 → 直接使用node
        if node_contains and not op_contains:
            return node, "coord_match_node_only"
        
        # 只有op_node包含点击点 → 使用op_node（说明device端选择了正确的父节点）
        if op_contains and not node_contains:
            return op_node, "coord_match_op_node_only"
        
        # 两者都包含（op_node是node的父容器，常见情况）→ 进入阶段2用标识符质量决定
        # 两者都不包含 → 也进入阶段2

    # ---- 阶段2: 节点标识符质量评分 ----
    def _node_score(nd: Dict[str, Any]) -> int:
        """对节点标识符质量打分（越高越可靠）。"""
        s = 0
        if (nd.get("text") or "").strip():
            s += 30
        rid = (nd.get("resource_id") or nd.get("resource-id") or "").strip()
        if rid:
            s += 25  # resourceID稳定但不如text直观
            if "@" in rid:  # 完整格式如 android:id/text1 更稳定
                s += 10
        if (nd.get("content_desc") or nd.get("content-desc") or "").strip():
            s += 20
        if (nd.get("xpath") or "").strip():
            s += 15
        cls = (nd.get("class") or nd.get("class_name") or "").strip()
        if cls:
            s += 5
        return s

    node_score = _node_score(node)
    op_score = _node_score(op_node)

    # node 分数明显更高(>5分差距) → 信任node的标识符
    if node_score > op_score + 5:
        # 但如果坐标明确指向op_node且node分数不高，需要额外警惕
        if has_touch:
            op_bounds = op_node.get("bounds") or op_node.get("boundingRect")
            if _point_in_bounds(touch_x, touch_y, op_bounds) and node_score < 30:
                # node有弱标识符但坐标不在范围内，op_node可能是正确选择
                pass  # 继续走下面的通用逻辑
            else:
                return node, f"score_node_{node_score}_vs_op_{op_score}"

    # op_node 分数明显更高 → 使用op_node
    if op_score > node_score + 5:
        return op_node, f"score_op_{op_score}_vs_node_{node_score}"

    # 分数接近时，如果有坐标信息且node非空，优先信任node（它是原始触摸命中目标）
    if has_touch and bool(node):
        return node, "coord_tiebreak_prefer_raw_node"

    # ---- 阶段3: 传统规则兜底（保持向后兼容）----
    if (node.get("text") or "").strip():
        return node, "fallback_node_text"
    if (node.get("resource_id") or node.get("resource-id") or "").strip():
        return node, "fallback_node_rid"
    
    if op_node and (op_node.get("text") or op_node.get("resource_id")
                    or op_node.get("content_desc")):
        return op_node, "fallback_op_node"

    return node, "no_valid_node"


def _validate_coord_node_consistency(
    cx: int,
    cy: int,
    primary_node: Dict[str, Any],
    margin_ratio: float = 0.15,
) -> Tuple[bool, str, Optional[Tuple[int, int, int, int]]]:
    """
    校验触摸坐标是否落在所选节点的bounds范围内。

    v4 新增功能: 解决根因4 —— 坐标来自event.x/y但selector来自不同节点的不一致问题。

    Args:
        cx, cy: 触摸坐标
        primary_node: 用于提取定位符的节点
        margin_ratio: 允许的超容差比例(相对于节点宽高)，默认15%

    Returns:
        (is_consistent, reason, node_bounds)
        - is_consistent=True: 坐标在节点范围内（含容差）
        - is_consistent=False: 坐标超出节点范围，可能存在定位偏差
    """
    bounds = (
        primary_node.get("bounds")
        or primary_node.get("boundingRect")
        or None
    )
    if not bounds:
        return True, "no_bounds_to_check", None

    try:
        if isinstance(bounds, (list, tuple)) and len(bounds) >= 4:
            l, t, r, b = [int(x) for x in bounds[:4]]
        elif isinstance(bounds, dict):
            l = int(bounds.get("left") or bounds.get("l") or 0)
            t = int(bounds.get("top") or bounds.get("t") or 0)
            r = int(bounds.get("right") or bounds.get("r") or l)
            b = int(bounds.get("bottom") or bounds.get("b") or t)
        else:
            return True, "invalid_bounds_format", None

        w = max(1, r - l)
        h = max(1, b - t)
        margin_x = max(10, int(w * margin_ratio))
        margin_y = max(10, int(h * margin_ratio))

        is_inside = (
            (l - margin_x) <= cx <= (r + margin_x)
            and (t - margin_y) <= cy <= (b + margin_y)
        )

        if is_inside:
            return True, "within_bounds", (l, t, r, b)
        else:
            return False, f"coord({cx},{cy}) outside node_bounds[{l},{t},{r},{b}]", (l, t, r, b)
    except (TypeError, ValueError):
        return True, "bounds_parse_error", None


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
    
    v4 修改：优先使用 primary_node（已通过智能选择），不再盲目优先 operation_node。
    原缺陷：operation_node 可能是设备端的妥协选择（如父容器），导致描述不准确。
    """
    desc = (event.get("description") or "").strip()
    if desc:
        return desc

    # v4: 优先检查 primary_node（用户实际触摸的目标节点），而非 operation_node
    for nd in (node, op_node):
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
    将助手 WebSocket 事件转为步骤字段草案（v4）。

    v4 修改要点：
      1. 使用 _select_primary_node() 智能选择定位符来源节点（修复根因3）
      2. 新增坐标与节点一致性校验（修复根因4）
      3. viewport_coord 无条件写入（修复条件判断遗漏）
      4. 增加 _diagnostic 字段用于问题排查
    支持 type: click | scroll | swipe | capture | input | dialog | long-press | open_app
    """
    etype = (event.get("type") or event.get("action") or "").strip().lower()
    raw_node = event.get("node") if isinstance(event.get("node"), dict) else {}
    op_node = event.get("operation_node") if isinstance(event.get("operation_node"), dict) else {}

    # ---- v4 Fix 1.1: 使用智能节点选择替代原 node/op_node 盲目替换 ----
    primary_node, node_select_reason = _select_primary_node(raw_node, op_node, event)

    # 坐标解析（保留原有逻辑，但增加来源追踪）
    bounds = event.get("bounds") or primary_node.get("bounds") or op_node.get("bounds") or raw_node.get("bounds")
    cx, cy = _bounds_center(bounds)
    coord_source = "bounds_center"
    if event.get("x") is not None and event.get("y") is not None:
        try:
            cx, cy = int(event["x"]), int(event["y"])
            coord_source = "event_touch"
        except (TypeError, ValueError):
            pass

    # ---- v4 Fix 1.2: 坐标与节点一致性校验 ----
    is_coord_consistent, consistency_reason, node_bounds = _validate_coord_node_consistency(
        cx, cy, primary_node
    )

    # ---- open_app: 自动获取应用名 ----
    if etype == "open_app":
        pkg = (event.get("package") or event.get("app_package") or "").strip()
        app_label = (event.get("app_label") or "").strip()
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
        result = {
            "action": "open_app",
            "selector_type": "",
            "selector_value": "",
            "input_value": pkg,
            "description": description,
            "automation_layer": "android",
            "mobile_spec": open_app_spec,
        }
        # 写入诊断信息
        result["mobile_spec"]["_diagnostic"] = {
            "node_select_reason": node_select_reason,
            "raw_node_text": (raw_node.get("text") or "")[:40],
            "op_node_text": (op_node.get("text") or "")[:40],
            "primary_node_text": (primary_node.get("text") or "")[:40],
        }
        return result

    stype, sval = suggest_locator_from_node(primary_node)
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

    # ---- v4 Fix 1.3: viewport_coord 无条件写入（移除 cx!=0/cy!=0 前置条件）----
    # 原条件 if cx != 0 or cy != 0 会跳过边界值(如点击屏幕左边缘 x=0)
    # 新逻辑：只要存在任何坐标来源就写入
    has_any_coord_source = (
        coord_source == "event_touch"
        or (cx != 0 or cy != 0)
        or (event.get("x") is not None and event.get("y") is not None)
        or bounds is not None
    )
    if has_any_coord_source:
        mobile_spec["viewport_coord"] = {
            "x": cx, "y": cy,
            "source": coord_source,
        }
        if screen_width > 0 and screen_height > 0:
            mobile_spec["viewport_coord"]["rx"] = round(cx / screen_width, 4)
            mobile_spec["viewport_coord"]["ry"] = round(cy / screen_height, 4)

    if screen_width > 0:
        mobile_spec["screen_width"] = screen_width
    if screen_height > 0:
        mobile_spec["screen_height"] = screen_height

    # ---- v4 Fix 3.1: 写入诊断信息 ----
    mobile_spec["_diagnostic"] = {
        "coord_source": coord_source,
        "node_select_reason": node_select_reason,
        "is_coord_consistent": is_coord_consistent,
        "consistency_reason": consistency_reason,
        "raw_event_x": event.get("x"),
        "raw_event_y": event.get("y"),
        "primary_node_text": (primary_node.get("text") or "")[:40],
        "primary_node_resource_id": (primary_node.get("resource_id") or primary_node.get("resource-id") or "")[:60],
        "op_node_text": (op_node.get("text") or "")[:40],
        "resolved_cx": cx,
        "resolved_cy": cy,
    }

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
            primary_node.get("resource_id")
            or primary_node.get("text")
            or primary_node.get("content_desc")
            or primary_node.get("xpath")
            or primary_node.get("class_name")
        )

        # 使用增强版描述生成
        description = _build_click_description(event, primary_node, op_node, cx, cy)

        # 定位策略选择
        if has_element:
            stype, sval = suggest_locator_from_node(primary_node)
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
