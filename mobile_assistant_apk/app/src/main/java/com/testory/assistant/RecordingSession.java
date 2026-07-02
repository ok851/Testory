package com.testory.assistant;

import android.content.Context;
import android.os.Handler;
import android.os.Looper;
import android.util.Log;
import android.widget.Toast;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.List;

/**
 * 录制会话：在无障碍进程内后台落库，不依赖前台服务类型权限。
 */
final class RecordingSession {

    private static final String TAG = "RecordingSession";
    private static final Handler HANDLER = new Handler(Looper.getMainLooper());

    private static long activeCaseId = -1L;
    private static boolean draining;
    private static RecordingOverlay.Listener overlayListener;
    private static volatile TouchEventCapture touchCapture;

    private static final Runnable drainRunnable = new Runnable() {
        @Override
        public void run() {
            if (!draining || activeCaseId < 0) return;
            drainSteps();
            HANDLER.postDelayed(this, 120);
        }
    };

    private RecordingSession() {
    }

    static void start(Context ctx, long caseId, RecordingOverlay.Listener listener) {
        start(ctx, caseId, listener, false);
    }

    static void start(Context ctx, long caseId, RecordingOverlay.Listener listener, boolean agentMode) {
        final long sessionId = caseId;
        activeCaseId = caseId;
        overlayListener = listener;
        AssistantSession.setLocalCaseId(caseId);
        draining = false;
        HANDLER.removeCallbacks(drainRunnable);
        AssistantSession.setArmedMode(AssistantSession.MODE_IDLE);
        AssistantSession.setRecordingPaused(false);
        PerformingActionGuard.reset();
        // 清理上一轮状态，避免残留步骤混入新录制
        PluginHttpServer.clearEventQueues();
        RecordEventFilter.resetDedupe();
        TouchCoordBuffer.reset();
        RecordingOverlay.hide();

        SessionForegroundGuard.retreatToDesktop(ctx, (desktopReady, message) -> {
            if (activeCaseId != sessionId) return;
            if (!message.isEmpty() && ctx != null) {
                Toast.makeText(ctx.getApplicationContext(), message, Toast.LENGTH_LONG).show();
            }
            RecordingOverlay.clearSteps();
            // P0 修复：RecordingOverlay.show() 移到 finishArming() 中 Cover 之后调用，
            // 确保悬浮窗始终在 Cover 上层（修复无悬浮窗权限时 Cover 遮挡按钮的问题）。
            finishArming(ctx, listener, desktopReady);
        });
    }

    private static void finishArming(
            Context ctx,
            RecordingOverlay.Listener listener,
            boolean desktopReady) {
        if (activeCaseId < 0) return;
        draining = true;
        // 降低抑制时间：原来 80-120ms 容易错过首屏操作，
        // 40-60ms 足够跳过 Home 键释放的噪声事件。
        AssistantSession.suppressRecordingFor(desktopReady ? 40 : 60);
        AssistantSession.setArmedMode(AssistantSession.MODE_RECORD);
        AssistantAccessibilityService live = AssistantSession.getService();
        if (live != null) {
            String fg = live.getForegroundPackage();
            if (fg != null && !fg.isEmpty() && !"com.testory.assistant".equals(fg)) {
                AssistantSession.setRecordingContextPackage(fg);
            }
        }
        if (ctx != null) {
            Toast.makeText(
                    ctx.getApplicationContext(),
                    "录制已开始",
                    Toast.LENGTH_SHORT).show();
        }
        Log.i(TAG, "recording armed");
        // 启动 getevent 触摸捕获（参考 SoloPi TouchEventTracker）
        // 确保桌面/Launcher 等不触发 TYPE_VIEW_SCROLLED 的场景也能捕获滑动
        startTouchCapture();
        // 先激活 Cover 透明覆盖层，再激活 RecordingOverlay：
        // Cover 使用 TYPE_ACCESSIBILITY_OVERLAY，
        // RecordingOverlay 使用 TYPE_APPLICATION_OVERLAY（z-order 更高），
        // 确保悬浮窗「暂停/结束」按钮始终可点击，不会被 Cover 拦截。
        // 原缺陷：顺序颠倒，Cover 后添加导致覆盖悬浮窗，按钮触摸被 Cover 消费。
        // P0 修复：Cover 先添加，RecordingOverlay 后添加。
        // Cover 使用 TYPE_ACCESSIBILITY_OVERLAY (2032)，
        // RecordingOverlay 使用 TYPE_APPLICATION_OVERLAY (2038) 或无权限降级，
        // 后添加 → 悬浮窗始终在 Cover 上层，按钮可正常点击。
        AssistantAccessibilityService svc = AssistantSession.getService();
        if (svc != null) {
            RecordCoverView.show(svc, () -> {
                Log.w(TAG, "RecordCoverView show failed");
            });
        }
        // RecordingOverlay 必须在 Cover 之后添加，确保 z-order 在 Cover 之上
        if (ctx != null) {
            RecordingOverlay.show(ctx, listener);
        }
        HANDLER.post(drainRunnable);
    }

