package com.testory.assistant;

import android.graphics.Rect;

import org.json.JSONArray;

/**
 * Adapted from SoloPi EventProxy (Apache 2.0): https://github.com/alipay/SoloPi
 * 原缺陷：纯无障碍事件常无 bounds，点击被 RecordEventFilter 丢弃。
 * 新方案：缓存最近触摸坐标，与 A11y 事件在 80ms 内融合补全 bounds。
 */
final class TouchCoordBuffer {

    private static final long MATCH_WINDOW_MS = 80;
    private static final int TAP_RADIUS_PX = 24;

    private static long lastTouchMs;
    private static int lastX;
    private static int lastY;

    private TouchCoordBuffer() {
    }

    static void recordTouch(int x, int y) {
        if (x <= 0 && y <= 0) return;
        lastTouchMs = System.currentTimeMillis();
        lastX = x;
        lastY = y;
    }

    static void reset() {
        lastTouchMs = 0L;
        lastX = 0;
        lastY = 0;
    }

    static JSONArray matchBoundsNear(long eventTs) {
        long ts = eventTs > 0 ? eventTs : System.currentTimeMillis();
        if (lastTouchMs <= 0 || Math.abs(ts - lastTouchMs) > MATCH_WINDOW_MS) {
            return null;
        }
        JSONArray arr = new JSONArray();
        arr.put(Math.max(0, lastX - TAP_RADIUS_PX));
        arr.put(Math.max(0, lastY - TAP_RADIUS_PX));
        arr.put(lastX + TAP_RADIUS_PX);
        arr.put(lastY + TAP_RADIUS_PX);
        return arr;
    }

    static void applyToPayload(org.json.JSONObject payload) {
        if (payload == null) return;
        if (payload.has("bounds")) return;
        JSONArray bounds = matchBoundsNear(payload.optLong("ts", System.currentTimeMillis()));
        if (bounds != null) {
            try {
                payload.put("bounds", bounds);
                payload.put("x", lastX);
                payload.put("y", lastY);
            } catch (Exception ignored) {
            }
        }
    }

    static Rect boundsFromSource(android.view.accessibility.AccessibilityNodeInfo src) {
        if (src == null) return null;
        Rect r = new Rect();
        src.getBoundsInScreen(r);
        if (r.width() > 0 || r.height() > 0) return r;
        // 部分机型节点 bounds 为 0，仍用中心点供坐标录制
        if (r.left != 0 || r.top != 0 || r.right != 0 || r.bottom != 0) {
            return r;
        }
        return null;
    }
}
