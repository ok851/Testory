package com.testory.assistant;

import android.view.accessibility.AccessibilityNodeInfo;

import org.json.JSONArray;
import org.json.JSONObject;

/**
 * SoloPi OperationNodeLocator 子集：多信号打分匹配 live 节点。
 */
final class OperationNodeLocator {

    private OperationNodeLocator() {
    }

    static AccessibilityNodeInfo findLiveNode(
            AssistantAccessibilityService svc, JSONObject step) {
        if (svc == null || step == null) return null;
        JSONObject spec = step.optJSONObject("mobile_spec");
        JSONObject opNode = null;
        if (spec != null && spec.has("operation_node")) {
            opNode = spec.optJSONObject("operation_node");
        }
        if (opNode == null) {
            opNode = step.optJSONObject("operation_node");
        }
        AccessibilityNodeInfo root = svc.getRootInActiveWindow();
        if (root == null) return null;
        if (opNode == null) {
            root.recycle();
            return null;
        }
        AccessibilityNodeInfo best = null;
        int[] bestScore = new int[]{0};
        best = scoreSubtree(root, opNode, best, bestScore);
        root.recycle();
        if (bestScore[0] <= 0) {
            if (best != null) best.recycle();
            return null;
        }
        return best;
    }

    private static AccessibilityNodeInfo scoreSubtree(
            AccessibilityNodeInfo node,
            JSONObject recorded,
            AccessibilityNodeInfo best,
            int[] bestScoreRef) {
        if (node == null) return best;
        int score = scoreNode(node, recorded);
        if (score > bestScoreRef[0]) {
            if (best != null) best.recycle();
            best = AccessibilityNodeInfo.obtain(node);
            bestScoreRef[0] = score;
        }
        for (int i = 0; i < node.getChildCount(); i++) {
            AccessibilityNodeInfo child = node.getChild(i);
            if (child == null) continue;
            best = scoreSubtree(child, recorded, best, bestScoreRef);
            child.recycle();
        }
        return best;
    }

    private static int scoreNode(AccessibilityNodeInfo live, JSONObject recorded) {
        int score = 0;
        String rid = recorded.optString("resource_id", "");
        if (!rid.isEmpty() && rid.equals(live.getViewIdResourceName())) score += 2;
        String text = recorded.optString("text", "");
        if (!text.isEmpty() && live.getText() != null && text.contentEquals(live.getText())) {
            score += 2;
        }
        String desc = recorded.optString("content_desc", "");
        if (!desc.isEmpty() && live.getContentDescription() != null
                && desc.contentEquals(live.getContentDescription())) {
            score += 2;
        }
        String xpath = recorded.optString("xpath", "");
        if (!xpath.isEmpty()) {
            String liveXpath = AccessibilityXpathBuilder.buildXpath(live);
            if (xpath.equals(liveXpath)) score += 1;
        }
        JSONArray assistants = recorded.optJSONArray("assistant_nodes");
        if (assistants != null) {
            for (int i = 0; i < assistants.length(); i++) {
                JSONObject a = assistants.optJSONObject(i);
                if (a == null) continue;
                if (matchesAssistant(live, a)) score += 2;
            }
        }
        return score;
    }

    private static boolean matchesAssistant(AccessibilityNodeInfo live, JSONObject a) {
        String t = a.optString("text", "");
        if (!t.isEmpty() && live.getText() != null && t.contentEquals(live.getText())) return true;
        String d = a.optString("content_desc", "");
        if (!d.isEmpty() && live.getContentDescription() != null
                && d.contentEquals(live.getContentDescription())) return true;
        String rid = a.optString("resource_id", "");
        return !rid.isEmpty() && rid.equals(live.getViewIdResourceName());
    }

    static int[] resolveTapFromStep(AssistantAccessibilityService svc, JSONObject step) {
        JSONObject spec = step.optJSONObject("mobile_spec");
        AccessibilityNodeInfo live = findLiveNode(svc, step);
        if (live != null) {
            try {
                android.graphics.Rect r = NodeLocatorHelper.boundsOf(live);
                if (r != null && r.width() > 0 && r.height() > 0) {
                    double nrx = spec != null ? spec.optDouble("node_rx", -1) : -1;
                    double nry = spec != null ? spec.optDouble("node_ry", -1) : -1;
                    if (nrx < 0 && spec != null && spec.has("local_click_pos")) {
                        JSONObject lc = spec.optJSONObject("local_click_pos");
                        if (lc != null) {
                            nrx = lc.optDouble("rx", -1);
                            nry = lc.optDouble("ry", -1);
                        }
                    }
                    if (nrx >= 0 && nry >= 0) {
                        int x = r.left + (int) Math.round(nrx * r.width());
                        int y = r.top + (int) Math.round(nry * r.height());
                        return new int[]{x, y};
                    }
                    return new int[]{r.centerX(), r.centerY()};
                }
            } finally {
                live.recycle();
            }
        }
        return new int[]{0, 0};
    }
}
