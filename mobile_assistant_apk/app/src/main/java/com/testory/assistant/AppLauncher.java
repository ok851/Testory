package com.testory.assistant;

import android.content.Context;
import android.content.Intent;
import android.content.pm.ApplicationInfo;
import android.content.pm.PackageManager;
import android.os.Build;
import android.view.accessibility.AccessibilityNodeInfo;

import org.json.JSONObject;

import java.util.Arrays;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.concurrent.TimeUnit;

/**
 * 应用启动与前台就绪检测（设备端回放核心）。
 * <p>
 * 原缺陷：{@link AppLaunchPreparer} 依赖 queryIntentActivities + getRunningTasks，
 * Android 11+ 常解析失败或前台校验假阴性，导致 open_app 误报「无法打开应用」。
 * 新方案：getLaunchIntentForPackage → MAIN/LAUNCHER Intent → am start shell 兜底，
 * 多通道检测前台（无障碍窗口 + dumpsys）。
 */
final class AppLauncher {

    static final int DEFAULT_TIMEOUT_MS = 10_000;
    static final int POLL_MS = 400;

    private static final Set<String> SKIP_PACKAGES = new HashSet<>(Arrays.asList(
            "com.testory.assistant",
            "com.android.systemui",
            "com.android.settings",
            "com.google.android.apps.nexuslauncher",
            "com.android.launcher",
            "com.android.launcher3",
            "com.miui.home",
            "com.mi.android.globallauncher",
            "com.huawei.android.launcher",
            "com.huawei.android.totemweather",
            "com.oppo.launcher",
            "com.oppo.quicksearchbox",
            "com.coloros.launcher",
            "com.bbk.launcher2",
            "com.vivo.launcher",
            "com.sec.android.app.launcher",
            "com.sec.android.app.launcher.activity",
            "com.oneplus.launcher",
            "com.google.android.apps.launcher",
            "com.microsoft.launcher",
            "com.actionlauncher.playstore",
            "com.nova.launcher",
            "com.teslacoilsw.launcher",
            "me.mvp.pixel_live_wallpaper_launcher",
            "com.amdroid.apolauncher",
            "org.cyanogenmod.trebuchet",
            "net.oneplus.launcher",
            "com.zui.launcher",
            "com.letv.android.desktop",
            "com.smartisanos.launcher",
            "com.meizu.flyme.launcher",
            "com.lge.launcher3",
            "com.asus.launcher",
            "com.htc.launcher"
    ));

    static final class Result {
        final boolean success;
        final String errorCode;
        final String message;
        final String packageName;
        final String appLabel;
        final String via;

        private Result(
                boolean success,
                String errorCode,
                String message,
                String packageName,
                String appLabel,
                String via) {
            this.success = success;
            this.errorCode = errorCode;
            this.message = message;
            this.packageName = packageName;
            this.appLabel = appLabel;
            this.via = via;
        }

        static Result ok(String pkg, String label, String via) {
            return new Result(true, "", "", pkg, label, via);
        }

        static Result fail(String code, String message, String pkg, String label) {
            return new Result(false, code, message, pkg, label, "");
        }
    }

    private AppLauncher() {
    }

    static Result launch(
            Context ctx,
            AssistantAccessibilityService svc,
            String pkg,
            String activity,
            int timeoutMs) {
        String normalized = normalizePackage(pkg);
        if (normalized.isEmpty()) {
            return Result.ok("", "", "noop");
        }
        if (isSkippablePackage(normalized)) {
            return Result.ok(normalized, friendlyLabel(ctx, normalized), "skipped_launcher");
        }
        if (!isInstalled(ctx, normalized)) {
            String label = friendlyLabel(ctx, normalized);
            return Result.fail(
                    "NOT_INSTALLED",
                    "应用「" + label + "」未安装，请先安装后再重试。",
                    normalized,
                    label);
        }
        String label = friendlyLabel(ctx, normalized);
        if (svc != null && normalized.equals(svc.getForegroundPackage())) {
            return Result.ok(normalized, label, "already_foreground");
        }

        int waitMs = timeoutMs > 0 ? timeoutMs : DEFAULT_TIMEOUT_MS;
        String[] strategies = {"launch_intent", "explicit_component", "am_start_shell"};
        for (String strategy : strategies) {
            if (!tryStrategy(ctx, normalized, activity, strategy)) {
                continue;
            }
            if (waitForeground(svc, normalized, waitMs)) {
                return Result.ok(normalized, label, strategy);
            }
        }
        if (svc != null && normalized.equals(svc.getForegroundPackage())) {
            return Result.ok(normalized, label, "foreground_late");
        }
        return Result.fail(
                "LAUNCH_TIMEOUT",
                "无法打开应用「" + label + "」。请确认已安装且可从桌面图标正常启动；"
                        + "若应用已在后台，请手动切到该应用后重试。",
                normalized,
                label);
    }

