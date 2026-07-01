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

        SessionForegroundGuard.retreatToDesktop(ctx, (desktopReady, message) -> {
            if (activeCaseId != sessionId) return;
            if (!message.isEmpty() && ctx != null) {
                Toast.makeText(ctx.getApplicationContext(), message, Toast.LENGTH_LONG).show();
            }
            RecordingOverlay.clearSteps();
            AssistantAccessibilityService svc = AssistantSession.getService();
            // SoloPi：Cover 在下、控制条在上，armed 仅在 Cover 就绪后开启。
            RecordCoverView.show(svc, () -> {
                if (ctx != null) {
                    Toast.makeText(
                            ctx.getApplicationContext(),
                            "触摸层不可用，已切换备用录制模式",
                            Toast.LENGTH_LONG).show();
                }
                finishArming(ctx, listener, desktopReady, false);
            }, coverShown -> {
                if (activeCaseId != sessionId) return;
                RecordingOverlay.show(ctx, listener);
                finishArming(ctx, listener, desktopReady, coverShown);
            });
        });
    }

    private static void finishArming(
            Context ctx,
            RecordingOverlay.Listener listener,
            boolean desktopReady,
            boolean coverShown) {
        if (activeCaseId < 0) return;
        draining = true;
        AssistantSession.suppressRecordingFor(desktopReady ? 80 : 120);
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
                    coverShown ? "录制已开始，可直接操作手机" : "录制已开始（备用模式）",
                    Toast.LENGTH_SHORT).show();
        }
        Log.i(TAG, "recording armed cover=" + coverShown);
        HANDLER.post(drainRunnable);
    }

    static void stop(Context ctx) {
        draining = false;
        HANDLER.removeCallbacks(drainRunnable);
        drainSteps();
        PerformingActionGuard.reset();
        AssistantSession.setRecordingPaused(false);
        AssistantSession.setArmedMode(AssistantSession.MODE_IDLE);
        RecordCoverView.hide();
        RecordingOverlay.hide();
        activeCaseId = -1L;
        overlayListener = null;
    }

    static void pause() {
        AssistantSession.setRecordingPaused(true);
        RecordingOverlay.setPaused(true);
        RecordCoverView.hideForPause();
    }

    static void resume() {
        AssistantSession.setRecordingPaused(false);
        RecordingOverlay.setPaused(false);
        RecordCoverView.enterTouchBlockMode();
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

    private static void drainSteps() {
        if (activeCaseId < 0) return;
        Context ctx = AssistantApplicationHolder.get();
        if (ctx == null) return;
        JSONArray batch = PluginHttpServer.drainPendingSteps(40);
        if (batch.length() == 0) return;
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
    }
}
