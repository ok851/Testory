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
 * Cover 视觉层：全屏透明覆盖，基于 SoloPi/Maestro 的「拦截→隐藏→注入→恢复」循环。
 * <p>
 * 核心原理：Cover 通过 {@code setClickable(true)} 拦截完整的触摸手势
 * （DOWN→MOVE→UP），录制后立即隐藏 Cover 并将手势通过
 * {@link AccessibilityService#dispatchGesture} 注入回应用层，确保
 * 应用（包括 Launcher 桌面）始终能收到触摸事件。
 * <p>
 * 悬浮窗（RecordingOverlay）使用 TYPE_APPLICATION_OVERLAY，z-order 高于
 * 本 Cover（TYPE_ACCESSIBILITY_OVERLAY），可正常接收点击。
 */
public final class RecordCoverView {

    private static final String TAG = "RecordCoverView";

    private static final Handler MAIN = new Handler(Looper.getMainLooper());
    /** 后台线程池：专门执行 OperationNodeExporter 节点导出，避免阻塞手势注入流水线 */
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
    /** 上一次触摸事件时间戳（用于去重） */
    private static long lastTouchMs = 0L;

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
        // 关键：setClickable(true) 使 View.onTouchEvent() 返回 true，
        // 从而消费 ACTION_DOWN 并建立触摸目标链，Cover 得以接收完整手势
        // （DOWN→MOVE→UP）用于录制。录制完成后通过 dispatchGesture 注入回去。
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

    /**
     * Cover 触摸事件入口。
     * <p>
     * 返回值含义与常规 View 不同：Cover 是 WindowManager 的顶层悬浮窗口，
     * setClickable(true) 使 View.onTouchEvent() 返回 true，确保 ACTION_DOWN
     * 被 Cover 窗口「认领」，后续 MOVE/UP 继续派发给 Cover，从而录制完整手势。
     * 录制完成后通过隐藏 Cover + dispatchGesture 将手势注入回应用。
     * <p>
     * 悬浮窗命中检测：hitTestPoint 识别 RecordingOverlay 区域，
     * 返回 false 使 Cover.onTouchEvent 消费事件 → 防止事件泄漏到 Cover 之下。
     * 但 RecordingOverlay 使用 TYPE_APPLICATION_OVERLAY（z-order 更高），
     * 其按钮点击不会到达此处，hitTest 仅作防御性兜底。
     */
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
        // 防御性检查：即使 RecordingOverlay z-order 更高，仍拒绝录制悬浮窗区域手势
        if (RecordingOverlay.hitTestPoint(x, y)) {
            return false;
        }
        int action = event.getActionMasked();
        if (action == MotionEvent.ACTION_DOWN) {
            startX = x;
            startY = y;
            startMs = System.currentTimeMillis();
            TouchGestureClassifier.get().onStart(x, y);
        }
        if (action == MotionEvent.ACTION_MOVE) {
            TouchGestureClassifier.get().onMove(x, y);
        }
        if (action == MotionEvent.ACTION_UP || action == MotionEvent.ACTION_CANCEL) {
            handleTouchUp(x, y);
        }
        return false;  // onTouchListener 不消费，由 View.onTouchEvent 消费（setClickable=true）
    }

    /**
     * 手势终止处理（Maestro / SoloPi 注入流水线）。
     * <p>
     * P0#2 修复：原实现中 TouchGestureClassifier.onEnd() 对快速点击（&lt;20ms）
     * 返回 null 后直接 return，Cover 不进入 dispatchMode → 触摸被 Cover 消费但
     * 既不注入也不录制 → 用户每次点击都需要多次尝试。
     * <p>
     * 修复方案：gesture 为 null 时仍执行注入流程（以短 tap 形式），确保
     * 应用始终收到触摸，Cover 不会永久阻塞 UI 交互。
     */
    private static void handleTouchUp(int x, int y) {
        if (PerformingActionGuard.isPerforming()) {
            return;
        }
        AssistantAccessibilityService svc = AssistantSession.getService();
        if (svc == null) return;
        long duration = System.currentTimeMillis() - startMs;
        JSONObject gesture = TouchGestureClassifier.get().onEnd(x, y);

        // 防御：即使 classifier 拒绝手势（快速点击 <20ms 返回 null），
        // 仍需注入到应用并让 Cover 进入 dispatchMode，防止触摸永久被吞。
        final int x1 = startX;
        final int y1 = startY;
        final int x2 = (gesture != null) ? gesture.optInt("x2", startX) : startX;
        final int y2 = (gesture != null) ? gesture.optInt("y2", startY) : startY;
        final long fd = duration;
        final String type;
        final JSONObject fGesture;

        if (gesture == null) {
            // 分类器拒绝 → 兜底为坐标点击注入
            type = "click";
            fGesture = null;
            Log.d(TAG, "[CoverTouch] gesture null (rejected by classifier), injecting fallback tap at (" + x1 + "," + y1 + ") dur=" + duration + "ms");
        } else {
            type = gesture.optString("type", "click");
            fGesture = gesture;
        }

        // Step 1: 立即隐藏 Cover（同帧同步），触摸可直达应用
        enterDispatchMode();
        PerformingActionGuard.beginPerforming(Math.max(320, fd + 200));

        // Step 2: 立即注入手势（mainHandler.post，在 coverPanel.setVisibility(GONE) 后生效）
        svc.performCoverRecordedAction(type, null, x1, y1, x2, y2, fd, () -> {
            RecordCoverView.finishDispatchCycle();
        });

        // Step 3: 异步导出节点信息 + 落库（仅有效手势）
        if (fGesture != null) {
            final int gx = fGesture.optInt("x", x2);
            final int gy = fGesture.optInt("y", y2);
            final String fType = type;
            COVER_WORKER.execute(() -> {
                try {
                    JSONObject payload = OperationNodeExporter.exportTouchAction(
                            svc, fType, gx, gy, x1, y1, x2, y2, fd);
                    payload.put("source", "cover");
                    RecordEventFilter.markTouchGesture(payload);
                    PluginHttpServer.enqueueStep(payload);
                    Log.d(TAG, "cover step type=" + fType + " @" + gx + "," + gy);
                    MAIN.post(() -> RecordingOverlay.addStep(payload.optString("description", fType)));
                } catch (Exception e) {
                    Log.w(TAG, "[CoverTouch] exportTouchAction failed, type=" + fType, e);
                    try {
                        JSONObject fallback = new JSONObject();
                        fallback.put("ts", System.currentTimeMillis());
                        fallback.put("type", fType);
                        fallback.put("x", x1);
                        fallback.put("y", y1);
                        if ("swipe".equals(fType)) {
                            fallback.put("x1", x1);
                            fallback.put("y1", y1);
                            fallback.put("x2", x2);
                            fallback.put("y2", y2);
                        }
                        fallback.put("source", "cover");
                        fallback.put("description", fType + " (" + x1 + "," + y1 + ")");
                        RecordEventFilter.markTouchGesture(fallback);
                        PluginHttpServer.enqueueStep(fallback);
                    } catch (Exception ignored) {}
                }
            });
        }
    }

    /** 注入完成 / 失败后恢复拦截（SoloPi 约 200ms 后再进入 touchBlockMode） */
    static void finishDispatchCycle() {
        Runnable restore = () -> {
            PerformingActionGuard.finishPerforming();
            enterTouchBlockMode();
        };
        // 触摸已直达应用，缩短恢复时间
        if (Looper.myLooper() == Looper.getMainLooper()) {
            MAIN.postDelayed(restore, 80);
        } else {
            MAIN.post(() -> MAIN.postDelayed(restore, 80));
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
