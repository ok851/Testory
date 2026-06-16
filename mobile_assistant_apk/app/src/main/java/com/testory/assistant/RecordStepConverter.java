package com.testory.assistant;

import org.json.JSONArray;
import org.json.JSONObject;

/** 将无障碍录制事件转为可回放步骤。 */
final class RecordStepConverter {

    private RecordStepConverter() {
    }

    static JSONObject toDbStep(JSONObject raw, int order) throws Exception {
        JSONObject step = new JSONObject();
        step.put("step_order", order);
        step.put("automation_layer", "android");
        String type = raw.optString("type", "click");
        JSONObject node = raw.optJSONObject("node");
        JSONArray bounds = raw.optJSONArray("bounds");

        if ("swipe".equals(type)) {
            step.put("action", "swipe");
            step.put("description", raw.optString("description", "滑动"));
            JSONObject spec = new JSONObject();
            if (bounds != null && bounds.length() >= 4) {
                int cx = (bounds.getInt(0) + bounds.getInt(2)) / 2;
                int cy = (bounds.getInt(1) + bounds.getInt(3)) / 2;
                spec.put("x1", cx);
                spec.put("y1", cy + 200);
                spec.put("x2", cx);
                spec.put("y2", cy - 200);
            } else {
                spec.put("x1", 540);
                spec.put("y1", 1600);
                spec.put("x2", 540);
                spec.put("y2", 800);
            }
            step.put("mobile_spec", spec);
            return step;
        }
        if ("input".equals(type)) {
            step.put("action", "input_text");
            step.put("input_value", raw.optString("text", ""));
            step.put("description", "输入文本");
            applySelector(step, node);
            return step;
        }

        step.put("action", "tap");
        step.put("description", describeNode(node, type));
        applySelector(step, node);
        if (bounds != null && bounds.length() >= 4) {
            JSONObject spec = new JSONObject();
            JSONObject vc = new JSONObject();
            vc.put("x", (bounds.getInt(0) + bounds.getInt(2)) / 2);
            vc.put("y", (bounds.getInt(1) + bounds.getInt(3)) / 2);
            spec.put("viewport_coord", vc);
            step.put("mobile_spec", spec);
        }
        return step;
    }

    private static void applySelector(JSONObject step, JSONObject node) throws Exception {
        if (node == null) {
            step.put("selector_type", "viewport_coord");
            step.put("selector_value", "");
            return;
        }
        if (node.has("resource_id") && !node.optString("resource_id", "").isEmpty()) {
            step.put("selector_type", "id");
            step.put("selector_value", node.getString("resource_id"));
            return;
        }
        if (node.has("content_desc") && !node.optString("content_desc", "").isEmpty()) {
            step.put("selector_type", "accessibility_id");
            step.put("selector_value", node.getString("content_desc"));
            return;
        }
        if (node.has("text") && !node.optString("text", "").isEmpty()) {
            step.put("selector_type", "accessibility_id");
            step.put("selector_value", node.getString("text"));
            return;
        }
        step.put("selector_type", "viewport_coord");
        step.put("selector_value", "");
    }

    private static String describeNode(JSONObject node, String type) {
        if (node == null) return "long-press".equals(type) ? "长按" : "点击";
        if (node.has("text")) return "点击 " + node.optString("text");
        if (node.has("content_desc")) return "点击 " + node.optString("content_desc");
        return "long-press".equals(type) ? "长按" : "点击";
    }
}
