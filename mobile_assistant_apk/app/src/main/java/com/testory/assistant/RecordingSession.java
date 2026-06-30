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
    /** 悬浮条布局就绪后再 armed，仅用于 overlay 空间过滤，不阻塞事件入队。 */
    private static final int FORCE_ARM_MS = 900;

    private static long activeCaseId = -1L;
    private static boolean draining;
    private static RecordingOverlay.Listener overlayListener;

    private static final Runnable drainRunnable = new Runnable() {
        @Override
        public void run() {
            if (!draining || activeCaseId < 0) return;
            drainSteps();
            HANDLER.postDelayed(this, PluginHttpServer.isAgentRecordingActive() ? 100 : 120);
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
        AssistantSession.setOverlayRecordPhase(AssistantSession.OverlayRecordPhase.IDLE);

        SessionForegroundGuard.retreatToDesktop(ctx, (desktopReady, message) -> {
            if (activeCaseId != sessionId) return;
            if (!message.isEmpty() && ctx != null) {
                Toast.makeText(ctx.getApplicationContext(), message, Toast.LENGTH_LONG).show();
            }
            RecordEventFilter.resetDedupe();
            TouchCoordBuffer.reset();
            RecordingOverlay.clearSteps();
            AssistantSession.setOverlayRecordPhase(AssistantSession.OverlayRecordPhase.RECORDING);
            RecordingOverlay.show(ctx, listener, null);
            finishRecordingArm(ctx, sessionId);
            HANDLER.postDelayed(() -> forceArmIfNeeded(ctx, sessionId), FORCE_ARM_MS);
        });
    }

    private static void forceArmIfNeeded(Context ctx, long sessionId) {
        if (activeCaseId != sessionId) return;
        if (AssistantSession.MODE_RECORD.equals(AssistantSession.getArmedMode())) {
            return;
        }
        Log.w(TAG, "force arm after " + FORCE_ARM_MS + "ms safety timeout");
        finishRecordingArm(ctx, sessionId);
    }

    private static void finishRecordingArm(Context ctx, long sessionId) {
        if (activeCaseId != sessionId) return;
        if (AssistantSession.MODE_RECORD.equals(AssistantSession.getArmedMode())) {
            return;
        }
        draining = true;
        AssistantSession.setOverlayRecordPhase(AssistantSession.OverlayRecordPhase.RECORDING);
        AssistantSession.setArmedMode(AssistantSession.MODE_RECORD);
        Log.i(TAG, "recording armed (caseId=" + sessionId + ")");
        AssistantAccessibilityService svc = AssistantSession.getService();
        if (svc != null) {
            String fg = svc.getForegroundPackage();
            if (fg != null && !fg.isEmpty() && !"com.testory.assistant".equals(fg)) {
                AssistantSession.setRecordingContextPackage(fg);
            }
        }
        HANDLER.post(drainRunnable);
    }

    static void stop(Context ctx) {
        draining = false;
        HANDLER.removeCallbacks(drainRunnable);
        drainSteps();
        if (PluginHttpServer.isAgentRecordingActive()) {
            PluginHttpServer.setAgentRecordingActive(false);
        }
        AssistantSession.setOverlayRecordPhase(AssistantSession.OverlayRecordPhase.IDLE);
        AssistantSession.setArmedMode(AssistantSession.MODE_IDLE);
        RecordingOverlay.hide();
        activeCaseId = -1L;
        overlayListener = null;
    }

    static void pause() {
        AssistantSession.setArmedMode(AssistantSession.MODE_IDLE);
        RecordingOverlay.setPaused(true);
    }

    static void resume() {
        AssistantSession.setArmedMode(AssistantSession.MODE_RECORD);
        AssistantSession.setOverlayRecordPhase(AssistantSession.OverlayRecordPhase.RECORDING);
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
