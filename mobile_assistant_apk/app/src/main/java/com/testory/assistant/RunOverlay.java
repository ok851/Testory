package com.testory.assistant;

import android.accessibilityservice.AccessibilityService;
import android.content.Context;
import android.graphics.PixelFormat;
import android.graphics.drawable.GradientDrawable;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.WindowManager;
import android.widget.LinearLayout;
import android.widget.TextView;

/** 运行用例时在底部显示进度与停止按钮（系统级手势，无需切换 App）。 */
public final class RunOverlay {

    private static final Handler MAIN = new Handler(Looper.getMainLooper());
    private static LinearLayout panel;
    private static WindowManager windowManager;
    private static TextView statusLabel;
    private static Runnable stopAction;

    private RunOverlay() {
    }

    static void show(Context ctx, Runnable onStop) {
        stopAction = onStop;
        AssistantAccessibilityService svc = AssistantSession.getService();
        if (svc == null) return;
        MAIN.post(() -> showOnMain(svc));
    }

    static void hide() {
        MAIN.post(RunOverlay::hideOnMain);
    }

    static void setStatus(String text) {
        MAIN.post(() -> {
            if (statusLabel != null) statusLabel.setText(text);
        });
    }

    static void setHiddenForLaunch(boolean hidden) {
        MAIN.post(() -> {
            if (panel == null) return;
            panel.setVisibility(hidden ? android.view.View.GONE : android.view.View.VISIBLE);
        });
    }

    private static void showOnMain(AccessibilityService svc) {
        hideOnMain();
        Context ctx = svc;
        windowManager = (WindowManager) ctx.getSystemService(Context.WINDOW_SERVICE);
        if (windowManager == null) return;

        LinearLayout root = new LinearLayout(ctx);
        root.setOrientation(LinearLayout.HORIZONTAL);
        root.setGravity(Gravity.CENTER_VERTICAL);
        int padH = dp(ctx, 10);
        int padV = dp(ctx, 8);
        root.setPadding(padH, padV, padH, padV);

        GradientDrawable bg = new GradientDrawable();
        bg.setColor(0xE62D3748);
        bg.setCornerRadius(dp(ctx, 20));
        root.setBackground(bg);

        statusLabel = new TextView(ctx);
        statusLabel.setText("运行中…");
        statusLabel.setTextColor(0xFFFFFFFF);
        statusLabel.setTextSize(TypedValue.COMPLEX_UNIT_SP, 11);
        LinearLayout.LayoutParams statusLp = new LinearLayout.LayoutParams(
                0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f);
        statusLabel.setLayoutParams(statusLp);
        root.addView(statusLabel);

        TextView stopBtn = new TextView(ctx);
        stopBtn.setText("停止");
        stopBtn.setTextColor(0xFFFFFFFF);
        stopBtn.setTextSize(TypedValue.COMPLEX_UNIT_SP, 11);
        stopBtn.setPadding(dp(ctx, 10), dp(ctx, 4), dp(ctx, 10), dp(ctx, 4));
        GradientDrawable d = new GradientDrawable();
        d.setColor(0xFF991B1B);
        d.setCornerRadius(dp(ctx, 12));
        stopBtn.setBackground(d);
        stopBtn.setClickable(true);
        stopBtn.setOnClickListener(v -> {
            RunSession.cancel();
            if (stopAction != null) stopAction.run();
        });
        root.addView(stopBtn);

        int overlayType = Build.VERSION.SDK_INT >= Build.VERSION_CODES.O
                ? WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY
                : WindowManager.LayoutParams.TYPE_SYSTEM_ALERT;

        WindowManager.LayoutParams lp = new WindowManager.LayoutParams(
                WindowManager.LayoutParams.WRAP_CONTENT,
                WindowManager.LayoutParams.WRAP_CONTENT,
                overlayType,
                WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE
                        | WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN,
                PixelFormat.TRANSLUCENT
        );
        lp.gravity = Gravity.BOTTOM | Gravity.CENTER_HORIZONTAL;
        lp.y = dp(ctx, 72);

        try {
            windowManager.addView(root, lp);
            panel = root;
        } catch (Exception ignored) {
            panel = null;
        }
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
        stopAction = null;
    }

    private static int dp(Context ctx, int value) {
        return Math.round(value * ctx.getResources().getDisplayMetrics().density);
    }
}
