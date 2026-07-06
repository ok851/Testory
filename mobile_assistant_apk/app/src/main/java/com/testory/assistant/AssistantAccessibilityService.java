package com.testory.assistant;

import android.accessibilityservice.AccessibilityService;
import android.accessibilityservice.GestureDescription;
import android.content.ComponentName;
import android.content.Context;
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
    private static final int TAP_RADIUS = 48;

    private String armedMode = AssistantSession.MODE_IDLE;
    private String lastRecordedPackage = "";
    private long lastAppSwitchMs = 0L;
    private long lastScrollEventMs = 0L;
    private long lastDirectClickMs = 0L;
    /** 最近一次触摸坐标（桌面图标点击时 TYPE_VIEW_CLICKED 可能不触发，用此兜底） */
    private int lastTouchX = 0;
    private int lastTouchY = 0;
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

        // P0 修复：顶层过滤自身包名事件，防止悬浮窗、Toast 等自身 UI 事件泄漏到录制管线。
        // 参考 SoloPi AccessibilityServiceImpl L94-96：第一时间丢弃自身包名事件。
        // 原有分散在各事件类型中的 if 过滤仅覆盖 CLICKED/TOUCH_INTERACTION 两类，
        // 其余 TYPES_ALL_MASK 下的 Windows事件/Notification 等仍可能从自身 UI 泄漏。
        CharSequence pkg = event.getPackageName();
        if (pkg != null && "com.testory.assistant".equals(pkg.toString())) {
            return;
        }

        if (AssistantSession.MODE_RECORD.equals(armedMode) && AssistantSession.isRecordingPaused()) {
            return;
        }
        if (AssistantSession.MODE_RECORD.equals(armedMode) && AssistantSession.isRecordingSuppressed()) {
            return;
        }

        // 纯 AccessibilityService 录制模式：
        // VIEW_CLICKED/LONG_CLICKED 作为 click 的兜底录制源，
        // TYPE_TOUCH_INTERACTION (API 31+) 作为高精度触摸坐标源，
        // TYPE_VIEW_SCROLLED 作为滑动兜底源。
        if (AssistantSession.MODE_RECORD.equals(armedMode)
                && !PluginHttpServer.isAgentRecordingActive()) {
            int type = event.getEventType();
            if (type == AccessibilityEvent.TYPE_VIEW_CLICKED
                    || type == AccessibilityEvent.TYPE_VIEW_LONG_CLICKED) {
                // TOUCH_INTERACTION 已通过 markTouchGesture 标记，
                // VIEW_CLICKED 事件可能是同一操作的回声，需去重
                if (RecordEventFilter.wasRecentTouchGesture()) {
                    return;
                }
            }
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

        // 纯 AccessibilityService 录制模式：不使用 getevent（非 root 不可用），
        // 不使用 TouchEventOverlay（导致重复录制）。
        // 所有触摸事件通过 TYPE_TOUCH_INTERACTION/VIEW_CLICKED/VIEW_SCROLLED 捕获。

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            if (type == AccessibilityEvent.TYPE_TOUCH_INTERACTION_START) {
                int[] pt = extractTouchPoint(event);
                lastTouchX = pt[0];
                lastTouchY = pt[1];
                TouchGestureClassifier.get().onStart(pt[0], pt[1]);
                TouchCoordBuffer.beginInteraction(pt[0], pt[1]);
                return;
            }
            if (type == AccessibilityEvent.TYPE_TOUCH_INTERACTION_END) {
                if (AssistantSession.MODE_RECORD.equals(armedMode)) {
                    int[] pt = extractTouchPoint(event);
                    if (pt[0] <= 0 && pt[1] <= 0) {
                        pt[0] = TouchCoordBuffer.getLastX();
                        pt[1] = TouchCoordBuffer.getLastY();
                    }
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
                        return;
                    }
                }
            }
        }

        // TYPE_VIEW_SCROLLED 兜底：当触摸管线未捕获滑动时，通过 scroll_delta 推断滑动。
        // 原缺陷：仅在 API < 31 时处理，导致 Android 12+ 设备上部分滑动丢失。
        // 新逻辑：所有 API 版本都处理 TYPE_VIEW_SCROLLED，但会检查触摸管线是否已产出。
        // Design inspired by mobile-automation-guide: 多策略回退确保兼容性。
        if (type == AccessibilityEvent.TYPE_VIEW_SCROLLED
                && AssistantSession.MODE_RECORD.equals(armedMode)) {
            if (!RecordEventFilter.wasRecentTouchSwipe()) {
                try {
                    JSONObject payload = buildPayload(event, null);
                    if (payload != null) {
                        enqueueRecordPayload(event, payload);
                    }
                } catch (Exception ignored) {
                }
            }
            return;
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
                Context appCtx = AssistantApplicationHolder.get();
                int screenW = 1080, screenH = 1920;
                if (appCtx != null) {
                    android.util.DisplayMetrics dm = appCtx.getResources().getDisplayMetrics();
                    screenW = dm.widthPixels;
                    screenH = dm.heightPixels;
                }
                AccessibilityNodeInfo srcNode = event.getSource();
                if (srcNode != null) {
                    android.graphics.Rect r = new android.graphics.Rect();
                    srcNode.getBoundsInScreen(r);
                    payload.put("bounds", rectToJson(r));
                    int cx = r.centerX();
                    int cy = r.centerY();
                    int dx = event.getScrollDeltaX();
                    int dy = event.getScrollDeltaY();
                    // 修复：dx/dy 通常为 1-3px，乘以固定倍数不可靠。
                    // 改为使用屏幕宽度/高度的 30% 作为滑动距离，方向由 delta 符号决定。
                    payload.put("x1", cx);
                    payload.put("y1", cy);
                    if (dx != 0) {
                        // 水平滑动：从中心出发，滑动屏幕宽度 30%
                        int swipeLen = Math.abs(screenW * 3 / 10);
                        payload.put("x2", dx > 0 ? cx - swipeLen : cx + swipeLen);
                        payload.put("y2", cy);
                    } else if (dy != 0) {
                        // 垂直滑动：从中心出发，滑动屏幕高度 30%
                        int swipeLen = Math.abs(screenH * 3 / 10);
                        payload.put("x2", cx);
                        payload.put("y2", dy > 0 ? cy - swipeLen : cy + swipeLen);
                    } else {
                        payload.put("x2", cx);
                        payload.put("y2", cy);
                    }
                    payload.put("screen_width", screenW);
                    payload.put("screen_height", screenH);
                    srcNode.recycle();
                } else {
                    // getSource() 返回 null 时（自定义 View），使用屏幕中心作为兜底
                    int dx = event.getScrollDeltaX();
                    int dy = event.getScrollDeltaY();
                    int cx = screenW / 2;
                    int cy = screenH / 2;
                    payload.put("x1", cx);
                    payload.put("y1", cy);
                    if (dx != 0) {
                        int swipeLen = Math.abs(screenW * 3 / 10);
                        payload.put("x2", dx > 0 ? cx - swipeLen : cx + swipeLen);
                        payload.put("y2", cy);
                    } else if (dy != 0) {
                        int swipeLen = Math.abs(screenH * 3 / 10);
                        payload.put("x2", cx);
                        payload.put("y2", dy > 0 ? cy - swipeLen : cy + swipeLen);
                    } else {
                        payload.put("x2", cx);
                        payload.put("y2", cy);
                    }
                    payload.put("screen_width", screenW);
                    payload.put("screen_height", screenH);
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

        // 修复原缺陷：当 event.getSource() 返回 null 且非 forcedType 时，
        // 仍尝试获取当前焦点节点信息，避免桌面图标点击时 node 为空。
        // Design inspired by mobile-automation-guide: 多策略回退获取元素信息。
        if (!payload.has("node") && !payload.has("bounds")) {
            AccessibilityNodeInfo root = getRootInActiveWindow();
            if (root != null) {
                // 尝试获取当前焦点节点
                AccessibilityNodeInfo focused = root.findFocus(AccessibilityNodeInfo.FOCUS_ACCESSIBILITY);
                if (focused == null) {
                    focused = root.findFocus(AccessibilityNodeInfo.FOCUS_INPUT);
                }
                if (focused != null) {
                    payload.put("node", nodeToJson(focused));
                    bounds = new Rect();
                    focused.getBoundsInScreen(bounds);
                    if (bounds.width() > 0 || bounds.height() > 0) {
                        payload.put("bounds", rectToJson(bounds));
                    }
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
        // 修复：当以上所有策略都未获取到 bounds 时，
        // 使用最近触摸坐标（lastTouchX/Y）创建合成 bounds。
        // 桌面图标点击时 event.getSource() 和焦点节点都可能为 null，
        // TouchCoordBuffer 的 500ms 窗口也可能因时序问题未命中。
        // API < 31 设备无 TYPE_TOUCH_INTERACTION，lastTouchX/Y 始终为 0，
        // 此时使用屏幕中心作为兜底（总比 0,0 好，回放时至少能产生有效手势）。
        if (!payload.has("bounds")) {
            int tx = lastTouchX > 0 ? lastTouchX : 0;
            int ty = lastTouchY > 0 ? lastTouchY : 0;
            // API < 31 兜底：lastTouchX/Y 为 0 时使用屏幕中心
            if (tx <= 0 && ty <= 0) {
                try {
                    Context appCtx = AssistantApplicationHolder.get();
                    if (appCtx != null) {
                        android.util.DisplayMetrics dm = appCtx.getResources().getDisplayMetrics();
                        tx = dm.widthPixels / 2;
                        ty = dm.heightPixels / 2;
                    }
                } catch (Exception ignored) {
                }
                if (tx <= 0) tx = 540;
                if (ty <= 0) ty = 960;
            }
            JSONArray tb = new JSONArray();
            tb.put(Math.max(0, tx - TAP_RADIUS));
            tb.put(Math.max(0, ty - TAP_RADIUS));
            tb.put(tx + TAP_RADIUS);
            tb.put(ty + TAP_RADIUS);
            try {
                payload.put("bounds", tb);
                payload.put("x", tx);
                payload.put("y", ty);
            } catch (Exception ignored) {
            }
            // 关键修复：更新局部 bounds 变量，确保后续 enrichPayload 能执行。
            // 原缺陷：仅写入 payload JSON 但未更新局部 bounds，
            // 导致 enrichPayload 的 bounds!=null 检查失败，坐标不写入 operation_node。
            bounds = new Rect(
                    Math.max(0, tx - TAP_RADIUS),
                    Math.max(0, ty - TAP_RADIUS),
                    tx + TAP_RADIUS,
                    ty + TAP_RADIUS);
        }
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
            int[] coords = extractTouchPointReflection(event);
            if (coords[0] > 0 || coords[1] > 0) {
                x = coords[0];
                y = coords[1];
            }
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

    /** 通过反射获取 AccessibilityEvent 的触摸坐标（API 31+ TYPE_TOUCH_INTERACTION 事件专用）。 */
    private int[] extractTouchPointReflection(AccessibilityEvent event) {
        int x = 0, y = 0;
        try {
            java.lang.reflect.Field xField = event.getClass().getDeclaredField("mX");
            java.lang.reflect.Field yField = event.getClass().getDeclaredField("mY");
            xField.setAccessible(true);
            yField.setAccessible(true);
            x = xField.getInt(event);
            y = yField.getInt(event);
        } catch (Exception ignored) {
        }
        if (x <= 0 && y <= 0) {
            try {
                java.lang.reflect.Field xField = event.getClass().getDeclaredField("x");
                java.lang.reflect.Field yField = event.getClass().getDeclaredField("y");
                xField.setAccessible(true);
                yField.setAccessible(true);
                x = xField.getInt(event);
                y = yField.getInt(event);
            } catch (Exception ignored) {
            }
        }
        return new int[]{x, y};
    }

    /**
     * 当 extractTouchPoint 返回 (0,0) 时的额外回退。
     * 尝试从当前活跃窗口的焦点节点获取坐标，避免触摸手势被完全丢弃。
     * 桌面/启动器等应用的触摸事件常不携带坐标，此方法确保手势仍能被录制。
     */
    private int[] extractFallbackTouchPoint() {
        int x = 0;
        int y = 0;
        
        try {
            AccessibilityNodeInfo root = getRootInActiveWindow();
            if (root != null) {
                AccessibilityNodeInfo focused = root.findFocus(AccessibilityNodeInfo.FOCUS_ACCESSIBILITY);
                if (focused == null) {
                    focused = root.findFocus(AccessibilityNodeInfo.FOCUS_INPUT);
                }
                if (focused != null) {
                    Rect r = new Rect();
                    focused.getBoundsInScreen(r);
                    if (r.width() > 0 || r.height() > 0) {
                        x = r.centerX();
                        y = r.centerY();
                    }
                    focused.recycle();
                }
                root.recycle();
            }
        } catch (Exception ignored) {
        }
        
        if (x <= 0 && y <= 0) {
            try {
                Context ctx = AssistantApplicationHolder.get();
                if (ctx != null) {
                    android.util.DisplayMetrics dm = ctx.getResources().getDisplayMetrics();
                    x = dm.widthPixels / 2;
                    y = dm.heightPixels / 2;
                }
            } catch (Exception ignored) {
                x = 540;
                y = 960;
            }
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

            String prevPkg = lastRecordedPackage;
            lastAppSwitchMs = now;
            lastRecordedPackage = pkg;

            AssistantSession.setRecordingContextPackage(pkg);

            if (isLauncherPackage(prevPkg) && !isLauncherPackage(pkg) && !isSystemUiPackage(pkg)
                    && !isLauncherIntermediatePackage(pkg)
                    && !RecordEventFilter.wasRecentTouchGesture()
                    // 修复：桌面点击图标时 VIEW_CLICKED 已记录 click 步骤，
                    // 紧随的 WINDOW_STATE_CHANGED 产生 open_app 步骤，导致同一操作生成两个步骤。
                    // 如果最近 500ms 内已有 click 步骤，跳过 open_app（语义重复）。
                    && (now - lastDirectClickMs > 500)) {
                JSONObject payload = new JSONObject();
                payload.put("ts", now);
                payload.put("type", "open_app");
                payload.put("package", pkg);
                payload.put("app_label", resolveAppLabelLocal(pkg));
                payload.put("description", "打开应用[" + resolveAppLabelLocal(pkg) + "]");
                payload.put("source", "a11y_switch");
                // 携带最近触摸坐标，确保回放时可以定位到图标位置
                if (lastTouchX > 0 && lastTouchY > 0) {
                    payload.put("x", lastTouchX);
                    payload.put("y", lastTouchY);
                    Context appCtx = AssistantApplicationHolder.get();
                    if (appCtx != null) {
                        android.util.DisplayMetrics dm = appCtx.getResources().getDisplayMetrics();
                        payload.put("screen_width", dm.widthPixels);
                        payload.put("screen_height", dm.heightPixels);
                    }
                }
                PluginHttpServer.enqueueStep(payload);
                // 重置触摸坐标，避免被后续 open_app 复用
                lastTouchX = 0;
                lastTouchY = 0;
            }
        } catch (Exception ignored) {
        }
    }

    /** 判断是否为桌面/启动器包名。 */
    private boolean isLauncherPackage(String pkg) {
        if (pkg == null || pkg.isEmpty()) return true;
        return pkg.contains("launcher") || pkg.contains("home")
                || pkg.contains("trebuchet") || pkg.contains("pixel")
                || "com.sec.android.app.launcher".equals(pkg)
                || "com.huawei.android.launcher".equals(pkg)
                || "com.miui.home".equals(pkg)
                || "com.oppo.launcher".equals(pkg)
                || "com.vivo.launcher".equals(pkg)
                || "com.google.android.apps.nexuslauncher".equals(pkg)
                || "com.android.launcher3".equals(pkg);
    }

    /** 判断是否为桌面/启动器相关的中间页面（如 vivo 速览、负一屏等）。 */
    private boolean isLauncherIntermediatePackage(String pkg) {
        if (pkg == null || pkg.isEmpty()) return false;
        // vivo 速览/负一屏/全局搜索等桌面附属页面
        return pkg.contains("vivo.globalsearch")
                || pkg.contains("vivo.launcher")
                || pkg.contains("vivo.tamingshen")
                || pkg.contains("com.bbk.launcher")
                || pkg.contains("com.iqoo.launcher")
                || pkg.contains("smartwake")
                || pkg.contains("miniclock")
                || pkg.contains("easyshare")
                // 通用：桌面小组件/快捷方式容器
                || pkg.contains("widget")
                || pkg.contains("shortcut")
                || pkg.contains("appwidget");
    }

    private boolean isSystemUiPackage(String pkg) {
        if (pkg == null) return false;
        return "com.android.systemui".equals(pkg)
                || "android".equals(pkg)
                || pkg.contains("permissioncontroller");
    }

    private String resolveAppLabelLocal(String packageName) {
        if (packageName == null || packageName.isEmpty()) return "未知应用";
        try {
            PackageManager pm = getPackageManager();
            android.content.pm.ApplicationInfo info = pm.getApplicationInfo(packageName, 0);
            CharSequence label = pm.getApplicationLabel(info);
            if (label != null && label.length() > 0) {
                String labelText = label.toString();
                if (!labelText.equals(packageName)) {
                    return labelText;
                }
            }
            // 回退：尝试从 launcher Activity 的 label 获取
            Intent launchIntent = pm.getLaunchIntentForPackage(packageName);
            if (launchIntent != null && launchIntent.getComponent() != null) {
                android.content.pm.ActivityInfo ai = pm.getActivityInfo(
                        launchIntent.getComponent(), 0);
                if (ai != null) {
                    CharSequence actLabel = ai.loadLabel(pm);
                    if (actLabel != null && actLabel.length() > 0
                            && !actLabel.toString().equals(packageName)) {
                        return actLabel.toString();
                    }
                }
            }
        } catch (Exception ignored) {
        }
        return packageName;
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

    // --- 以下为回用手势注入方法（回放引擎使用） ---
    // 已移除：Cover 拦截注入流水线、getevent 旁路监听
    // 录制完全通过 AccessibilityService 事件捕获

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


