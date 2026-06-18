package com.testory.assistant;

import android.content.Context;

/**
 * 录制入口：不启动前台服务，避免 targetSdk 34 权限导致进程崩溃。
 */
public final class RecordingController {

    private RecordingController() {
    }

    static void startRecording(Context ctx, long caseId) {
        AssistantApplicationHolder.init(ctx);
        RecordingSession.start(ctx, caseId, RecordingSession.defaultListener(ctx));
    }

    static void stopRecording(Context ctx) {
        AssistantApplicationHolder.init(ctx);
        RecordingSession.stop(ctx);
    }
}
