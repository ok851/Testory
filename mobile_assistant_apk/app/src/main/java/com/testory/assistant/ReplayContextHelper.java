package com.testory.assistant;

import android.content.Context;

import org.json.JSONObject;

import java.util.List;

/**
 * 回放会话上下文：从步骤序列推断「应在哪个应用执行」，并在需要时软恢复前台。
 * <p>
 * 原缺陷：每个 tap 步骤携带的 context_package 被当成必须启动的目标，或录制自动生成
 * open_app 第一步失败即终止。新逻辑：仅对显式 open_app 强制启动；其余步骤按坐标/元素执行，
 * 必要时尽力恢复上下文但不阻断跨应用/桌面流程。
 */
final class ReplayContextHelper {

    private ReplayContextHelper() {
    }

    static AppLauncher.Result prepareSession(
            Context ctx, AssistantAccessibilityService svc, List<JSONObject> steps) {
        if (steps == null || steps.isEmpty()) {
            return AppLauncher.Result.ok("", "", "empty");
        }
        for (JSONObject step : steps) {
            if (step == null || RecordEventFilter.isAssistantStep(step)) continue;
            String action = step.optString("action", "").toLowerCase();
            if ("open_app".equals(action)) {
                String pkg = openAppPackage(step);
                if (AppLauncher.isSkippablePackage(pkg)) {
                    continue;
                }
                AppLauncher.Result r = AppLauncher.launch(ctx, svc, pkg, openAppActivity(step),
                        AppLauncher.DEFAULT_TIMEOUT_MS);
                if (r.success) return r;
                // 原缺陷：open_app 启动失败时直接终止回放，导致后续步骤全部跳过。
                // 新逻辑：启动失败时继续执行后续步骤（用户可能已手动打开应用）。
                continue;
            }
            if ("press_home".equals(action) || "home".equals(action)
                    || "press_back".equals(action) || "back".equals(action)) {
                return AppLauncher.Result.ok("", "", "navigation");
            }
            String ctxPkg = AppLauncher.extractContextPackage(step);
            if (!ctxPkg.isEmpty() && !AppLauncher.isSkippablePackage(ctxPkg)) {
                AppLauncher.Result r = AppLauncher.softRestoreContext(
                        ctx, svc, ctxPkg, AppLauncher.DEFAULT_TIMEOUT_MS);
                if (r.success || AppLauncher.isCoordinateStep(step)) {
                    return AppLauncher.Result.ok(ctxPkg, r.appLabel, "context_soft");
                }
                // 原缺陷：上下文恢复失败时直接终止回放。
                // 新逻辑：继续执行后续步骤，让步骤级重试机制处理。
                return AppLauncher.Result.ok(ctxPkg, r.appLabel, "context_soft_failed");
            }
            break;
        }
        return AppLauncher.Result.ok("", "", "no_context_needed");
    }

    static boolean shouldSkipOpenAppStep(JSONObject step) {
        String pkg = openAppPackage(step);
        if (pkg.isEmpty()) return true;
        if (AppLauncher.isSkippablePackage(pkg)) return true;
        JSONObject spec = step.optJSONObject("mobile_spec");
        if (spec != null && spec.optBoolean("auto_app_switch", false)) {
            return true;
        }
        return false;
    }

    static String openAppPackage(JSONObject step) {
        if (step == null) return "";
        String pkg = step.optString("input_value", "");
        JSONObject spec = step.optJSONObject("mobile_spec");
        if (pkg.isEmpty() && spec != null) {
            pkg = spec.optString("appPackage", spec.optString("app_package", ""));
        }
        return pkg == null ? "" : pkg.trim();
    }

    static String openAppActivity(JSONObject step) {
        JSONObject spec = step == null ? null : step.optJSONObject("mobile_spec");
        if (spec == null) return "";
        return spec.optString("appActivity", spec.optString("app_activity", ""));
    }

    private static boolean hasOnlyCoordinateStepsAfter(List<JSONObject> steps, JSONObject failedOpen) {
        boolean after = false;
        for (JSONObject step : steps) {
            if (step == failedOpen) {
                after = true;
                continue;
            }
            if (!after || step == null || RecordEventFilter.isAssistantStep(step)) continue;
            String action = step.optString("action", "").toLowerCase();
            if ("open_app".equals(action) && !shouldSkipOpenAppStep(step)) return false;
            if ("tap".equals(action) || "click".equals(action) || "swipe".equals(action)) {
                if (!AppLauncher.isCoordinateStep(step)) return false;
                continue;
            }
            if ("press_home".equals(action) || "home".equals(action)
                    || "press_back".equals(action) || "back".equals(action)
                    || "wait".equals(action) || "dialog".equals(action)) {
                continue;
            }
            return false;
        }
        return after;
    }
}