    static void stop(Context ctx) {
        draining = false;
        HANDLER.removeCallbacks(drainRunnable);
        drainSteps();
        PerformingActionGuard.reset();
        AssistantSession.setRecordingPaused(false);
        AssistantSession.setArmedMode(AssistantSession.MODE_IDLE);
        RecordingOverlay.hide();
        RecordCoverView.hide();
        // 停止 getevent 触摸捕获
        stopTouchCapture();
        activeCaseId = -1L;
        overlayListener = null;
    }

    static void pause() {
        AssistantSession.setRecordingPaused(true);
        RecordingOverlay.setPaused(true);
    }

    static void resume() {
        AssistantSession.setRecordingPaused(false);
        RecordingOverlay.setPaused(false);
    }

    static RecordingOverlay.Listener defaultListener(final Context ctx) {
        return new RecordingOverlay.Listener() {
            @Override
            public void onStop() {
                stop(ctx);
            }

            @Override
            public void onPause() {
                pause();
            }

            @Override
            public void onResume() {
                resume();
            }
        };
    }

    private static final java.util.concurrent.ExecutorService DB_WRITER =
            java.util.concurrent.Executors.newSingleThreadExecutor(r -> {
                Thread t = new Thread(r, "testory-rec-db");
                t.setDaemon(true);
                return t;
            });

    private static void drainSteps() {
        if (activeCaseId < 0) return;
        final Context ctx = AssistantApplicationHolder.get();
        if (ctx == null) return;
        final JSONArray batch = PluginHttpServer.drainPendingSteps(40);
        if (batch.length() == 0) return;
        // DB 写入移到后台线程，避免阻塞主线程 Handler
        DB_WRITER.execute(() -> {
            try {
                LocalStore store = LocalStore.get(ctx);
                List<JSONObject> existing = store.getSteps(activeCaseId);
                int baseOrder = existing.size();
                for (int i = 0; i < batch.length(); i++) {
                    JSONObject raw = batch.getJSONObject(i);
                    JSONObject step = RecordStepConverter.toDbStep(raw, baseOrder + i + 1);
                    if (RecordEventFilter.isAssistantStep(step)) {
                        continue;
                    }
                    store.appendNormalizedStep(activeCaseId, step);
                    String label = step.optString("action", "tap")
                            + " — " + step.optString("description", "");
                    RecordingOverlay.addStep(label);
                }
                AssistantSession.notifyStepsUpdated();
            } catch (Exception e) {
                Log.w(TAG, "drainSteps failed", e);
            }
        });
    }

    /** 启动 getevent 触摸捕获（参考 SoloPi TouchEventTracker）。 */
    private static void startTouchCapture() {
        stopTouchCapture();
        try {
            TouchEventCapture capture = new TouchEventCapture("");
            // 获取屏幕分辨率
            Context ctx = AssistantApplicationHolder.get();
            if (ctx != null) {
                android.util.DisplayMetrics dm = ctx.getResources().getDisplayMetrics();
                capture.setScreenSize(dm.widthPixels, dm.heightPixels);
            }
            capture.setListener(gesture -> {
                // getevent 手势回调：将步骤写入 PluginHttpServer 队列
                RecordEventFilter.markTouchGesture(gesture);
                PluginHttpServer.enqueueStep(gesture);
            });
            capture.start();
            touchCapture = capture;
            Log.i(TAG, "getevent touch capture started");
        } catch (Exception e) {
            Log.w(TAG, "startTouchCapture failed", e);
        }
    }

    /** 停止 getevent 触摸捕获。 */
    private static void stopTouchCapture() {
        TouchEventCapture capture = touchCapture;
        if (capture != null) {
            capture.stop();
            touchCapture = null;
            Log.i(TAG, "getevent touch capture stopped");
        }
    }
}
