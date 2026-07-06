package com.testory.assistant;

import android.util.Log;

import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.util.HashMap;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * 设备端 getevent 触摸捕获（参考 SoloPi TouchEventTracker）。
 *
 * 通过执行 "getevent -lt" 命令直接读取 Linux 内核级触摸事件，
 * 不依赖 AccessibilityService，可在桌面/Launcher 等场景下捕获滑动手势。
 *
 * 原缺陷：设备端 APK 仅依赖 AccessibilityService 的 TYPE_TOUCH_INTERACTION 和
 * TYPE_VIEW_SCROLLED 事件捕获手势，桌面 Launcher 的自定义 View 通常不触发这些事件，
 * 导致桌面滑动无法录制。
 *
 * Design inspired by SoloPi: getevent 是唯一可靠的桌面触摸捕获方案。
 */
final class TouchEventCapture {

    private static final String TAG = "TouchEventCapture";

    // getevent 输出格式有两种：
    // 1. 十六进制格式：[1234.567890] dev: 0003 0035 00000001
    // 2. 事件名格式：[1234.567890] dev: EV_ABS ABS_MT_POSITION_X 00000001
    // 多数设备输出事件名格式，必须同时支持两种。
    private static final Pattern LINE_PATTERN = Pattern.compile(
            "^\\s*\\[\\s*(\\d+)\\.(\\d+)\\]\\s+\\S+:\\s+([0-9a-fA-F]{4})\\s+([0-9a-fA-F]{4})\\s+([0-9a-fA-F]+)");
    private static final Pattern BTN_PATTERN = Pattern.compile(
            "^\\s*\\[\\s*(\\d+)\\.(\\d+)\\]\\s+\\S+:\\s+([0-9a-fA-F]{4})\\s+([0-9a-fA-F]{4})\\s+(DOWN|UP)\\s*");
    // 事件名格式正则：type 和 code 为字符串，value 为十六进制
    private static final Pattern LINE_LABEL_PATTERN = Pattern.compile(
            "^\\s*\\[\\s*(\\d+)\\.(\\d+)\\]\\s+\\S+:\\s+(\\w+)\\s+(\\w+)\\s+([0-9a-fA-F]+)\\s*");
    // BTN_TOUCH DOWN/UP 事件名格式
    private static final Pattern BTN_LABEL_PATTERN = Pattern.compile(
            "^\\s*\\[\\s*(\\d+)\\.(\\d+)\\]\\s+\\S+:\\s+(\\w+)\\s+(\\w+)\\s+(DOWN|UP)\\s*");

    // getevent 常用事件名映射
    private static final Map<String, Integer> EVENT_TYPE_MAP = new HashMap<>();
    private static final Map<String, Integer> EVENT_CODE_MAP = new HashMap<>();
    static {
        EVENT_TYPE_MAP.put("EV_SYN", 0x00);
        EVENT_TYPE_MAP.put("EV_KEY", 0x01);
        EVENT_TYPE_MAP.put("EV_REL", 0x02);
        EVENT_TYPE_MAP.put("EV_ABS", 0x03);
        EVENT_CODE_MAP.put("BTN_TOUCH", 0x14a);
        EVENT_CODE_MAP.put("ABS_MT_POSITION_X", 0x35);
        EVENT_CODE_MAP.put("ABS_MT_POSITION_Y", 0x36);
        EVENT_CODE_MAP.put("ABS_MT_TRACKING_ID", 0x39);
        EVENT_CODE_MAP.put("ABS_MT_TOUCH_MAJOR", 0x30);
        EVENT_CODE_MAP.put("ABS_MT_WIDTH_MAJOR", 0x32);
        EVENT_CODE_MAP.put("ABS_MT_PRESSURE", 0x3a);
    }

    // 事件类型
    private static final int EV_ABS = 0x03;
    private static final int EV_KEY = 0x01;
    // ABS_MT 代码
    private static final int ABS_MT_TRACKING_ID = 0x39;
    private static final int ABS_MT_POSITION_X = 0x35;
    private static final int ABS_MT_POSITION_Y = 0x36;
    private static final int ABS_MT_TOUCH_MAJOR = 0x30;
    // 按键代码
    private static final int BTN_TOUCH = 0x14a;

