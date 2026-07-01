package com.testory.assistant;

import android.accessibilityservice.AccessibilityService;
import android.graphics.PixelFormat;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.util.Log;
import android.view.Gravity;
import android.view.MotionEvent;
import android.view.View;
import android.view.WindowManager;

import org.json.JSONObject;

import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * SoloPi Cover 脉冲模式（对齐 CaseRecordManager touchBlockMode）：
 * <ol>
 *   <li>touchBlockMode：Cover 可见并拦截触摸，TouchGestureClassifier 分类</li>
 *   <li>收到手势 → 立即隐藏 Cover（setServiceToNormalMode）</li>
 *   <li>导出节点 + 注入手势到下层应用（dispatchGesture / performAction）</li>
 *   <li>完成后再次显示 Cover（setServiceToTouchBlockMode），等待下一次操作</li>
 * </ol>
 * Cover 隐藏期间用户触摸直达应用，与正常使用一致；仅「等待下一次操作」时 Cover 拦截。
 */
public final class RecordCoverView {

    private static final String TAG = "RecordCoverView";

    private static final Handler MAIN = new Handler(Looper.getMainLooper());
    private static final ExecutorService COVER_WORKER =
            Executors.newSingleThreadExecutor(r -> {
                Thread t = new Thread(r, "testory-cover");
                t.setDaemon(true);
                return t;
            });

    private static View coverPanel;
    private static WindowManager windowManager;
    private static int startX;
    private static int startY;
    private static long startMs;
    /** SoloPi touchBlockMode：true = Cover 可见并拦截 */
    private static volatile boolean touchBlockMode = true;

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

    static boolean isTouchBlockMode() {
        return touchBlockMode;
    }

    /** SoloPi setServiceToTouchBlockModeNoDelay */
    static void enterTouchBlockMode() {
        if (AssistantSession.isRecordingPaused()) {
            return;
        }
        if (!AssistantSession.MODE_RECORD.equals(AssistantSession.getArmedMode())) {
            return;
        }
        touchBlockMode = true;
        runOnMain(() -> {
            if (coverPanel != null) {
                coverPanel.setVisibility(View.VISIBLE);
                Log.d(TAG, "touchBlockMode ON");
            }
        });
    }

    /** SoloPi setServiceToNormalModeNoDelay — 注入前隐藏 Cover，触摸直达应用 */
    static void enterDispatchMode() {
        touchBlockMode = false;
        runOnMain(() -> {
            if (coverPanel != null) {
                coverPanel.setVisibility(View.GONE);
                Log.d(TAG, "touchBlockMode OFF (dispatch)");
            }
        });
    }

    static void setTouchEnabled(boolean enabled) {
        if (enabled) {
            enterTouchBlockMode();
        } else {
            hideForPause();
        }
    }

    static void setTouchCaptureEnabled(boolean enabled) {
        setTouchEnabled(enabled);
    }

    /** 暂停：Cover 隐藏，用户可自由操作 */
    static void hideForPause() {
        touchBlockMode = false;
        MAIN.post(() -> {
            if (coverPanel != null) {
                coverPanel.setVisibility(View.GONE);
            }
        });
    }

    static void setPassThroughMode(boolean passThrough) {
        if (passThrough) {
            enterDispatchMode();
        } else {
            enterTouchBlockMode();
        }
    }

    static boolean isPassThroughMode() {
        return !touchBlockMode;
    }

