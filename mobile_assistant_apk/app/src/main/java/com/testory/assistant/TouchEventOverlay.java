package com.testory.assistant;

import android.accessibilityservice.AccessibilityService;
import android.content.Context;
import android.graphics.PixelFormat;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.util.Log;
import android.view.Gravity;
import android.view.MotionEvent;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowManager;
import android.view.accessibility.AccessibilityNodeInfo;

import org.json.JSONObject;

/**
 * 使用透明覆盖层直接捕获触摸坐标（借鉴 SoloPi Cover 方案）。
 * 核心思想：通过 FLAG_NOT_TOUCH_MODAL 让触摸事件穿透，同时记录坐标。
 */
final class TouchEventOverlay {

    private static final String TAG = "TouchEventOverlay";
    private static final Handler MAIN = new Handler(Looper.getMainLooper());

    private static WindowManager windowManager;
    private static View touchView;
    private static boolean visible;
    private static AccessibilityService service;

    private TouchEventOverlay() {
    }

    static void show(AccessibilityService svc) {
        if (visible) return;
        service = svc;
        MAIN.post(() -> {
            try {
                windowManager = (WindowManager) svc.getSystemService(Context.WINDOW_SERVICE);
                if (windowManager == null) {
                    Log.e(TAG, "windowManager is null");
                    return;
                }

                touchView = new View(svc);
                touchView.setLayoutParams(new ViewGroup.LayoutParams(
                        ViewGroup.LayoutParams.MATCH_PARENT,
                        ViewGroup.LayoutParams.MATCH_PARENT
                ));

                int overlayType;
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                    overlayType = WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY;
                } else if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                    overlayType = WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY;
                } else if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                    overlayType = WindowManager.LayoutParams.TYPE_PHONE;
                } else {
                    overlayType = WindowManager.LayoutParams.TYPE_SYSTEM_ALERT;
                }

                WindowManager.LayoutParams lp = new WindowManager.LayoutParams(
                        WindowManager.LayoutParams.MATCH_PARENT,
                        WindowManager.LayoutParams.MATCH_PARENT,
                        overlayType,
                        WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE
                                | WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL
                                | WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN,
                        PixelFormat.TRANSPARENT
                );
                lp.gravity = Gravity.LEFT | Gravity.TOP;
                lp.x = 0;
                lp.y = 0;

                touchView.setOnTouchListener(new View.OnTouchListener() {
                    @Override
                    public boolean onTouch(View v, MotionEvent event) {
                        int action = event.getActionMasked();
                        float x = event.getRawX();
                        float y = event.getRawY();
                        int ix = (int) x;
                        int iy = (int) y;

                        Log.d(TAG, "onTouch action=" + action + " rawX=" + x + " rawY=" + y);

                        switch (action) {
                            case MotionEvent.ACTION_DOWN:
                                Log.i(TAG, "ACTION_DOWN: (" + ix + "," + iy + ")");
                                TouchGestureClassifier.get().onStart(ix, iy);
                                TouchCoordBuffer.beginInteraction(ix, iy);
                                TouchCoordBuffer.recordTouch(ix, iy);
                                break;
                            case MotionEvent.ACTION_MOVE:
                                TouchGestureClassifier.get().onMove(ix, iy);
                                TouchCoordBuffer.recordTouch(ix, iy);
                                break;
                            case MotionEvent.ACTION_UP:
                            case MotionEvent.ACTION_CANCEL:
                                Log.i(TAG, "ACTION_UP: (" + ix + "," + iy + ")");
                                TouchCoordBuffer.recordTouch(ix, iy);
                                JSONObject gesture = TouchGestureClassifier.get().onEnd(ix, iy);
                                if (gesture == null) {
                                    gesture = TouchCoordBuffer.finishInteraction(ix, iy);
                                }
                                if (gesture != null) {
                                    try {
                                        gesture.put("ts", System.currentTimeMillis());
                                        RecordEventFilter.markTouchGesture(gesture);
                                        String type = gesture.optString("type", "");
                                        if ("click".equals(type) && service != null) {
                                            String pkg = findPackageAtPosition(ix, iy);
                                            if (pkg != null && !pkg.isEmpty()) {
                                                gesture.put("type", "open_app");
                                                gesture.put("package", pkg);
                                                gesture.put("app_label", resolveAppLabel(pkg));
                                                gesture.put("description", "打开应用[" + resolveAppLabel(pkg) + "]");
                                            }
                                        }
                                        PluginHttpServer.enqueueStep(gesture);
                                        Log.i(TAG, "enqueued gesture: " + gesture.toString());
                                    } catch (Exception e) {
                                        Log.e(TAG, "enqueue failed", e);
                                    }
                                } else {
                                    Log.w(TAG, "gesture is null, no step enqueued");
                                }
                                break;
                        }
                        return false;
                    }
                });

                windowManager.addView(touchView, lp);
                visible = true;
                Log.i(TAG, "TouchEventOverlay shown with type=" + overlayType);
            } catch (Exception e) {
                Log.e(TAG, "show failed", e);
            }
        });
    }

    static void show(Context ctx) {
        if (ctx instanceof AccessibilityService) {
            show((AccessibilityService) ctx);
        } else {
            Log.w(TAG, "Context is not AccessibilityService, overlay may not work");
        }
    }

    private static String findPackageAtPosition(int x, int y) {
        if (service == null) return null;
        try {
            AccessibilityNodeInfo node = NodeLocatorHelper.findDeepestNodeAt(service, x, y);
            if (node != null) {
                String pkg = (String) node.getPackageName();
                if (pkg != null && !pkg.isEmpty() && !pkg.equals("com.testory.assistant")) {
                    String text = (String) node.getText();
                    if (text != null && !text.isEmpty()) {
                        node.recycle();
                        return pkg;
                    }
                }
                node.recycle();
            }
        } catch (Exception e) {
            Log.e(TAG, "findPackageAtPosition failed", e);
        }
        return null;
    }

    private static String resolveAppLabel(String pkg) {
        if (service == null || pkg == null) return pkg;
        try {
            return service.getPackageManager().getApplicationLabel(
                    service.getPackageManager().getApplicationInfo(pkg, 0)).toString();
        } catch (Exception e) {
            return pkg;
        }
    }

    static void hide() {
        if (!visible || touchView == null || windowManager == null) {
            visible = false;
            return;
        }
        MAIN.post(() -> {
            try {
                windowManager.removeView(touchView);
                Log.i(TAG, "TouchEventOverlay hidden");
            } catch (Exception e) {
                Log.e(TAG, "hide failed", e);
            } finally {
                touchView = null;
                visible = false;
                service = null;
            }
        });
    }

    static boolean isVisible() {
        return visible;
    }
}