    // 手势识别参数（参考 SoloPi TouchWrapper）
    private static final int MIN_SWIPE_PX = 40;
    private static final long LONG_PRESS_MS = 500;
    private static final long MIN_TAP_MS = 20;

    private final String udid;
    private Process process;
    private Thread readerThread;
    private volatile boolean running;

    // 触摸状态
    private int touchStartX, touchStartY;
    private int touchEndX, touchEndY;
    private long touchStartMs;
    private boolean touching;
    private boolean hasXY;
    private boolean startSet;  // 是否已记录起始坐标

    // 触摸设备分辨率（从 getevent -p 解析）
    private int touchMaxX = 0;
    private int touchMaxY = 0;
    // 屏幕分辨率
    private int screenWidth = 1080;
    private int screenHeight = 1920;

    private TouchEventListener listener;

    interface TouchEventListener {
        void onTouchGesture(JSONObject gesture);
    }

    TouchEventCapture(String udid) {
        this.udid = (udid != null) ? udid : "";
    }

    void setListener(TouchEventListener listener) {
        this.listener = listener;
    }

    void setScreenSize(int width, int height) {
        this.screenWidth = width;
        this.screenHeight = height;
    }

    synchronized void start() {
        if (running) return;
        running = true;
        readerThread = new Thread(this::readLoop, "getevent-reader-" + udid);
        readerThread.setDaemon(true);
        readerThread.start();
    }

    synchronized void stop() {
        running = false;
        if (process != null) {
            try {
                process.destroy();
            } catch (Exception ignored) {
            }
            process = null;
        }
        if (readerThread != null) {
            readerThread.interrupt();
            readerThread = null;
        }
    }

    private void readLoop() {
        boolean geteventAvailable = false;
        try {
            // 先解析触摸设备分辨率
            parseTouchDeviceResolution();

            // 启动 getevent -lt 读取触摸事件
            // 在非 root 设备上可能失败（Permission denied），此时静默退出
            String[] cmd = buildCmd("getevent", "-lt");
            process = Runtime.getRuntime().exec(cmd);
            geteventAvailable = true;
            AssistantSession.setGeteventCaptureActive(true);
            BufferedReader reader = new BufferedReader(
                    new InputStreamReader(process.getInputStream()), 4096);

            String line;
            while (running && (line = reader.readLine()) != null) {
                try {
                    parseLine(line);
                } catch (Exception ignored) {
                }
            }
        } catch (Exception e) {
            if (running) {
                // 非 root 设备上 getevent 会因 Permission denied 失败，降级为 a11y 模式
                Log.w(TAG, "getevent 不可用（可能需要 root），降级为 AccessibilityService 模式: " + e.getMessage());
            }
        } finally {
            running = false;
            if (geteventAvailable) {
                AssistantSession.setGeteventCaptureActive(false);
            }
        }
    }

    private void parseTouchDeviceResolution() {
        try {
            String[] cmd = buildCmd("getevent", "-p");
            Process p = Runtime.getRuntime().exec(cmd);
            BufferedReader reader = new BufferedReader(
                    new InputStreamReader(p.getInputStream()), 8192);

            String line;
            boolean inAbs = false;
            while ((line = reader.readLine()) != null) {
                line = line.trim();
                if (line.startsWith("ABS")) {
                    inAbs = true;
                    continue;
                }
                if (inAbs && line.isEmpty()) {
                    inAbs = false;
                    continue;
                }
                if (inAbs) {
                    // 解析 ABS 0035 (X) 和 ABS 0036 (Y) 的 max 值
                    if (line.contains("0035") || line.contains("0036")) {
                        String[] parts = line.split("\\s+");
                        for (String part : parts) {
                            if (part.startsWith("max")) {
                                try {
                                    int max = Integer.parseInt(part.substring(4));
                                    if (line.contains("0035")) touchMaxX = max;
                                    if (line.contains("0036")) touchMaxY = max;
                                } catch (NumberFormatException ignored) {
                                }
                            }
                        }
                    }
                }
            }
            reader.close();
            p.destroy();

            if (touchMaxX > 0 && touchMaxY > 0) {
                Log.i(TAG, "touch device resolution: " + touchMaxX + "x" + touchMaxY);
            }
        } catch (Exception e) {
            Log.w(TAG, "parseTouchDeviceResolution failed", e);
        }
    }

