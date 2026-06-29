package com.testory.assistant;

import android.content.Context;

/**
 * @deprecated 请使用 {@link AppLauncher}。保留此类仅为兼容旧调用点。
 */
final class AppLaunchPreparer {

    private AppLaunchPreparer() {
    }

    static boolean prepare(Context ctx, AssistantAccessibilityService svc, String pkg, String activity) {
        return AppLauncher.launch(ctx, svc, pkg, activity, AppLauncher.DEFAULT_TIMEOUT_MS).success;
    }
}
