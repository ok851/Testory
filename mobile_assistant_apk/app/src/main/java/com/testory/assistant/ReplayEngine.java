package com.testory.assistant;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.List;

/** 本地回放引擎（与 PC replay.py 语义对齐）。 */
public final class ReplayEngine {

    public interface Callback {
        void onStep(int index, JSONObject result);
    }

    private ReplayEngine() {
    }

    public static JSONObject runSteps(List<JSONObject> steps, Callback cb) throws Exception {
        AssistantAccessibilityService svc = AssistantSession.getService();
        if (svc == null) {
            throw new IllegalStateException("无障碍服务未就绪");
        }
        JSONArray results = new JSONArray();
        for (int i = 0; i < steps.size(); i++) {
            JSONObject step = steps.get(i);
            JSONObject result = executeStep(svc, step, i + 1);
            results.put(result);
            if (cb != null) cb.onStep(i + 1, result);
            if ("error".equals(result.optString("status"))) {
                JSONObject out = new JSONObject();
                out.put("success", false);
                out.put("error", result.optString("error"));
                out.put("failed_at", i + 1);
                out.put("results", results);
                return out;
            }
            Thread.sleep(350);
        }
        JSONObject out = new JSONObject();
        out.put("success", true);
        out.put("results", results);
        return out;
    }

    private static JSONObject executeStep(
            AssistantAccessibilityService svc, JSONObject step, int index) throws Exception {
        String action = step.optString("action", "").toLowerCase();
        String st = step.optString("selector_type", "");
        String sv = step.optString("selector_value", "");
        JSONObject spec = step.optJSONObject("mobile_spec");
        JSONObject result = new JSONObject();
        result.put("action", action);
        result.put("step_order", step.optInt("step_order", index));
        try {
            boolean ok;
            if ("tap".equals(action) || "click".equals(action)) {
                int x = 0, y = 0;
                if (spec != null && spec.has("viewport_coord")) {
                    JSONObject vc = spec.getJSONObject("viewport_coord");
                    x = vc.optInt("x", 0);
                    y = vc.optInt("y", 0);
                }
                ok = svc.performTap(st, sv, x, y);
            } else if ("swipe".equals(action)) {
                int x1 = spec != null ? spec.optInt("x1", 0) : 0;
                int y1 = spec != null ? spec.optInt("y1", 0) : 0;
                int x2 = spec != null ? spec.optInt("x2", x1) : x1;
                int y2 = spec != null ? spec.optInt("y2", y1) : y1;
                ok = svc.performSwipe(x1, y1, x2, y2);
            } else if ("input_text".equals(action) || "input".equals(action) || "type".equals(action)) {
                ok = svc.performInput(st, sv, step.optString("input_value", ""));
            } else if ("wait".equals(action)) {
                int ms = 1000;
                try {
                    ms = Integer.parseInt(step.optString("input_value", "1")) * 1000;
                } catch (NumberFormatException ignored) {
                }
                Thread.sleep(Math.min(ms, 120000));
                ok = true;
            } else {
                result.put("status", "error");
                result.put("error", "不支持的操作: " + action);
                return result;
            }
            result.put("status", ok ? "success" : "error");
            if (!ok) result.put("error", "执行失败");
        } catch (Exception e) {
            result.put("status", "error");
            result.put("error", e.getMessage());
        }
        return result;
    }
}
