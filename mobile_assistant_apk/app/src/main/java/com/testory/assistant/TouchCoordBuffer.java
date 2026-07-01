package com.testory.assistant;

import android.graphics.Rect;

import org.json.JSONArray;

/**
 * Adapted from SoloPi EventProxy (Apache 2.0): https://github.com/alipay/SoloPi
 * 原缺陷：纯无障碍事件常无 bounds，点击被 RecordEventFilter 丢弃。
 * 新方案：缓存最近触摸坐标，与 A11y 事件在 80ms 内融合补全 bounds。
 * 扩展：跟踪一次触摸交互起止点，在无 TYPE_VIEW_SCROLLED 时仍能生成滑动步骤。
 */
final class TouchCoordBuffer {

    private static final long MATCH_WINDOW_MS = 80;
    private static final int TAP_RADIUS_PX = 24;
    private static final int MIN_SWIPE_PX = 40;

    private static long lastTouchMs;
    private static int lastX;
    private static int lastY;
    private static int interactionStartX;
    private static int interactionStartY;
    private static long interactionStartMs;

    private TouchCoordBuffer() {
    }

    static void recordTouch(int x, int y) {
        if (x < 0 && y < 0) return;
        lastTouchMs = System.currentTimeMillis();
        lastX = x;
        lastY = y;
    }

    /** 触摸交互开始（TYPE_TOUCH_INTERACTION_START）时记录起点。 */
    static void beginInteraction(int x, int y) {
        interactionStartMs = System.currentTimeMillis();
        if (x > 0 || y > 0) {
            interactionStartX = x;
            interactionStartY = y;
            recordTouch(x, y);
        } else if (lastTouchMs > 0) {
            interactionStartX = lastX;
            interactionStartY = lastY;
        } else {
            interactionStartX = 0;
            interactionStartY = 0;
        }
    }

    /**
     * 触摸交互结束：若位移超过阈值则返回 swipe payload，供桌面端生成滑动步骤。
     * 原缺陷：仅依赖 TYPE_VIEW_SCROLLED，桌面/空白区域滑动无法被捕获。
     */
    static org.json.JSONObject finishInteraction(int endX, int endY) {
        int x2 = endX > 0 ? endX : lastX;
        int y2 = endY > 0 ? endY : lastY;
        int x1 = interactionStartX > 0 || interactionStartY > 0
                ? interactionStartX : lastX;
        int y1 = interactionStartX > 0 || interactionStartY > 0
                ? interactionStartY : lastY;
        interactionStartMs = 0L;
        interactionStartX = 0;
        interactionStartY = 0;
        if (x1 <= 0 && y1 <= 0 && x2 <= 0 && y2 <= 0) return null;
        int dx = Math.abs(x2 - x1);
        int dy = Math.abs(y2 - y1);
        if (dx < MIN_SWIPE_PX && dy < MIN_SWIPE_PX) return null;
        try {
            org.json.JSONObject payload = new org.json.JSONObject();
            payload.put("ts", System.currentTimeMillis());
            payload.put("type", "swipe");
            payload.put("x1", x1);
            payload.put("y1", y1);
            payload.put("x2", x2);
            payload.put("y2", y2);
            payload.put("description", "滑动 (" + x1 + "," + y1 + ")→(" + x2 + "," + y2 + ")");
            org.json.JSONArray bounds = new org.json.JSONArray();
            bounds.put(Math.min(x1, x2)).put(Math.min(y1, y2));
            bounds.put(Math.max(x1, x2)).put(Math.max(y1, y2));
            payload.put("bounds", bounds);
            return payload;
        } catch (Exception ignored) {
            return null;
        }
    }

    static void reset() {
        lastTouchMs = 0L;
        lastX = 0;
        lastY = 0;
        interactionStartMs = 0L;
        interactionStartX = 0;
        interactionStartY = 0;
        TouchGestureClassifier.get().reset();
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

    static int getLastX() {
        return lastX;
    }

    static int getLastY() {
        return lastY;
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
