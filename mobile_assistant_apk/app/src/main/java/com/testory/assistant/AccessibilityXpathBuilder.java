package com.testory.assistant;

import android.graphics.Rect;
import android.view.accessibility.AccessibilityNodeInfo;

import java.util.ArrayList;
import java.util.List;

/** SoloPi 风格 xpath：/ClassName[index]/ClassName… */
final class AccessibilityXpathBuilder {

    private AccessibilityXpathBuilder() {
    }

    static String buildXpath(AccessibilityNodeInfo node) {
        if (node == null) return "";
        List<AccessibilityNodeInfo> chain = new ArrayList<>();
        AccessibilityNodeInfo cur = AccessibilityNodeInfo.obtain(node);
        try {
            while (cur != null) {
                chain.add(0, AccessibilityNodeInfo.obtain(cur));
                AccessibilityNodeInfo parent = cur.getParent();
                cur.recycle();
                cur = parent;
            }
            StringBuilder sb = new StringBuilder();
            for (int i = 0; i < chain.size(); i++) {
                AccessibilityNodeInfo n = chain.get(i);
                int index = i > 0 ? indexAmongSiblings(chain.get(i - 1), n) : -1;
                appendSegment(n, sb, index);
            }
            return sb.length() > 0 ? sb.toString() : "/";
        } finally {
            for (AccessibilityNodeInfo n : chain) {
                if (n != null) n.recycle();
            }
        }
    }

    private static void appendSegment(AccessibilityNodeInfo node, StringBuilder sb, int index) {
        CharSequence cls = node.getClassName();
        String name = cls != null ? cls.toString() : "node";
        int dot = name.lastIndexOf('.');
        if (dot >= 0) name = name.substring(dot + 1);
        sb.append('/').append(name);
        if (index > 0) sb.append('[').append(index).append(']');
    }

    private static int indexAmongSiblings(AccessibilityNodeInfo parent, AccessibilityNodeInfo target) {
        if (parent == null || target == null) return -1;
        CharSequence targetClass = target.getClassName();
        int idx = 0;
        int sameCount = 0;
        for (int i = 0; i < parent.getChildCount(); i++) {
            AccessibilityNodeInfo child = parent.getChild(i);
            if (child == null) continue;
            try {
                CharSequence cc = child.getClassName();
                boolean sameClass = (targetClass == null && cc == null)
                        || (targetClass != null && targetClass.equals(cc));
                if (sameClass) {
                    sameCount++;
                    if (sameNode(child, target)) {
                        idx = sameCount;
                    }
                }
            } finally {
                child.recycle();
            }
        }
        return sameCount > 1 ? idx : -1;
    }

    private static boolean sameNode(AccessibilityNodeInfo a, AccessibilityNodeInfo b) {
        if (a == null || b == null) return false;
        Rect ra = new Rect();
        Rect rb = new Rect();
        a.getBoundsInScreen(ra);
        b.getBoundsInScreen(rb);
        if (!ra.equals(rb)) return false;
        CharSequence ca = a.getClassName();
        CharSequence cb = b.getClassName();
        if (ca != null ? !ca.equals(cb) : cb != null) return false;
        String raId = a.getViewIdResourceName();
        String rbId = b.getViewIdResourceName();
        return raId == null ? rbId == null : raId.equals(rbId);
    }
}
