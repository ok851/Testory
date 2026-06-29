package com.testory.assistant;

import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;

/** 助手全局状态：武装模式、平台连接、设备标识。 */
public final class AssistantSession {

    public static final String MODE_IDLE = "idle";
    public static final String MODE_RECORD = "record";
    public static final String MODE_CAPTURE = "capture_element";

    private static final AtomicReference<String> armedMode = new AtomicReference<>(MODE_IDLE);
    private static final AtomicReference<String> platformUdid = new AtomicReference<>("");
    private static final AtomicReference<Integer> caseId = new AtomicReference<>(null);
    private static final AtomicBoolean socketConnected = new AtomicBoolean(false);
    private static final AtomicReference<String> deviceId = new AtomicReference<>("");
    private static final AtomicBoolean screenshotPerStep = new AtomicBoolean(true);
    /** 本地录制用例（无 remote_id）在筛选项中的 project_id。 */
    public static final int LOCAL_PROJECT_ID = -1;

    private static volatile long suppressRecordingUntilMs;
    private static final AtomicReference<Long> localCaseId = new AtomicReference<>(-1L);
    private static final AtomicReference<String> recordingContextPackage = new AtomicReference<>("");
    private static volatile Runnable stepsUpdatedListener;

    private static volatile AssistantAccessibilityService serviceInstance;

    private AssistantSession() {
    }

    static void setDeviceId(String id) {
        deviceId.set(id == null ? "" : id.trim());
    }

    static String getDeviceId() {
        return deviceId.get();
    }

    static void setArmedMode(String mode) {
        String m = normalizeMode(mode);
        armedMode.set(m);
        AssistantAccessibilityService svc = serviceInstance;
        if (svc != null) {
            svc.onArmedModeChanged(m);
        }
        if (MODE_IDLE.equals(m)) {
            HighlightOverlay.hide();
            recordingContextPackage.set("");
        }
    }

    static String getArmedMode() {
        return armedMode.get();
    }

    static void setPlatformUdid(String udid) {
        platformUdid.set(udid == null ? "" : udid.trim());
    }

    static String getPlatformUdid() {
        return platformUdid.get();
    }

    static void setCaseId(Integer id) {
        caseId.set(id);
    }

    static Integer getCaseId() {
        return caseId.get();
    }

    static void setSocketConnected(boolean connected) {
        socketConnected.set(connected);
    }

    static boolean isSocketConnected() {
        return PluginHttpServer.isRunning();
    }

    static void bindService(AssistantAccessibilityService service) {
        serviceInstance = service;
        service.onArmedModeChanged(armedMode.get());
    }

    static void unbindService(AssistantAccessibilityService service) {
        if (serviceInstance == service) {
            serviceInstance = null;
        }
    }

    static boolean isAccessibilityReady() {
        return serviceInstance != null;
    }

    static AssistantAccessibilityService getService() {
        return serviceInstance;
    }

    static void setScreenshotPerStep(boolean v) {
        screenshotPerStep.set(v);
    }

    static boolean isScreenshotPerStep() {
        return screenshotPerStep.get();
    }

    static void setLocalCaseId(long id) {
        localCaseId.set(id);
    }

    static long getLocalCaseId() {
        Long v = localCaseId.get();
        return v == null ? -1L : v;
    }

    static void suppressRecordingFor(long ms) {
        suppressRecordingUntilMs = System.currentTimeMillis() + Math.max(0, ms);
    }

    static boolean isRecordingSuppressed() {
        return System.currentTimeMillis() < suppressRecordingUntilMs;
    }

    static void setStepsUpdatedListener(Runnable listener) {
        stepsUpdatedListener = listener;
    }

    static void setRecordingContextPackage(String pkg) {
        recordingContextPackage.set(pkg == null ? "" : pkg.trim());
    }

    static String getRecordingContextPackage() {
        return recordingContextPackage.get();
    }

    static void notifyStepsUpdated() {
        Runnable r = stepsUpdatedListener;
        if (r != null) r.run();
    }

    private static String normalizeMode(String mode) {
        if (mode == null) return MODE_IDLE;
        String m = mode.trim().toLowerCase();
        if (MODE_RECORD.equals(m) || "capture".equals(m) || MODE_CAPTURE.equals(m)) {
            return MODE_CAPTURE.equals(m) || "capture".equals(m) ? MODE_CAPTURE : MODE_RECORD;
        }
        return MODE_IDLE;
    }
}
