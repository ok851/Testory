package com.testory.assistant;

import org.json.JSONArray;
import org.json.JSONObject;

/** 与 PC 端 mobile_assistant_events.normalize_assistant_event 对齐的步骤归一化。 */
final class StepNormalizer {

    private StepNormalizer() {
    }

    static JSONObject toDbStep(JSONObject raw, int order) throws Exception {
        String type = (raw.optString("type", "click")).trim().toLowerCase();

        if ("open_app".equals(type)) {
            String pkg = raw.optString("package", raw.optString("app_package", ""));
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
            step.put("description", raw.optString("description", "打开应用"));
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
        JSONArray bounds = raw.optJSONArray("bounds");
        int cx = 0;
        int cy = 0;
        if (bounds != null && bounds.length() >= 4) {
            cx = (bounds.getInt(0) + bounds.getInt(2)) / 2;
            cy = (bounds.getInt(1) + bounds.getInt(3)) / 2;
        }

        JSONObject mobileSpec = new JSONObject();
        mobileSpec.put("source", "assistant");
        if (bounds != null) {
            mobileSpec.put("bounds", bounds);
        }
        if (cx > 0 || cy > 0) {
            JSONObject vc = new JSONObject();
            vc.put("x", cx);
            vc.put("y", cy);
            mobileSpec.put("viewport_coord", vc);
        }

        JSONObject step = new JSONObject();
        step.put("step_order", order);
        step.put("automation_layer", "android");

        if ("swipe".equals(type) || "scroll".equals(type) || "view_scrolled".equals(type)) {
            step.put("action", "swipe");
            step.put("description", raw.optString("description", "滑动"));
            step.put("selector_type", "");
            step.put("selector_value", "");
            step.put("input_value", "");
            applySwipeSpec(mobileSpec, raw, bounds, cx, cy);
            applyPackage(mobileSpec, raw);
            step.put("mobile_spec", mobileSpec);
            return step;
        }

        if ("input".equals(type) || "text_changed".equals(type) || "type".equals(type)) {
            step.put("action", "input_text");
            step.put("input_value", raw.optString("text", raw.optString("input_value", "")));
            step.put("description", raw.optString("description", "输入文本"));
            applySelector(step, node, cx, cy);
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

        String action = "long-press".equals(type) || "long_press".equals(type) ? "long_press" : "tap";
        step.put("action", action);
        step.put("description", describeNode(node, type, cx, cy));
        step.put("input_value", "");
        applySelector(step, node, cx, cy);
        applyPackage(mobileSpec, raw);
        step.put("mobile_spec", mobileSpec);
        return step;
    }

    private static JSONObject navigationStep(int order, String action, String description) throws Exception {
        JSONObject mobileSpec = new JSONObject();
        mobileSpec.put("source", "assistant");
        JSONObject step = new JSONObject();
        step.put("step_order", order);
        step.put("automation_layer", "android");
        step.put("action", action);
        step.put("description", description);
        step.put("selector_type", "");
        step.put("selector_value", "");
        step.put("input_value", "");
        step.put("mobile_spec", mobileSpec);
        return step;
    }

    private static void applyPackage(JSONObject mobileSpec, JSONObject raw) throws Exception {
        String pkg = raw.optString("package", "");
        if (pkg.isEmpty()) {
            pkg = raw.optString("app_package", "");
        }
        if (!pkg.isEmpty() && !"com.testory.assistant".equals(pkg)) {
            mobileSpec.put("app_package", pkg);
            mobileSpec.put("appPackage", pkg);
        }
    }

    private static void applySwipeSpec(
            JSONObject spec, JSONObject raw, JSONArray bounds, int cx, int cy) throws Exception {
        int dx = raw.optInt("scroll_delta_x", 0);
        int dy = raw.optInt("scroll_delta_y", 0);
        int dist = 320;
        if (bounds != null && bounds.length() >= 4) {
            if (dx != 0 || dy != 0) {
                spec.put("x1", cx - dx);
                spec.put("y1", cy - dy);
                spec.put("x2", cx + dx);
                spec.put("y2", cy + dy);
                return;
            }
            if (dy < 0) {
                spec.put("x1", cx);
                spec.put("y1", cy + dist / 2);
                spec.put("x2", cx);
                spec.put("y2", cy - dist / 2);
                return;
            }
            if (dy > 0) {
                spec.put("x1", cx);
                spec.put("y1", cy - dist / 2);
                spec.put("x2", cx);
                spec.put("y2", cy + dist / 2);
                return;
            }
            if (dx < 0) {
                spec.put("x1", cx + dist / 2);
                spec.put("y1", cy);
                spec.put("x2", cx - dist / 2);
                spec.put("y2", cy);
                return;
            }
            if (dx > 0) {
                spec.put("x1", cx - dist / 2);
                spec.put("y1", cy);
                spec.put("x2", cx + dist / 2);
                spec.put("y2", cy);
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

    private static void applySelector(JSONObject step, JSONObject node, int cx, int cy) throws Exception {
        if (node != null && node.has("resource_id")
                && !node.optString("resource_id", "").isEmpty()) {
            step.put("selector_type", "id");
            step.put("selector_value", node.getString("resource_id"));
            return;
        }
        if (node != null && node.has("content_desc")
                && !node.optString("content_desc", "").isEmpty()) {
            step.put("selector_type", "accessibility_id");
            step.put("selector_value", node.getString("content_desc"));
            return;
        }
        if (node != null && node.has("text") && !node.optString("text", "").isEmpty()) {
            step.put("selector_type", "android_uiautomator");
            step.put("selector_value",
                    "new UiSelector().text(\"" + node.getString("text").replace("\"", "\\\"") + "\")");
            return;
        }
        if (cx > 0 || cy > 0) {
            step.put("selector_type", "viewport_coord");
            JSONObject coord = new JSONObject();
            coord.put("x", cx);
            coord.put("y", cy);
            step.put("selector_value", coord.toString());
            return;
        }
        step.put("selector_type", "viewport_coord");
        step.put("selector_value", "");
    }

    private static String describeNode(JSONObject node, String type, int cx, int cy) {
        if (node != null) {
            if (node.has("text") && !node.optString("text", "").isEmpty()) {
                return "点击 " + node.optString("text");
            }
            if (node.has("content_desc") && !node.optString("content_desc", "").isEmpty()) {
                return "点击 " + node.optString("content_desc");
            }
        }
        if (cx > 0 || cy > 0) {
            return "点击 (" + cx + "," + cy + ")";
        }
        return "long-press".equals(type) || "long_press".equals(type) ? "长按" : "点击";
    }
}
