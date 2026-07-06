package com.testory.assistant;

import android.accessibilityservice.AccessibilityService;
import android.graphics.PixelFormat;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.util.Log;
import android.view.Gravity;
import android.view.View;
import android.view.WindowManager;

/**
 * Cover 视觉层：全屏透明覆盖层（仅保留基本生命周期，用于回放护盾等场景）。
 * <p>
 * 历史变更：移除了 SoloPi/Maestro 风格的「拦截→隐藏→注入→恢复」触摸拦截流水线。
 * 录制触摸事件现在完全通过 getevent 旁路监听 + AccessibilityService 事件捕获，
 * 不再依赖 Cover 层拦截触摸。
 * <p>
 * 设计参考 SoloPi：getevent 在 Linux input 层面读取触摸数据，不侵入事件分发链，
 * 悬浮窗（RecordingOverlay）仅作为控制面板，触摸事件自然穿透到下层应用。
 */
public final class RecordCoverView {

    private static final String TAG = "RecordCoverView";

    private static final Handler MAIN = new Handler(Looper.getMainLooper());

    private static View coverPanel;
    private static WindowManager windowManager;

    private RecordCoverView() {
    }

    interface ShowCallback {
        void onReady(boolean coverShown);
    }

    static void show(AccessibilityService svc, Runnable onFailed) {
        show(svc, onFailed, null);
    }

    static void show(AccessibilityService svc, Runnable onFailed, ShowCallback cb) {
        if (svc == null) {
            if (onFailed != null) MAIN.post(onFailed);
            if (cb != null) MAIN.post(() -> cb.onReady(false));
            return;
        }
        MAIN.post(() -> {
            showOnMain(svc);
            boolean ok = coverPanel != null;
            if (!ok && onFailed != null) {
                onFailed.run();
            }
            if (cb != null) {
                cb.onReady(ok);
            }
        });
    }

    static void hide() {
        MAIN.post(RecordCoverView::hideOnMain);
    }

    static boolean isShowing() {
        return coverPanel != null;
    }

    private static void showOnMain(AccessibilityService svc) {
        hideOnMain();
        windowManager = (WindowManager) svc.getSystemService(AccessibilityService.WINDOW_SERVICE);
        if (windowManager == null) return;

        View panel = new View(svc);
        panel.setBackgroundColor(0x01000000);
        // 不再设置 setClickable(true)：Cover 不再拦截触摸事件。
        // 触摸录制已改为 getevent 旁路监听（参考 SoloPi TouchEventTracker）。
        panel.setFocusable(false);

        int overlayType = Build.VERSION.SDK_INT >= Build.VERSION_CODES.O
                ? WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY
                : WindowManager.LayoutParams.TYPE_SYSTEM_ALERT;

        WindowManager.LayoutParams lp = new WindowManager.LayoutParams(
                WindowManager.LayoutParams.MATCH_PARENT,
                WindowManager.LayoutParams.MATCH_PARENT,
                overlayType,
                WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE
                        | WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE
                        | WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN
                        | WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS,
                PixelFormat.TRANSLUCENT
        );
        lp.gravity = Gravity.TOP | Gravity.START;

        try {
            windowManager.addView(panel, lp);
            coverPanel = panel;
            Log.i(TAG, "Cover overlay shown (pass-through mode, no touch interception)");
        } catch (Exception e) {
            Log.w(TAG, "Cover addView failed", e);
            coverPanel = null;
        }
    }

    private static void hideOnMain() {
        if (coverPanel != null && windowManager != null) {
            try {
                windowManager.removeView(coverPanel);
            } catch (Exception ignored) {
            }
        }
        coverPanel = null;
        windowManager = null;
    }

    private static void runOnMain(Runnable r) {
        if (Looper.myLooper() == Looper.getMainLooper()) {
            r.run();
        } else {
            MAIN.post(r);
        }
    }
}
