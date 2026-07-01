package com.testory.assistant;

import android.content.Context;
import android.content.pm.PackageManager;
import android.util.DisplayMetrics;

import org.json.JSONArray;
import org.json.JSONObject;

/**
 * 与 PC 端 mobile_assistant_events.normalize_assistant_event 对齐的步骤归一化。
 *
 * v2 修复要点：
 *  1. 元素定位策略优先级调整：text > content_desc > 可读 resource_id > 坐标兜底
 *     原缺陷：resource_id 优先导致步骤显示 "com.android.settings:id/icon_frame" 等晦涩 ID。
 *     Design inspired by mobile-automation-guide: 元素定位应以人类可读标识优先。
 *  2. 桌面图标点击自动获取应用名：open_app 事件携带 package 时，通过 PackageManager 获取
 *     友好应用名（如"设置"、"时钟"），写入 description。
 *  3. describeNode 优先使用 operation_node 的 text/content_desc，提供更高优先级的可读描述。
 *  4. 滑动步骤确保携带起止坐标，描述格式统一为 "滑动 (x1,y1)→(x2,y2)"。
 */
final class StepNormalizer {

    private StepNormalizer() {
    }

    static JSONObject toDbStep(JSONObject raw, int order) throws Exception {
        String type = (raw.optString("type", "click")).trim().toLowerCase();

        // ---- open_app: 自动解析友好应用名 ----
        // Design inspired by mobile-automation-guide: 应用启动应记录可读应用名。
        if ("open_app".equals(type)) {
            String pkg = raw.optString("package", raw.optString("app_package", ""));
            String appLabel = raw.optString("app_label", "");
            if (appLabel.isEmpty() && !pkg.isEmpty()) {
                appLabel = resolveAppLabel(pkg);
            }
            JSONObject mobileSpec = new JSONObject();
            mobileSpec.put("source", "assistant");
            if (!pkg.isEmpty()) {
                mobileSpec.put("app_package", pkg);
                mobileSpec.put("appPackage", pkg);
            }
            JSONObject step = new JSONObject();
            step.put("step_order", order);
            step.put("automation_layer", "android");
            step.put("action", "open_app");
            // 原缺陷：description 仅为 "打开应用"，不含应用名。
            // 新逻辑：有应用名时生成 "打开应用[设置]"。
            String desc = appLabel.isEmpty()
                    ? raw.optString("description", "打开应用")
                    : "打开应用[" + appLabel + "]";
            step.put("description", desc);
            step.put("selector_type", "");
            step.put("selector_value", "");
            step.put("input_value", pkg);
            step.put("mobile_spec", mobileSpec);
            return step;
        }

        if ("press_home".equals(type) || "home".equals(type)) {
            return navigationStep(order, "press_home", raw.optString("description", "返回桌面"));
        }
        if ("press_back".equals(type) || "back".equals(type)) {
            return navigationStep(order, "press_back", raw.optString("description", "返回上一页"));
        }

        JSONObject node = raw.optJSONObject("node");
        if (node == null && raw.has("operation_node")) {
            node = raw.optJSONObject("operation_node");
        }
        // 保存 operation_node 引用，供 describeNode 使用高优先级节点
        JSONObject opNodeRef = raw.has("operation_node") ? raw.optJSONObject("operation_node") : null;

        JSONArray bounds = raw.optJSONArray("bounds");
        int cx = raw.optInt("x", 0);
        int cy = raw.optInt("y", 0);
        if (cx == 0 && cy == 0 && bounds != null && bounds.length() >= 4) {
            cx = (bounds.getInt(0) + bounds.getInt(2)) / 2;
            cy = (bounds.getInt(1) + bounds.getInt(3)) / 2;
        }

        JSONObject mobileSpec = new JSONObject();
        mobileSpec.put("source", "assistant");
        if (bounds != null) {
            mobileSpec.put("bounds", bounds);
        }
        if (raw.has("node_rx") && raw.has("node_ry")) {
            mobileSpec.put("node_rx", raw.optDouble("node_rx"));
            mobileSpec.put("node_ry", raw.optDouble("node_ry"));
        }
        if (raw.has("action_duration_ms")) {
            mobileSpec.put("action_duration_ms", raw.optLong("action_duration_ms"));
        }
        if (raw.has("operation_node")) {
            mobileSpec.put("operation_node", raw.getJSONObject("operation_node"));
        }
        if (raw.has("local_click_pos")) {
            mobileSpec.put("local_click_pos", raw.getJSONObject("local_click_pos"));
        }
        if (raw.has("screen_size")) {
            JSONObject ss = raw.getJSONObject("screen_size");
            mobileSpec.put("screen_width", ss.optInt("width", 0));
            mobileSpec.put("screen_height", ss.optInt("height", 0));
        }
        if (cx != 0 || cy != 0) {
            JSONObject vc = new JSONObject();
            vc.put("x", cx);
            vc.put("y", cy);
            mobileSpec.put("viewport_coord", vc);
        }
        applyScreenMetrics(mobileSpec);

        JSONObject step = new JSONObject();
        step.put("step_order", order);
        step.put("automation_layer", "android");

        if ("swipe".equals(type) || "scroll".equals(type) || "view_scrolled".equals(type)) {
            step.put("action", "swipe");
            // 确保滑动描述包含起止坐标
            applySwipeSpec(mobileSpec, raw, bounds, cx, cy);
            String swipeDesc = "滑动 (" + mobileSpec.optInt("x1", cx) + "," + mobileSpec.optInt("y1", cy)
                    + ")→(" + mobileSpec.optInt("x2", cx) + "," + mobileSpec.optInt("y2", cy) + ")";
            step.put("description", raw.optString("description", swipeDesc));
            step.put("selector_type", "");
            step.put("selector_value", "");
            step.put("input_value", "");
            applyPackage(mobileSpec, raw);
            step.put("mobile_spec", mobileSpec);
            return step;
        }

        if ("input".equals(type) || "text_changed".equals(type) || "type".equals(type)) {
            step.put("action", "input_text");
            step.put("input_value", raw.optString("text", raw.optString("input_value", "")));
            step.put("description", raw.optString("description", "输入文本"));
            applySelector(step, node, cx, cy, mobileSpec);
            applyPackage(mobileSpec, raw);
            step.put("mobile_spec", mobileSpec);
            return step;
        }

        if ("dialog".equals(type)) {
            step.put("action", "tap");
            step.put("description", raw.optString("description", "处理系统弹窗"));
            step.put("selector_type", "");
            step.put("selector_value", "");
            step.put("input_value", "");
            applyPackage(mobileSpec, raw);
            step.put("mobile_spec", mobileSpec);
            return step;
        }

        // click / long-press / capture / 兜底
        String action = "long-press".equals(type) || "long_press".equals(type)
                ? "long_press" : "tap";
        step.put("action", action);
        // 使用增强版 describeNode：优先 operation_node > node > 坐标
        step.put("description", raw.optString("description", describeNode(opNodeRef, node, type, cx, cy)));
        applySelector(step, node, cx, cy, mobileSpec);
        applyPackage(mobileSpec, raw);
        step.put("mobile_spec", mobileSpec);
        return step;
    }

