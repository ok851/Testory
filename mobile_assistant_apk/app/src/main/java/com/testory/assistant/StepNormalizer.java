package com.testory.assistant;

import org.json.JSONArray;
import org.json.JSONObject;

import android.content.Context;
import android.util.DisplayMetrics;

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
        if (node == null && raw.has("operation_node")) {
            node = raw.optJSONObject("operation_node");
        }
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

        String action = "long-press".equals(type) || "long_press".equals(type) ? "long_press" : "tap";
        step.put("action", action);
        step.put("description", describeNode(node, type, cx, cy));
        step.put("input_value", "");
        applySelector(step, node, cx, cy, mobileSpec);
        applyPackage(mobileSpec, raw);
        if (tryPromoteLauncherIconToOpenApp(step, mobileSpec, node)) {
            step.put("mobile_spec", mobileSpec);
            return step;
        }
        step.put("mobile_spec", mobileSpec);
        return step;
    }

    /**
     * Launcher 桌面图标：记录为 open_app（按包名启动），避免仅坐标回放导致图标移位/翻页失败。
     */
    private static boolean tryPromoteLauncherIconToOpenApp(
            JSONObject step, JSONObject mobileSpec, JSONObject node) throws Exception {
        if (step == null || mobileSpec == null) return false;
        String ctxPkg = mobileSpec.optString("context_package", "");
        if (ctxPkg.isEmpty()) {
            ctxPkg = mobileSpec.optString("app_package", "");
        }
        if (!AppLauncher.isSkippablePackage(ctxPkg)) return false;

        JSONObject opNode = mobileSpec.optJSONObject("operation_node");
        if (opNode == null) opNode = node;
        String label = pickNodeLabel(opNode);
        if (label.isEmpty()) return false;

        Context appCtx = AssistantApplicationHolder.get();
        if (appCtx == null) return false;
        String targetPkg = AppLauncher.resolvePackageByLabel(appCtx, label);
        if (targetPkg.isEmpty() || AppLauncher.isSkippablePackage(targetPkg)) return false;

        mobileSpec.put("target_package", targetPkg);
        mobileSpec.put("target_label", label);
        mobileSpec.put("app_package", targetPkg);
        mobileSpec.put("appPackage", targetPkg);
        step.put("action", "open_app");
        step.put("input_value", targetPkg);
        step.put("description", "打开 " + label);
        step.put("selector_type", "accessibility_id");
        step.put("selector_value", label);
        return true;
    }

    private static String pickNodeLabel(JSONObject opNode) {
        if (opNode == null) return "";
        String label = opNode.optString("content_desc", "").trim();
        if (!label.isEmpty()) return label;
        label = opNode.optString("text", "").trim();
        if (!label.isEmpty()) return label;
        JSONArray assistants = opNode.optJSONArray("assistant_nodes");
        if (assistants != null) {
            for (int i = 0; i < assistants.length(); i++) {
                JSONObject a = assistants.optJSONObject(i);
                if (a == null) continue;
                String d = a.optString("content_desc", "").trim();
                if (!d.isEmpty()) return d;
                String t = a.optString("text", "").trim();
                if (!t.isEmpty()) return t;
            }
        }
        return "";
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
        if (pkg.isEmpty()) {
            pkg = AssistantSession.getRecordingContextPackage();
        }
        if (!pkg.isEmpty() && !"com.testory.assistant".equals(pkg)) {
            mobileSpec.put("context_package", pkg);
        }
    }

    private static void applyScreenMetrics(JSONObject mobileSpec) throws Exception {
        Context ctx = AssistantApplicationHolder.get();
        if (ctx == null) return;
        DisplayMetrics dm = ctx.getResources().getDisplayMetrics();
        int sw = dm.widthPixels;
        int sh = dm.heightPixels;
        if (sw <= 0 || sh <= 0) return;
        mobileSpec.put("screen_width", sw);
        mobileSpec.put("screen_height", sh);
        JSONObject vc = mobileSpec.optJSONObject("viewport_coord");
        if (vc == null) return;
        int x = vc.optInt("x", 0);
        int y = vc.optInt("y", 0);
        if (x > 0 || y > 0) {
            vc.put("rx", Math.round(x * 10000.0 / sw) / 10000.0);
            vc.put("ry", Math.round(y * 10000.0 / sh) / 10000.0);
        }
    }

    // Improved swipe spec: use scroll deltas when available, fallback to screen-relative defaults
    static void enrichSwipePayload(JSONObject payload) throws Exception {
        if (payload == null) return;
        JSONArray bounds = payload.optJSONArray("bounds");
        int cx = 0;
        int cy = 0;
        if (bounds != null && bounds.length() >= 4) {
            cx = (bounds.getInt(0) + bounds.getInt(2)) / 2;
            cy = (bounds.getInt(1) + bounds.getInt(3)) / 2;
        }
        JSONObject spec = new JSONObject();
        applySwipeSpec(spec, payload, bounds, cx, cy);
        payload.put("x1", spec.getInt("x1"));
        payload.put("y1", spec.getInt("y1"));
        payload.put("x2", spec.getInt("x2"));
        payload.put("y2", spec.getInt("y2"));
    }

    private static void applySwipeSpec(
            JSONObject spec, JSONObject raw, JSONArray bounds, int cx, int cy) throws Exception {
        // 优先使用触摸管线产生的真实起止坐标（SoloPi TouchWrapper）
        int tx1 = raw.optInt("x1", 0);
        int ty1 = raw.optInt("y1", 0);
        int tx2 = raw.optInt("x2", 0);
        int ty2 = raw.optInt("y2", 0);
        if (tx1 != tx2 || ty1 != ty2) {
            spec.put("x1", tx1);
            spec.put("y1", ty1);
            spec.put("x2", tx2);
            spec.put("y2", ty2);
            applySwipePercent(spec);
            return;
        }
        int dx = raw.optInt("scroll_delta_x", 0);
        int dy = raw.optInt("scroll_delta_y", 0);
        int dist = 400;  // increased from 320 for more noticeable swipe
        if (bounds != null && bounds.length() >= 4) {
            if (dx != 0 || dy != 0) {
                // Use actual scroll deltas for accurate swipe replication
                spec.put("x1", cx - dx);
                spec.put("y1", cy - dy);
                spec.put("x2", cx + dx);
                spec.put("y2", cy + dy);
                return;
            }
            // No deltas: use bounds center with sensible defaults
            // vertical scroll: swipe upward (typical scroll-down gesture)
            spec.put("x1", cx);
            spec.put("y1", cy + dist / 2);
            spec.put("x2", cx);
            spec.put("y2", cy - dist / 2);
            return;
        }
        // Absolute fallback: use common center-screen coordinates
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

    private static void applySelector(
            JSONObject step, JSONObject node, int cx, int cy, JSONObject mobileSpec) throws Exception {
        JSONObject opNode = mobileSpec.optJSONObject("operation_node");
        if (opNode == null) opNode = node;
        if (opNode != null && opNode.has("resource_id")
                && !opNode.optString("resource_id", "").isEmpty()) {
            step.put("selector_type", "id");
            step.put("selector_value", opNode.getString("resource_id"));
            return;
        }
        if (opNode != null && opNode.has("content_desc")
                && !opNode.optString("content_desc", "").isEmpty()) {
            step.put("selector_type", "accessibility_id");
            step.put("selector_value", opNode.getString("content_desc"));
            return;
        }
        if (opNode != null && opNode.has("text") && !opNode.optString("text", "").isEmpty()) {
            step.put("selector_type", "accessibility_id");
            step.put("selector_value", opNode.getString("text"));
            return;
        }
        if (opNode != null && opNode.has("xpath")
                && !opNode.optString("xpath", "").isEmpty()) {
            step.put("selector_type", "xpath");
            step.put("selector_value", opNode.getString("xpath"));
            return;
        }
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
