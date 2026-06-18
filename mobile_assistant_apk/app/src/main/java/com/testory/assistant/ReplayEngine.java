package com.testory.assistant;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;

/** 本地回放引擎（与 PC replay.py / plugin_rpc 语义对齐）。 */
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
        List<JSONObject> runnable = new ArrayList<>();
        for (JSONObject step : steps) {
            if (!RecordEventFilter.isAssistantStep(step)) {
                runnable.add(step);
            }
        }
        if (runnable.isEmpty()) {
            JSONObject out = new JSONObject();
            out.put("success", false);
            out.put("error", "无有效步骤（请录制桌面/跨应用操作，勿录助手按钮）");
            out.put("results", new JSONArray());
            return out;
        }

        JSONArray results = new JSONArray();
        for (int i = 0; i < runnable.size(); i++) {
            if (RunSession.isCancelled()) {
                return RunSession.cancelledResult();
            }
            JSONObject step = runnable.get(i);
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
            Thread.sleep(500);
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
            if ("open_app".equals(action)) {
                String pkg = step.optString("input_value", "");
                if (pkg.isEmpty() && spec != null) {
                    pkg = spec.optString("appPackage", spec.optString("app_package", ""));
                }
                String activity = spec != null
                        ? spec.optString("appActivity", spec.optString("app_activity", "")) : "";
                ok = svc.launchPackage(pkg, activity);
                if (!ok) {
                    result.put("status", "error");
                    result.put("error", "无法启动应用: " + pkg);
                    return result;
                }
            } else if ("press_home".equals(action) || "home".equals(action)) {
                ok = svc.goHome();
            } else if ("press_back".equals(action) || "back".equals(action)) {
                ok = svc.goBack();
            } else if ("tap".equals(action) || "click".equals(action)) {
                if (!svc.ensureAppContext(spec)) {
                    result.put("status", "error");
                    result.put("error", "无法切换到目标应用");
                    return result;
                }
                int[] coord = resolveCoord(st, sv, spec);
                int x = coord[0];
                int y = coord[1];
                ok = svc.performTap(st, sv, x, y);
                if (!ok) {
                    result.put("status", "error");
                    result.put("error", x > 0 && y > 0
                            ? "坐标点击失败 (" + x + "," + y + ")"
                            : "未找到可点击元素");
                    return result;
                }
            } else if ("long_press".equals(action) || "long-press".equals(action)) {
                if (!svc.ensureAppContext(spec)) {
                    result.put("status", "error");
                    result.put("error", "无法切换到目标应用");
                    return result;
                }
                int[] coord = resolveCoord(st, sv, spec);
                int x = coord[0];
                int y = coord[1];
                ok = svc.performLongPress(st, sv, x, y);
                if (!ok) {
                    result.put("status", "error");
                    result.put("error", "长按失败");
                    return result;
                }
            } else if ("swipe".equals(action)) {
                if (!svc.ensureAppContext(spec)) {
                    result.put("status", "error");
                    result.put("error", "无法切换到目标应用");
                    return result;
                }
                int x1 = spec != null ? spec.optInt("x1", 0) : 0;
                int y1 = spec != null ? spec.optInt("y1", 0) : 0;
                int x2 = spec != null ? spec.optInt("x2", x1) : x1;
                int y2 = spec != null ? spec.optInt("y2", y1) : y1;
                ok = svc.performSwipe(x1, y1, x2, y2);
                if (!ok) {
                    result.put("status", "error");
                    result.put("error", "滑动手势失败");
                    return result;
                }
            } else if ("input_text".equals(action) || "input".equals(action) || "type".equals(action)) {
                if (!svc.ensureAppContext(spec)) {
                    result.put("status", "error");
                    result.put("error", "无法切换到目标应用");
                    return result;
                }
                ok = svc.performInput(st, sv, step.optString("input_value", ""));
                if (!ok) {
                    result.put("status", "error");
                    result.put("error", "输入失败，未找到输入框");
                    return result;
                }
            } else if ("wait".equals(action)) {
                int ms = 1000;
                try {
                    ms = Integer.parseInt(step.optString("input_value", "1")) * 1000;
                } catch (NumberFormatException ignored) {
                }
                Thread.sleep(Math.min(ms, 120000));
                ok = true;
            } else if ("dialog".equals(action)) {
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

    private static int[] resolveCoord(String st, String sv, JSONObject spec) {
        int x = 0;
        int y = 0;
        if ("viewport_coord".equals(st) && sv != null && !sv.isEmpty()) {
            try {
                JSONObject coord = new JSONObject(sv);
                x = coord.optInt("x", 0);
                y = coord.optInt("y", 0);
            } catch (Exception ignored) {
            }
        }
        if ((x <= 0 && y <= 0) && spec != null && spec.has("viewport_coord")) {
            try {
                JSONObject vc = spec.getJSONObject("viewport_coord");
                x = vc.optInt("x", 0);
                y = vc.optInt("y", 0);
            } catch (Exception ignored) {
            }
        }
        return new int[]{x, y};
    }
}