    private void parseLine(String line) {
        if (line.isEmpty()) return;

        // 优先尝试匹配事件名格式（多数设备输出这种格式）
        // BTN_TOUCH DOWN/UP 事件名格式
        Matcher btnLabelMatcher = BTN_LABEL_PATTERN.matcher(line);
        if (btnLabelMatcher.matches()) {
            String typeStr = btnLabelMatcher.group(3);
            String codeStr = btnLabelMatcher.group(4);
            int type = EVENT_TYPE_MAP.containsKey(typeStr) ? EVENT_TYPE_MAP.get(typeStr) : -1;
            int code = EVENT_CODE_MAP.containsKey(codeStr) ? EVENT_CODE_MAP.get(codeStr) : -1;
            if (type == EV_KEY && code == BTN_TOUCH) {
                long ts = parseTimestamp(btnLabelMatcher.group(1), btnLabelMatcher.group(2));
                if ("DOWN".equals(btnLabelMatcher.group(5))) {
                    onTouchDown(ts);
                } else {
                    onTouchUp(ts);
                }
            }
            return;
        }

        // ABS/KEY 事件名格式
        Matcher labelMatcher = LINE_LABEL_PATTERN.matcher(line);
        if (labelMatcher.matches()) {
            long ts = parseTimestamp(labelMatcher.group(1), labelMatcher.group(2));
            String typeStr = labelMatcher.group(3);
            String codeStr = labelMatcher.group(4);
            String valueStr = labelMatcher.group(5);
            int type = EVENT_TYPE_MAP.containsKey(typeStr) ? EVENT_TYPE_MAP.get(typeStr) : -1;
            int code = EVENT_CODE_MAP.containsKey(codeStr) ? EVENT_CODE_MAP.get(codeStr) : -1;
            int value = (int) Long.parseLong(valueStr, 16); // 支持负数

            if (type == EV_ABS && code > 0) {
                handleAbsEvent(ts, code, value);
            } else if (type == EV_KEY && code == BTN_TOUCH) {
                if (value == 0) {
                    onTouchUp(ts);
                } else if (value == 1) {
                    onTouchDown(ts);
                }
            }
            return;
        }

        // 回退：尝试匹配十六进制格式（少数设备输出）
        Matcher btnMatcher = BTN_PATTERN.matcher(line);
        if (btnMatcher.matches()) {
            int type = Integer.parseInt(btnMatcher.group(3), 16);
            int code = Integer.parseInt(btnMatcher.group(4), 16);
            if (type == EV_KEY && code == BTN_TOUCH) {
                long ts = parseTimestamp(btnMatcher.group(1), btnMatcher.group(2));
                if ("DOWN".equals(btnMatcher.group(5))) {
                    onTouchDown(ts);
                } else {
                    onTouchUp(ts);
                }
            }
            return;
        }

        Matcher matcher = LINE_PATTERN.matcher(line);
        if (matcher.matches()) {
            long ts = parseTimestamp(matcher.group(1), matcher.group(2));
            int type = Integer.parseInt(matcher.group(3), 16);
            int code = Integer.parseInt(matcher.group(4), 16);
            int value = (int) Long.parseLong(matcher.group(5), 16);

            if (type == EV_ABS) {
                handleAbsEvent(ts, code, value);
            } else if (type == EV_KEY && code == BTN_TOUCH) {
                if (value == 0) {
                    onTouchUp(ts);
                } else if (value == 1) {
                    onTouchDown(ts);
                }
            }
        }
    }

    private void handleAbsEvent(long ts, int code, int value) {
        switch (code) {
            case ABS_MT_TRACKING_ID:
                if (value < 0) {
                    onTouchUp(ts);
                } else {
                    onTouchDown(ts);
                }
                break;
            case ABS_MT_POSITION_X:
                touchEndX = value;
                hasXY = true;
                // 如果正在触摸且还没记录起始坐标，记录起始坐标
                if (touching && !startSet) {
                    touchStartX = value;
                    startSet = true;
                }
                break;
            case ABS_MT_POSITION_Y:
                touchEndY = value;
                hasXY = true;
                // 如果正在触摸且还没记录起始坐标，记录起始坐标
                if (touching && !startSet) {
                    touchStartY = value;
                    startSet = true;
                }
                break;
        }
    }

