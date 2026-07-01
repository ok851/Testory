package com.testory.assistant;

import android.accessibilityservice.AccessibilityService;
import android.accessibilityservice.GestureDescription;
import android.content.ComponentName;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Bitmap;
import android.graphics.Path;
import android.graphics.Rect;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.Display;
import android.view.accessibility.AccessibilityEvent;
import android.view.accessibility.AccessibilityNodeInfo;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;

/**
 * 无障碍服务：录制点击/滑动/输入，经本地 JSON-RPC 队列供 Agent 轮询。
 */
public class AssistantAccessibilityService extends AccessibilityService {

    private static final int FOCUS_RECORD_DELAY_MS = 80;

    private String armedMode = AssistantSession.MODE_IDLE;
    private String lastRecordedPackage = "";
    private long lastAppSwitchMs = 0L;
    private long lastScrollEventMs = 0L;
    private long lastDirectClickMs = 0L;
    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private Runnable pendingFocusRecord;
    private Runnable pendingInputRecord;
    private JSONObject pendingInputPayload;

    @Override
    public void onAccessibilityEvent(AccessibilityEvent event) {
        try {
            handleAccessibilityEvent(event);
        } catch (Throwable t) {
            android.util.Log.e("TestoryA11y", "onAccessibilityEvent failed", t);
        }
    }

    private void handleAccessibilityEvent(AccessibilityEvent event) {
        if (event == null || AssistantSession.MODE_IDLE.equals(armedMode)) return;
        if (AssistantSession.MODE_RECORD.equals(armedMode) && AssistantSession.isRecordingPaused()) {
            return;
        }
        if (AssistantSession.MODE_RECORD.equals(armedMode) && AssistantSession.isRecordingSuppressed()) {
            return;
        }

        int type = event.getEventType();
        if (ContentChangeWatcher.isWindowContentEvent(type)) {
            ContentChangeWatcher.notifyContentChanged();
        }
        if (type == AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED) {
            handleAppSwitchRecord(event);
            handleDialog(event);
            return;
        }

        if (type == AccessibilityEvent.TYPE_VIEW_FOCUSED) {
            // 录制模式不用 focus 推断 click，避免 Launcher 幽灵步骤；仅 capture 模式保留。
            if (AssistantSession.MODE_CAPTURE.equals(armedMode)) {
                scheduleFocusClickRecord(event);
            }
            return;
        }

        // Cover 脉冲模式：触摸由 Cover 拦截+注入，禁用被动 touch/click 避免重复步骤
        if (AssistantSession.isCoverModeActive()) {
            if (type == AccessibilityEvent.TYPE_VIEW_TEXT_CHANGED) {
                // 仍录制文本输入
            } else if (type == AccessibilityEvent.TYPE_TOUCH_INTERACTION_START
                    || type == AccessibilityEvent.TYPE_TOUCH_INTERACTION_END
                    || type == AccessibilityEvent.TYPE_VIEW_CLICKED
                    || type == AccessibilityEvent.TYPE_VIEW_LONG_CLICKED
                    || type == AccessibilityEvent.TYPE_VIEW_SCROLLED
                    || type == AccessibilityEvent.TYPE_VIEW_SELECTED) {
                return;
            }
        }

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S && !AssistantSession.isCoverModeActive()) {
            if (type == AccessibilityEvent.TYPE_TOUCH_INTERACTION_START) {
                int[] pt = extractTouchPoint(event);
                TouchGestureClassifier.get().onStart(pt[0], pt[1]);
                TouchCoordBuffer.beginInteraction(pt[0], pt[1]);
                return;
            }
            if (type == AccessibilityEvent.TYPE_TOUCH_INTERACTION_END) {
                if (AssistantSession.MODE_RECORD.equals(armedMode)) {
                    int[] pt = extractTouchPoint(event);
                    TouchCoordBuffer.recordTouch(pt[0], pt[1]);
                    JSONObject gesture = TouchGestureClassifier.get().onEnd(pt[0], pt[1]);
                    if (gesture == null) {
                        gesture = TouchCoordBuffer.finishInteraction(pt[0], pt[1]);
                    }
                    if (gesture != null) {
                        int gx = gesture.optInt("x", pt[0]);
                        int gy = gesture.optInt("y", pt[1]);
                        if ("swipe".equals(gesture.optString("type"))) {
                            gx = (gesture.optInt("x1", 0) + gesture.optInt("x2", 0)) / 2;
                            gy = (gesture.optInt("y1", 0) + gesture.optInt("y2", 0)) / 2;
                        }
                        if ("swipe".equals(gesture.optString("type"))) {
                            int sx = gesture.optInt("x1", pt[0]);
                            int sy = gesture.optInt("y1", pt[1]);
                            NodeLocatorHelper.enrichPayload(this, gesture, sx, sy);
                        } else {
                            NodeLocatorHelper.enrichPayload(this, gesture, gx, gy);
                        }
                        CharSequence pkgCs = event.getPackageName();
                        if (pkgCs != null && pkgCs.length() > 0) {
                            try {
                                gesture.put("package", pkgCs.toString());
                            } catch (Exception ignored) {
                            }
                        }
                        RecordEventFilter.markTouchGesture(gesture);
                        enqueueRecordPayload(event, gesture);
                    }
                }
                return;
            }
        }