    private static void showOnMain(AccessibilityService svc) {
        hideOnMain();
        windowManager = (WindowManager) svc.getSystemService(AccessibilityService.WINDOW_SERVICE);
        if (windowManager == null) return;

        View panel = new View(svc);
        panel.setBackgroundColor(0x01000000);
        panel.setClickable(true);
        panel.setEnabled(true);
        panel.setFocusable(false);
        panel.setOnTouchListener(RecordCoverView::onCoverTouch);

        int overlayType = Build.VERSION.SDK_INT >= Build.VERSION_CODES.O
                ? WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY
                : WindowManager.LayoutParams.TYPE_SYSTEM_ALERT;

        WindowManager.LayoutParams lp = new WindowManager.LayoutParams(
                WindowManager.LayoutParams.MATCH_PARENT,
                WindowManager.LayoutParams.MATCH_PARENT,
                overlayType,
                WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE
                        | WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN
                        | WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS,
                PixelFormat.TRANSLUCENT
        );
        lp.gravity = Gravity.TOP | Gravity.START;

        try {
            windowManager.addView(panel, lp);
            coverPanel = panel;
            touchBlockMode = true;
            AssistantSession.setCoverModeActive(true);
            Log.i(TAG, "Cover overlay shown (pulse block mode)");
        } catch (Exception e) {
            Log.w(TAG, "Cover addView failed", e);
            coverPanel = null;
            touchBlockMode = false;
            AssistantSession.setCoverModeActive(false);
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
        touchBlockMode = true;
        AssistantSession.setCoverModeActive(false);
    }

    private static boolean onCoverTouch(View v, MotionEvent event) {
        if (!AssistantSession.MODE_RECORD.equals(AssistantSession.getArmedMode())) {
            return false;
        }
        if (AssistantSession.isRecordingPaused()) {
            return false;
        }
        if (!touchBlockMode || PerformingActionGuard.isPerforming()) {
            return false;
        }
        if (AssistantSession.isRecordingSuppressed()) {
            return true;
        }
        int x = (int) event.getRawX();
        int y = (int) event.getRawY();
        if (RecordingOverlay.hitTestPoint(x, y)) {
            return false;
        }
        int action = event.getActionMasked();
        if (action == MotionEvent.ACTION_DOWN) {
            startX = x;
            startY = y;
            startMs = System.currentTimeMillis();
            TouchGestureClassifier.get().onStart(x, y);
            return true;
        }
        if (action == MotionEvent.ACTION_MOVE) {
            TouchGestureClassifier.get().onMove(x, y);
            return true;
        }
        if (action == MotionEvent.ACTION_UP || action == MotionEvent.ACTION_CANCEL) {
            handleTouchUp(x, y);
            return true;
        }
        return true;
    }

    private static void handleTouchUp(int x, int y) {
        if (!touchBlockMode || PerformingActionGuard.isPerforming()) {
            return;
        }
        AssistantAccessibilityService svc = AssistantSession.getService();
        if (svc == null) return;
        long duration = System.currentTimeMillis() - startMs;
        JSONObject gesture = TouchGestureClassifier.get().onEnd(x, y);
        if (gesture == null) {
            return;
        }

        final int fx1 = startX;
        final int fy1 = startY;
        final int fx = x;
        final int fy = y;
        final long fd = duration;
        final JSONObject fGesture = gesture;

        // SoloPi：先隐藏 Cover，再导出节点并注入（此时触摸可直达应用）
        enterDispatchMode();
        PerformingActionGuard.beginPerforming(Math.max(320, fd + 200));

        COVER_WORKER.execute(() -> {
            try {
                String type = fGesture.optString("type", "click");
                int gx = fGesture.optInt("x", fx);
                int gy = fGesture.optInt("y", fy);
                int x1 = fGesture.optInt("x1", fx1);
                int y1 = fGesture.optInt("y1", fy1);
                int x2 = fGesture.optInt("x2", fx);
                int y2 = fGesture.optInt("y2", fy);
                JSONObject payload = OperationNodeExporter.exportTouchAction(
                        svc, type, gx, gy, x1, y1, x2, y2, fd);
                payload.put("source", "cover");
                RecordEventFilter.markTouchGesture(payload);
                PluginHttpServer.enqueueStep(payload);
                Log.d(TAG, "enqueue cover step type=" + type + " @" + gx + "," + gy);
                MAIN.post(() -> RecordingOverlay.addStep(payload.optString("description", type)));

                svc.performCoverRecordedAction(type, payload, x1, y1, x2, y2, fd,
                        RecordCoverView::finishDispatchCycle);
            } catch (Exception e) {
                Log.w(TAG, "handleTouchUp failed", e);
                MAIN.post(RecordCoverView::finishDispatchCycle);
            }
        });
    }

    /** 注入完成 / 失败后恢复拦截（SoloPi 约 200ms 后再进入 touchBlockMode） */
    static void finishDispatchCycle() {
        Runnable restore = () -> {
            PerformingActionGuard.finishPerforming();
            enterTouchBlockMode();
        };
        if (Looper.myLooper() == Looper.getMainLooper()) {
            MAIN.postDelayed(restore, 200);
        } else {
            MAIN.post(() -> MAIN.postDelayed(restore, 200));
        }
    }

    private static void runOnMain(Runnable r) {
        if (Looper.myLooper() == Looper.getMainLooper()) {
            r.run();
        } else {
            MAIN.post(r);
        }
    }
}
