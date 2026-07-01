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

        // 原缺陷：回放前强制拉起步骤中的 app_package，桌面/跨应用场景第一步即失败。
        // 新逻辑：按步骤语义软恢复上下文，仅显式 open_app 才强制启动。
        AppLauncher.Result prep = ReplayContextHelper.prepareSession(
                svc.getApplicationContext(), svc, runnable);
        if (!prep.success) {
            JSONObject out = new JSONObject();
            out.put("success", false);
            out.put("error", prep.message);
            out.put("error_code", prep.errorCode);
            out.put("results", new JSONArray());
            return out;
        }

        JSONArray results = new JSONArray();
        for (int i = 0; i < runnable.size(); i++) {
            if (RunSession.isCancelled()) {
                return RunSession.cancelledResult();
            }
            JSONObject step = runnable.get(i);
            ContentChangeWatcher.sleepUntilStable();
            DialogDismissHelper.tryDismiss(svc);
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
            if ("open_app".equals(action)) {
                if (ReplayContextHelper.shouldSkipOpenAppStep(step)) {
                    ok = true;
                    result.put("message", "已跳过启动器/系统自动切换步骤");
                } else {
                    String pkg = ReplayContextHelper.openAppPackage(step);
                    String activity = ReplayContextHelper.openAppActivity(step);
                    AppLauncher.Result launch = AppLauncher.launch(
                            svc.getApplicationContext(),
                            svc,
                            pkg,
                            activity,
                            AppLauncher.DEFAULT_TIMEOUT_MS);
                    ok = launch.success;
                    if (!ok) {
                        result.put("status", "error");
                        result.put("error", launch.message);
                        result.put("error_code", launch.errorCode);
                        return result;
                    }
                    result.put("message", "已启动 " + launch.appLabel);
                }
            } else if ("press_home".equals(action) || "home".equals(action)) {
                ok = svc.goHome();
            } else if ("press_back".equals(action) || "back".equals(action)) {
                ok = svc.goBack();
            } else if ("tap".equals(action) || "click".equals(action)) {
                maybeSoftRestore(svc, step);
                int[] coord = OperationNodeLocator.resolveTapFromStep(svc, step);
                int x = coord[0];
                int y = coord[1];
                if (x == 0 && y == 0) {
                    coord = resolveCoord(st, sv, spec);
                    x = coord[0];
                    y = coord[1];
                }
                ok = svc.performTapSelectorFirst(st, sv, x, y,
                        spec != null ? spec.optJSONObject("operation_node") : null);
                if (!ok && spec != null) {
                    ok = tryLaunchTargetPackage(svc, spec);
                }
                if (!ok && "accessibility_id".equals(st) && sv != null && !sv.isEmpty()) {
                    String pkg = AppLauncher.resolvePackageByLabel(
                            svc.getApplicationContext(), sv);
                    if (!pkg.isEmpty()) {
                        ok = AppLauncher.launch(
                                svc.getApplicationContext(), svc, pkg, "",
                                AppLauncher.DEFAULT_TIMEOUT_MS).success;
                    }
                }
                if (!ok) {
                    result.put("status", "error");
                    result.put("error", x > 0 && y > 0
                            ? "坐标点击失败 (" + x + "," + y + ")"
                            : "未找到可点击元素");
                    return result;
                }
            } else if ("long_press".equals(action) || "long-press".equals(action)) {
                maybeSoftRestore(svc, step);
                int[] coord = OperationNodeLocator.resolveTapFromStep(svc, step);
                int x = coord[0];
                int y = coord[1];
                if (x == 0 && y == 0) {
                    coord = resolveCoord(st, sv, spec);
                    x = coord[0];
                    y = coord[1];
                }
                ok = svc.performLongPressSelectorFirst(st, sv, x, y,
                        spec != null ? spec.optJSONObject("operation_node") : null);
                if (!ok) {
                    result.put("status", "error");
                    result.put("error", "长按失败");
                    return result;
                }
            } else if ("swipe".equals(action)) {
                int[] p1 = resolveSwipeCoords(spec, "x1", "y1", "rx1", "ry1");
                int[] p2 = resolveSwipeCoords(spec, "x2", "y2", "rx2", "ry2");
                long dur = spec != null ? spec.optLong("action_duration_ms", 320) : 320;
                ok = svc.performSwipe(p1[0], p1[1], p2[0], p2[1], dur);
                if (!ok) {
                    result.put("status", "error");
                    result.put("error", "滑动手势失败");
                    return result;
                }
            } else if ("input_text".equals(action) || "input".equals(action) || "type".equals(action)) {
                maybeSoftRestore(svc, step);
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

    private static void maybeSoftRestore(AssistantAccessibilityService svc, JSONObject step) {
        if (AppLauncher.isCoordinateStep(step)) return;
        String pkg = AppLauncher.extractContextPackage(step);
        if (pkg.isEmpty() || AppLauncher.isSkippablePackage(pkg)) return;
        AppLauncher.softRestoreContext(
                svc.getApplicationContext(), svc, pkg, AppLauncher.DEFAULT_TIMEOUT_MS);
    }

    private static boolean tryLaunchTargetPackage(AssistantAccessibilityService svc, JSONObject spec) {
        if (svc == null || spec == null) return false;
        String pkg = spec.optString("target_package", "");
        if (pkg.isEmpty()) {
            pkg = spec.optString("app_package", spec.optString("appPackage", ""));
        }
        if (pkg.isEmpty() || AppLauncher.isSkippablePackage(pkg)) return false;
        AppLauncher.Result launch = AppLauncher.launch(
                svc.getApplicationContext(),
                svc,
                pkg,
                "",
                AppLauncher.DEFAULT_TIMEOUT_MS);
        return launch.success;
    }

    private static int[] resolveCoord(String st, String sv, JSONObject spec) {
        int x = 0;
        int y = 0;
        double rx = -1;
        double ry = -1;
        if ("viewport_coord".equals(st) && sv != null && !sv.isEmpty()) {
            try {
                JSONObject coord = new JSONObject(sv);
                x = coord.optInt("x", 0);
                y = coord.optInt("y", 0);
                rx = coord.optDouble("rx", -1);
                ry = coord.optDouble("ry", -1);
            } catch (Exception ignored) {
            }
        }
        if ((x <= 0 && y <= 0) && spec != null && spec.has("viewport_coord")) {
            try {
                JSONObject vc = spec.getJSONObject("viewport_coord");
                x = vc.optInt("x", 0);
                y = vc.optInt("y", 0);
                if (rx < 0) rx = vc.optDouble("rx", -1);
                if (ry < 0) ry = vc.optDouble("ry", -1);
            } catch (Exception ignored) {
            }
        }
        if ((rx >= 0 && ry >= 0) && spec != null) {
            int sw = spec.optInt("screen_width", 0);
            int sh = spec.optInt("screen_height", 0);
            if (sw > 0 && sh > 0) {
                x = (int) Math.round(rx * sw);
                y = (int) Math.round(ry * sh);
            }
        }
        // SoloPi: 节点内相对坐标 + 当前 bounds 解析点击位置
        if ((x <= 0 && y <= 0) && spec != null && spec.has("node_rx") && spec.has("node_ry")) {
            org.json.JSONArray bounds = spec.optJSONArray("bounds");
            if (bounds != null && bounds.length() >= 4) {
                int left = bounds.optInt(0);
                int top = bounds.optInt(1);
                int right = bounds.optInt(2);
                int bottom = bounds.optInt(3);
                int bw = right - left;
                int bh = bottom - top;
                if (bw > 0 && bh > 0) {
                    x = left + (int) Math.round(spec.optDouble("node_rx") * bw);
                    y = top + (int) Math.round(spec.optDouble("node_ry") * bh);
                }
            }
        }
        return new int[]{x, y};
    }

    private static int[] resolveSwipeCoords(JSONObject spec, String keyX, String keyY, String keyRx, String keyRy) {
        int val = spec != null ? spec.optInt(keyX, 0) : 0;
        int vy = spec != null ? spec.optInt(keyY, 0) : 0;
        if (spec != null && spec.has(keyRx) && spec.has(keyRy)) {
            int sw = spec.optInt("screen_width", 0);
            int sh = spec.optInt("screen_height", 0);
            if (sw > 0 && sh > 0) {
                val = (int) Math.round(spec.optDouble(keyRx) * sw);
                vy = (int) Math.round(spec.optDouble(keyRy) * sh);
            }
        }
        return new int[]{val, vy};
    }
}