        if (type == AccessibilityEvent.TYPE_VIEW_SELECTED) {
            if (AssistantSession.MODE_CAPTURE.equals(armedMode)) {
                cancelPendingFocusRecord();
                try {
                    JSONObject payload = buildPayload(event, "click");
                    enqueueRecordPayload(event, payload);
                } catch (Exception ignored) {
                }
            }
            return;
        }

        if (type != AccessibilityEvent.TYPE_VIEW_CLICKED
                && type != AccessibilityEvent.TYPE_VIEW_LONG_CLICKED
                && type != AccessibilityEvent.TYPE_VIEW_SCROLLED
                && type != AccessibilityEvent.TYPE_VIEW_TEXT_CHANGED) {
            return;
        }

        if (type == AccessibilityEvent.TYPE_VIEW_SCROLLED && RecordEventFilter.wasRecentTouchSwipe()) {
            return;
        }

        cancelPendingFocusRecord();
        if (type == AccessibilityEvent.TYPE_VIEW_CLICKED
                || type == AccessibilityEvent.TYPE_VIEW_LONG_CLICKED) {
            lastDirectClickMs = System.currentTimeMillis();
            // SoloPi 模式：触摸管线已录制的 click/long-press 不再重复录 a11y 事件
            if (RecordEventFilter.wasRecentTouchGesture()) {
                return;
            }
        }

