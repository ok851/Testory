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
        // 回放前已经退到桌面，不需要额外准备
        // 第一个步骤如果是点击桌面图标（context_package 是 launcher 或空的），不需要启动应用
        // 后续步骤会通过 maybeSoftRestore 动态恢复上下文
        // 关键修复：不要在 prepareSession 中启动应用，因为：
        // 1. 用户可能录制的是桌面操作（点击图标启动应用）
        // 2. 退到桌面后，前台应用是 launcher，context_package 检查会失败
        // 3. 让第一个 open_app 步骤或 tap 步骤自己处理应用启动
        return AppLauncher.Result.ok("", "", "desktop_ready");
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
