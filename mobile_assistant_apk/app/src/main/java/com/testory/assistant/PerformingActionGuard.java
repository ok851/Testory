package com.testory.assistant;

import android.os.Handler;
import android.os.Looper;
import android.util.Log;

/**
 * 回放护盾：回放执行手势注入期间，禁止录制管线将注入的回声事件录为新步骤。
 * <p>
 * 历史变更：原来还用于 Cover 拦截注入期间防止回声（Cover 已移除拦截功能），
 * 现在仅服务于回放引擎的 performingAction 门控。
 */
final class PerformingActionGuard {

    private static final String TAG = "PerformingActionGuard";
    private static final Handler MAIN = new Handler(Looper.getMainLooper());

    private static volatile long performingUntilMs;
    private static Runnable restoreCoverRunnable;

    private PerformingActionGuard() {
    }

    static void beginPerforming(long durationMs) {
        long guardMs = Math.max(50, Math.min(durationMs + 30, 200));
        performingUntilMs = System.currentTimeMillis() + guardMs;
        AssistantSession.suppressRecordingFor(Math.max(40, guardMs + 30));
        scheduleRestoreFallback(guardMs + 60);
        Log.d(TAG, "beginPerforming ms=" + durationMs + " guardMs=" + guardMs);
    }

    private static void scheduleRestoreFallback(long delayMs) {
        if (restoreCoverRunnable != null) {
            MAIN.removeCallbacks(restoreCoverRunnable);
        }
        restoreCoverRunnable = () -> {
            if (System.currentTimeMillis() >= performingUntilMs) {
                finishPerforming();
            }
        };
        MAIN.postDelayed(restoreCoverRunnable, delayMs);
    }

    static void beginTapPerforming() {
        beginPerforming(420);
    }

    static void beginLongPressPerforming() {
        beginPerforming(720);
    }

    static void beginSwipePerforming(long swipeDurationMs) {
        beginPerforming(Math.max(320, swipeDurationMs + 200));
    }

    static boolean isPerforming() {
        return System.currentTimeMillis() < performingUntilMs;
    }

    static void finishPerforming() {
        performingUntilMs = 0L;
        if (restoreCoverRunnable != null) {
            MAIN.removeCallbacks(restoreCoverRunnable);
            restoreCoverRunnable = null;
        }
        Log.d(TAG, "finishPerforming");
    }

    static void reset() {
        performingUntilMs = 0L;
        if (restoreCoverRunnable != null) {
            MAIN.removeCallbacks(restoreCoverRunnable);
            restoreCoverRunnable = null;
        }
    }
}
