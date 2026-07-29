package com.testory.assistant.v2.service.foreground

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.graphics.PixelFormat
import android.os.Build
import android.os.IBinder
import android.view.Gravity
import android.view.LayoutInflater
import android.view.View
import android.view.WindowManager
import android.widget.ImageButton
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.TextView
import android.widget.Toast
import androidx.core.app.NotificationCompat
import com.testory.assistant.v2.service.accessibility.AccessibilityServiceHolder
import com.testory.assistant.v2.service.accessibility.ReplaySessionController

/**
 * 录制悬浮窗控制服务 — 在录制/回放时显示系统级悬浮控制条。
 *
 * 录制开始后自动回到桌面并显示此悬浮窗，用户可通过悬浮窗
 * 暂停/停止/继续录制，无需切换回 App。
 *
 * 关键修复：
 * - 悬浮窗直接控制 AccessibilityService 的录制状态，避免 Activity 在后台时广播无人接收导致后台仍在录制
 * - 实时显示已录制步数
 */
class FloatingControlService : Service() {

    companion object {
        const val CHANNEL_ID = "testory_floating_control"
        const val NOTIFICATION_ID = 10087

        const val ACTION_PAUSE = "com.testory.assistant.v2.FLOATING_PAUSE"
        const val ACTION_STOP = "com.testory.assistant.v2.FLOATING_STOP"
        const val ACTION_RESUME = "com.testory.assistant.v2.FLOATING_RESUME"
        const val ACTION_OPEN_APP = "com.testory.assistant.v2.FLOATING_OPEN_APP"
        const val ACTION_UPDATE_STEP_COUNT = "com.testory.assistant.v2.UPDATE_STEP_COUNT"

        const val EXTRA_MODE = "extra_mode" // "recording" or "replaying"
        const val EXTRA_STEP_COUNT = "extra_step_count"
        const val EXTRA_CURRENT_STEP = "extra_current_step"
        const val EXTRA_TOTAL_STEPS = "extra_total_steps"
        const val EXTRA_COMPLETE_RESULT = "extra_complete_result"
        const val ACTION_UPDATE_REPLAY_PROGRESS = "com.testory.assistant.v2.FLOATING_UPDATE_REPLAY_PROGRESS"
        const val ACTION_REPLAY_COMPLETE = "com.testory.assistant.v2.FLOATING_REPLAY_COMPLETE"

        /** 对外广播：通知 RecorderViewModel 暂停/停止 */
        const val BROADCAST_PAUSE = "com.testory.assistant.v2.FLOATING_BROADCAST_PAUSE"
        const val BROADCAST_STOP = "com.testory.assistant.v2.FLOATING_BROADCAST_STOP"
        const val BROADCAST_RESUME = "com.testory.assistant.v2.FLOATING_BROADCAST_RESUME"
    }

    private var windowManager: WindowManager? = null
    private var floatingView: View? = null
    private var progressView: View? = null
    private var btnPause: ImageButton? = null
    private var btnStop: ImageButton? = null
    private var tvStepCount: TextView? = null
    private var mode: String = "recording"

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        createNotificationChannel()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        mode = intent?.getStringExtra(EXTRA_MODE) ?: "recording"
        val stepCount = intent?.getIntExtra(EXTRA_STEP_COUNT, 0) ?: 0