    private static JSONObject navigationStep(int order, String action, String desc) throws Exception {
        JSONObject step = new JSONObject();
        step.put("step_order", order);
        step.put("automation_layer", "android");
        step.put("action", action);
        step.put("description", desc);
        step.put("selector_type", "");
        step.put("selector_value", "");
        step.put("input_value", "");
        JSONObject spec = new JSONObject();
        spec.put("source", "assistant");
        step.put("mobile_spec", spec);
        return step;
    }

    private static void applyScreenMetrics(JSONObject spec) {
        try {
            Context ctx = AssistantApplicationHolder.get();
            if (ctx == null) return;
            DisplayMetrics dm = ctx.getResources().getDisplayMetrics();
            int sw = dm.widthPixels;
            int sh = dm.heightPixels;
            if (sw <= 0 || sh <= 0) return;
            spec.put("screen_width", sw);
            spec.put("screen_height", sh);
            int cx = spec.optInt("x", 0);
            int cy = spec.optInt("y", 0);
            if (cx > 0 && sw > 0) spec.put("rx", Math.round(cx * 10000.0 / sw) / 10000.0);
            if (cy > 0 && sh > 0) spec.put("ry", Math.round(cy * 10000.0 / sh) / 10000.0);
            applySwipePercent(spec);
        } catch (Exception ignored) {
        }
    }

