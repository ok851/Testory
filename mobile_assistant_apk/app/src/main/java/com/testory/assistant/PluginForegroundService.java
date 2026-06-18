package com.testory.assistant;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.os.Build;
import android.os.IBinder;
import android.util.Log;

import androidx.core.app.NotificationCompat;

/**
 * 可选通知栏提示；录制核心逻辑在 RecordingSession（无障碍进程内）。
 */
public class PluginForegroundService extends Service {

    private static final String TAG = "TestoryFGS";
    private static final String CHANNEL_ID = "testory_recording";
    private static final int NOTIFICATION_ID = 7701;
    private static final String EXTRA_CASE_ID = "case_id";

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    static void startRecording(Context ctx, long caseId) {
        AssistantApplicationHolder.init(ctx);
        RecordingSession.start(ctx, caseId, RecordingSession.defaultListener(ctx));
        Intent i = new Intent(ctx, PluginForegroundService.class);
        i.setAction("start");
        i.putExtra(EXTRA_CASE_ID, caseId);
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                ctx.startForegroundService(i);
            } else {
                ctx.startService(i);
            }
        } catch (Exception e) {
            Log.w(TAG, "optional FGS start skipped", e);
        }
    }

    static void stopRecording(Context ctx) {
        RecordingSession.stop(ctx);
        Intent i = new Intent(ctx, PluginForegroundService.class);
        i.setAction("stop");
        try {
            ctx.startService(i);
        } catch (Exception e) {
            Log.w(TAG, "FGS stop skipped", e);
        }
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        String action = intent != null ? intent.getAction() : "";
        if ("stop".equals(action)) {
            stopSelfSafely();
            return START_NOT_STICKY;
        }
        try {
            ensureChannel();
            startForeground(NOTIFICATION_ID, buildNotification());
        } catch (Exception e) {
            Log.w(TAG, "startForeground skipped (recording continues via overlay)", e);
        }
        return START_STICKY;
    }

    private void stopSelfSafely() {
        try {
            stopForeground(true);
        } catch (Exception ignored) {
        }
        stopSelf();
    }

    private void ensureChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return;
        NotificationChannel ch = new NotificationChannel(
                CHANNEL_ID,
                "Testory 录制",
                NotificationManager.IMPORTANCE_LOW
        );
        NotificationManager nm = getSystemService(NotificationManager.class);
        if (nm != null) nm.createNotificationChannel(ch);
    }

    private Notification buildNotification() {
        Intent stopIntent = new Intent(this, PluginForegroundService.class);
        stopIntent.setAction("stop");
        int piFlags = PendingIntent.FLAG_UPDATE_CURRENT;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            piFlags |= PendingIntent.FLAG_IMMUTABLE;
        }
        PendingIntent stopPi = PendingIntent.getService(this, 1, stopIntent, piFlags);
        return new NotificationCompat.Builder(this, CHANNEL_ID)
                .setContentTitle("Testory 正在录制")
                .setContentText("顶部悬浮条可暂停/结束")
                .setSmallIcon(R.drawable.ic_launcher)
                .addAction(0, "结束录制", stopPi)
                .setOngoing(true)
                .build();
    }

    @Override
    public void onDestroy() {
        super.onDestroy();
    }
}
