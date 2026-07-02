package com.testory.assistant;

import android.accessibilityservice.AccessibilityService;
import android.content.Context;
import android.content.Intent;
import android.graphics.PixelFormat;
import android.graphics.Rect;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;
import android.util.Log;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.ViewTreeObserver;
import android.view.WindowManager;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;

/** 录制时在屏幕右上角显示紧凑悬浮条（须从无障碍服务上下文添加）。 */
public final class RecordingOverlay {

    private static final String TAG = "RecordingOverlay";

    public interface Listener {
        void onStop();
        void onPause();
        void onResume();
    }

    private static final Handler MAIN = new Handler(Looper.getMainLooper());
    private static LinearLayout panel;
    private static WindowManager windowManager;
    private static TextView statusLabel;
    private static TextView pauseBtn;
    private static TextView dotView;
    private static boolean paused;
    private static Listener activeListener;
    private static int stepCount;
    private static volatile Rect overlayBounds = new Rect();

    private RecordingOverlay() {
    }

    static void show(Context ctx, Listener listener) {
        activeListener = listener;
        AssistantAccessibilityService svc = AssistantSession.getService();
        if (svc == null) {
            Log.w(TAG, "accessibility not ready, skip overlay");
            return;
        }
        MAIN.post(() -> showOnMain(svc, listener));
    }

    static void hide() {
        MAIN.post(RecordingOverlay::hideOnMain);
    }

    static void setPaused(boolean value) {
        paused = value;
        MAIN.post(RecordingOverlay::updateStatusText);
    }

    static void addStep(String label) {
        stepCount++;
        MAIN.post(RecordingOverlay::updateStatusText);
    }

    static void clearSteps() {
        stepCount = 0;
        MAIN.post(RecordingOverlay::updateStatusText);
    }

    /** 坐标点是否落在悬浮条区域（Cover 模式透传触摸给控制条）。 */
    static boolean hitTestPoint(int x, int y) {
        if (overlayBounds.isEmpty()) return false;
        return overlayBounds.contains(x, y);
    }

    /** 事件 bounds 与悬浮条重叠则忽略（避免录制到暂停/结束操作）。 */
    static boolean hitTest(Rect eventBounds) {
        if (eventBounds == null || overlayBounds.isEmpty()) return false;
        return Rect.intersects(overlayBounds, eventBounds);
    }

    private static void updateStatusText() {
        if (statusLabel == null) return;
        if (paused) {
            statusLabel.setText(stepCount > 0 ? ("已暂停 · " + stepCount + " 步") : "已暂停");
        } else {
            statusLabel.setText(stepCount > 0 ? ("录制 " + stepCount + " 步") : "录制中");
        }
        if (dotView != null) {
            dotView.setTextColor(paused ? 0xFFB45309 : 0xFFC53030);
        }
        if (pauseBtn != null) {
            pauseBtn.setText(paused ? "继续" : "暂停");
        }
    }

    private static void refreshOverlayBounds() {
        if (panel == null) {
            overlayBounds = new Rect();
            return;
        }
        int[] loc = new int[2];
        panel.getLocationOnScreen(loc);
        overlayBounds = new Rect(
                loc[0], loc[1],
                loc[0] + panel.getWidth(),
                loc[1] + panel.getHeight());
    }

    private static void showOnMain(AccessibilityService svc, Listener listener) {
        hideOnMain();
        Context ctx = svc;
        windowManager = (WindowManager) ctx.getSystemService(Context.WINDOW_SERVICE);
        if (windowManager == null) {
            Log.e(TAG, "[OverlayShow] windowManager is null, cannot create overlay");
            return;
        }

        stepCount = 0;
        paused = false;

        LinearLayout root = new LinearLayout(ctx);
        root.setOrientation(LinearLayout.HORIZONTAL);
        root.setGravity(Gravity.CENTER_VERTICAL);
        int padH = dp(ctx, 8);
        int padV = dp(ctx, 6);
        root.setPadding(padH, padV, padH, padV);

        GradientDrawable bg = new GradientDrawable();
        bg.setColor(0xE62D3748);
        bg.setCornerRadius(dp(ctx, 20));
        bg.setStroke(dp(ctx, 1), 0x44FFFFFF);
        root.setBackground(bg);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP) {
            root.setElevation(dp(ctx, 6));
        }

        dotView = new TextView(ctx);
        dotView.setText("\u25CF");
        dotView.setTextColor(0xFFC53030);
        dotView.setTextSize(TypedValue.COMPLEX_UNIT_SP, 10);
        dotView.setPadding(0, 0, dp(ctx, 4), 0);
        root.addView(dotView);

        statusLabel = new TextView(ctx);
        statusLabel.setText("\u5F55\u5236\u4E2D");
        statusLabel.setTextColor(0xFFFFFFFF);
        statusLabel.setTextSize(TypedValue.COMPLEX_UNIT_SP, 11);
        statusLabel.setMaxLines(1);
        LinearLayout.LayoutParams statusLp = new LinearLayout.LayoutParams(
                0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f);
        statusLabel.setLayoutParams(statusLp);
        root.addView(statusLabel);

