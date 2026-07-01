package com.testory.assistant;

import android.view.accessibility.AccessibilityNodeInfo;

import java.util.Arrays;
import java.util.List;

/** 回放前轻量弹窗处理（允许/确定）。 */
final class DialogDismissHelper {

    private static final List<String> DISMISS_TEXTS = Arrays.asList(
            "允许", "确定", "同意", "OK", "Allow", "Accept", "继续", "知道了"
    );

    private DialogDismissHelper() {
    }

    static boolean tryDismiss(AssistantAccessibilityService svc) {
        if (svc == null) return false;
        AccessibilityNodeInfo root = svc.getRootInActiveWindow();
        if (root == null) return false;
        try {
            for (String label : DISMISS_TEXTS) {
                List<AccessibilityNodeInfo> nodes = root.findAccessibilityNodeInfosByText(label);
                if (nodes == null) continue;
                for (AccessibilityNodeInfo n : nodes) {
                    if (n == null) continue;
                    if (n.isClickable() && n.performAction(AccessibilityNodeInfo.ACTION_CLICK)) {
                        for (AccessibilityNodeInfo x : nodes) {
                            if (x != null) x.recycle();
                        }
                        return true;
                    }
                }
                for (AccessibilityNodeInfo n : nodes) {
                    if (n != null) n.recycle();
                }
            }
        } finally {
            root.recycle();
        }
        return false;
    }
}
