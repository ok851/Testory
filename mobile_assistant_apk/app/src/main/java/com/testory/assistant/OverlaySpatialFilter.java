package com.testory.assistant;

import android.graphics.Rect;

import org.json.JSONArray;
import org.json.JSONObject;

/** 录制悬浮条空间命中（对标 SoloPi checkInFloat），便于单测。 */
final class OverlaySpatialFilter {

    private OverlaySpatialFilter() {
    }

    static boolean intersectsOverlay(Rect overlayBounds, int hitSlopPx, int x, int y, Rect eventBounds) {
        if (overlayBounds == null) {
            return false;
        }
        int eventL = eventBounds != null ? eventBounds.left : 0;
        int eventT = eventBounds != null ? eventBounds.top : 0;
        int eventR = eventBounds != null ? eventBounds.right : 0;
        int eventB = eventBounds != null ? eventBounds.bottom : 0;
        return intersectsOverlay(
                overlayBounds.left, overlayBounds.top, overlayBounds.right, overlayBounds.bottom,
                hitSlopPx, x, y, eventL, eventT, eventR, eventB);
    }

    /** 纯 int 实现，供 JVM 单测与生产代码共用。 */
    static boolean intersectsOverlay(
            int overlayL, int overlayT, int overlayR, int overlayB,
            int hitSlopPx, int x, int y,
            int eventL, int eventT, int eventR, int eventB) {
        if (overlayR <= overlayL || overlayB <= overlayT) {
            return false;
        }
        int pl = overlayL - Math.max(0, hitSlopPx);
        int pt = overlayT - Math.max(0, hitSlopPx);
        int pr = overlayR + Math.max(0, hitSlopPx);
        int pb = overlayB + Math.max(0, hitSlopPx);
        if (x > 0 || y > 0) {
            if (x >= pl && x <= pr && y >= pt && y <= pb) {
                return true;
            }
        }
        if (eventR > eventL && eventB > eventT) {
            return !(eventR < pl || eventL > pr || eventB < pt || eventT > pb);
        }
        return false;
    }

    static boolean shouldIgnorePayload(Rect overlayBounds, int hitSlopPx, JSONObject payload) {
        if (payload == null) {
            return true;
        }
        TouchCoordBuffer.applyToPayload(payload);
        int x = payload.optInt("x", 0);
        int y = payload.optInt("y", 0);
        int[] bounds = boundsArrayFromPayload(payload);
        if (overlayBounds == null) {
            return false;
        }
        return intersectsOverlay(
                overlayBounds.left, overlayBounds.top, overlayBounds.right, overlayBounds.bottom,
                hitSlopPx, x, y,
                bounds[0], bounds[1], bounds[2], bounds[3]);
    }

    static Rect paddedBounds(Rect overlayBounds, int hitSlopPx) {
        Rect padded = new Rect(overlayBounds);
        padded.inset(-Math.max(0, hitSlopPx), -Math.max(0, hitSlopPx));
        return padded;
    }

    private static int[] boundsArrayFromPayload(JSONObject payload) {
        JSONArray arr = payload.optJSONArray("bounds");
        if (arr == null || arr.length() < 4) {
            return new int[]{0, 0, 0, 0};
        }
        return new int[]{
                arr.optInt(0),
                arr.optInt(1),
                arr.optInt(2),
                arr.optInt(3)};
    }
}
