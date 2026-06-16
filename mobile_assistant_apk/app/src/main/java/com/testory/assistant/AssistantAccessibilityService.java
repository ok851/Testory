package com.testory.assistant;

import android.accessibilityservice.AccessibilityService;
import android.accessibilityservice.GestureDescription;
import android.graphics.Bitmap;
import android.graphics.Path;
import android.graphics.Rect;
import android.os.Build;
import android.os.Bundle;
import android.view.Display;
import android.view.accessibility.AccessibilityEvent;
import android.view.accessibility.AccessibilityNodeInfo;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

/**
 * 无障碍服务：录制点击/滑动/输入，经本地 JSON-RPC 队列供 Agent 轮询。
 */
public class AssistantAccessibilityService extends AccessibilityService {

    private String armedMode = AssistantSession.MODE_IDLE;
    private long lastScrollEventMs = 0L;

    @Override
    public void onAccessibilityEvent(AccessibilityEvent event) {
        if (event == null || AssistantSession.MODE_IDLE.equals(armedMode)) return;

        int type = event.getEventType();
        if (type == AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED) {
            handleDialog(event);
            return;
        }
        if (type != AccessibilityEvent.TYPE_VIEW_CLICKED
                && type != AccessibilityEvent.TYPE_VIEW_LONG_CLICKED
                && type != AccessibilityEvent.TYPE_VIEW_SCROLLED
                && type != AccessibilityEvent.TYPE_VIEW_TEXT_CHANGED) {
            return;
        }

        try {
            JSONObject payload = new JSONObject();
            payload.put("ts", System.currentTimeMillis());

            if (type == AccessibilityEvent.TYPE_VIEW_SCROLLED) {
                if (System.currentTimeMillis() - lastScrollEventMs < 400) return;
                lastScrollEventMs = System.currentTimeMillis();
                payload.put("type", "swipe");
                payload.put("description", describeScroll(event));
            } else if (type == AccessibilityEvent.TYPE_VIEW_TEXT_CHANGED) {
                if (!AssistantSession.MODE_RECORD.equals(armedMode)) return;
                payload.put("type", "input");
                CharSequence text = event.getText() != null && !event.getText().isEmpty()
                        ? event.getText().get(0) : null;
                String raw = text != null ? text.toString() : "";
                payload.put("text", maskIfSensitive(event, raw));
            } else if (type == AccessibilityEvent.TYPE_VIEW_LONG_CLICKED) {
                payload.put("type", "long-press");
            } else {
                payload.put("type", AssistantSession.MODE_CAPTURE.equals(armedMode) ? "capture" : "click");
            }

            AccessibilityNodeInfo src = event.getSource();
            Rect bounds = null;
            if (src != null) {
                payload.put("node", nodeToJson(src));
                bounds = new Rect();
                src.getBoundsInScreen(bounds);
                payload.put("bounds", rectToJson(bounds));
                src.recycle();
            }

            if (AssistantSession.MODE_CAPTURE.equals(armedMode) && bounds != null) {
                HighlightOverlay.show(this, bounds);
            }

            PluginHttpServer.enqueueStep(payload);
        } catch (Exception ignored) {
        }
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
            PluginHttpServer.enqueueStep(payload);
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
            HighlightOverlay.hide();
        }
    }

    boolean performTap(String selectorType, String selectorValue, int x, int y) {
        AccessibilityNodeInfo node = findNode(selectorType, selectorValue);
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
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.N) return false;
        Path path = new Path();
        path.moveTo(x1, y1);
        path.lineTo(x2, y2);
        GestureDescription.StrokeDescription stroke =
                new GestureDescription.StrokeDescription(path, 0, 280);
        GestureDescription gesture = new GestureDescription.Builder().addStroke(stroke).build();
        return dispatchGesture(gesture, null, null);
    }

    boolean performInput(String selectorType, String selectorValue, String text) {
        AccessibilityNodeInfo node = findNode(selectorType, selectorValue);
        if (node == null) return false;
        Bundle args = new Bundle();
        args.putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text);
        boolean ok = node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args);
        node.recycle();
        return ok;
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

    private boolean dispatchTap(int x, int y) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.N) return false;
        Path path = new Path();
        path.moveTo(x, y);
        GestureDescription.StrokeDescription stroke =
                new GestureDescription.StrokeDescription(path, 0, 50);
        GestureDescription gesture = new GestureDescription.Builder().addStroke(stroke).build();
        return dispatchGesture(gesture, null, null);
    }

    private AccessibilityNodeInfo findNode(String selectorType, String selectorValue) {
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
        AssistantSession.bindService(this);
        PluginHttpServer.start(this);
        onArmedModeChanged(AssistantSession.getArmedMode());
    }

    @Override
    public void onDestroy() {
        AssistantSession.unbindService(this);
        HighlightOverlay.hide();
        PluginHttpServer.stop();
        super.onDestroy();
    }
}
