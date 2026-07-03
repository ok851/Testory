package com.testory.assistant;

import android.accessibilityservice.AccessibilityService;
import android.graphics.Rect;
import android.util.DisplayMetrics;
import android.view.accessibility.AccessibilityNodeInfo;

import org.json.JSONArray;
import org.json.JSONObject;

/**
 * SoloPi OperationStepExporter 子集：Cover 触摸 → OperationNode JSON。
 */
final class OperationNodeExporter {

    private OperationNodeExporter() {
    }

    static JSONObject exportTouchAction(
            AccessibilityService svc,
            String type,
            int x,
            int y,
            int x1,
            int y1,
            int x2,
            int y2,
            long durationMs) throws Exception {
        JSONObject payload = new JSONObject();
        payload.put("ts", System.currentTimeMillis());
        payload.put("type", type);
        payload.put("source", "cover");
        payload.put("x", x);
        payload.put("y", y);
        if ("swipe".equals(type)) {
            payload.put("x1", x1);
            payload.put("y1", y1);
            payload.put("x2", x2);
            payload.put("y2", y2);
            payload.put("description", "滑动 (" + x1 + "," + y1 + ")→(" + x2 + "," + y2 + ")");
            if (durationMs > 0) payload.put("action_duration_ms", durationMs);
        } else if ("long-press".equals(type) || "long_press".equals(type)) {
            payload.put("description", "长按 (" + x + "," + y + ")");
        } else {
            payload.put("description", "点击 (" + x + "," + y + ")");
        }

        AccessibilityNodeInfo deepest = NodeLocatorHelper.findDeepestNodeAt(svc, x, y);
        if (deepest != null) {
            try {
                JSONObject opNode = NodeLocatorHelper.exportOperationNode(deepest);
                payload.put("operation_node", opNode);
                payload.put("node", opNode);
                Rect bounds = NodeLocatorHelper.boundsOf(deepest);
                if (bounds != null) {
                    payload.put("bounds", NodeLocatorHelper.rectToJson(bounds));
                    NodeLocatorHelper.applyBoundsRelative(payload, x, y, bounds);
                }
            } finally {
                deepest.recycle();
            }
        }

        JSONObject localClick = new JSONObject();
        localClick.put("rx", payload.optDouble("node_rx", 0));
        localClick.put("ry", payload.optDouble("node_ry", 0));
        payload.put("local_click_pos", localClick);

        DisplayMetrics dm = svc.getResources().getDisplayMetrics();
        JSONObject screen = new JSONObject();
        screen.put("width", dm.widthPixels);
        screen.put("height", dm.heightPixels);
        payload.put("screen_size", screen);

        String pkg = svc instanceof AssistantAccessibilityService
                ? ((AssistantAccessibilityService) svc).getForegroundPackage() : "";
        if (pkg != null && !pkg.isEmpty()) {
            payload.put("package", pkg);
            // 动态更新录制上下文：用户实际操作的应用才是正确的 context_package
            if (!"com.testory.assistant".equals(pkg) && AssistantSession.MODE_RECORD.equals(AssistantSession.getArmedMode())) {
                AssistantSession.setRecordingContextPackage(pkg);
            }
        }
        return payload;
    }
}
