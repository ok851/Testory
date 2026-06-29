package com.testory.assistant;

import android.content.Context;

/**
 * 录制入口：不启动前台服务，避免 targetSdk 34 权限导致进程崩溃。
 */
public final class RecordingController {

    private RecordingController() {
    }

    static void startRecording(Context ctx, long caseId) {
        startRecording(ctx, caseId, false);
    }

    static void startRecording(Context ctx, long caseId, boolean agentMode) {
        AssistantApplicationHolder.init(ctx);
        if (!agentMode) {
            // 原缺陷：PC Agent 录制后 agentRecordingActive 仍为 true，本地 drain 读空镜像队列。
            PluginHttpServer.setAgentRecordingActive(false);
        }
        RecordingSession.start(ctx, caseId, RecordingSession.defaultListener(ctx), agentMode);
    }

    static void stopRecording(Context ctx) {
        AssistantApplicationHolder.init(ctx);
        RecordingSession.stop(ctx);
    }

    static void pauseRecording(Context ctx) {
        AssistantApplicationHolder.init(ctx);
        RecordingSession.pause();
    }

    static void resumeRecording(Context ctx) {
        AssistantApplicationHolder.init(ctx);
        RecordingSession.resume();
    }
}
