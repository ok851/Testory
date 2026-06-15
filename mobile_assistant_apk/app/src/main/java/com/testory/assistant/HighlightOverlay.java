package com.testory.assistant;

import android.content.Context;
import android.graphics.PixelFormat;
import android.graphics.Rect;
import android.graphics.drawable.GradientDrawable;
import android.os.Handler;
import android.os.Looper;
import android.view.Gravity;
import android.view.WindowManager;
import android.widget.FrameLayout;

/** 捕获模式下在设备屏上绘制元素高亮框。 */
public final class HighlightOverlay {

    private static final Handler MAIN = new Handler(Looper.getMainLooper());
    private static FrameLayout overlayView;
    private static WindowManager windowManager;

    private HighlightOverlay() {
    }

    static void show(Context ctx, Rect bounds) {
        if (ctx == null || bounds == null || bounds.isEmpty()) return;
        Context app = ctx.getApplicationContext();
        MAIN.post(() -> showOnMain(app, bounds));
    }

    static void hide() {
        MAIN.post(HighlightOverlay::hideOnMain);
    }

    private static void showOnMain(Context app, Rect bounds) {
        hideOnMain();
        windowManager = (WindowManager) app.getSystemService(Context.WINDOW_SERVICE);
        if (windowManager == null) return;

        int pad = dp(app, 2);
        FrameLayout box = new FrameLayout(app);
        GradientDrawable border = new GradientDrawable();
        border.setStroke(dp(app, 3), 0xFF059669);
        border.setColor(0x33059669);
        border.setCornerRadius(dp(app, 4));
        box.setBackground(border);

        WindowManager.LayoutParams lp = new WindowManager.LayoutParams(
                bounds.width() + pad * 2,
                bounds.height() + pad * 2,
                WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY,
                WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE
                        | WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE
                        | WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN,
                PixelFormat.TRANSLUCENT
        );
        lp.gravity = Gravity.TOP | Gravity.START;
        lp.x = bounds.left - pad;
        lp.y = bounds.top - pad;

        try {
            windowManager.addView(box, lp);
            overlayView = box;
        } catch (Exception ignored) {
            overlayView = null;
        }
    }

    private static void hideOnMain() {
        if (overlayView != null && windowManager != null) {
            try {
                windowManager.removeView(overlayView);
            } catch (Exception ignored) {
            }
        }
        overlayView = null;
    }

    private static int dp(Context ctx, int value) {
        float density = ctx.getResources().getDisplayMetrics().density;
        return Math.round(value * density);
    }
}