    private static void applyPackage(JSONObject spec, JSONObject raw) throws Exception {
        String pkg = raw.optString("package", raw.optString("app_package", ""));
        if (!pkg.isEmpty() && !"com.testory.assistant".equals(pkg)) {
            spec.put("context_package", pkg);
        }
    }

    private static void applySwipeSpec(JSONObject spec, JSONObject raw, JSONArray bounds, int cx, int cy) throws Exception {
        // 优先使用原始事件中的绝对起止坐标（TouchGestureClassifier 产出）
        int rawX1 = raw.optInt("x1", 0);
        int rawY1 = raw.optInt("y1", 0);
        int rawX2 = raw.optInt("x2", 0);
        int rawY2 = raw.optInt("y2", 0);
        if (rawX1 != 0 || rawY1 != 0 || rawX2 != 0 || rawY2 != 0) {
            spec.put("x1", rawX1);
            spec.put("y1", rawY1);
            spec.put("x2", rawX2);
            spec.put("y2", rawY2);
            return;
        }
        int dx = raw.optInt("scroll_delta_x", 0);
        int dy = raw.optInt("scroll_delta_y", 0);
        int dist = 400;
        if (bounds != null && bounds.length() >= 4) {
            if (dx != 0 || dy != 0) {
                spec.put("x1", cx - dx);
                spec.put("y1", cy - dy);
                spec.put("x2", cx + dx);
                spec.put("y2", cy + dy);
                return;
            }
            spec.put("x1", cx);
            spec.put("y1", cy + dist / 2);
            spec.put("x2", cx);
            spec.put("y2", cy - dist / 2);
            return;
        }
        spec.put("x1", 540);
        spec.put("y1", 1600);
        spec.put("x2", 540);
        spec.put("y2", 800);
    }

    private static void applySwipePercent(JSONObject spec) throws Exception {
        Context ctx = AssistantApplicationHolder.get();
        if (ctx == null) return;
        DisplayMetrics dm = ctx.getResources().getDisplayMetrics();
        int sw = dm.widthPixels;
        int sh = dm.heightPixels;
        if (sw <= 0 || sh <= 0) return;
        spec.put("rx1", Math.round(spec.optInt("x1") * 10000.0 / sw) / 10000.0);
        spec.put("ry1", Math.round(spec.optInt("y1") * 10000.0 / sh) / 10000.0);
        spec.put("rx2", Math.round(spec.optInt("x2") * 10000.0 / sw) / 10000.0);
        spec.put("ry2", Math.round(spec.optInt("y2") * 10000.0 / sh) / 10000.0);
    }