        try {
            JSONObject payload = buildPayload(event, null);
            if (payload == null) return;
            enqueueRecordPayload(event, payload);
        } catch (Exception ignored) {
        }
    }

    private JSONObject buildPayload(AccessibilityEvent event, String forcedType) throws Exception {
        int type = event.getEventType();
        JSONObject payload = new JSONObject();
        payload.put("ts", System.currentTimeMillis());
        CharSequence pkgCs = event.getPackageName();
        if (pkgCs != null && pkgCs.length() > 0) {
            payload.put("package", pkgCs.toString());
        }

        // Scroll/swipe: extract bounds from source node if available, reduce dedup window
        if (type == AccessibilityEvent.TYPE_VIEW_SCROLLED) {
            if (System.currentTimeMillis() - lastScrollEventMs < 80) return null;
            lastScrollEventMs = System.currentTimeMillis();
            payload.put("type", "swipe");
            payload.put("description", describeScroll(event));
            payload.put("scroll_delta_x", event.getScrollDeltaX());
            payload.put("scroll_delta_y", event.getScrollDeltaY());
            // Extract source bounds for coordinate resolution
            try {
                AccessibilityNodeInfo srcNode = event.getSource();
                if (srcNode != null) {
                    android.graphics.Rect r = new android.graphics.Rect();
                    srcNode.getBoundsInScreen(r);
                    payload.put("bounds", rectToJson(r));
                    int cx = r.centerX();
                    int cy = r.centerY();
                    int dx = event.getScrollDeltaX();
                    int dy = event.getScrollDeltaY();
                    // 原缺陷：仅有 scroll_delta 时 x1==x2，桌面端显示/回放均为零位移滑动。
                    payload.put("x1", cx);
                    payload.put("y1", cy);
                    payload.put("x2", cx - dx * 4);
                    payload.put("y2", cy - dy * 4);
                    srcNode.recycle();
                }
            } catch (Exception ignored) {}
        } else if (type == AccessibilityEvent.TYPE_VIEW_TEXT_CHANGED) {
            if (!AssistantSession.MODE_RECORD.equals(armedMode)) return null;
            payload.put("type", "input");
            CharSequence text = event.getText() != null && !event.getText().isEmpty()
                    ? event.getText().get(0) : null;
            String raw = text != null ? text.toString() : "";
            payload.put("text", maskIfSensitive(event, raw));
            scheduleInputRecord(event, payload);
            return null;
        } else if (type == AccessibilityEvent.TYPE_VIEW_LONG_CLICKED) {
            payload.put("type", "long-press");
        } else {
            payload.put("type", forcedType != null ? forcedType
                    : (AssistantSession.MODE_CAPTURE.equals(armedMode) ? "capture" : "click"));
        }

        AccessibilityNodeInfo src = event.getSource();
        Rect bounds = null;
        if (src != null) {
            payload.put("node", nodeToJson(src));
            bounds = TouchCoordBuffer.boundsFromSource(src);
            if (bounds != null) {
                payload.put("bounds", rectToJson(bounds));
            }
            src.recycle();
        } else if (forcedType != null) {
            AccessibilityNodeInfo root = getRootInActiveWindow();
            if (root != null) {
                AccessibilityNodeInfo focused = root.findFocus(AccessibilityNodeInfo.FOCUS_INPUT);
                if (focused == null) {
                    focused = root.findFocus(AccessibilityNodeInfo.FOCUS_ACCESSIBILITY);
                }
                if (focused != null) {
                    payload.put("node", nodeToJson(focused));
                    bounds = new Rect();
                    focused.getBoundsInScreen(bounds);
                    payload.put("bounds", rectToJson(bounds));
                    focused.recycle();
                }
                root.recycle();
            }
        }

        if (AssistantSession.MODE_RECORD.equals(armedMode) && bounds != null
                && RecordingOverlay.hitTest(bounds)) {
            return null;
        }
        if (AssistantSession.MODE_CAPTURE.equals(armedMode) && bounds != null) {
            HighlightOverlay.show(this, bounds);
        }
        TouchCoordBuffer.applyToPayload(payload);
        String payloadType = payload.optString("type", "");
        if (AssistantSession.MODE_RECORD.equals(armedMode) && bounds != null
                && ("click".equals(payloadType) || "long-press".equals(payloadType))) {
            int cx = (bounds.left + bounds.right) / 2;
            int cy = (bounds.top + bounds.bottom) / 2;
            NodeLocatorHelper.enrichPayload(this, payload, cx, cy);
        }
        return payload;
    }

    /** 从触摸/滚动类无障碍事件中尽量提取屏幕坐标（多字段回退）。 */
    private int[] extractTouchPoint(AccessibilityEvent event) {
        int x = 0;
        int y = 0;
        if (event == null) return new int[]{x, y};
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                x = event.getScrollX();
                y = event.getScrollY();
            }
        } catch (Exception ignored) {
        }
        if (x <= 0 && y <= 0) {
            AccessibilityNodeInfo src = event.getSource();
            if (src != null) {
                Rect r = new Rect();
                src.getBoundsInScreen(r);
                if (r.width() > 0 || r.height() > 0) {
                    x = r.centerX();
                    y = r.centerY();
                }
                src.recycle();
            }
        }
        if (x <= 0 && y <= 0) {
            x = TouchCoordBuffer.getLastX();
            y = TouchCoordBuffer.getLastY();
        }
        return new int[]{x, y};
    }

    private void recordTouchFromEvent(AccessibilityEvent event) {
        int[] pt = extractTouchPoint(event);
        if (pt[0] > 0 || pt[1] > 0) {
            TouchCoordBuffer.recordTouch(pt[0], pt[1]);
        }
    }

    private void scheduleInputRecord(AccessibilityEvent event, JSONObject payload) {
        cancelPendingInputRecord();
        try {
            pendingInputPayload = new JSONObject(payload.toString());
        } catch (Exception e) {
            pendingInputPayload = payload;
        }
        pendingInputRecord = () -> {
            try {
                if (pendingInputPayload != null) {
                    pendingInputPayload.put("ts", System.currentTimeMillis());
                    enqueueRecordPayload(event, pendingInputPayload);
                }
            } catch (Exception ignored) {
            } finally {
                pendingInputPayload = null;
                pendingInputRecord = null;
            }
        };
        mainHandler.postDelayed(pendingInputRecord, 200);
    }

    private void cancelPendingInputRecord() {
        if (pendingInputRecord != null) {
            mainHandler.removeCallbacks(pendingInputRecord);
            pendingInputRecord = null;
        }
        pendingInputPayload = null;
    }

    private void enqueueRecordPayload(AccessibilityEvent event, JSONObject payload) {
        if (payload == null) return;
        if (PerformingActionGuard.isPerforming()) return;
        if (RecordEventFilter.shouldSkip(event, payload)) return;
        PluginHttpServer.enqueueStep(payload);
    }

    private void scheduleFocusClickRecord(AccessibilityEvent event) {
        if (!AssistantSession.MODE_RECORD.equals(armedMode)) return;
        AccessibilityNodeInfo src = event.getSource();
        if (src == null) return;
        if (!src.isClickable() && !src.isFocusable()) {
            src.recycle();
            return;
        }
        if (src.isPassword()) {
            src.recycle();
            return;
        }
        final AccessibilityNodeInfo nodeCopy = AccessibilityNodeInfo.obtain(src);
        src.recycle();
        cancelPendingFocusRecord();
        pendingFocusRecord = () -> {
            if (System.currentTimeMillis() - lastDirectClickMs < 150) {
                nodeCopy.recycle();
                return;
            }
            try {
                JSONObject payload = new JSONObject();
                payload.put("ts", System.currentTimeMillis());
                payload.put("type", "click");
                CharSequence pkgCs = event != null ? event.getPackageName() : null;
                if (pkgCs != null && pkgCs.length() > 0) {
                    payload.put("package", pkgCs.toString());
                }
                payload.put("node", nodeToJson(nodeCopy));
                Rect bounds = new Rect();
                nodeCopy.getBoundsInScreen(bounds);
                payload.put("bounds", rectToJson(bounds));
                enqueueRecordPayload(null, payload);
            } catch (Exception ignored) {
            } finally {
                nodeCopy.recycle();
            }
        };
        mainHandler.postDelayed(pendingFocusRecord, FOCUS_RECORD_DELAY_MS);
    }

    private void cancelPendingFocusRecord() {
        if (pendingFocusRecord != null) {
            mainHandler.removeCallbacks(pendingFocusRecord);
            pendingFocusRecord = null;
        }
    }

    private void handleAppSwitchRecord(AccessibilityEvent event) {
        if (!AssistantSession.MODE_RECORD.equals(armedMode)) return;
        try {
            CharSequence pkgCs = event.getPackageName();
            if (pkgCs == null) return;
            String pkg = pkgCs.toString();
            if (pkg.isEmpty() || "com.testory.assistant".equals(pkg)) return;
            if (pkg.equals(lastRecordedPackage)) return;
            long now = System.currentTimeMillis();
            if (now - lastAppSwitchMs < 500) return;
            lastAppSwitchMs = now;
            lastRecordedPackage = pkg;
            // 原缺陷：每次窗口切换都写入 open_app 步骤，回放第一步常误启动桌面/启动器而失败。
            // 新逻辑：仅更新内存上下文，由后续 tap/swipe 步骤携带 context_package。
            AssistantSession.setRecordingContextPackage(pkg);
        } catch (Exception ignored) {
        }
    }

    private String friendlyPackageLabel(String pkg) {
        try {
            PackageManager pm = getPackageManager();
            CharSequence label = pm.getApplicationLabel(pm.getApplicationInfo(pkg, 0));
            if (label != null && label.length() > 0) return label.toString();
        } catch (Exception ignored) {
        }
        return pkg;
    }

    private void handleDialog(AccessibilityEvent event) {
        if (!AssistantSession.MODE_RECORD.equals(armedMode)) return;
        try {
            CharSequence cls = event.getClassName();
            if (cls == null) return;
            String name = cls.toString().toLowerCase();
            if (!name.contains("alert") && !name.contains("dialog") && !name.contains("permission")) return;
            JSONObject payload = new JSONObject();
            payload.put("type", "dialog");
            payload.put("ts", System.currentTimeMillis());
            if (event.getText() != null && !event.getText().isEmpty()) {
                payload.put("description", "处理弹窗：" + event.getText().get(0));
            } else {
                payload.put("description", "处理系统弹窗");
            }
            enqueueRecordPayload(event, payload);
        } catch (Exception ignored) {
        }
    }

    private String maskIfSensitive(AccessibilityEvent event, String raw) {
        AccessibilityNodeInfo src = event.getSource();
        if (src == null) return raw;
        try {
            if (src.isPassword()) return "***";
        } finally {
            src.recycle();
        }
        return raw;
    }

    private String describeScroll(AccessibilityEvent event) {
        if (event.getScrollDeltaX() > 0) return "向右滑动";
        if (event.getScrollDeltaX() < 0) return "向左滑动";
        if (event.getScrollDeltaY() > 0) return "向下滑动列表";
        if (event.getScrollDeltaY() < 0) return "向上滑动列表";
        return "滑动";
    }

    void onArmedModeChanged(String mode) {
        armedMode = mode == null ? AssistantSession.MODE_IDLE : mode;
        if (AssistantSession.MODE_IDLE.equals(armedMode)) {
            cancelPendingFocusRecord();
            cancelPendingInputRecord();
            HighlightOverlay.hide();
            lastRecordedPackage = "";
        }
    }

    String getForegroundPackage() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP) {
            try {
                List<android.view.accessibility.AccessibilityWindowInfo> windows = getWindows();
                if (windows != null) {
                    for (android.view.accessibility.AccessibilityWindowInfo window : windows) {
                        if (window == null || !window.isActive()) continue;
                        AccessibilityNodeInfo root = window.getRoot();
                        if (root == null) continue;
                        try {
                            CharSequence pkg = root.getPackageName();
                            String name = pkg != null ? pkg.toString() : "";
                            if (!name.isEmpty() && !"com.testory.assistant".equals(name)) {
                                return name;
                            }
                        } finally {
                            root.recycle();
                        }
                    }
                }
            } catch (Exception ignored) {
            }
        }
        AccessibilityNodeInfo root = getRootInActiveWindow();
        if (root == null) return "";
        CharSequence pkg = root.getPackageName();
        String out = pkg != null ? pkg.toString() : "";
        root.recycle();
        if ("com.testory.assistant".equals(out)) {
            String ctx = AssistantSession.getRecordingContextPackage();
            if (ctx != null && !ctx.isEmpty()) return ctx;
        }
        return out;
    }

    AppLauncher.Result launchPackageResult(String pkg, String activity) {
        return AppLauncher.launch(this, this, pkg, activity, AppLauncher.DEFAULT_TIMEOUT_MS);
    }

    boolean launchPackage(String pkg, String activity) {
        return launchPackageResult(pkg, activity).success;
    }

    boolean goBack() {
        return performGlobalAction(GLOBAL_ACTION_BACK);
    }

    boolean performLongPress(String selectorType, String selectorValue, int x, int y) {
        return performLongPressSelectorFirst(selectorType, selectorValue, x, y, null);
    }

    boolean performLongPressSelectorFirst(
            String selectorType, String selectorValue, int x, int y, JSONObject opNode) {
        AccessibilityNodeInfo node = findNode(selectorType, selectorValue);
        if (node == null && opNode != null) {
            node = OperationNodeLocator.findLiveNode(this, wrapOpNode(opNode));
        }
        if (node != null) {
            boolean ok = node.performAction(AccessibilityNodeInfo.ACTION_LONG_CLICK);
            node.recycle();
            if (ok) return true;
        }
        if (x > 0 && y > 0) {
            return dispatchLongPress(x, y);
        }
        return false;
    }

    boolean performTap(String selectorType, String selectorValue, int x, int y) {
        return performTapSelectorFirst(selectorType, selectorValue, x, y, null);
    }

    boolean performTapSelectorFirst(
            String selectorType, String selectorValue, int x, int y, JSONObject opNode) {
        AccessibilityNodeInfo node = findNode(selectorType, selectorValue);
        if (node == null && opNode != null) {
            node = OperationNodeLocator.findLiveNode(this, wrapOpNode(opNode));
        }
        if (node != null) {
            boolean ok = node.performAction(AccessibilityNodeInfo.ACTION_CLICK);
            node.recycle();
            if (ok) return true;
        }
        if (x > 0 || y > 0) {
            return dispatchTap(x, y);
        }
        return false;
    }

    boolean performSwipe(int x1, int y1, int x2, int y2) {
        return performSwipe(x1, y1, x2, y2, 320);
    }

    boolean performSwipe(int x1, int y1, int x2, int y2, long durationMs) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.N) return false;
        if (x1 == 0 && y1 == 0 && x2 == 0 && y2 == 0) return false;
        long dur = Math.max(80, Math.min(durationMs > 0 ? durationMs : 320, 2000));
        Path path = new Path();
        path.moveTo(x1, y1);
        path.lineTo(x2, y2);
        GestureDescription.StrokeDescription stroke =
                new GestureDescription.StrokeDescription(path, 0, dur);
        GestureDescription gesture = new GestureDescription.Builder().addStroke(stroke).build();
        return dispatchGestureSync(gesture);
    }

    /** Cover 录制：Cover 已隐藏后注入手势，完成后回调恢复 touchBlockMode。 */
    void performCoverRecordedAction(
            String type,
            JSONObject payload,
            int x1,
            int y1,
            int x2,
            int y2,
            long durationMs,
            Runnable onComplete) {
        mainHandler.post(() -> {
            Runnable done = () -> {
                if (onComplete != null) {
                    onComplete.run();
                }
            };
            try {
                if ("swipe".equals(type)) {
                    long dur = effectiveSwipeDurationMs(x1, y1, x2, y2, durationMs);
                    dispatchCoverSwipeAsync(x1, y1, x2, y2, dur, done);
                    return;
                }
                JSONObject spec = payload != null ? payload.optJSONObject("operation_node") : null;
                String st = "";
                String sv = "";
                if (spec != null) {
                    if (spec.has("resource_id")) {
                        st = "id";
                        sv = spec.optString("resource_id");
                    } else if (spec.has("content_desc")) {
                        st = "accessibility_id";
                        sv = spec.optString("content_desc");
                    }
                }
                int x = payload != null ? payload.optInt("x", 0) : 0;
                int y = payload != null ? payload.optInt("y", 0) : 0;
                if ("long-press".equals(type) || "long_press".equals(type)) {
                    if (trySelectorAction(st, sv, spec, true, x, y)) {
                        done.run();
                        return;
                    }
                    dispatchCoverLongPressAsync(x, y, done);
                    return;
                }
                if (trySelectorAction(st, sv, spec, false, x, y)) {
                    done.run();
                    return;
                }
                dispatchCoverTapAsync(x, y, done);
            } catch (Exception e) {
                android.util.Log.w("TestoryA11y", "performCoverRecordedAction failed", e);
                done.run();
            }
        });
    }

    void performCoverRecordedAction(
            String type,
            JSONObject payload,
            int x1,
            int y1,
            int x2,
            int y2,
            long durationMs) {
        performCoverRecordedAction(type, payload, x1, y1, x2, y2, durationMs, null);
    }

    private static long effectiveSwipeDurationMs(int x1, int y1, int x2, int y2, long durationMs) {
        long dur = Math.max(80, Math.min(durationMs > 0 ? durationMs : 320, 2000));
        int dx = Math.abs(x2 - x1);
        int dy = Math.abs(y2 - y1);
        if (Math.max(dx, dy) >= 120 && dur < 280) {
            dur = 280;
        }
        return dur;
    }

    private boolean trySelectorAction(
            String selectorType,
            String selectorValue,
            JSONObject opNode,
            boolean longPress,
            int x,
            int y) {
        AccessibilityNodeInfo node = findNode(selectorType, selectorValue);
        if (node == null && opNode != null) {
            node = OperationNodeLocator.findLiveNode(this, wrapOpNode(opNode));
        }
        if (node == null) return false;
        int action = longPress
                ? AccessibilityNodeInfo.ACTION_LONG_CLICK
                : AccessibilityNodeInfo.ACTION_CLICK;
        boolean ok = node.performAction(action);
        node.recycle();
        if (ok) return true;
        return x <= 0 && y <= 0;
    }

    private void dispatchCoverTapAsync(int x, int y, Runnable onComplete) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.N || x <= 0 || y <= 0) {
            if (onComplete != null) onComplete.run();
            return;
        }
        Path path = new Path();
        path.moveTo(x, y);
        GestureDescription.StrokeDescription stroke =
                new GestureDescription.StrokeDescription(path, 0, 80);
        GestureDescription gesture = new GestureDescription.Builder().addStroke(stroke).build();
        dispatchCoverGestureAsync(gesture, onComplete);
    }

    private void dispatchCoverLongPressAsync(int x, int y, Runnable onComplete) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.N || x <= 0 || y <= 0) {
            if (onComplete != null) onComplete.run();
            return;
        }
        Path path = new Path();
        path.moveTo(x, y);
        GestureDescription.StrokeDescription stroke =
                new GestureDescription.StrokeDescription(path, 0, 650);
        GestureDescription gesture = new GestureDescription.Builder().addStroke(stroke).build();
        dispatchCoverGestureAsync(gesture, onComplete);
    }

    private void dispatchCoverSwipeAsync(int x1, int y1, int x2, int y2, long durationMs, Runnable onComplete) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.N) {
            if (onComplete != null) onComplete.run();
            return;
        }
        if (x1 == 0 && y1 == 0 && x2 == 0 && y2 == 0) {
            if (onComplete != null) onComplete.run();
            return;
        }
        Path path = new Path();
        path.moveTo(x1, y1);
        path.lineTo(x2, y2);
        GestureDescription.StrokeDescription stroke =
                new GestureDescription.StrokeDescription(path, 0, durationMs);
        GestureDescription gesture = new GestureDescription.Builder().addStroke(stroke).build();
        dispatchCoverGestureAsync(gesture, onComplete);
    }

    private void dispatchCoverGestureAsync(GestureDescription gesture, Runnable onComplete) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.N || gesture == null) {
            if (onComplete != null) onComplete.run();
            return;
        }
        boolean dispatched = dispatchGesture(gesture, new GestureResultCallback() {
            @Override
            public void onCompleted(GestureDescription gestureDescription) {
                if (onComplete != null) onComplete.run();
            }

            @Override
            public void onCancelled(GestureDescription gestureDescription) {
                if (onComplete != null) onComplete.run();
            }
        }, null);
        if (!dispatched) {
            android.util.Log.w("TestoryA11y", "dispatchCoverGestureAsync not dispatched");
            if (onComplete != null) onComplete.run();
        }
    }

    private void dispatchCoverTapAsync(int x, int y) {
        dispatchCoverTapAsync(x, y, null);
    }

    private void dispatchCoverLongPressAsync(int x, int y) {
        dispatchCoverLongPressAsync(x, y, null);
    }

    private void dispatchCoverSwipeAsync(int x1, int y1, int x2, int y2, long durationMs) {
        dispatchCoverSwipeAsync(x1, y1, x2, y2, durationMs, null);
    }

    private void dispatchCoverGestureAsync(GestureDescription gesture) {
        dispatchCoverGestureAsync(gesture, null);
    }

    private void dispatchTapAsync(int x, int y) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.N || x <= 0 || y <= 0) return;
        Path path = new Path();
        path.moveTo(x, y);
        GestureDescription.StrokeDescription stroke =
                new GestureDescription.StrokeDescription(path, 0, 80);
        GestureDescription gesture = new GestureDescription.Builder().addStroke(stroke).build();
        dispatchGestureAsync(gesture);
    }

    private void dispatchLongPressAsync(int x, int y) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.N || x <= 0 || y <= 0) return;
        Path path = new Path();
        path.moveTo(x, y);
        GestureDescription.StrokeDescription stroke =
                new GestureDescription.StrokeDescription(path, 0, 650);
        GestureDescription gesture = new GestureDescription.Builder().addStroke(stroke).build();
        dispatchGestureAsync(gesture);
    }

    private void dispatchSwipeAsync(int x1, int y1, int x2, int y2, long durationMs) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.N) return;
        if (x1 == 0 && y1 == 0 && x2 == 0 && y2 == 0) return;
        long dur = Math.max(80, Math.min(durationMs > 0 ? durationMs : 320, 2000));
        Path path = new Path();
        path.moveTo(x1, y1);
        path.lineTo(x2, y2);
        GestureDescription.StrokeDescription stroke =
                new GestureDescription.StrokeDescription(path, 0, dur);
        GestureDescription gesture = new GestureDescription.Builder().addStroke(stroke).build();
        dispatchGestureAsync(gesture);
    }

    private void dispatchGestureAsync(GestureDescription gesture) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.N || gesture == null) return;
        dispatchGesture(gesture, null, null);
    }

    boolean performInput(String selectorType, String selectorValue, String text) {
        AccessibilityNodeInfo node = findNode(selectorType, selectorValue);
        if (node == null) return false;
        if (!node.isFocused()) {
            node.performAction(AccessibilityNodeInfo.ACTION_FOCUS);
            try {
                Thread.sleep(120);
            } catch (InterruptedException ignored) {
                Thread.currentThread().interrupt();
            }
        }
        Bundle args = new Bundle();
        args.putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text);
        boolean ok = node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args);
        node.recycle();
        return ok;
    }

    private static JSONObject wrapOpNode(JSONObject opNode) {
        try {
            JSONObject step = new JSONObject();
            JSONObject spec = new JSONObject();
            spec.put("operation_node", opNode);
            step.put("mobile_spec", spec);
            return step;
        } catch (Exception e) {
            return new JSONObject();
        }
    }

    boolean goHome() {
        return performGlobalAction(GLOBAL_ACTION_HOME);
    }

    byte[] captureScreenshotPng() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            AtomicReference<byte[]> ref = new AtomicReference<>();
            CountDownLatch latch = new CountDownLatch(1);
            takeScreenshot(Display.DEFAULT_DISPLAY, getMainExecutor(), new TakeScreenshotCallback() {
                @Override
                public void onSuccess(ScreenshotResult screenshotResult) {
                    try {
                        Bitmap bmp = Bitmap.wrapHardwareBuffer(
                                screenshotResult.getHardwareBuffer(),
                                screenshotResult.getColorSpace());
                        if (bmp != null) {
                            ByteArrayOutputStream bos = new ByteArrayOutputStream();
                            bmp.compress(Bitmap.CompressFormat.PNG, 90, bos);
                            ref.set(bos.toByteArray());
                            bmp.recycle();
                        }
                    } catch (Exception ignored) {
                    } finally {
                        latch.countDown();
                    }
                }

                @Override
                public void onFailure(int errorCode) {
                    latch.countDown();
                }
            });
            try {
                latch.await(5, TimeUnit.SECONDS);
            } catch (InterruptedException ignored) {
                Thread.currentThread().interrupt();
            }
            return ref.get();
        }
        return null;
    }

    private boolean dispatchLongPress(int x, int y) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.N) return false;
        Path path = new Path();
        path.moveTo(x, y);
        GestureDescription.StrokeDescription stroke =
                new GestureDescription.StrokeDescription(path, 0, 650);
        GestureDescription gesture = new GestureDescription.Builder().addStroke(stroke).build();
        return dispatchGestureSync(gesture);
    }

    private boolean dispatchTap(int x, int y) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.N) return false;
        Path path = new Path();
        path.moveTo(x, y);
        GestureDescription.StrokeDescription stroke =
                new GestureDescription.StrokeDescription(path, 0, 80);
        GestureDescription gesture = new GestureDescription.Builder().addStroke(stroke).build();
        return dispatchGestureSync(gesture);
    }

    private boolean dispatchGestureSync(GestureDescription gesture) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.N) return false;
        CountDownLatch latch = new CountDownLatch(1);
        AtomicBoolean ok = new AtomicBoolean(false);
        boolean dispatched = dispatchGesture(gesture, new GestureResultCallback() {
            @Override
            public void onCompleted(GestureDescription gestureDescription) {
                ok.set(true);
                latch.countDown();
            }

            @Override
            public void onCancelled(GestureDescription gestureDescription) {
                latch.countDown();
            }
        }, null);
        if (!dispatched) return false;
        try {
            latch.await(4, TimeUnit.SECONDS);
        } catch (InterruptedException ignored) {
            Thread.currentThread().interrupt();
        }
        return ok.get();
    }

    private AccessibilityNodeInfo findNode(String selectorType, String selectorValue) {
        if (selectorValue == null || selectorValue.isEmpty()) return null;
        AccessibilityNodeInfo root = getRootInActiveWindow();
        if (root == null) return null;
        AccessibilityNodeInfo found = searchNode(root, selectorType, selectorValue);
        root.recycle();
        return found;
    }

    private AccessibilityNodeInfo searchNode(AccessibilityNodeInfo n, String st, String sv) {
        if (n == null) return null;
        if ("id".equals(st) && sv != null && sv.equals(n.getViewIdResourceName())) {
            return AccessibilityNodeInfo.obtain(n);
        }
        if ("accessibility_id".equals(st)) {
            CharSequence desc = n.getContentDescription();
            if (desc != null && sv != null && sv.contentEquals(desc)) {
                return AccessibilityNodeInfo.obtain(n);
            }
        }
        if (n.getText() != null && sv != null && sv.contentEquals(n.getText())) {
            return AccessibilityNodeInfo.obtain(n);
        }
        for (int i = 0; i < n.getChildCount(); i++) {
            AccessibilityNodeInfo child = n.getChild(i);
            if (child == null) continue;
            AccessibilityNodeInfo hit = searchNode(child, st, sv);
            child.recycle();
            if (hit != null) return hit;
        }
        return null;
    }

    private JSONObject nodeToJson(AccessibilityNodeInfo n) throws Exception {
        JSONObject o = new JSONObject();
        if (n.getViewIdResourceName() != null) {
            o.put("resource_id", n.getViewIdResourceName());
        }
        if (n.getText() != null) {
            o.put("text", n.getText().toString());
        }
        if (n.getContentDescription() != null) {
            o.put("content_desc", n.getContentDescription().toString());
        }
        if (n.getClassName() != null) {
            o.put("class", n.getClassName().toString());
        }
        return o;
    }

    private JSONArray rectToJson(Rect r) {
        JSONArray arr = new JSONArray();
        arr.put(r.left);
        arr.put(r.top);
        arr.put(r.right);
        arr.put(r.bottom);
        return arr;
    }

    @Override
    public void onInterrupt() {
    }

    @Override
    protected void onServiceConnected() {
        super.onServiceConnected();
        try {
            AssistantApplicationHolder.init(this);
            AssistantSession.bindService(this);
            onArmedModeChanged(AssistantSession.getArmedMode());
            new Thread(() -> {
                try {
                    PluginHttpServer.start(AssistantAccessibilityService.this);
                } catch (Exception e) {
                    android.util.Log.e("TestoryA11y", "PluginHttpServer start failed", e);
                }
            }, "plugin-http-start").start();
        } catch (Exception e) {
            android.util.Log.e("TestoryA11y", "onServiceConnected failed", e);
        }
    }

    @Override
    public void onDestroy() {
        cancelPendingFocusRecord();
        cancelPendingInputRecord();
        AssistantSession.unbindService(this);
        RecordCoverView.hide();
        RecordingOverlay.hide();
        HighlightOverlay.hide();
        PluginHttpServer.stopServer();
        super.onDestroy();
    }
}
