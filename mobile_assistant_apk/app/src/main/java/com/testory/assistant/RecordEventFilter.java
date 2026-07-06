package com.testory.assistant;

import android.view.accessibility.AccessibilityEvent;

import org.json.JSONArray;
import org.json.JSONObject;

/** 录制/回放时过滤助手自身 UI 与重复噪声事件。 */
final class RecordEventFilter {

    private static final String ASSISTANT_PKG = "com.testory.assistant";
    private static final long MERGE_WINDOW_MS = 30;
    private static final long DEDUP_WINDOW_MS = 60;
    private static final int MIN_SWIPE_DELTA_PX = 30;
    private static final String[] ASSISTANT_UI_TEXT = {
            "暂停", "结束", "录制中", "已暂停", "开启无障碍", "Testory Assistant",
            "本地录制草稿", "全部项目", "本地录制",
    };
    private static final String[] ASSISTANT_VIEW_IDS = {
            "btnStartRecord", "btnStopRecord", "btnRunCase", "btnPair",
    };

    private static long lastAcceptedMs;
    private static String lastAcceptedKey = "";

    private static long lastTouchGestureMs;
    private static long lastTouchSwipeMs;
    private static String lastTouchGestureKey = "";
    /** getevent 与 a11y 事件的时序差可能超过 180ms，增加到 300ms */
    private static final long TOUCH_GESTURE_SUPPRESS_MS = 300;

    private RecordEventFilter() {
    }

    /** 触摸管线已产出步骤时标记，抑制紧随其后的 VIEW_CLICKED 重复录制。 */
    static void markTouchGesture(JSONObject payload) {
        if (payload == null) return;
        long now = System.currentTimeMillis();
        lastTouchGestureMs = now;
        String type = payload.optString("type", "");
        if ("swipe".equals(type) || "scroll".equals(type)) {
            lastTouchSwipeMs = now;
        }
        int x = payload.optInt("x", 0);
        int y = payload.optInt("y", 0);
        lastTouchGestureKey = type + "|" + x + "," + y;
    }

    static boolean wasRecentTouchGesture() {
        return System.currentTimeMillis() - lastTouchGestureMs < TOUCH_GESTURE_SUPPRESS_MS;
    }

    static boolean wasRecentTouchSwipe() {
        return System.currentTimeMillis() - lastTouchSwipeMs < 120;
    }

    static void resetDedupe() {
        lastAcceptedMs = 0L;
        lastAcceptedKey = "";
        lastTouchGestureMs = 0L;
        lastTouchSwipeMs = 0L;
        lastTouchGestureKey = "";
    }

    static boolean shouldSkip(AccessibilityEvent event, JSONObject payload) {
        if (payload == null) return true;
        CharSequence pkg = event != null ? event.getPackageName() : null;
        if (pkg != null && ASSISTANT_PKG.contentEquals(pkg)) {
            return true;
        }
        if (isAssistantNode(payload.optJSONObject("node"))) {
            return true;
        }
        String type = payload.optString("type", "");
        if ("swipe".equals(type) || "scroll".equals(type)) {
            int dx = Math.abs(payload.optInt("scroll_delta_x", 0));
            int dy = Math.abs(payload.optInt("scroll_delta_y", 0));
            int x1 = payload.optInt("x1", 0);
            int y1 = payload.optInt("y1", 0);
            int x2 = payload.optInt("x2", 0);
            int y2 = payload.optInt("y2", 0);
            int gestureDx = Math.abs(x2 - x1);
            int gestureDy = Math.abs(y2 - y1);
            if (dx < MIN_SWIPE_DELTA_PX && dy < MIN_SWIPE_DELTA_PX
                    && gestureDx < MIN_SWIPE_DELTA_PX && gestureDy < MIN_SWIPE_DELTA_PX) {
                return true;
            }
        }
        JSONArray bounds = payload.optJSONArray("bounds");
        TouchCoordBuffer.applyToPayload(payload);
        bounds = payload.optJSONArray("bounds");
        if (bounds == null || bounds.length() < 4) {
            if ("open_app".equals(type) || "press_home".equals(type) || "press_back".equals(type)
                    || "swipe".equals(type) || "scroll".equals(type) || "input".equals(type)) {
                return isDuplicate(payload);
            }
            if ("click".equals(type) || "long-press".equals(type) || "capture".equals(type)) {
                // 原缺陷：无 bounds 且无坐标时 return true 直接丢弃，导致录制列表恒为空。
                // 新逻辑：有触摸坐标、有可描述节点、或兜底坐标占位均允许通过去重窗口。
                int x = payload.optInt("x", 0);
                int y = payload.optInt("y", 0);
                if (x > 0 || y > 0) {
                    return isDuplicate(payload);
                }
                JSONObject node = payload.optJSONObject("node");
                if (node != null) {
                    return isDuplicate(payload);
                }
                // 无 bounds、无坐标、无节点：用宽松窗口去重
                return isDuplicateLenient(payload);
            }
        }
        if (isDuplicate(payload)) {
            return true;
        }
        return false;
    }

