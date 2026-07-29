package com.testory.assistant.v2.service.foreground

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.graphics.Color
import android.graphics.PixelFormat
import android.graphics.drawable.GradientDrawable
import android.os.Build
import android.os.IBinder
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.view.WindowManager
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.core.app.NotificationCompat
import com.testory.assistant.v2.service.accessibility.CaptureSessionController
import com.testory.assistant.v2.service.accessibility.PickModeController
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch

/**
 * 元素捕获悬浮窗：用户可先自由逛到目标页，再点「捕获」拾取控件。
 */
class ElementCaptureService : Service() {

    companion object {
        const val CHANNEL_ID = "testory_element_capture"
        const val NOTIFICATION_ID = 10088

        const val EXTRA_CASE_ID = "extra_case_id"
        const val EXTRA_AFTER_INDEX = "extra_after_index"
        const val EXTRA_KIND = "extra_kind" // CREATE | REPICK
        const val EXTRA_STEP_ID = "extra_step_id"

        const val ACTION_ARM_CAPTURE = "com.testory.assistant.v2.CAPTURE_ARM"
        const val ACTION_CANCEL = "com.testory.assistant.v2.CAPTURE_CANCEL"
    }

    private var windowManager: WindowManager? = null
    private var floatingView: View? = null
    private var statusTv: TextView? = null
    private var captureBtn: TextView? = null
    private var pickJob: Job? = null
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        createChannel()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_ARM_CAPTURE -> armCapture()
            ACTION_CANCEL -> {
                finishCapture(null)
                return START_NOT_STICKY
            }
            else -> {
                val caseId = intent?.getStringExtra(EXTRA_CASE_ID).orEmpty()
                if (caseId.isBlank()) {
                    stopSelf()
                    return START_NOT_STICKY
                }
                val kind = when (intent?.getStringExtra(EXTRA_KIND)) {
                    "REPICK" -> CaptureSessionController.Kind.REPICK
                    else -> CaptureSessionController.Kind.CREATE
                }
                CaptureSessionController.begin(
                    CaptureSessionController.Request(
                        caseId = caseId,
                        afterIndex = intent?.getIntExtra(EXTRA_AFTER_INDEX, -1) ?: -1,
                        kind = kind,
                        stepId = intent?.getStringExtra(EXTRA_STEP_ID).orEmpty()
                    )
                )
                startForeground(NOTIFICATION_ID, buildNotification("请前往目标页面，再点「捕获」"))
                showFloating()
                Toast.makeText(this, "请先打开目标页面，再点悬浮窗「捕获」", Toast.LENGTH_LONG).show()
            }
        }
        return START_STICKY
    }

    override fun onDestroy() {
        pickJob?.cancel()
        PickModeController.cancel()
        removeFloating()
        super.onDestroy()
    }

    private fun armCapture() {
        if (PickModeController.isActive()) {
            Toast.makeText(this, "已在捕获中，请点击目标控件", Toast.LENGTH_SHORT).show()
            return
        }
        statusTv?.text = "请点击目标控件…"
        captureBtn?.isEnabled = false
        PickModeController.startPick()
        pickJob?.cancel()
        pickJob = scope.launch {
            val picked = PickModeController.awaitPick(90_000L)
            if (picked != null) {
                Toast.makeText(this@ElementCaptureService, "已捕获：${picked.label}", Toast.LENGTH_SHORT).show()
                finishCapture(picked)
            } else {
                statusTv?.text = "已取消或超时，可再点「捕获」"
                captureBtn?.isEnabled = true
            }
        }
    }

    private fun finishCapture(picked: com.testory.assistant.v2.service.accessibility.PickedElement?) {
        pickJob?.cancel()
        PickModeController.cancel()
        if (picked != null) {
            CaptureSessionController.complete(picked)
        } else {
            CaptureSessionController.cancel()
        }
        bringAppFront()
        removeFloating()
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    private fun bringAppFront() {
        try {
            val launch = packageManager.getLaunchIntentForPackage(packageName)?.apply {
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_REORDER_TO_FRONT)
            }
            if (launch != null) startActivity(launch)
        } catch (_: Exception) { }
    }

    private fun showFloating() {
        if (floatingView != null) return
        windowManager = getSystemService(WINDOW_SERVICE) as WindowManager

        val density = resources.displayMetrics.density
        fun dp(v: Int) = (v * density).toInt()

        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(14), dp(12), dp(14), dp(12))
            background = GradientDrawable().apply {
                cornerRadius = dp(14).toFloat()
                setColor(Color.parseColor("#E6121820"))
                setStroke(dp(1), Color.parseColor("#66FFFFFF"))
            }
            elevation = dp(8).toFloat()
        }

        statusTv = TextView(this).apply {
            text = "元素捕获 · 先到目标页"
            setTextColor(Color.WHITE)
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 13f)
        }
        root.addView(statusTv)

        val row = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            setPadding(0, dp(10), 0, 0)
        }

        captureBtn = TextView(this).apply {
            text = "捕获"
            setTextColor(Color.WHITE)
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 14f)
            setPadding(dp(16), dp(8), dp(16), dp(8))
            background = GradientDrawable().apply {
                cornerRadius = dp(8).toFloat()
                setColor(Color.parseColor("#3B82F6"))
            }
            setOnClickListener {
                startService(Intent(this@ElementCaptureService, ElementCaptureService::class.java).apply {
                    action = ACTION_ARM_CAPTURE
                })
            }
        }
        val cancelBtn = TextView(this).apply {
            text = "取消"
            setTextColor(Color.WHITE)
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 14f)
            setPadding(dp(16), dp(8), dp(16), dp(8))
            background = GradientDrawable().apply {
                cornerRadius = dp(8).toFloat()
                setColor(Color.parseColor("#64748B"))
            }
            setOnClickListener {
                startService(Intent(this@ElementCaptureService, ElementCaptureService::class.java).apply {
                    action = ACTION_CANCEL
                })
            }
        }
        row.addView(captureBtn)
        row.addView(View(this), LinearLayout.LayoutParams(dp(8), 1))
        row.addView(cancelBtn)
        root.addView(row)

        floatingView = root

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
                    or WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN,
            PixelFormat.TRANSLUCENT
        ).apply {
            gravity = Gravity.TOP or Gravity.CENTER_HORIZONTAL
            y = dp(72)
        }
        try {
            windowManager?.addView(root, params)
        } catch (_: Exception) {
            Toast.makeText(this, "请开启悬浮窗权限后再试", Toast.LENGTH_LONG).show()
            finishCapture(null)
        }
    }

    private fun removeFloating() {
        try {
            floatingView?.let { windowManager?.removeView(it) }
        } catch (_: Exception) { }
        floatingView = null
        statusTv = null
        captureBtn = null
    }

    private fun createChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val nm = getSystemService(NotificationManager::class.java) ?: return
        nm.createNotificationChannel(
            NotificationChannel(CHANNEL_ID, "元素捕获", NotificationManager.IMPORTANCE_LOW)
        )
    }

    private fun buildNotification(content: String): Notification {
        val cancelPi = PendingIntent.getService(
            this, 1,
            Intent(this, ElementCaptureService::class.java).setAction(ACTION_CANCEL),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("Testory 元素捕获")
            .setContentText(content)
            .setSmallIcon(android.R.drawable.ic_menu_camera)
            .setOngoing(true)
            .addAction(0, "取消", cancelPi)
            .build()
    }
}