    static boolean isInstalled(Context ctx, String pkg) {
        if (ctx == null || pkg == null || pkg.isEmpty()) return false;
        try {
            PackageManager pm = ctx.getPackageManager();
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                pm.getPackageInfo(pkg, PackageManager.PackageInfoFlags.of(0));
            } else {
                pm.getPackageInfo(pkg, 0);
            }
            return true;
        } catch (PackageManager.NameNotFoundException e) {
            return false;
        } catch (Exception e) {
            try {
                return pmGetLaunchIntent(ctx, pkg) != null;
            } catch (Exception ignored) {
                return false;
            }
        }
    }

    static boolean isSkippablePackage(String pkg) {
        if (pkg == null || pkg.isEmpty()) return true;
        if (SKIP_PACKAGES.contains(pkg)) return true;
        String lower = pkg.toLowerCase(Locale.US);
        return lower.contains("launcher")
                || lower.endsWith(".home")
                || lower.endsWith(".desktop")
                || lower.endsWith(".totemweather")
                || lower.contains("quicksearchbox")
                || lower.startsWith("com.miui.") && lower.contains("home");
    }

    static String friendlyLabel(Context ctx, String pkg) {
        if (pkg == null || pkg.isEmpty()) return "未知应用";
        try {
            PackageManager pm = ctx.getPackageManager();
            ApplicationInfo info = pm.getApplicationInfo(pkg, 0);
            CharSequence label = pm.getApplicationLabel(info);
            if (label != null && label.length() > 0) return label.toString();
        } catch (Exception ignored) {
        }
        return pkg;
    }

    /** 按桌面显示名称解析包名（用于 Launcher 图标点击 → open_app）。 */
    static String resolvePackageByLabel(Context ctx, String label) {
        if (ctx == null || label == null) return "";
        String want = label.trim();
        if (want.isEmpty()) return "";
        try {
            PackageManager pm = ctx.getPackageManager();
            List<ApplicationInfo> apps = pm.getInstalledApplications(PackageManager.GET_META_DATA);
            String fallback = "";
            for (ApplicationInfo info : apps) {
                CharSequence appLabel = pm.getApplicationLabel(info);
                if (appLabel == null || appLabel.length() == 0) continue;
                String candidate = appLabel.toString().trim();
                if (!want.equals(candidate)) continue;
                if (pmGetLaunchIntent(ctx, info.packageName) != null) {
                    return info.packageName;
                }
                if (fallback.isEmpty()) fallback = info.packageName;
            }
            return fallback;
        } catch (Exception ignored) {
        }
        return "";
    }

    private static String normalizePackage(String pkg) {
        return pkg == null ? "" : pkg.trim();
    }

    private static boolean tryStrategy(Context ctx, String pkg, String activity, String strategy) {
        try {
            if ("launch_intent".equals(strategy)) {
                Intent intent = pmGetLaunchIntent(ctx, pkg);
                if (intent == null) return false;
                intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK
                        | Intent.FLAG_ACTIVITY_CLEAR_TOP
                        | Intent.FLAG_ACTIVITY_SINGLE_TOP);
                ctx.startActivity(intent);
                return true;
            }
            if ("explicit_component".equals(strategy)) {
                String act = resolveLauncherActivity(ctx, pkg, activity);
                if (act == null || act.isEmpty()) return false;
                Intent intent = new Intent();
                intent.setClassName(pkg, act);
                intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK
                        | Intent.FLAG_ACTIVITY_CLEAR_TOP
                        | Intent.FLAG_ACTIVITY_SINGLE_TOP);
                ctx.startActivity(intent);
                return true;
            }
            if ("am_start_shell".equals(strategy)) {
                String act = resolveLauncherActivity(ctx, pkg, activity);
                if (act == null || act.isEmpty()) {
                    return shellAmStart(ctx, "am start -a android.intent.action.MAIN "
                            + "-c android.intent.category.LAUNCHER -p " + pkg);
                }
                return shellAmStart(ctx, "am start -n " + pkg + "/" + act);
            }
        } catch (Exception ignored) {
        }
        return false;
    }

    private static Intent pmGetLaunchIntent(Context ctx, String pkg) {
        PackageManager pm = ctx.getPackageManager();
        return pm.getLaunchIntentForPackage(pkg);
    }

    private static String resolveLauncherActivity(Context ctx, String pkg, String activity) {
        if (activity != null && !activity.isEmpty()) {
            return activity.startsWith(".") ? pkg + activity : activity;
        }
        try {
            PackageManager pm = ctx.getPackageManager();
            Intent probe = new Intent(Intent.ACTION_MAIN);
            probe.addCategory(Intent.CATEGORY_LAUNCHER);
            probe.setPackage(pkg);
            List<android.content.pm.ResolveInfo> infos = pm.queryIntentActivities(
                    probe, PackageManager.MATCH_DEFAULT_ONLY);
            if (infos != null && !infos.isEmpty()) {
                return infos.get(0).activityInfo.name;
            }
        } catch (Exception ignored) {
        }
        Intent launch = pmGetLaunchIntent(ctx, pkg);
        if (launch != null && launch.getComponent() != null) {
            return launch.getComponent().getClassName();
        }
        return null;
    }

    private static boolean shellAmStart(Context ctx, String cmd) {
        try {
            Process proc = Runtime.getRuntime().exec(cmd);
            boolean finished = proc.waitFor(3, TimeUnit.SECONDS);
            if (!finished) {
                proc.destroy();
                return false;
            }
            return proc.exitValue() == 0;
        } catch (Exception ignored) {
            return false;
        }
    }

    static boolean waitForeground(AssistantAccessibilityService svc, String pkg, int timeoutMs) {
        if (svc == null) return false;
        long deadline = System.currentTimeMillis() + Math.max(POLL_MS, timeoutMs);
        while (System.currentTimeMillis() < deadline) {
            if (pkg.equals(svc.getForegroundPackage())) return true;
            sleep(POLL_MS);
        }
        return pkg.equals(svc.getForegroundPackage());
    }

    /** 回放前软恢复：尽力拉起上下文应用，失败不阻断坐标类步骤。 */
    static Result softRestoreContext(
            Context ctx, AssistantAccessibilityService svc, String pkg, int timeoutMs) {
        if (pkg == null || pkg.isEmpty() || isSkippablePackage(pkg)) {
            return Result.ok(pkg, "", "skip_context");
        }
        if (svc != null && pkg.equals(svc.getForegroundPackage())) {
            return Result.ok(pkg, friendlyLabel(ctx, pkg), "already_foreground");
        }
        return launch(ctx, svc, pkg, "", timeoutMs);
    }

    static String extractContextPackage(JSONObject step) {
        if (step == null) return "";
        JSONObject spec = step.optJSONObject("mobile_spec");
        if (spec == null) return "";
        String pkg = spec.optString("context_package", "");
        if (pkg.isEmpty()) {
            pkg = spec.optString("app_package", spec.optString("appPackage", ""));
        }
        return pkg;
    }

    static boolean isCoordinateStep(JSONObject step) {
        if (step == null) return false;
        String action = step.optString("action", "").toLowerCase();
        if ("swipe".equals(action)) return true;
        if (!"tap".equals(action) && !"click".equals(action)) return false;
        String st = step.optString("selector_type", "");
        if ("viewport_coord".equals(st)) return true;
        JSONObject spec = step.optJSONObject("mobile_spec");
        return spec != null && spec.has("viewport_coord");
    }

    private static void sleep(long ms) {
        try {
            Thread.sleep(ms);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }
}
