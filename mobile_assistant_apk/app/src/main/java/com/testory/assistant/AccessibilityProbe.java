package com.testory.assistant;

import android.accessibilityservice.AccessibilityServiceInfo;
import android.content.ComponentName;
import android.content.Context;
import android.provider.Settings;
import android.view.accessibility.AccessibilityManager;

import java.util.List;

/** 检测 Testory 无障碍服务是否已在系统中启用/已连接。 */
final class AccessibilityProbe {

    private AccessibilityProbe() {
    }

    static boolean isEnabledInSettings(Context ctx) {
        ComponentName cn = serviceComponent(ctx);
        String flat = cn.flattenToString();
        String shortFlat = cn.flattenToShortString();
        try {
            String enabled = Settings.Secure.getString(
                    ctx.getContentResolver(),
                    Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES
            );
            if (enabled == null || enabled.isEmpty()) {
                return false;
            }
            String lower = enabled.toLowerCase();
            if (lower.contains(flat.toLowerCase()) || lower.contains(shortFlat.toLowerCase())) {
                return true;
            }
            String pkg = ctx.getPackageName().toLowerCase();
            return lower.contains(pkg)
                    && lower.contains("assistantaccessibilityservice");
        } catch (Exception ignored) {
            return false;
        }
    }

    static boolean isEnabledViaManager(Context ctx) {
        try {
            AccessibilityManager am =
                    (AccessibilityManager) ctx.getSystemService(Context.ACCESSIBILITY_SERVICE);
            if (am == null) return false;
            List<AccessibilityServiceInfo> list =
                    am.getEnabledAccessibilityServiceList(AccessibilityServiceInfo.FEEDBACK_ALL_MASK);
            if (list == null) return false;
            String pkg = ctx.getPackageName();
            for (AccessibilityServiceInfo info : list) {
                if (info.getResolveInfo() == null || info.getResolveInfo().serviceInfo == null) {
                    continue;
                }
                if (pkg.equals(info.getResolveInfo().serviceInfo.packageName)) {
                    String name = info.getResolveInfo().serviceInfo.name;
                    if (name != null && name.contains("AssistantAccessibilityService")) {
                        return true;
                    }
                }
            }
        } catch (Exception ignored) {
        }
        return false;
    }

    static boolean isAccessibilityOn(Context ctx) {
        return AssistantSession.isAccessibilityReady()
                || isEnabledViaManager(ctx)
                || isEnabledInSettings(ctx);
    }

    static ComponentName serviceComponent(Context ctx) {
        return new ComponentName(ctx, AssistantAccessibilityService.class);
    }
}