    /**
     * 元素定位策略选择器。
     * 原缺陷：resource_id 优先，导致步骤显示 "com.android.settings:id/icon_frame"。
     * 新逻辑：text > content_desc > 可读 resource_id（取 / 后部分） > xpath > 坐标兜底。
     * Design inspired by mobile-automation-guide: 元素定位应以人类可读标识为首选策略。
     */
    private static void applySelector(
            JSONObject step, JSONObject node, int cx, int cy, JSONObject mobileSpec) throws Exception {
        JSONObject opNode = mobileSpec.optJSONObject("operation_node");
        if (opNode == null) opNode = node;

        // 优先级 1：text（最易读，如 "Wi-Fi"）
        if (opNode != null && opNode.has("text") && !opNode.optString("text", "").isEmpty()) {
            step.put("selector_type", "accessibility_id");
            step.put("selector_value", opNode.getString("text"));
            return;
        }
        // 优先级 2：content_desc（无障碍描述，如 "设置图标"）
        if (opNode != null && opNode.has("content_desc")
                && !opNode.optString("content_desc", "").isEmpty()) {
            step.put("selector_type", "accessibility_id");
            step.put("selector_value", opNode.getString("content_desc"));
            return;
        }
        // 优先级 3：resource_id 中可读部分（如 "settings_button" 而非完整包名路径）
        // 原缺陷：selector_value 使用完整 rid，导致步骤显示 "com.android.settings:id/icon_frame"。
        // 新逻辑：selector_value 使用可读的短 ID，description 中也使用短 ID。
        // Design inspired by mobile-automation-guide: 步骤应使用人类可读的标识符。
        if (opNode != null && opNode.has("resource_id")
                && !opNode.optString("resource_id", "").isEmpty()) {
            String rid = opNode.getString("resource_id");
            // 提取可读部分：去掉包名前缀，取 "/" 后的 ID 部分
            String shortId = rid.contains("/") ? rid.substring(rid.lastIndexOf("/") + 1) : rid;
            step.put("selector_type", "id");
            // 使用可读的短 ID 作为 selector_value，而非完整的 resource_id
            step.put("selector_value", shortId);
            return;
        }
        // 优先级 4：xpath
        if (opNode != null && opNode.has("xpath")
                && !opNode.optString("xpath", "").isEmpty()) {
            step.put("selector_type", "xpath");
            step.put("selector_value", opNode.getString("xpath"));
            return;
        }
        // 兜底：坐标
        if (cx > 0 || cy > 0) {
            step.put("selector_type", "viewport_coord");
            JSONObject coord = new JSONObject();
            coord.put("x", cx);
            coord.put("y", cy);
            JSONObject vc = mobileSpec.optJSONObject("viewport_coord");
            if (vc != null) {
                if (vc.has("rx")) coord.put("rx", vc.getDouble("rx"));
                if (vc.has("ry")) coord.put("ry", vc.getDouble("ry"));
            }
            step.put("selector_value", coord.toString());
            return;
        }
        step.put("selector_type", "viewport_coord");
        step.put("selector_value", "");
    }

    /**
     * 增强版元素描述生成。
     * 优先级：operation_node.text > node.text > content_desc > 可读 resource_id > 坐标。
     * 原缺陷：仅检查单一 node，且 resource_id 未做可读化截断。
     * Design inspired by mobile-automation-guide: 步骤描述应以人类可读文本为核心。
     */
    private static String describeNode(JSONObject opNode, JSONObject node, String type, int cx, int cy) {
        // 先检查 operation_node（通常有更丰富的信息）
        JSONObject primary = opNode != null ? opNode : node;
        JSONObject secondary = (primary == node) ? null : node;

        // 尝试 primary node
        if (primary != null) {
            String text = primary.optString("text", "");
            if (!text.isEmpty()) {
                return "点击「" + trunc(text, 20) + "」";
            }
            String desc = primary.optString("content_desc", "");
            if (!desc.isEmpty()) {
                return "点击「" + trunc(desc, 20) + "」";
            }
            String rid = primary.optString("resource_id", "");
            if (!rid.isEmpty()) {
                String shortId = rid.contains("/") ? rid.substring(rid.lastIndexOf("/") + 1) : rid;
                return "点击 " + trunc(shortId, 24);
            }
        }
        // 尝试 secondary node
        if (secondary != null) {
            String text = secondary.optString("text", "");
            if (!text.isEmpty()) {
                return "点击「" + trunc(text, 20) + "」";
            }
            String desc = secondary.optString("content_desc", "");
            if (!desc.isEmpty()) {
                return "点击「" + trunc(desc, 20) + "」";
            }
        }
        // 坐标兜底
        if (cx > 0 || cy > 0) {
            return "long-press".equals(type) || "long_press".equals(type)
                    ? "长按 (" + cx + "," + cy + ")"
                    : "点击 (" + cx + "," + cy + ")";
        }
        return "long-press".equals(type) || "long_press".equals(type) ? "长按" : "点击";
    }

