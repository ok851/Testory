package com.testory.assistant;

import android.content.Context;
import android.content.Intent;
import android.os.Handler;
import android.os.Looper;

/**
 * 录制/回放前将助手 Activity 退到后台并回到桌面，避免助手界面遮挡与误录。
 * 原缺陷：点击录制后助手仍在前台，首几次操作落在助手 UI 上或被抑制窗口丢弃。
 */
final class SessionForegroundGuard {

    static final int DEFAULT_TIMEOUT_MS = 5000;
    private static final int POLL_MS = 50;
    private static final Handler MAIN = new Handler(Looper.getMainLooper());

    interface ReadyCallback {
        void onReady(boolean desktopReady, String message);
    }

    private SessionForegroundGuard() {
    }

    static void retreatToDesktop(Context ctx, ReadyCallback cb) {
        new Thread(() -> {
            Result r = retreatBlocking(ctx, DEFAULT_TIMEOUT_MS);
            if (cb != null) {
                MAIN.post(() -> cb.onReady(r.desktopReady, r.message));
            }
        }, "testory-retreat-desktop").start();
    }

    static boolean retreatToDesktopBlocking(Context ctx, int timeoutMs) {
        return retreatBlocking(ctx, timeoutMs).desktopReady;
    }

    private static Result retreatBlocking(Context ctx, int timeoutMs) {
        AssistantAccessibilityService svc = AssistantSession.getService();
        // 录制/回放启动前丢弃残留事件，避免“点好几次才开始记”的脏缓冲。
        PluginHttpServer.clearEventQueues();
        RecordEventFilter.resetDedupe();
        TouchCoordBuffer.reset();

        MainActivity.moveTaskToBackIfVisible();
        sleep(120);
        boolean homeSent = false;
        if (svc != null) {
            homeSent = svc.goHome();
        }
        if (!homeSent && ctx != null) {
            try {
                Intent home = new Intent(Intent.ACTION_MAIN);
                home.addCategory(Intent.CATEGORY_HOME);
                home.setFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                ctx.startActivity(home);
                homeSent = true;
            } catch (Exception ignored) {
            }
        }
        // 兜底2：通过 shell input keyevent HOME 方式
        if (!homeSent) {
            try {
                Runtime.getRuntime().exec("input keyevent 3");
                homeSent = true;
            } catch (Exception ignored) {
            }
        }

        long deadline = System.currentTimeMillis() + Math.max(POLL_MS, timeoutMs);
        while (System.currentTimeMillis() < deadline) {
            if (isDesktopForeground(svc)) {
                PluginHttpServer.clearEventQueues();
                RecordEventFilter.resetDedupe();
                TouchCoordBuffer.reset();
                return Result.ok();
            }
            sleep(POLL_MS);
            if (svc == null) {
                svc = AssistantSession.getService();
            }
        }

        if (isDesktopForeground(svc)) {
            PluginHttpServer.clearEventQueues();
            return Result.ok();
        }
        return Result.fallback();
    }

    private static boolean isDesktopForeground(AssistantAccessibilityService svc) {
        if (svc == null) return false;
        String pkg = svc.getForegroundPackage();
        if (pkg == null || pkg.isEmpty()) return false;
        // 必须确认已离开助手且处于桌面/启动器，而非仅切到其他 App。
        if ("com.testory.assistant".equals(pkg)) return false;
        return AppLauncher.isSkippablePackage(pkg);
    }

    private static void sleep(long ms) {
        try {
            Thread.sleep(ms);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }

    private static final class Result {
        final boolean desktopReady;
        final String message;

        private Result(boolean desktopReady, String message) {
            this.desktopReady = desktopReady;
            this.message = message;
        }

        static Result ok() {
            return new Result(true, "");
        }

        static Result fallback() {
            return new Result(
                    false,
                    "未能自动返回桌面，请手动按 Home 键后再操作手机");
        }
    }
}
