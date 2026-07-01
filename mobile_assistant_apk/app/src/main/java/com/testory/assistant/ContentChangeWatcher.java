package com.testory.assistant;

import android.view.accessibility.AccessibilityEvent;

/**
 * SoloPi ContentChangeWatcher 子集：等待 UI 稳定后再回放。
 */
final class ContentChangeWatcher {

    private static final long STABLE_MS = 1000L;
    private static final long POLL_MS = 100L;
    private static final long MAX_WAIT_MS = 10000L;

    private static volatile long lastContentChangeMs;

    private ContentChangeWatcher() {
    }

    static void notifyContentChanged() {
        lastContentChangeMs = System.currentTimeMillis();
    }

    static void sleepUntilStable() {
        long deadline = System.currentTimeMillis() + MAX_WAIT_MS;
        while (System.currentTimeMillis() < deadline) {
            long since = System.currentTimeMillis() - lastContentChangeMs;
            if (since >= STABLE_MS) return;
            try {
                Thread.sleep(POLL_MS);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                return;
            }
        }
    }

    static boolean isWindowContentEvent(int type) {
        return type == AccessibilityEvent.TYPE_WINDOW_CONTENT_CHANGED
                || type == AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED
                || type == AccessibilityEvent.TYPE_VIEW_SCROLLED;
    }
}
