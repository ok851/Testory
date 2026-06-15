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

import androidx.core.app.NotificationCompat;

/**
 * 录制时显示通知栏，支持点击「停止录制」。
 */
public class PluginForegroundService extends Service {

    private static final String CHANNEL_ID = "testory_recording";
    private static final int NOTIFICATION_ID = 7701;

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    static void startRecording(Context ctx) {
        Intent i = new Intent(ctx, PluginForegroundService.class);
        i.setAction("start");
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            ctx.startForegroundService(i);
        } else {
            ctx.startService(i);
        }
    }

    static void stopRecording(Context ctx) {
        Intent i = new Intent(ctx, PluginForegroundService.class);
        i.setAction("stop");
        ctx.startService(i);
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        String action = intent != null ? intent.getAction() : "";
        if ("stop".equals(action)) {
            AssistantSession.setArmedMode(AssistantSession.MODE_IDLE);
            stopForeground(true);
            stopSelf();
            return START_NOT_STICKY;
        }
        ensureChannel();
        Notification n = buildNotification();
        startForeground(NOTIFICATION_ID, n);
        return START_STICKY;
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
        PendingIntent stopPi = PendingIntent.getService(
                this, 1, stopIntent,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE
        );
        return new NotificationCompat.Builder(this, CHANNEL_ID)
                .setContentTitle("Testory 正在录制")
                .setContentText("在手机端操作；点此停止录制")
                .setSmallIcon(R.drawable.ic_launcher)
                .setContentIntent(stopPi)
                .addAction(0, "停止录制", stopPi)
                .setOngoing(true)
                .build();
    }
}