        when (intent?.action) {
            ACTION_PAUSE -> {
                if (mode == "replaying") {
                    ReplaySessionController.requestPause()
                    sendBroadcast(Intent(BROADCAST_PAUSE))
                    updatePauseButton(false)
                    updateNotificationTitle("回放已暂停")
                } else {
                    AccessibilityServiceHolder.instance?.pauseRecording()
                    sendBroadcast(Intent(BROADCAST_PAUSE))
                    updatePauseButton(false)
                    updateNotificationTitle("录制已暂停")
                }
            }
            ACTION_STOP -> {
                if (mode == "replaying") {
                    ReplaySessionController.requestResume() // 解除挂起
                    sendBroadcast(Intent(BROADCAST_STOP))
                } else {
                    AccessibilityServiceHolder.instance?.stopRecording()
                    sendBroadcast(Intent(BROADCAST_STOP))
                }
                stopRecorderForegroundService()
                removeFloatingView()
                stopForeground(STOP_FOREGROUND_REMOVE)
                stopSelf()
                return START_NOT_STICKY
            }
            ACTION_RESUME -> {
                if (mode == "replaying") {
                    ReplaySessionController.requestResume()
                    sendBroadcast(Intent(BROADCAST_RESUME))
                    updatePauseButton(true)
                    updateNotificationTitle("回放中")
                } else {
                    AccessibilityServiceHolder.instance?.resumeRecording()
                    sendBroadcast(Intent(BROADCAST_RESUME))
                    updatePauseButton(true)
                    updateNotificationTitle("录制中")
                }
            }
            ACTION_UPDATE_STEP_COUNT -> {
                updateStepCount(stepCount)
                updateNotificationStepCount(stepCount)
            }
            ACTION_UPDATE_REPLAY_PROGRESS -> {
                val current = intent?.getIntExtra(EXTRA_CURRENT_STEP, 0) ?: 0
                val total = intent?.getIntExtra(EXTRA_TOTAL_STEPS, 0) ?: 0
                updateReplayProgress(current, total)
            }
            ACTION_REPLAY_COMPLETE -> {
                val result = intent?.getStringExtra(EXTRA_COMPLETE_RESULT) ?: "完成"
                updateReplayComplete(result)
            }
            else -> {
                if (mode == "replaying") {
                    if (windowManager == null) {
                        windowManager = getSystemService(WINDOW_SERVICE) as WindowManager
                    }
                    removeFloatingViewKeepProgress()
                    showProgressView()
                    val current = intent?.getIntExtra(EXTRA_CURRENT_STEP, 0) ?: 0
                    val total = intent?.getIntExtra(EXTRA_TOTAL_STEPS, 0) ?: 0
                    updateReplayProgress(current, total)
                } else {
                    showFloatingView()
                    updateStepCount(stepCount)
                }
            }
        }

