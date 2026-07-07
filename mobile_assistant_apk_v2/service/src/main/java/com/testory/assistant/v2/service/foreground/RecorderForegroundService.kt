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
 * 录制/回放前台服务 — 防止 Android 杀进程，确保录制不中断。
 */
@AndroidEntryPoint
class RecorderForegroundService : Service() {

    companion object {
        const val CHANNEL_ID = "testory_recorder_channel"
        const val CHANNEL_NAME = "Testory 录制服务"
        const val NOTIFICATION_ID = 10086

        const val ACTION_STOP = "com.testory.assistant.v2.STOP_RECORDING"
        const val ACTION_PAUSE = "com.testory.assistant.v2.PAUSE_RECORDING"
        const val ACTION_RESUME = "com.testory.assistant.v2.RESUME_RECORDING"
        const val ACTION_UPDATE_STEP_COUNT = "com.testory.assistant.v2.UPDATE_STEP_COUNT"

        const val EXTRA_MODE = "extra_mode"  // "recording" or "replaying"
        const val EXTRA_STEP_COUNT = "extra_step_count"
        const val EXTRA_STEP_DESC = "extra_step_desc"
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
        if (stepCount > 0) {
            currentStepCount = stepCount
        }
        val stepDesc = intent?.getStringExtra(EXTRA_STEP_DESC) ?: ""

        val isRecording = mode == "recording"
        val title = if (isRecording) "正在录制测试" else "正在回放测试"
        val content = if (isRecording && currentStepCount > 0) {
            "已录制 $currentStepCount 步"
        } else if (!isRecording && stepDesc.isNotBlank()) {
            "执行: $stepDesc"
        } else {
            "Testory 运行中..."
        }

        when (intent?.action) {
            ACTION_STOP -> stopForegroundIfNeeded()
            ACTION_PAUSE -> updateNotification(
                if (isRecording) "录制已暂停" else "回放已暂停",
                "点击继续"
            )
            ACTION_UPDATE_STEP_COUNT -> {
                currentStepCount = stepCount
                if (isRecording) {
                    startOrUpdateForeground(title, "已录制 $currentStepCount 步")
                }
            }
            else -> startOrUpdateForeground(title, content)
        }

        return START_STICKY
    }

    override fun onBind(intent: Intent?): IBinder? = null

    fun updateProgress(stepIndex: Int, totalSteps: Int, description: String) {
        val notification = buildNotification(
            title = "正在回放: $stepIndex/$totalSteps",
            content = description,
            progressMax = totalSteps,
            progressCurrent = stepIndex
        )
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            startForeground(NOTIFICATION_ID, notification,
                android.content.pm.ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
        } else {
            startForeground(NOTIFICATION_ID, notification)
        }
    }

    private fun startOrUpdateForeground(title: String, content: String) {
        val notification = buildNotification(title, content)
        try {
            startForeground(NOTIFICATION_ID, notification)
        } catch (e: Exception) {
            // Fallback for Android 14+ without service type
            startForeground(NOTIFICATION_ID, notification)
        }
    }

    private fun updateNotification(title: String, content: String) {
        val notification = buildNotification(title, content)
        val manager = getSystemService(NOTIFICATION_SERVICE) as NotificationManager
        manager.notify(NOTIFICATION_ID, notification)
    }

    private fun stopForegroundIfNeeded() {
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    private fun buildNotification(
        title: String,
        content: String,
        progressMax: Int = 0,
        progressCurrent: Int = 0
    ): Notification {
        val builder = NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.ic_media_play)
            .setContentTitle(title)
            .setContentText(content)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .setOngoing(true)
            .setSilent(true)

        if (progressMax > 0) {
            builder.setProgress(progressMax, progressCurrent, false)
        }

        // Add actions
        val pauseIntent = Intent(this, RecorderForegroundService::class.java).apply {
            action = ACTION_PAUSE
        }
        val pausePending = PendingIntent.getService(
            this, 0, pauseIntent,
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )
        builder.addAction(android.R.drawable.ic_media_pause, "暂停", pausePending)

        val stopIntent = Intent(this, RecorderForegroundService::class.java).apply {
            action = ACTION_STOP
        }
        val stopPending = PendingIntent.getService(
            this, 1, stopIntent,
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )
        builder.addAction(android.R.drawable.ic_media_play, "停止", stopPending)

        return builder.build()
    }

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                CHANNEL_ID,
                CHANNEL_NAME,
                NotificationManager.IMPORTANCE_LOW
            ).apply {
                description = "Testory 录制/回放服务通知"
                setShowBadge(false)
            }
            val manager = getSystemService(NOTIFICATION_SERVICE) as NotificationManager
            manager.createNotificationChannel(channel)
        }
    }
}