    static boolean isAssistantStep(JSONObject step) {
        if (step == null) return true;
        String desc = step.optString("description", "");
        for (String t : ASSISTANT_UI_TEXT) {
            if (desc.contains(t)) return true;
        }
        String sv = step.optString("selector_value", "");
        for (String t : ASSISTANT_UI_TEXT) {
            if (sv.contains(t)) return true;
        }
        for (String id : ASSISTANT_VIEW_IDS) {
            if (sv.contains(id)) return true;
        }
        JSONObject spec = step.optJSONObject("mobile_spec");
        if (spec == null) return false;
        return false;
    }

    private static boolean isAssistantNode(JSONObject node) {
        if (node == null) return false;
        String rid = node.optString("resource_id", "");
        for (String id : ASSISTANT_VIEW_IDS) {
            if (rid.contains(id)) return true;
        }
        String text = node.optString("text", "");
        String desc = node.optString("content_desc", "");
        for (String t : ASSISTANT_UI_TEXT) {
            if (text.contains(t) || desc.contains(t)) return true;
        }
        return false;
    }

    private static boolean isDuplicate(JSONObject payload) {
        long now = payload.optLong("ts", System.currentTimeMillis());
        String type = payload.optString("type", "");
        String key = dedupeKey(payload);
        // 点击按坐标区分，避免 120ms 内连续点不同位置被误判为重复。
        long window;
        if ("click".equals(type) || "long-press".equals(type)) {
            window = key.equals(lastAcceptedKey) ? 25L : 60L;
        } else if ("swipe".equals(type) || "scroll".equals(type)) {
            // 滑动窗口极短：只合并同一手势的重复事件，不丢弃后续滑动
            window = key.equals(lastAcceptedKey) ? 20L : 30L;
        } else {
            window = key.equals(lastAcceptedKey) ? MERGE_WINDOW_MS : DEDUP_WINDOW_MS;
        }
        if (now - lastAcceptedMs < window && key.equals(lastAcceptedKey)) {
            return true;
        }
        lastAcceptedMs = now;
        lastAcceptedKey = key;
        return false;
    }

    /** 宽松去重：仅在极短时间窗口内跳过，适用于无坐标无节点事件。 */
    private static boolean isDuplicateLenient(JSONObject payload) {
        long now = payload.optLong("ts", System.currentTimeMillis());
        if (now - lastAcceptedMs < 60) {
            return true;
        }
        lastAcceptedMs = now;
        return false;
    }

    private static String dedupeKey(JSONObject payload) {
        String type = payload.optString("type", "");
        int x = payload.optInt("x", 0);
        int y = payload.optInt("y", 0);
        if ("click".equals(type) || "long-press".equals(type)) {
            if (x > 0 || y > 0) {
                return type + "|" + x + "," + y;
            }
            JSONArray bounds = payload.optJSONArray("bounds");
            if (bounds != null && bounds.length() >= 4) {
                int cx = (bounds.optInt(0, 0) + bounds.optInt(2, 0)) / 2;
                int cy = (bounds.optInt(1, 0) + bounds.optInt(3, 0)) / 2;
                return type + "|" + cx + "," + cy;
            }
            JSONObject node = payload.optJSONObject("node");
            if (node != null) {
                String rid = node.optString("resource_id", "");
                String text = node.optString("text", "");
                return type + "|node:" + rid + ":" + text;
            }
            return type + "|ts|" + (payload.optLong("ts", 0) / 200);
        }
        // 滑动用坐标起止点+时间戳区分，避免同区域连续滑动被误去重
        if ("swipe".equals(type) || "scroll".equals(type)) {
            int x1 = payload.optInt("x1", 0);
            int y1 = payload.optInt("y1", 0);
            int x2 = payload.optInt("x2", 0);
            int y2 = payload.optInt("y2", 0);
            return type + "|" + x1 + "," + y1 + "->" + x2 + "," + y2
                    + "|ts" + (payload.optLong("ts", 0) / 50);
        }
        JSONArray bounds = payload.optJSONArray("bounds");
        return type + "|" + (bounds != null ? bounds.toString() : "")
                + "|" + payload.optJSONObject("node");
    }
}