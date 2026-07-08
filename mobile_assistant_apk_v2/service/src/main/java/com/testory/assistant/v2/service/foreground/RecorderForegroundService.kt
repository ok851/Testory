package com.testory.assistant.v2.service.foreground

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import dagger.hilt.android.AndroidEntryPoint

/**
 * 录制/回放前台服务 — 防止 Android 杀进程。
 * 不再管理悬浮窗（已迁移到 AssistantAccessibilityService 使用 TYPE_ACCESSIBILITY_OVERLAY）。
 */
@AndroidEntryPoint
class RecorderForegroundService : Service() {

    companion object {
        const val CHANNEL_ID = "testory_recorder_channel"
        const val CHANNEL_NAME = "Testory 录制服务"
        const val NOTIFICATION_ID = 10086

        const val ACTION_STOP = "com.testory.assistant.v2.STOP_RECORDING"
        const val ACTION_UPDATE_STEP_COUNT = "com.testory.assistant.v2.UPDATE_STEP_COUNT"

        const val EXTRA_MODE = "extra_mode"
        const val EXTRA_STEP_COUNT = "extra_step_count"
    }

    private var mode: String = "recording"
    private var currentStepCount: Int = 0

    override fun onCreate() {
        super.onCreate()
        createNotificationChannel()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        mode = intent?.getStringExtra(EXTRA_MODE) ?: "recording"
        val stepCount = intent?.getIntExtra(EXTRA_STEP_COUNT, 0) ?: 0
        if (stepCount > 0) currentStepCount = stepCount

        when (intent?.action) {
            ACTION_STOP -> {
                stopForeground(STOP_FOREGROUND_REMOVE)
                stopSelf()
            }
            else -> {
                val title = if (mode == "recording") "正在录制测试" else "正在回放测试"
                val content = if (mode == "recording" && currentStepCount > 0) {
                    "已录制 $currentStepCount 步"
                } else {
                    "Testory 运行中..."
                }
                val notification = buildNotification(title, content)
                startForeground(NOTIFICATION_ID, notification)
            }
        }
        return START_STICKY
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private fun buildNotification(title: String, content: String): Notification {
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.ic_media_play)
            .setContentTitle(title)
            .setContentText(content)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .setOngoing(true)
            .setSilent(true)
            .build()
    }

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                CHANNEL_ID, CHANNEL_NAME, NotificationManager.IMPORTANCE_LOW
            ).apply {
                description = "Testory 录制/回放服务通知"
                setShowBadge(false)
            }
            val manager = getSystemService(NOTIFICATION_SERVICE) as NotificationManager
            manager.createNotificationChannel(channel)
        }
    }
}