    private void onTouchDown(long ts) {
        if (touching) return;
        touching = true;
        touchStartMs = ts;
        // 起始坐标在收到第一个 ABS_MT_POSITION_X/Y 事件时记录
        // 不在此处设置，因为此时坐标事件可能还未到达
        startSet = false;
        hasXY = false;
    }

    private void onTouchUp(long ts) {
        if (!touching) return;
        touching = false;
        long duration = ts - touchStartMs;

        if (!hasXY) return;

        int dx = Math.abs(touchEndX - touchStartX);
        int dy = Math.abs(touchEndY - touchStartY);

        // 坐标转换：触摸坐标 → 屏幕坐标
        int sx1 = scaleX(touchStartX);
        int sy1 = scaleY(touchStartY);
        int sx2 = scaleX(touchEndX);
        int sy2 = scaleY(touchEndY);

        // 使用 OperationNodeExporter enrich 节点信息（应用内可找到节点，桌面找不到则保留坐标）
        AssistantAccessibilityService svc = AssistantSession.getService();
        if (svc != null) {
            try {
                JSONObject payload = OperationNodeExporter.exportTouchAction(
                        svc,
                        (dx < MIN_SWIPE_PX && dy < MIN_SWIPE_PX)
                                ? (duration >= LONG_PRESS_MS ? "long-press" : "click")
                                : "swipe",
                        sx2, sy2, sx1, sy1, sx2, sy2, duration);
                if (listener != null) {
                    listener.onTouchGesture(payload);
                }
                return;
            } catch (Exception e) {
                Log.w(TAG, "exportTouchAction failed, fallback to basic payload", e);
            }
        }

        // Fallback：无法 enrich 时生成基础 payload（确保桌面场景仍可录制）
        JSONObject gesture = new JSONObject();
        try {
            if (dx < MIN_SWIPE_PX && dy < MIN_SWIPE_PX) {
                if (duration < MIN_TAP_MS) return;
                if (duration >= LONG_PRESS_MS) {
                    gesture.put("type", "long-press");
                    gesture.put("x", sx2);
                    gesture.put("y", sy2);
                    gesture.put("description", "长按 (" + sx2 + "," + sy2 + ")");
                } else {
                    gesture.put("type", "click");
                    gesture.put("x", sx2);
                    gesture.put("y", sy2);
                    gesture.put("description", "点击 (" + sx2 + "," + sy2 + ")");
                }
            } else {
                gesture.put("type", "swipe");
                gesture.put("x1", sx1);
                gesture.put("y1", sy1);
                gesture.put("x2", sx2);
                gesture.put("y2", sy2);
                gesture.put("action_duration_ms", duration);
                gesture.put("description", "滑动 (" + sx1 + "," + sy1 + ")→(" + sx2 + "," + sy2 + ")");
            }
            gesture.put("source", "getevent");
            gesture.put("ts", System.currentTimeMillis());
            if (listener != null) {
                listener.onTouchGesture(gesture);
            }
        } catch (Exception ignored) {
        }
    }

    private int scaleX(int touchX) {
        if (touchMaxX > 0 && screenWidth > 0) {
            return (int) Math.round((double) touchX / touchMaxX * screenWidth);
        }
        return touchX;
    }

    private int scaleY(int touchY) {
        if (touchMaxY > 0 && screenHeight > 0) {
            return (int) Math.round((double) touchY / touchMaxY * screenHeight);
        }
        return touchY;
    }

    private long parseTimestamp(String sec, String usec) {
        try {
            return Long.parseLong(sec) * 1000 + Long.parseLong(usec) / 1000;
        } catch (NumberFormatException e) {
            return System.currentTimeMillis();
        }
    }

    private String[] buildCmd(String... args) {
        // 在 Android 上，getevent 不在应用 PATH 中，必须通过 shell 执行
        // 使用 sh -c 包装命令，确保能找到 getevent 可执行文件
        StringBuilder cmd = new StringBuilder();
        for (int i = 0; i < args.length; i++) {
            if (i > 0) cmd.append(" ");
            cmd.append(args[i]);
        }
        return new String[]{"sh", "-c", cmd.toString()};
    }
}
