package com.testory.assistant;

import android.accessibilityservice.AccessibilityService;
import android.graphics.Rect;
import android.view.accessibility.AccessibilityNodeInfo;

import org.json.JSONArray;
import org.json.JSONObject;

/**
 * SoloPi PositionLocator 子集：最小面积最深节点 + xpath + assistant_nodes。
 */
final class NodeLocatorHelper {

    private NodeLocatorHelper() {
    }

    static AccessibilityNodeInfo findDeepestNodeAt(AccessibilityService svc, int x, int y) {
        if (svc == null) return null;
        AccessibilityNodeInfo root = svc.getRootInActiveWindow();
        if (root == null) return null;
        AccessibilityNodeInfo hit = findSmallestDeepest(root, x, y);
        root.recycle();
        return hit;
    }

    static void enrichPayload(AccessibilityService svc, JSONObject payload, int x, int y) {
        if (payload == null || svc == null) return;
        if (x < 0 || y < 0) return;
        AccessibilityNodeInfo deepest = findDeepestNodeAt(svc, x, y);
        if (deepest == null) return;
        try {
            JSONObject opNode = exportOperationNode(deepest);
            payload.put("operation_node", opNode);
            payload.put("node", opNode);
            Rect bounds = boundsOf(deepest);
            if (bounds != null) {
                payload.put("bounds", rectToJson(bounds));
                applyBoundsRelative(payload, x, y, bounds);
            }
            payload.put("x", x);
            payload.put("y", y);
        } catch (Exception ignored) {
        } finally {
            deepest.recycle();
        }
    }

    static JSONObject exportOperationNode(AccessibilityNodeInfo n) throws Exception {
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
            o.put("class_name", n.getClassName().toString());
            o.put("class", n.getClassName().toString());
        }
        CharSequence pkg = n.getPackageName();
        if (pkg != null) o.put("package_name", pkg.toString());
        o.put("xpath", AccessibilityXpathBuilder.buildXpath(n));
        o.put("depth", depthOf(n));
        Rect r = boundsOf(n);
        if (r != null) o.put("bounds", rectToJson(r));
        JSONArray assistants = collectAssistantNodes(n);
        if (assistants.length() > 0) o.put("assistant_nodes", assistants);
        return o;
    }

    static Rect boundsOf(AccessibilityNodeInfo n) {
        if (n == null) return null;
        Rect r = new Rect();
        n.getBoundsInScreen(r);
        return r;
    }

    static JSONArray rectToJson(Rect r) {
        JSONArray arr = new JSONArray();
        arr.put(r.left);
        arr.put(r.top);
        arr.put(r.right);
        arr.put(r.bottom);
        return arr;
    }

    static void applyBoundsRelative(JSONObject payload, int x, int y, Rect bounds)
            throws Exception {
        if (bounds == null || bounds.width() <= 0 || bounds.height() <= 0) return;
        double nrx = (x - bounds.left) / (double) bounds.width();
        double nry = (y - bounds.top) / (double) bounds.height();
        nrx = Math.max(0, Math.min(1, nrx));
        nry = Math.max(0, Math.min(1, nry));
        payload.put("node_rx", Math.round(nrx * 10000.0) / 10000.0);
        payload.put("node_ry", Math.round(nry * 10000.0) / 10000.0);
        JSONObject local = new JSONObject();
        local.put("rx", payload.getDouble("node_rx"));
        local.put("ry", payload.getDouble("node_ry"));
        payload.put("local_click_pos", local);
    }

    /** SoloPi: 命中点下面积最小的最深节点。 */
    private static AccessibilityNodeInfo findSmallestDeepest(
            AccessibilityNodeInfo node, int x, int y) {
        if (node == null) return null;
        Rect r = new Rect();
        node.getBoundsInScreen(r);
        if (!r.contains(x, y)) return null;

        AccessibilityNodeInfo best = AccessibilityNodeInfo.obtain(node);
        int bestArea = r.width() * r.height();

        for (int i = 0; i < node.getChildCount(); i++) {
            AccessibilityNodeInfo child = node.getChild(i);
            if (child == null) continue;
            AccessibilityNodeInfo hit = findSmallestDeepest(child, x, y);
            child.recycle();
            if (hit == null) continue;
            Rect hr = new Rect();
            hit.getBoundsInScreen(hr);
            int area = hr.width() * hr.height();
            if (area <= bestArea) {
                best.recycle();
                best = hit;
                bestArea = area;
            } else {
                hit.recycle();
            }
        }
        return best;
    }

    private static int depthOf(AccessibilityNodeInfo node) {
        int d = 0;
        AccessibilityNodeInfo cur = AccessibilityNodeInfo.obtain(node);
        while (cur != null) {
            AccessibilityNodeInfo p = cur.getParent();
            cur.recycle();
            if (p == null) break;
            d++;
            cur = p;
        }
        return d;
    }

    private static JSONArray collectAssistantNodes(AccessibilityNodeInfo node) throws Exception {
        JSONArray arr = new JSONArray();
        collectAssistantsRecursive(node, arr, 0, 3);
        return arr;
    }

    private static void collectAssistantsRecursive(
            AccessibilityNodeInfo node, JSONArray arr, int depth, int maxDepth) throws Exception {
        if (node == null || depth > maxDepth || arr.length() >= 6) return;
        for (int i = 0; i < node.getChildCount(); i++) {
            AccessibilityNodeInfo child = node.getChild(i);
            if (child == null) continue;
            if (isUsableForLocating(child)) {
                JSONObject a = new JSONObject();
                if (child.getClassName() != null) a.put("class", child.getClassName().toString());
                if (child.getViewIdResourceName() != null) {
                    a.put("resource_id", child.getViewIdResourceName());
                }
                if (child.getText() != null) a.put("text", child.getText().toString());
                if (child.getContentDescription() != null) {
                    a.put("content_desc", child.getContentDescription().toString());
                }
                a.put("depth_delta", depth + 1);
                arr.put(a);
            }
            collectAssistantsRecursive(child, arr, depth + 1, maxDepth);
            child.recycle();
        }
    }

    private static boolean isUsableForLocating(AccessibilityNodeInfo n) {
        if (n == null || !n.isVisibleToUser()) return false;
        CharSequence t = n.getText();
        CharSequence d = n.getContentDescription();
        String rid = n.getViewIdResourceName();
        return (t != null && t.length() > 0)
                || (d != null && d.length() > 0)
                || (rid != null && rid.length() > 0);
    }
}