        pauseBtn = makeButton(ctx, "\u6682\u505C", 0xFF5A6B7D);
        pauseBtn.setOnClickListener(v -> onOverlayTap(() -> {
            Listener l = listener != null ? listener : activeListener;
            if (l == null) return;
            if (paused) {
                paused = false;
                l.onResume();
            } else {
                paused = true;
                l.onPause();
            }
            updateStatusText();
        }));
        root.addView(pauseBtn);

        TextView stopBtn = makeButton(ctx, "\u7ED3\u675F", 0xFF991B1B);
        LinearLayout.LayoutParams stopLp = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.WRAP_CONTENT,
                LinearLayout.LayoutParams.WRAP_CONTENT);
        stopLp.leftMargin = dp(ctx, 4);
        stopBtn.setLayoutParams(stopLp);
        stopBtn.setOnClickListener(v -> onOverlayTap(() -> {
            Listener l = listener != null ? listener : activeListener;
            if (l != null) l.onStop();
        }));
        root.addView(stopBtn);

        // P0#1: 悬浮窗权限全版本适配
        // TYPE_APPLICATION_OVERLAY 需 SYSTEM_ALERT_WINDOW 权限 (SDK 23+)，
        // 无权限时静默降级为 TYPE_ACCESSIBILITY_OVERLAY（无障碍上下文可用）。
        // 两个 window type 都不需要用户额外授权（AccessibilityService 已授权）。
        int overlayType;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M
                && !Settings.canDrawOverlays(ctx)) {
            // 无悬浮窗权限 → 降级为无障碍覆盖层
            overlayType = Build.VERSION.SDK_INT >= Build.VERSION_CODES.O
                    ? WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY
                    : WindowManager.LayoutParams.TYPE_SYSTEM_ALERT;
            Log.w(TAG, "[OverlayShow] SYSTEM_ALERT_WINDOW not granted, fallback to TYPE_ACCESSIBILITY_OVERLAY."
                    + " Please grant overlay permission for best z-order.");
        } else if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            overlayType = WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY;
        } else if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            overlayType = WindowManager.LayoutParams.TYPE_PHONE;
        } else {
            overlayType = WindowManager.LayoutParams.TYPE_SYSTEM_ALERT;
        }

        WindowManager.LayoutParams lp = new WindowManager.LayoutParams(
                WindowManager.LayoutParams.WRAP_CONTENT,
                WindowManager.LayoutParams.WRAP_CONTENT,
                overlayType,
                WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE
                        | WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL
                        | WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN,
                PixelFormat.TRANSLUCENT
        );
        lp.gravity = Gravity.TOP | Gravity.END;
        lp.x = dp(ctx, 8);
        lp.y = dp(ctx, 36);

        try {
            windowManager.addView(root, lp);
            panel = root;
            Log.i(TAG, "[OverlayShow] window added successfully, type=" + overlayType);
            root.getViewTreeObserver().addOnGlobalLayoutListener(new ViewTreeObserver.OnGlobalLayoutListener() {
                @Override
                public void onGlobalLayout() {
                    refreshOverlayBounds();
                    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.JELLY_BEAN) {
                        root.getViewTreeObserver().removeOnGlobalLayoutListener(this);
                    }
                }
            });
        } catch (SecurityException se) {
            Log.e(TAG, "[OverlayShow] SecurityException: SYSTEM_ALERT_WINDOW permission denied."
                    + " Please enable overlay permission in Settings.", se);
            panel = null;
            statusLabel = null;
            overlayBounds = new Rect();
            // 如果降级方案也失败，提示用户手动授权
            MAIN.post(() -> Toast.makeText(ctx,
                    "\u60AC\u6D6E\u7A97\u6743\u9650\u672A\u5F00\u542F\uFF0C\u8BF7\u5728\u8BBE\u7F6E\u4E2D\u6253\u5F00\u201C\u663E\u793A\u5728\u5176\u4ED6\u5E94\u7528\u4E0A\u5C42\u201D",
                    Toast.LENGTH_LONG).show());
        } catch (Exception e) {
            Log.e(TAG, "[OverlayShow] addView failed, type=" + overlayType, e);
            panel = null;
            statusLabel = null;
            overlayBounds = new Rect();
        }
    }

    private static void onOverlayTap(Runnable action) {
        AssistantSession.suppressRecordingFor(500);
        if (action != null) action.run();
    }

    private static TextView makeButton(Context ctx, String label, int color) {
        TextView btn = new TextView(ctx);
        btn.setText(label);
        btn.setTextColor(0xFFFFFFFF);
        btn.setTextSize(TypedValue.COMPLEX_UNIT_SP, 11);
        btn.setPadding(dp(ctx, 8), dp(ctx, 4), dp(ctx, 8), dp(ctx, 4));
        GradientDrawable d = new GradientDrawable();
        d.setColor(color);
        d.setCornerRadius(dp(ctx, 12));
        btn.setBackground(d);
        btn.setClickable(true);
        return btn;
    }

    private static void hideOnMain() {
        if (panel != null && windowManager != null) {
            try {
                windowManager.removeView(panel);
            } catch (Exception ignored) {
            }
        }
        panel = null;
        statusLabel = null;
        pauseBtn = null;
        dotView = null;
        paused = false;
        stepCount = 0;
        activeListener = null;
        overlayBounds = new Rect();
    }

    private static int dp(Context ctx, int value) {
        return Math.round(value * ctx.getResources().getDisplayMetrics().density);
    }
}
