package com.testory.assistant;

import org.json.JSONArray;
import org.json.JSONObject;

/**
 * Adapted from SoloPi TouchWrapper (Apache 2.0): https://github.com/alipay/SoloPi
 * 将 TYPE_TOUCH_INTERACTION 起止点分类为 click / long-press / swipe。
 */
final class TouchGestureClassifier {

    /** 80px 内视为点击 (降低阈值，桌面/Launcher 小范围手势不应误判) */
    private static final int CLICK_RANGE_PX = 80;
    /** 500ms 以上视为长按 */
    private static final long LONG_CLICK_MS = 500;
    /** 最小滑动位移 (降低以适配桌面轻扫) */
    private static final int MIN_SWIPE_PX = 30;
    /** 有效点击最小时长，过滤注入回声/幽灵 UP */
    private static final long MIN_TAP_MS = 20;
    /** 无 MOVE 时最小位移才记为 click */
    private static final int MIN_TAP_MOVE_PX = 8;

    private int startX;
    private int startY;
    private int endX;
    private int endY;
    private long startMs;
    private long endMs;
    private boolean active;
    private boolean moved;

    private TouchGestureClassifier() {
    }

    private static final TouchGestureClassifier INSTANCE = new TouchGestureClassifier();

    static TouchGestureClassifier get() {
        return INSTANCE;
    }

    void reset() {
        active = false;
        moved = false;
        startX = startY = endX = endY = 0;
        startMs = endMs = 0L;
    }

    void onStart(int x, int y) {
        startMs = System.currentTimeMillis();
        startX = x;
        startY = y;
        endX = x;
        endY = y;
        active = true;
        moved = false;
        TouchCoordBuffer.recordTouch(x, y);
    }

    void onMove(int x, int y) {
        if (!active) return;
        endX = x;
        endY = y;
        if (Math.abs(x - startX) >= MIN_TAP_MOVE_PX || Math.abs(y - startY) >= MIN_TAP_MOVE_PX) {
            moved = true;
        }
        TouchCoordBuffer.recordTouch(x, y);
    }

    /** 返回 null 表示位移过小或无效触摸，忽略。 */
    JSONObject onEnd(int x, int y) {
        if (!active) return null;
        active = false;
        endMs = System.currentTimeMillis();
        if (x > 0 || y > 0) {
            endX = x;
            endY = y;
        }
        int dx = Math.abs(endX - startX);
        int dy = Math.abs(endY - startY);
        long duration = endMs - startMs;
        TouchCoordBuffer.recordTouch(endX, endY);

        if (dx < MIN_SWIPE_PX && dy < MIN_SWIPE_PX) {
            if (duration >= LONG_CLICK_MS) {
                return buildPointPayload("long-press", endX, endY, "长按 (" + endX + "," + endY + ")");
            }
            if (duration < MIN_TAP_MS) return null;
            // TYPE_TOUCH_INTERACTION events have no MOVE in between, so moved is always false for taps
            // A tap with zero displacement is valid - do not discard it
            // Only filter out truly zero-duration/noise events via MIN_TAP_MS
            return buildPointPayload("click", endX, endY, "点击 (" + endX + "," + endY + ")");
        }
        if (distance(startX, startY, endX, endY) < CLICK_RANGE_PX && duration >= LONG_CLICK_MS) {
            return buildPointPayload("long-press", endX, endY, "长按 (" + endX + "," + endY + ")");
        }
        return buildSwipePayload(startX, startY, endX, endY, duration);
    }

    private static double distance(int x1, int y1, int x2, int y2) {
        return Math.sqrt(Math.pow(x2 - x1, 2) + Math.pow(y2 - y1, 2) + 1);
    }

    private static JSONObject buildPointPayload(String type, int x, int y, String desc) {
        try {
            JSONObject payload = new JSONObject();
            payload.put("ts", System.currentTimeMillis());
            payload.put("type", type);
            payload.put("x", x);
            payload.put("y", y);
            payload.put("description", desc);
            payload.put("source", "touch");
            JSONArray bounds = new JSONArray();
            bounds.put(Math.max(0, x - 24)).put(Math.max(0, y - 24));
            bounds.put(x + 24).put(y + 24);
            payload.put("bounds", bounds);
            return payload;
        } catch (Exception ignored) {
            return null;
        }
    }

    private static JSONObject buildSwipePayload(int x1, int y1, int x2, int y2, long durationMs) {
        try {
            JSONObject payload = new JSONObject();
            payload.put("ts", System.currentTimeMillis());
            payload.put("type", "swipe");
            payload.put("x1", x1);
            payload.put("y1", y1);
            payload.put("x2", x2);
            payload.put("y2", y2);
            payload.put("description", "滑动 (" + x1 + "," + y1 + ")→(" + x2 + "," + y2 + ")");
            payload.put("source", "touch");
            payload.put("action_duration_ms", durationMs);
            JSONArray bounds = new JSONArray();
            bounds.put(Math.min(x1, x2)).put(Math.min(y1, y2));
            bounds.put(Math.max(x1, x2)).put(Math.max(y1, y2));
            payload.put("bounds", bounds);
            return payload;
        } catch (Exception ignored) {
            return null;
        }
    }
}
