package com.testory.assistant;

import android.view.accessibility.AccessibilityEvent;

import org.json.JSONArray;
import org.json.JSONObject;

/** 录制/回放时过滤助手自身 UI 与重复噪声事件。 */
final class RecordEventFilter {

    private static final String ASSISTANT_PKG = "com.testory.assistant";
    private static final String[] ASSISTANT_UI_TEXT = {
            "开始录制", "停止录制", "运行用例", "配对", "同步用例", "保存到 PC", "看屏生成用例",
            "暂停", "结束", "录制中", "已暂停", "开启无障碍", "Testory Assistant",
            "本地录制草稿", "全部项目", "本地录制",
    };
    private static final String[] ASSISTANT_VIEW_IDS = {
            "btnStartRecord", "btnStopRecord", "btnRunCase", "btnPair",
            "btnSyncCases", "btnSaveToPc", "btnVisionProbe", "openAccessibilityBtn",
    };

    private static long lastAcceptedMs;
    private static String lastAcceptedKey = "";

    private RecordEventFilter() {
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
        JSONArray bounds = payload.optJSONArray("bounds");
        if (bounds == null || bounds.length() < 4) {
            String type = payload.optString("type", "");
            if ("open_app".equals(type) || "press_home".equals(type) || "press_back".equals(type)) {
                return isDuplicate(payload);
            }
            if ("click".equals(type) || "long-press".equals(type) || "capture".equals(type)) {
                return true;
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
        if (spec == null && "viewport_coord".equals(step.optString("selector_type"))) {
            return "点击".equals(desc) || desc.endsWith("点击");
        }
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
        JSONArray bounds = payload.optJSONArray("bounds");
        String key = type + "|" + (bounds != null ? bounds.toString() : "")
                + "|" + payload.optJSONObject("node");
        if (now - lastAcceptedMs < 700 && key.equals(lastAcceptedKey)) {
            return true;
        }
        lastAcceptedMs = now;
        lastAcceptedKey = key;
        return false;
    }

    static void resetDedupe() {
        lastAcceptedMs = 0L;
        lastAcceptedKey = "";
    }
}