    private static String trunc(String s, int max) {
        if (s == null) return "";
        return s.length() <= max ? s : s.substring(0, max) + "…";
    }

    /**
     * 通过 PackageManager 获取应用友好名称。
     * 原缺陷：open_app 步骤 description 仅为 "打开应用"，用户无法从步骤列表中识别目标应用。
     * 新逻辑：先查 PackageManager，失败时查内置映射表，最后回退到包名。
     * Design inspired by mobile-automation-guide: 应用操作应记录人类可读标识。
     */
    private static String resolveAppLabel(String packageName) {
        if (packageName == null || packageName.isEmpty()) return "";
        try {
            Context ctx = AssistantApplicationHolder.get();
            if (ctx != null) {
                PackageManager pm = ctx.getPackageManager();
                android.content.pm.ApplicationInfo info = pm.getApplicationInfo(packageName, 0);
                CharSequence label = pm.getApplicationLabel(info);
                if (label != null && label.length() > 0) {
                    String labelText = label.toString();
                    // 避免返回包名本身（某些设备上 getApplicationLabel 返回包名）
                    if (!labelText.equals(packageName)) {
                        return labelText;
                    }
                }
            }
        } catch (PackageManager.NameNotFoundException ignored) {
        } catch (Exception ignored) {
        }
        // 回退：常见包名映射表
        String mapped = COMMON_APP_LABELS.get(packageName);
        return mapped != null ? mapped : packageName;
    }

    /** 常见应用包名到友好名称的映射（PackageManager 失败时的回退）。 */
    private static final java.util.Map<String, String> COMMON_APP_LABELS = new java.util.HashMap<>();
    static {
        COMMON_APP_LABELS.put("com.tencent.mm", "微信");
        COMMON_APP_LABELS.put("com.tencent.mobileqq", "QQ");
        COMMON_APP_LABELS.put("com.eg.android.AlipayGphone", "支付宝");
        COMMON_APP_LABELS.put("com.taobao.taobao", "淘宝");
        COMMON_APP_LABELS.put("com.jingdong.app.mall", "京东");
        COMMON_APP_LABELS.put("com.ss.android.ugc.aweme", "抖音");
        COMMON_APP_LABELS.put("com.sina.weibo", "微博");
        COMMON_APP_LABELS.put("com.tencent.qqlive", "腾讯视频");
        COMMON_APP_LABELS.put("com.youku.phone", "优酷");
        COMMON_APP_LABELS.put("com.netease.cloudmusic", "网易云音乐");
        COMMON_APP_LABELS.put("com.tencent.qqmusic", "QQ音乐");
        COMMON_APP_LABELS.put("com.baidu.BaiduMap", "百度地图");
        COMMON_APP_LABELS.put("com.autonavi.minimap", "高德地图");
        COMMON_APP_LABELS.put("com.didi.passenger", "滴滴出行");
        COMMON_APP_LABELS.put("com.meituan", "美团");
        COMMON_APP_LABELS.put("me.ele", "饿了么");
        COMMON_APP_LABELS.put("com.xunmeng.pinduoduo", "拼多多");
        COMMON_APP_LABELS.put("com.android.settings", "设置");
        COMMON_APP_LABELS.put("com.android.camera", "相机");
        COMMON_APP_LABELS.put("com.android.gallery3d", "相册");
        COMMON_APP_LABELS.put("com.android.contacts", "联系人");
        COMMON_APP_LABELS.put("com.android.mms", "信息");
        COMMON_APP_LABELS.put("com.android.browser", "浏览器");
        COMMON_APP_LABELS.put("com.android.calendar", "日历");
        COMMON_APP_LABELS.put("com.android.deskclock", "时钟");
        COMMON_APP_LABELS.put("com.android.calculator2", "计算器");
        COMMON_APP_LABELS.put("com.android.email", "邮件");
    }
}