        return START_STICKY
    }

    override fun onDestroy() {
        removeFloatingView()
        super.onDestroy()
    }

    private fun showFloatingView() {
        if (floatingView != null) {
            updateStepCount(0)
            return
        }

        windowManager = getSystemService(WINDOW_SERVICE) as WindowManager

        val inflater = getSystemService(LAYOUT_INFLATER_SERVICE) as LayoutInflater
        floatingView = inflater.inflate(
            resources.getIdentifier("layout_floating_control", "layout", packageName),
            null
        )

        btnPause = floatingView?.findViewById(
            resources.getIdentifier("btn_floating_pause", "id", packageName)
        )
        btnStop = floatingView?.findViewById(
            resources.getIdentifier("btn_floating_stop", "id", packageName)
        )
        tvStepCount = floatingView?.findViewById(
            resources.getIdentifier("tv_floating_step_count", "id", packageName)
        )

        // Button click handlers
        btnPause?.setOnClickListener {
            val actionIntent = Intent(this, FloatingControlService::class.java).apply {
                action = if (btnPause?.tag == "paused") ACTION_RESUME else ACTION_PAUSE
            }
            startService(actionIntent)
        }

        btnStop?.setOnClickListener {
            val actionIntent = Intent(this, FloatingControlService::class.java).apply {
                action = ACTION_STOP
            }
            startService(actionIntent)
        }

        // Tap to open app
        floatingView?.findViewById<View>(
            resources.getIdentifier("layout_floating_root", "id", packageName)
        )?.setOnLongClickListener {
            val openIntent = packageManager.getLaunchIntentForPackage(packageName)
            openIntent?.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            startActivity(openIntent)
            true
        }

        val layoutType = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY
        } else {
            @Suppress("DEPRECATION")
            WindowManager.LayoutParams.TYPE_PHONE
        }

        val params = WindowManager.LayoutParams(
            WindowManager.LayoutParams.WRAP_CONTENT,
            WindowManager.LayoutParams.WRAP_CONTENT,
            layoutType,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE
                    or WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN
                    or WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS,
            PixelFormat.TRANSLUCENT
        ).apply {
            gravity = Gravity.TOP or Gravity.END
            x = (16 * resources.displayMetrics.density).toInt()
            y = (48 * resources.displayMetrics.density).toInt()
        }

        try {
            windowManager?.addView(floatingView, params)
        } catch (e: Exception) {
            // Fallback: use notification-only mode
            Toast.makeText(this, "悬浮窗权限不足，请前往设置开启", Toast.LENGTH_LONG).show()
        }
    }

    private fun removeFloatingView() {
        try {
            floatingView?.let { windowManager?.removeView(it) }
        } catch (_: Exception) { }
        floatingView = null
        btnPause = null
        btnStop = null
        tvStepCount = null
        removeProgressView()
    }

    private fun showProgressView() {
        if (progressView != null) return
        if (windowManager == null) {
            windowManager = getSystemService(WINDOW_SERVICE) as WindowManager
        }

        val inflater = getSystemService(LAYOUT_INFLATER_SERVICE) as LayoutInflater
        progressView = inflater.inflate(
            resources.getIdentifier("layout_replay_progress", "layout", packageName),
            null
        )

        val layoutType = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY
        } else {
            @Suppress("DEPRECATION")
            WindowManager.LayoutParams.TYPE_PHONE
        }

        val params = WindowManager.LayoutParams(
            WindowManager.LayoutParams.MATCH_PARENT,
            WindowManager.LayoutParams.WRAP_CONTENT,
            layoutType,
            // NOT_TOUCHABLE：手势必须穿透进度条，否则回放点击全被浮层吃掉
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE
                    or WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE
                    or WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN
                    or WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS,
            PixelFormat.TRANSLUCENT
        ).apply {
            gravity = Gravity.TOP or Gravity.START
            y = 0
        }

        try {
            windowManager?.addView(progressView, params)
        } catch (_: Exception) { }
    }

    /** 回放模式去掉录制悬浮条，保留进度条容器状态 */
    private fun removeFloatingViewKeepProgress() {
        try {
            floatingView?.let { windowManager?.removeView(it) }
        } catch (_: Exception) { }
        floatingView = null
        btnPause = null
        btnStop = null
        tvStepCount = null
    }

    private fun removeProgressView() {
        try {
            progressView?.let { windowManager?.removeView(it) }
        } catch (_: Exception) { }
        progressView = null
    }

    private fun updateReplayProgress(current: Int, total: Int) {
        if (progressView == null) {
            showProgressView()
        }
        val tv = progressView?.findViewById<TextView>(
            resources.getIdentifier("tv_progress_text", "id", packageName)
        )
        val pb = progressView?.findViewById<ProgressBar>(
            resources.getIdentifier("pb_replay_progress", "id", packageName)
        )
        tv?.text = "${current}/${total}"
        pb?.max = if (total > 0) total else 1
        pb?.progress = current
    }

    private fun updateReplayComplete(result: String) {
        val tvLabel = progressView?.findViewById<TextView>(
            resources.getIdentifier("tv_progress_label", "id", packageName)
        )
        val tvText = progressView?.findViewById<TextView>(
            resources.getIdentifier("tv_progress_text", "id", packageName)
        )
        tvLabel?.text = result
        tvText?.text = ""

        android.os.Handler(mainLooper).postDelayed({
            removeProgressView()
        }, 3000)
    }

    private fun updatePauseButton(isRecording: Boolean) {
        btnPause?.let { btn ->
            if (isRecording) {
                // Show pause icon
                btn.setImageResource(android.R.drawable.ic_media_pause)
                btn.tag = "recording"
            } else {
                // Show resume icon
                btn.setImageResource(android.R.drawable.ic_media_play)
                btn.tag = "paused"
            }
        }
    }

    fun updateStepCount(count: Int) {
        tvStepCount?.text = "$count 步"
    }

    private fun stopRecorderForegroundService() {
        try {
            stopService(Intent(this, RecorderForegroundService::class.java))
        } catch (_: Exception) { }
    }

    private fun updateNotificationTitle(title: String) {
        if (mode != "recording") return
        val manager = getSystemService(NOTIFICATION_SERVICE) as NotificationManager
        val notification = buildNotification(
            title = title,
            content = tvStepCount?.text?.toString() ?: "Testory 运行中"
        )
        manager.notify(NOTIFICATION_ID, notification)
    }

    private fun updateNotificationStepCount(count: Int) {
        if (mode != "recording") return
        val manager = getSystemService(NOTIFICATION_SERVICE) as NotificationManager
        val content = if (count > 0) "已录制 $count 步" else "Testory 运行中"
        val notification = buildNotification(
            title = if (btnPause?.tag == "paused") "录制已暂停" else "录制中",
            content = content
        )
        manager.notify(NOTIFICATION_ID, notification)
    }

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                CHANNEL_ID,
                "Testory 悬浮窗控制",
                NotificationManager.IMPORTANCE_LOW
            ).apply {
                description = "录制/回放悬浮窗服务"
                setShowBadge(false)
            }
            val manager = getSystemService(NOTIFICATION_SERVICE) as NotificationManager
            manager.createNotificationChannel(channel)
        }

        val notification = buildNotification(
            title = if (mode == "recording") "录制中" else "回放中",
            content = "Testory 正在运行"
        )

        try {
            startForeground(NOTIFICATION_ID, notification)
        } catch (_: Exception) { }
    }

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
}
