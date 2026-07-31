package com.testory.assistant.v2.service.accessibility

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.content.Intent
import android.graphics.Path
import android.graphics.PixelFormat
import android.graphics.Rect
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.view.Display
import android.view.Gravity
import android.view.LayoutInflater
import android.view.View
import android.view.WindowManager
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import android.widget.ImageButton
import android.widget.TextView
import com.testory.assistant.v2.core.model.*
import dagger.hilt.android.AndroidEntryPoint
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.*
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import javax.inject.Inject

/**
 * 重构后的无障碍服务 — Kotlin 重写，事件管线模式。
 *
 * 相比 v1 (Java 49KB 巨石) 的关键改进:
 * 1. 事件处理管线化: 采集 → 过滤 → 分类 → 转换 → 持久化
 * 2. node 与 operation_node 严格分离 (不再混用导致定位偏差)
 * 3. viewport_coord 总是写入真实坐标 (修复坐标 (0,0) 静默失败)
 * 4. 坐标与 selector 来源互斥标记 (LocationSource)
 * 5. 在主线程中把 AccessibilityNodeInfo 提取为纯数据，避免跨线程 source 失效
 */
@AndroidEntryPoint
class AssistantAccessibilityService : AccessibilityService() {

    @Inject lateinit var eventPipeline: EventPipeline
    @Inject lateinit var nodeAnalyzer: NodeAnalyzer
    @Inject lateinit var pcRunJobPoller: PcRunJobPoller

    // ── Session state ──
    private val _sessionState = MutableStateFlow(SessionState())
    val sessionState: StateFlow<SessionState> = _sessionState.asStateFlow()

    // ── Event processing pipeline steps ──
    private val rawEventFlow = MutableSharedFlow<RecordedEvent>(
        extraBufferCapacity = 64,
        onBufferOverflow = kotlinx.coroutines.channels.BufferOverflow.DROP_OLDEST
    )

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)

    // ── Floating control bar (TYPE_ACCESSIBILITY_OVERLAY persists across apps) ──
    private var floatingView: View? = null
    private var floatingBtnPause: ImageButton? = null
    private var floatingBtnStop: ImageButton? = null
    private var floatingTvCount: TextView? = null

    // ── Package tracking（仅更新会话包名；打开应用按桌面 TAP 录制）──
    private var lastRecordedPackage: String = ""
    private var lastDirectClickMs: Long = 0

    // ── PC JSON-RPC tunnel (adb forward) ──
    private var pluginHttpServer: PluginHttpServer? = null

    override fun onServiceConnected() {
        super.onServiceConnected()
        AccessibilityServiceHolder.attach(this)
        scope.launch {
            eventPipeline.start(rawEventFlow, sessionState)
        }
        try {
            pcRunJobPoller.start(scope)
        } catch (e: Exception) {
            android.util.Log.e("AssistantA11y", "PcRunJobPoller start failed", e)
        }
        try {
            pluginHttpServer = PluginHttpServer(applicationContext) { this }.also { it.start() }
        } catch (e: Exception) {
            android.util.Log.e("AssistantA11y", "PluginHttpServer start failed", e)
        }
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent) {
        // 在主线程中立即提取 source 信息并转为纯数据，避免跨线程后 AccessibilityNodeInfo 失效。
        // 这是录制坐标不丢失的关键：AccessibilityNodeInfo 在事件传递后可能被系统回收。
        // 当 event.source 为 null 时（部分 WebView/自定义控件/透明浮层不暴露 source），
        // 用当前窗口的聚焦节点作为坐标回退，避免录制成 (0,0)。
        val sourceNode = try {
            extractSourceNode(event)
        } catch (e: Exception) {
            android.util.Log.e("AssistantA11y", "Failed to extract source node for event ${event.eventType}", e)
            null
        }

        var sourceBounds: ScreenRect? = null
        if (sourceNode != null && sourceNode.bounds.isValid) {
            sourceBounds = sourceNode.bounds
        } else {
            try {
                event.source?.let { node ->
                    val r = Rect()
                    node.getBoundsInScreen(r)
                    if (r.width() > 0 || r.height() > 0) {
                        sourceBounds = ScreenRect(r.left, r.top, r.right, r.bottom)
                    }
                }
            } catch (_: Exception) { }
        }

        val recordedEvent = RecordedEvent(
            eventType = event.eventType,
            packageName = event.packageName?.toString() ?: "",
            className = event.className?.toString() ?: "",
            text = event.text?.joinToString(" ") ?: "",
            sourceNode = sourceNode,
            sourceBounds = sourceBounds,
            eventTime = event.eventTime,
            scrollX = event.scrollX,
            scrollY = event.scrollY
        )

        // Feed event into pipeline (non-blocking)
        rawEventFlow.tryEmit(recordedEvent)

        // Track click timestamp for app-launch detection
        if (event.eventType == AccessibilityEvent.TYPE_VIEW_CLICKED
            || event.eventType == AccessibilityEvent.TYPE_VIEW_LONG_CLICKED) {
            lastDirectClickMs = System.currentTimeMillis()
        }

        // Handle special events
        when (event.eventType) {
            AccessibilityEvent.TYPE_NOTIFICATION_STATE_CHANGED -> {
                val pkg = event.packageName?.toString().orEmpty()
                val text = buildString {
                    event.text?.forEach { append(it).append(' ') }
                    event.contentDescription?.let { append(it) }
                }.trim()
                if (text.isNotBlank()) {
                    NotificationTextBuffer.push(text, pkg)
                }
            }
            AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED,
            AccessibilityEvent.TYPE_WINDOW_CONTENT_CHANGED -> {
                handleWindowEvent(event)
            }
        }
    }

    /**
     * 提取事件源节点。
     * 1) event.source
     * 2) 点击坐标 hit-test（findBestNode）——修复 WebView/Flutter/自定义 View source 为空或过大容器
     * 3) 聚焦节点回退
     */
    private fun extractSourceNode(event: AccessibilityEvent): NodeInfo? {
        var fromSource: NodeInfo? = null
        var sourceBounds: ScreenRect? = null

        event.source?.let { node ->
            try {
                fromSource = nodeAnalyzer.extractNodeInfo(node)
                if (fromSource?.bounds?.isValid == true) {
                    sourceBounds = fromSource?.bounds
                }
            } finally {
                try { node.recycle() } catch (_: Exception) {}
            }
        }

        val needHitTest = event.eventType == AccessibilityEvent.TYPE_VIEW_CLICKED ||
            event.eventType == AccessibilityEvent.TYPE_VIEW_LONG_CLICKED ||
            event.eventType == AccessibilityEvent.TYPE_VIEW_SELECTED ||
            event.eventType == AccessibilityEvent.TYPE_TOUCH_INTERACTION_START

        if (needHitTest) {
            val root = rootInActiveWindow
            if (root != null) {
                try {
                    val cx = sourceBounds?.centerX
                        ?: fromSource?.bounds?.centerX
                        ?: 0
                    val cy = sourceBounds?.centerY
                        ?: fromSource?.bounds?.centerY
                        ?: 0
                    if (cx > 0 || cy > 0) {
                        val best = nodeAnalyzer.findBestNode(root, cx, cy)
                        if (best != null && nodeIdentityScore(best) >= nodeIdentityScore(fromSource)) {
                            return best
                        }
                    }
                    // source 有 bounds 但无标签时，补全桌面图标旁应用名
                    if (fromSource != null && fromSource!!.text.isBlank()
                        && fromSource!!.contentDescription.isBlank()
                    ) {
                        return nodeAnalyzer.enrichWithNearbyLabel(root, fromSource!!)
                    }
                    // source 缺失时，尝试用窗口中心附近不做盲点；保留 fromSource
                } finally {
                    try { root.recycle() } catch (_: Exception) {}
                }
            }
        }

        if (fromSource != null) {
            // 再试一次补全标签（hit-test 未跑或未改善时）
            val root2 = rootInActiveWindow
            if (root2 != null && fromSource!!.text.isBlank() && fromSource!!.contentDescription.isBlank()) {
                try {
                    return nodeAnalyzer.enrichWithNearbyLabel(root2, fromSource!!)
                } finally {
                    try { root2.recycle() } catch (_: Exception) {}
                }
            }
            return fromSource
        }

        if (event.eventType == AccessibilityEvent.TYPE_VIEW_CLICKED ||
            event.eventType == AccessibilityEvent.TYPE_VIEW_LONG_CLICKED ||
            event.eventType == AccessibilityEvent.TYPE_VIEW_FOCUSED ||
            event.eventType == AccessibilityEvent.TYPE_VIEW_SELECTED) {
            val root = rootInActiveWindow ?: return null
            try {
                val focused = root.findFocus(AccessibilityNodeInfo.FOCUS_INPUT)
                    ?: root.findFocus(AccessibilityNodeInfo.FOCUS_ACCESSIBILITY)
                if (focused != null) {
                    return nodeAnalyzer.extractNodeInfo(focused).also {
                        try { focused.recycle() } catch (_: Exception) {}
                    }
                }
            } finally {
                try { root.recycle() } catch (_: Exception) {}
            }
        }

        return null
    }

    private fun nodeIdentityScore(node: NodeInfo?): Int {
        if (node == null) return -1
        var score = 0
        if (node.text.isNotBlank()) score += 40
        if (node.contentDescription.isNotBlank()) score += 35
        if (node.resourceId.isNotBlank()) score += 45
        if (node.isClickable || node.isEditable) score += 15
        val w = (node.bounds.right - node.bounds.left).coerceAtLeast(0)
        val h = (node.bounds.bottom - node.bounds.top).coerceAtLeast(0)
        val area = w * h
        // 更小的可交互叶子更优
        if (area in 1..80_000) score += 20
        if (area > 200_000) score -= 30
        val cls = node.className.lowercase()
        if ("webview" in cls || "flutter" in cls || "surfaceview" in cls) score -= 40
        return score
    }

    override fun onInterrupt() {
        _sessionState.update { it.copy(armedMode = ArmedMode.IDLE) }
    }

    override fun onDestroy() {
        hideFloatingControl()
        try { pcRunJobPoller.stop() } catch (_: Exception) {}
        try { pluginHttpServer?.stop() } catch (_: Exception) {}
        pluginHttpServer = null
        AccessibilityServiceHolder.detach(this)
        scope.cancel()
        super.onDestroy()
    }

    // ── Public API for other modules ──

    fun startRecording(): Boolean {
        if (_sessionState.value.armedMode != ArmedMode.IDLE) return false
        _sessionState.update {
            it.copy(
                armedMode = ArmedMode.RECORDING,
                isRecording = true,
                recordStartTime = System.currentTimeMillis()
            )
        }
        showFloatingControl()
        return true
    }

    fun stopRecording() {
        _sessionState.update {
            it.copy(
                armedMode = ArmedMode.IDLE,
                isRecording = false,
                isPaused = false
            )
        }
        hideFloatingControl()
    }

    fun pauseRecording() {
        _sessionState.update { it.copy(isPaused = true) }
        floatingBtnPause?.tag = "paused"
        floatingBtnPause?.setImageResource(android.R.drawable.ic_media_play)
    }

    fun resumeRecording() {
        _sessionState.update { it.copy(isPaused = false) }
        floatingBtnPause?.tag = null
        floatingBtnPause?.setImageResource(android.R.drawable.ic_media_pause)
    }

    /**
     * 执行回放 — 按步骤顺序执行用户操作。
     */
    fun executeStep(step: Step): StepResult {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.N) {
            return StepResult(
                stepIndex = step.index,
                stepId = step.id,
                success = false,
                errorMessage = "Gesture API requires API 24+"
            )
        }

        val startTime = System.currentTimeMillis()
        val rootNode = rootInActiveWindow ?: run {
            return StepResult(
                stepIndex = step.index,
                stepId = step.id,
                success = false,
                errorMessage = "No active window root node"
            )
        }

        return try {
            val rootPkg = rootNode.packageName?.toString().orEmpty()
            // 若仍停留在 Testory 自身界面，节点定位会命中回放页控件 → 「成功」但目标 App 无操作
            val isSelf = rootPkg.startsWith("com.testory.assistant")
            val allowOnSelf = step.action == ActionType.HOME || step.action == ActionType.BACK
                    || step.action == ActionType.WAIT || step.action == ActionType.OPEN_APP
                    || step.action == ActionType.SCREENSHOT || step.action == ActionType.HUMAN_GATE
                    || step.action == ActionType.PRESS_KEY || step.action == ActionType.CLOSE_APP
                    || step.action == ActionType.WAIT_UNTIL || step.action == ActionType.REPEAT
                    || step.action == ActionType.WHILE
            if (isSelf && !allowOnSelf) {
                return StepResult(
                    stepIndex = step.index,
                    stepId = step.id,
                    success = false,
                    errorMessage = "回放时仍在 Testory 界面（$rootPkg），已跳过以免误点自身 UI",
                    actualStrategy = "BLOCKED_ON_SELF_UI",
                    stepDescription = step.description
                )
            }

            val targetNode = locateTargetForAction(rootNode, step)
            if (targetNode == null && needsNodeForAction(step.action, step)) {
                return StepResult(
                    stepIndex = step.index,
                    stepId = step.id,
                    success = false,
                    errorMessage = "Element not found: '${step.description}'",
                    actualStrategy = "NODE_LOOKUP_FAILED",
                    stepDescription = step.description,
                    durationMs = System.currentTimeMillis() - startTime
                )
            }

            val result = performAction(targetNode, step)
            val durationMs = System.currentTimeMillis() - startTime

            result.copy(
                stepIndex = step.index,
                stepId = step.id,
                durationMs = durationMs,
                stepDescription = step.description
            )
        } catch (e: Exception) {
            StepResult(
                stepIndex = step.index,
                stepId = step.id,
                success = false,
                errorMessage = e.message ?: "Unknown error",
                durationMs = System.currentTimeMillis() - startTime,
                stepDescription = step.description
            )
        } finally {
            rootNode.recycle()
        }
    }

    /** 当前前台窗口包名（供回放确认已离开 Testory） */
    fun activeWindowPackage(): String {
        val root = rootInActiveWindow ?: return ""
        return try {
            root.packageName?.toString().orEmpty()
        } finally {
            try { root.recycle() } catch (_: Exception) {}
        }
    }

    /** 按包名拉起应用（跨应用步骤前确保上下文） */
    fun launchPackage(targetPackage: String): Boolean {
        if (targetPackage.isBlank() || targetPackage.startsWith("com.testory.assistant")) return false
        val intent = packageManager.getLaunchIntentForPackage(targetPackage) ?: return false
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_RESET_TASK_IF_NEEDED)
        return try {
            startActivity(intent)
            true
        } catch (e: Exception) {
            android.util.Log.w("AssistantA11y", "launchPackage failed: $targetPackage", e)
            false
        }
    }

    /**
     * 获取当前屏幕的控件树 (用于 AI 分析和 PC 端展示)。
     */
    fun getCurrentUiTree(): UiTree {
        val root = rootInActiveWindow ?: return UiTree(emptyList())
        return try {
            nodeAnalyzer.buildUiTree(root)
        } finally {
            root.recycle()
        }
    }

    /**
     * 坐标处最优节点（供 PC JSON-RPC pickAtPoint）。
     */
    fun pickNodeAt(x: Int, y: Int): NodeInfo? {
        val root = rootInActiveWindow ?: return null
        return try {
            nodeAnalyzer.findBestNode(root, x, y)
        } finally {
            try { root.recycle() } catch (_: Exception) {}
        }
    }

    /**
     * 执行滑动手势（同步等待完成；异步 fire-and-forget 会导致假成功）。
     */
    fun performGesture(
        path: Path,
        durationMs: Long = 300,
        callback: ((Boolean) -> Unit)? = null
    ): Boolean {
        if (callback != null) {
            if (Build.VERSION.SDK_INT < Build.VERSION_CODES.N) {
                callback(false)
                return false
            }
            val gesture = GestureDescription.Builder()
                .addStroke(GestureDescription.StrokeDescription(path, 0, durationMs.coerceAtLeast(1L)))
                .build()
            val start = Runnable {
                val ok = dispatchGesture(gesture, object : GestureResultCallback() {
                    override fun onCompleted(gestureDescription: GestureDescription?) = callback(true)
                    override fun onCancelled(gestureDescription: GestureDescription?) = callback(false)
                }, null)
                if (!ok) callback(false)
            }
            if (Looper.myLooper() == Looper.getMainLooper()) start.run()
            else Handler(Looper.getMainLooper()).post(start)
            return true
        }
        return performGestureSync(path, durationMs)
    }

    /**
     * 执行点击手势（同步等待完成）。
     */
    fun performClick(x: Float, y: Float, callback: ((Boolean) -> Unit)? = null): Boolean {
        // 带 callback 时走异步派发，避免在主线程上 CountDownLatch 死锁
        // （PluginHttpServer.runOnMainAwaitGesture 会在主线程调用本方法）
        if (callback != null) {
            return dispatchClickAsync(x, y, callback)
        }
        return performClickSync(x, y)
    }

    fun performClickSync(x: Float, y: Float, timeoutMs: Long = 3000L): Boolean {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.N) return false
        // 部分 OEM 对「单点 moveTo + 极短 duration」点击识别不稳，补 1px 位移并加长到 100ms
        val path = Path().apply {
            moveTo(x, y)
            lineTo(x + 1f, y + 1f)
        }
        return performGestureSync(path, durationMs = 100L, timeoutMs = timeoutMs)
    }

    /**
     * 同步等待手势完成。必须在非主线程调用。
     * dispatchGesture 一律 post 到主线程执行（部分机型后台线程直接派发会「回调成功但未注入」）。
     */
    fun performGestureSync(
        path: Path,
        durationMs: Long = 300,
        timeoutMs: Long = 5000L
    ): Boolean {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.N) return false
        if (Looper.myLooper() == Looper.getMainLooper()) {
            android.util.Log.e(
                "AssistantA11y",
                "performGestureSync called on main thread — would deadlock; refusing"
            )
            return false
        }
        val latch = CountDownLatch(1)
        val completed = AtomicBoolean(false)
        val accepted = AtomicBoolean(false)
        val gesture = GestureDescription.Builder()
            .addStroke(GestureDescription.StrokeDescription(path, 0, durationMs.coerceAtLeast(1L)))
            .build()
        val mainHandler = Handler(Looper.getMainLooper())
        mainHandler.post {
            try {
                val ok = dispatchGesture(gesture, object : GestureResultCallback() {
                    override fun onCompleted(gestureDescription: GestureDescription?) {
                        completed.set(true)
                        latch.countDown()
                    }
                    override fun onCancelled(gestureDescription: GestureDescription?) {
                        completed.set(false)
                        latch.countDown()
                    }
                }, null)
                accepted.set(ok)
                if (!ok) {
                    android.util.Log.w("AssistantA11y", "dispatchGesture rejected by system")
                    latch.countDown()
                }
            } catch (e: Exception) {
                android.util.Log.e("AssistantA11y", "dispatchGesture failed", e)
                accepted.set(false)
                latch.countDown()
            }
        }
        val waited = latch.await(timeoutMs, TimeUnit.MILLISECONDS)
        val success = waited && accepted.get() && completed.get()
        if (!success) {
            android.util.Log.w(
                "AssistantA11y",
                "gesture sync failed waited=$waited accepted=${accepted.get()} completed=${completed.get()}"
            )
        }
        return success
    }

    private fun dispatchClickAsync(x: Float, y: Float, callback: (Boolean) -> Unit): Boolean {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.N) {
            callback(false)
            return false
        }
        val path = Path().apply {
            moveTo(x, y)
            lineTo(x + 1f, y + 1f)
        }
        val gesture = GestureDescription.Builder()
            .addStroke(GestureDescription.StrokeDescription(path, 0, 100L))
            .build()
        val start = Runnable {
            val ok = dispatchGesture(gesture, object : GestureResultCallback() {
                override fun onCompleted(gestureDescription: GestureDescription?) = callback(true)
                override fun onCancelled(gestureDescription: GestureDescription?) = callback(false)
            }, null)
            if (!ok) callback(false)
        }
        if (Looper.myLooper() == Looper.getMainLooper()) {
            start.run()
        } else {
            Handler(Looper.getMainLooper()).post(start)
        }
        return true
    }

    fun updateFloatingStepCount(count: Int) {
        floatingTvCount?.post { floatingTvCount?.text = "$count 步" }
    }

    // ── Floating control bar (TYPE_ACCESSIBILITY_OVERLAY) ──
    // 使用无障碍覆盖层类型，确保进入其他应用后悬浮窗仍然可见。
    // 原缺陷：RecorderForegroundService 使用 TYPE_APPLICATION_OVERLAY，
    // 在 Android 10+ 切换应用后悬浮窗被系统隐藏。

    private fun showFloatingControl() {
        if (floatingView != null) return

        try {
            val wm = getSystemService(WINDOW_SERVICE) as WindowManager
            val inflater = getSystemService(LAYOUT_INFLATER_SERVICE) as LayoutInflater
            val layoutId = resources.getIdentifier("layout_floating_control", "layout", packageName)

            floatingView = inflater.inflate(layoutId, null)
            floatingBtnPause = floatingView?.findViewById(
                resources.getIdentifier("btn_floating_pause", "id", packageName))
            floatingBtnStop = floatingView?.findViewById(
                resources.getIdentifier("btn_floating_stop", "id", packageName))
            floatingTvCount = floatingView?.findViewById(
                resources.getIdentifier("tv_floating_step_count", "id", packageName))

            floatingBtnPause?.setOnClickListener {
                if (floatingBtnPause?.tag == "paused") resumeRecording() else pauseRecording()
            }
            floatingBtnStop?.setOnClickListener {
                stopRecording()
                val app = applicationContext
                app.stopService(Intent(app, com.testory.assistant.v2.service.foreground.RecorderForegroundService::class.java))
            }

            val layoutType = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY
            } else if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
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

            wm.addView(floatingView, params)
        } catch (e: Exception) {
            android.util.Log.e("AssistantA11y", "Failed to show floating control: ${e.message}")
        }
    }

    private fun hideFloatingControl() {
        try {
            floatingView?.let {
                val wm = getSystemService(WINDOW_SERVICE) as WindowManager
                wm.removeView(it)
            }
        } catch (_: Exception) {}
        floatingView = null
        floatingBtnPause = null
        floatingBtnStop = null
        floatingTvCount = null
    }

    // ── Private ──

    private fun handleWindowEvent(event: AccessibilityEvent) {
        val pkg = event.packageName?.toString() ?: return
        val className = event.className?.toString() ?: ""
        _sessionState.update {
            it.copy(
                currentPackage = pkg,
                currentActivity = className
            )
        }

        // 更新当前包名；打开应用由桌面图标 TAP 步骤表达，不再合成 OPEN_APP
        if (event.eventType == AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED
            && _sessionState.value.isRecording
            && !_sessionState.value.isPaused
            && pkg != lastRecordedPackage
        ) {
            lastRecordedPackage = pkg
        }
    }

    /**
     * 多级定位 — text / hint / content-desc / resource-id 优先，坐标仅作无 selector 时的兜底。
     * 跨页滑动**仅限桌面/Launcher**；应用内只做短等待重试，避免把登录页划走。
     */
    private fun locateTargetForAction(
        root: AccessibilityNodeInfo,
        step: Step
    ): AccessibilityNodeInfo? {
        val hasSelector = step.locator.text.isNotBlank()
                || step.locator.contentDesc.isNotBlank()
                || step.locator.resourceId.isNotBlank()

        // 1) 当前页 selector
        locateBySelector(root, step)?.let { return it }

        if (hasSelector) {
            val pkg = root.packageName?.toString().orEmpty()
            if (!isLauncherPackage(pkg)) {
                // 应用内：页面切换常有延迟，轮询等待（绝不左右翻页）
                locateBySelectorWithRetry(step, timeoutMs = 2500L)?.let { return it }
                return null
            }
            // 仅桌面：跨页找应用图标
            locateAcrossPages(step)?.let { return it }
            return null
        }

        return locateByCoordinate(root, step)
    }

    private fun isLauncherPackage(pkg: String): Boolean {
        val p = pkg.lowercase()
        if (p.isBlank()) return false
        return p.contains("launcher")
                || p.contains("homescreen")
                || p.contains("leanbacklauncher")
                || p == "com.android.launcher3"
                || p.contains("ldmobile")
                || p.contains("flysilkworm")
    }

    private fun locateBySelectorWithRetry(step: Step, timeoutMs: Long): AccessibilityNodeInfo? {
        val deadline = System.currentTimeMillis() + timeoutMs
        while (System.currentTimeMillis() < deadline) {
            try {
                Thread.sleep(250)
            } catch (_: InterruptedException) {
                return null
            }
            val r = rootInActiveWindow ?: continue
            try {
                locateBySelector(r, step)?.let { return it }
            } finally {
                try { r.recycle() } catch (_: Exception) {}
            }
        }
        return null
    }

    private fun locateBySelector(root: AccessibilityNodeInfo, step: Step): AccessibilityNodeInfo? {
        val locator = step.locator
        val preferCheckable = wantsCheckable(step)

        if (locator.text.isNotBlank()) {
            pickBestTextMatch(root, locator.text, preferCheckable)?.let { textNode ->
                if (preferCheckable) {
                    resolveCheckableTarget(root, textNode)?.let { checkable ->
                        if (checkable != textNode) {
                            try { textNode.recycle() } catch (_: Exception) {}
                        }
                        return checkable
                    }
                }
                return textNode
            }
            findNodeByHintOrEditableLabel(root, locator.text)?.let { return it }
        }
        if (locator.contentDesc.isNotBlank()) {
            val results = root.findAccessibilityNodeInfosByText(locator.contentDesc)
            if (results.isNotEmpty()) {
                val best = pickPreferClickable(results, preferCheckable)
                results.filter { it != best }.forEach { try { it.recycle() } catch (_: Exception) {} }
                if (best != null) {
                    if (preferCheckable) {
                        resolveCheckableTarget(root, best)?.let { checkable ->
                            if (checkable != best) {
                                try { best.recycle() } catch (_: Exception) {}
                            }
                            return checkable
                        }
                    }
                    return best
                }
            }
            findNodeByContentDesc(root, locator.contentDesc)?.let { return it }
            findNodeByHintOrEditableLabel(root, locator.contentDesc)?.let { return it }
        }
        if (locator.resourceId.isNotBlank()) {
            val results = root.findAccessibilityNodeInfosByViewId(locator.resourceId)
            if (results.isNotEmpty()) {
                val best = pickPreferClickable(results, preferCheckable)
                results.filter { it != best }.forEach { try { it.recycle() } catch (_: Exception) {} }
                if (best != null) return best
            }
        }
        return null
    }

    /** 按 hint / 占位文案查找，优先返回可编辑输入框 */
    private fun findNodeByHintOrEditableLabel(
        root: AccessibilityNodeInfo,
        label: String
    ): AccessibilityNodeInfo? {
        val target = label.trim()
        if (target.isEmpty()) return null
        var bestEditable: AccessibilityNodeInfo? = null
        var bestOther: AccessibilityNodeInfo? = null

        fun matchLabel(node: AccessibilityNodeInfo): Boolean {
            val text = node.text?.toString()?.trim().orEmpty()
            val desc = node.contentDescription?.toString()?.trim().orEmpty()
            val hint = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                node.hintText?.toString()?.trim().orEmpty()
            } else ""
            return text.equals(target, true) || text.contains(target, true)
                    || desc.equals(target, true) || desc.contains(target, true)
                    || hint.equals(target, true) || hint.contains(target, true)
        }

        fun walk(node: AccessibilityNodeInfo) {
            if (matchLabel(node)) {
                if (node.isEditable) {
                    bestEditable?.recycle()
                    bestEditable = AccessibilityNodeInfo.obtain(node)
                } else if (bestOther == null) {
                    bestOther = AccessibilityNodeInfo.obtain(node)
                }
            }
            for (i in 0 until node.childCount) {
                val child = node.getChild(i) ?: continue
                walk(child)
                child.recycle()
            }
        }
        walk(root)
        if (bestEditable != null) {
            bestOther?.recycle()
            return bestEditable
        }
        bestOther?.let { other ->
            val rect = Rect()
            other.getBoundsInScreen(rect)
            findEditableNear(root, rect.centerX(), rect.centerY())?.let { editable ->
                other.recycle()
                return editable
            }
        }
        return bestOther
    }

    private fun pickBestTextMatch(
        root: AccessibilityNodeInfo,
        text: String,
        preferCheckable: Boolean = false
    ): AccessibilityNodeInfo? {
        val results = root.findAccessibilityNodeInfosByText(text)
        if (results.isEmpty()) return null
        val needle = text.trim()
        fun score(node: AccessibilityNodeInfo): Int {
            val t = node.text?.toString().orEmpty()
            val cd = node.contentDescription?.toString().orEmpty()
            var s = 0
            when {
                t.equals(needle, ignoreCase = true) || cd.equals(needle, ignoreCase = true) -> s += 100
                t.startsWith(needle, ignoreCase = true) || cd.startsWith(needle, ignoreCase = true) -> s += 70
                t.contains(needle, ignoreCase = true) || cd.contains(needle, ignoreCase = true) -> s += 40
                else -> s -= 20
            }
            // 长文案里嵌协议链接时降权，避免点到超链接区域
            val longer = maxOf(t.length, cd.length)
            if (longer > needle.length * 2 + 8) s -= 25
            if (longer > needle.length * 4) s -= 25
            if (node.isEditable) s += 35
            if (preferCheckable && node.isCheckable) s += 60
            if (node.isClickable) s += 20
            if (node.isEnabled) s += 5
            val rect = Rect()
            node.getBoundsInScreen(rect)
            val area = rect.width() * rect.height()
            if (area in 1..120_000) s += 15
            if (area > 250_000) s -= 30
            return s
        }
        val best = results.maxByOrNull { score(it) }
        results.filter { it != best }.forEach { try { it.recycle() } catch (_: Exception) {} }
        return best
    }

    private fun pickPreferClickable(
        nodes: List<AccessibilityNodeInfo>,
        preferCheckable: Boolean = false
    ): AccessibilityNodeInfo? {
        if (nodes.isEmpty()) return null
        if (preferCheckable) {
            nodes.firstOrNull { it.isCheckable }?.let { return it }
        }
        return nodes.firstOrNull { it.isClickable }
            ?: nodes.firstOrNull { it.isEditable }
            ?: nodes[0]
    }

    private fun wantsCheckable(step: Step): Boolean {
        if (step.extras.preferCheckable) return true
        val blob = "${step.description} ${step.locator.text} ${step.locator.contentDesc}".lowercase()
        val keys = listOf(
            "勾选", "选中", "打勾", "勾上", "打鉤", "复选", "勾选框",
            "check ", "checkbox", "tick ", "toggle check"
        )
        return keys.any { it in blob }
    }

    private fun wantsUncheck(step: Step): Boolean {
        val blob = "${step.description} ${step.locator.text}".lowercase()
        return listOf("取消勾选", "取消选中", "取消打勾", "uncheck", "deselect").any { it in blob }
    }

    private fun looksLikeSubmitIntent(step: Step): Boolean {
        val blob = "${step.description} ${step.locator.text}".lowercase()
        val keys = listOf(
            "继续", "提交", "下一步", "完成", "确认", "登录", "注册", "发送", "开始",
            "continue", "submit", "next", "confirm", "sign in", "log in", "done", "finish",
            "agree and", "同意并"
        )
        return keys.any { it in blob }
    }

    /**
     * 勾选意图：在文案锚点附近找 checkable（通常在标签左侧），避免点协议链接。
     */
    private fun resolveCheckableTarget(
        root: AccessibilityNodeInfo,
        anchor: AccessibilityNodeInfo
    ): AccessibilityNodeInfo? {
        if (anchor.isCheckable || isLikelyCheckboxClass(anchor)) {
            return AccessibilityNodeInfo.obtain(anchor)
        }
        val anchorRect = Rect().also { anchor.getBoundsInScreen(it) }
        findCheckableInAncestors(anchor, anchorRect)?.let { return it }
        return findCheckableInTree(root, anchorRect)
    }

    private fun findCheckableNear(scope: AccessibilityNodeInfo): AccessibilityNodeInfo? {
        val anchorRect = Rect().also { scope.getBoundsInScreen(it) }
        return findCheckableInAncestors(scope, anchorRect)
    }

    private fun findCheckableInAncestors(
        start: AccessibilityNodeInfo,
        anchor: Rect
    ): AccessibilityNodeInfo? {
        var best: AccessibilityNodeInfo? = null
        var bestScore = Int.MIN_VALUE
        var cur: AccessibilityNodeInfo? = try {
            start.parent
        } catch (_: Exception) {
            null
        }
        var depth = 0
        while (cur != null && depth < 5) {
            scoreCheckablesInSubtree(cur, anchor) { node, score ->
                if (score > bestScore) {
                    best?.let { try { it.recycle() } catch (_: Exception) {} }
                    best = AccessibilityNodeInfo.obtain(node)
                    bestScore = score
                }
            }
            val next = try {
                cur.parent
            } catch (_: Exception) {
                null
            }
            try {
                cur.recycle()
            } catch (_: Exception) {
            }
            cur = next
            depth++
        }
        return best
    }

    private fun findCheckableInTree(
        root: AccessibilityNodeInfo,
        anchor: Rect
    ): AccessibilityNodeInfo? {
        var best: AccessibilityNodeInfo? = null
        var bestScore = Int.MIN_VALUE
        scoreCheckablesInSubtree(root, anchor) { node, score ->
            if (score > bestScore) {
                best?.let { try { it.recycle() } catch (_: Exception) {} }
                best = AccessibilityNodeInfo.obtain(node)
                bestScore = score
            }
        }
        return best
    }

    private fun scoreCheckablesInSubtree(
        node: AccessibilityNodeInfo,
        anchor: Rect,
        onCandidate: (AccessibilityNodeInfo, Int) -> Unit
    ) {
        if (node.isCheckable || isLikelyCheckboxClass(node)) {
            val r = Rect()
            node.getBoundsInScreen(r)
            if (r.width() > 0 && r.height() > 0) {
                val cyDist = kotlin.math.abs(r.centerY() - anchor.centerY())
                val rowSlop = maxOf(anchor.height(), r.height(), 24) * 1.5f
                if (cyDist <= rowSlop) {
                    var score = 50
                    if (node.isCheckable) score += 40
                    if (r.centerX() <= anchor.left + r.width()) score += 35
                    if (r.right <= anchor.left + 8) score += 25
                    val area = r.width() * r.height()
                    if (area in 1..20_000) score += 30
                    if (area > 80_000) score -= 50
                    score -= (cyDist / 2).toInt()
                    onCandidate(node, score)
                }
            }
        }
        for (i in 0 until node.childCount) {
            val child = node.getChild(i) ?: continue
            scoreCheckablesInSubtree(child, anchor, onCandidate)
            try {
                child.recycle()
            } catch (_: Exception) {
            }
        }
    }

    private fun isLikelyCheckboxClass(node: AccessibilityNodeInfo): Boolean {
        val cls = node.className?.toString()?.lowercase().orEmpty()
        return "checkbox" in cls || "radiobutton" in cls || "switch" in cls || "toggle" in cls
    }

    /** 标签左侧手势点：通用「框在文案左边」布局兜底 */
    private fun clickLeftOfLabel(anchor: AccessibilityNodeInfo): Boolean {
        val r = Rect()
        anchor.getBoundsInScreen(r)
        if (r.height() <= 0) return false
        val x = (r.left - r.height() * 1.15f).coerceAtLeast(4f)
        val y = r.centerY().toFloat()
        return performClickSync(x, y)
    }

    private fun nodeOrAncestorsMatchConsent(node: AccessibilityNodeInfo): Boolean {
        var p: AccessibilityNodeInfo? = AccessibilityNodeInfo.obtain(node)
        var depth = 0
        try {
            while (p != null && depth < 4) {
                val blob = buildString {
                    append(p?.text?.toString().orEmpty())
                    append(' ')
                    append(p?.contentDescription?.toString().orEmpty())
                }.lowercase()
                val keys = listOf(
                    "同意", "协议", "隐私", "条款", "已阅读", "terms", "privacy", "policy",
                    "agree", "license", "licence", "consent"
                )
                if (keys.any { it in blob }) return true
                val parent = try {
                    p?.parent
                } catch (_: Exception) {
                    null
                }
                try {
                    p?.recycle()
                } catch (_: Exception) {
                }
                p = parent
                depth++
            }
        } finally {
            try {
                p?.recycle()
            } catch (_: Exception) {
            }
        }
        return false
    }

    private fun hasBlockingUncheckedConsent(root: AccessibilityNodeInfo): Boolean {
        var found = false
        fun walk(node: AccessibilityNodeInfo) {
            if (found) return
            if (node.isCheckable && !node.isChecked && nodeOrAncestorsMatchConsent(node)) {
                found = true
                return
            }
            for (i in 0 until node.childCount) {
                val child = node.getChild(i) ?: continue
                walk(child)
                try {
                    child.recycle()
                } catch (_: Exception) {
                }
            }
        }
        walk(root)
        return found
    }

    private fun screenHasExactText(root: AccessibilityNodeInfo, text: String): Boolean {
        val needle = text.trim()
        if (needle.isEmpty()) return false
        val results = root.findAccessibilityNodeInfosByText(needle)
        val hit = results.any {
            val t = it.text?.toString().orEmpty()
            val cd = it.contentDescription?.toString().orEmpty()
            t.equals(needle, true) || cd.equals(needle, true)
        }
        results.forEach { try { it.recycle() } catch (_: Exception) {} }
        return hit
    }

    private fun locateByCoordinate(root: AccessibilityNodeInfo, step: Step): AccessibilityNodeInfo? {
        val coord = step.screenCoordinate
        if (coord != null && coord.isValid) {
            nodeAnalyzer.findBestNode(root, coord.x, coord.y)?.let {
                return findNodeAtCoordinate(root, coord) ?: findNodeAtOrNearCoordinate(root, coord)
            }
            return findNodeAtOrNearCoordinate(root, coord)
        }
        return null
    }

    /** 仅在桌面分页中查找应用名。禁止在 App 内调用。 */
    private fun locateAcrossPages(step: Step): AccessibilityNodeInfo? {
        val directions = listOf(
            SwipeDirection.LEFT, SwipeDirection.LEFT, SwipeDirection.LEFT, SwipeDirection.LEFT,
            SwipeDirection.RIGHT, SwipeDirection.RIGHT, SwipeDirection.RIGHT,
            SwipeDirection.RIGHT, SwipeDirection.RIGHT
        )
        for (dir in directions) {
            val pkgBefore = activeWindowPackage()
            if (!isLauncherPackage(pkgBefore)) {
                android.util.Log.i("AssistantA11y", "stop page-swipe: left launcher ($pkgBefore)")
                return null
            }
            performPageSwipe(dir)
            try {
                Thread.sleep(450)
            } catch (_: InterruptedException) {
                return null
            }
            val pageRoot = rootInActiveWindow ?: continue
            try {
                if (!isLauncherPackage(pageRoot.packageName?.toString().orEmpty())) {
                    return null
                }
                locateBySelector(pageRoot, step)?.let { return it }
            } finally {
                try { pageRoot.recycle() } catch (_: Exception) {}
            }
        }
        return null
    }

    private fun performPageSwipe(direction: SwipeDirection) {
        val dm = resources.displayMetrics
        val w = dm.widthPixels.toFloat()
        val h = dm.heightPixels.toFloat()
        val path = Path()
        when (direction) {
            SwipeDirection.LEFT -> {
                path.moveTo(w * 0.85f, h * 0.5f)
                path.lineTo(w * 0.15f, h * 0.5f)
            }
            SwipeDirection.RIGHT -> {
                path.moveTo(w * 0.15f, h * 0.5f)
                path.lineTo(w * 0.85f, h * 0.5f)
            }
            SwipeDirection.UP -> {
                path.moveTo(w * 0.5f, h * 0.7f)
                path.lineTo(w * 0.5f, h * 0.3f)
            }
            SwipeDirection.DOWN -> {
                path.moveTo(w * 0.5f, h * 0.3f)
                path.lineTo(w * 0.5f, h * 0.7f)
            }
        }
        performGestureSync(path, durationMs = 280)
    }

    /** @deprecated 使用 locateTargetForAction */
    private fun locateTarget(root: AccessibilityNodeInfo, step: Step): AccessibilityNodeInfo? {
        return locateTargetForAction(root, step)
    }

    private fun findNodeByContentDesc(root: AccessibilityNodeInfo, desc: String): AccessibilityNodeInfo? {
        val target = desc.trim()
        if (target.isEmpty()) return null
        fun walk(node: AccessibilityNodeInfo): AccessibilityNodeInfo? {
            val cd = node.contentDescription?.toString()?.trim().orEmpty()
            if (cd.equals(target, ignoreCase = true) || cd.contains(target, ignoreCase = true)) {
                return AccessibilityNodeInfo.obtain(node)
            }
            for (i in 0 until node.childCount) {
                val child = node.getChild(i) ?: continue
                val found = walk(child)
                child.recycle()
                if (found != null) return found
            }
            return null
        }
        return walk(root)
    }

    private fun findNodeAtOrNearCoordinate(root: AccessibilityNodeInfo, coord: ScreenCoordinate, tolerance: Int = 30): AccessibilityNodeInfo? {
        val rect = Rect()
        var best: AccessibilityNodeInfo? = null
        var bestDist = Int.MAX_VALUE
        for (i in 0 until root.childCount) {
            val child = root.getChild(i) ?: continue
            child.getBoundsInScreen(rect)
            val cx = rect.centerX()
            val cy = rect.centerY()
            val dx = Math.abs(cx - coord.x)
            val dy = Math.abs(cy - coord.y)
            if (dx <= tolerance && dy <= tolerance && dx + dy < bestDist) {
                val node = AccessibilityNodeInfo.obtain(child)
                best?.recycle()
                best = node
                bestDist = dx + dy
            }
        }
        return best
    }

    private fun findNodeAtCoordinate(root: AccessibilityNodeInfo, coord: ScreenCoordinate): AccessibilityNodeInfo? {
        if (coord.x == 0 && coord.y == 0) return null
        val rect = Rect()
        for (i in 0 until root.childCount) {
            val child = root.getChild(i) ?: continue
            child.getBoundsInScreen(rect)
            if (rect.contains(coord.x, coord.y)) {
                val deepChild = findDeepestClickableNode(child, coord.x, coord.y)
                root.recycleChildren(listOf(child))
                return deepChild
            }
            child.recycle()
        }
        return null
    }

    private fun findDeepestClickableNode(node: AccessibilityNodeInfo, x: Int, y: Int): AccessibilityNodeInfo {
        val rect = Rect()
        var current = node
        var found = false

        while (!found) {
            found = true
            for (i in 0 until current.childCount) {
                val child = current.getChild(i) ?: continue
                child.getBoundsInScreen(rect)
                if (rect.contains(x, y)) {
                    if (child.isClickable || child.childCount == 0) {
                        current = child
                        found = false
                        break
                    }
                }
                child.recycle()
            }
        }
        return current
    }

    private fun performAction(targetNode: AccessibilityNodeInfo?, step: Step): StepResult {
        return when (step.action) {
            ActionType.TAP -> performTapAction(targetNode, step)
            ActionType.LONG_PRESS -> performLongPressAction(targetNode, step)
            ActionType.INPUT -> performInputAction(targetNode, step)
            ActionType.SWIPE -> performSwipeAction(step)
            ActionType.WAIT -> performWaitAction(step)
            ActionType.BACK -> performBackAction()
            ActionType.HOME -> performHomeAction()
            ActionType.OPEN_APP -> performOpenAppAction(step)
            ActionType.ASSERT -> performAssertAction(targetNode, step)
            ActionType.SCREENSHOT -> performScreenshotAction(step)
            ActionType.EXTRACT_TEXT -> performExtractTextAction(targetNode, step)
            ActionType.WAIT_UNTIL -> performWaitUntilAction(step)
            ActionType.CLOSE_APP -> performCloseAppAction(step)
            ActionType.PRESS_KEY -> performPressKeyAction(step)
            ActionType.SCROLL -> performScrollAction(step)
            ActionType.SCROLL_UNTIL -> performScrollUntilAction(step)
            ActionType.SCAN_QR -> performScanQrAction(step)
            ActionType.REPEAT, ActionType.WHILE -> performWaitUntilAction(
                step.copy(
                    action = ActionType.WAIT_UNTIL,
                    assertText = step.extras.untilAssertText.ifBlank { step.assertText },
                    waitDurationMs = if (step.waitDurationMs > 0) step.waitDurationMs
                    else (step.extras.repeatMax.coerceAtLeast(1) * 1000L)
                )
            )
            ActionType.SOLVE_CAPTCHA -> StepResult(
                success = false,
                errorMessage = "SOLVE_CAPTCHA must be orchestrated by ReplayViewModel",
                actualStrategy = "SOLVE_CAPTCHA_DELEGATED"
            )
            ActionType.HUMAN_GATE -> StepResult(
                success = true,
                actualStrategy = "HUMAN_GATE",
                errorMessage = "await_human"
            )
        }
    }

    private fun performTapAction(node: AccessibilityNodeInfo?, step: Step): StepResult {
        val hasSelector = step.locator.text.isNotBlank()
                || step.locator.contentDesc.isNotBlank()
                || step.locator.resourceId.isNotBlank()
        val checkIntent = wantsCheckable(step)
        val wantChecked = !wantsUncheck(step)

        // 有 text/id 时只点定位到的节点，绝不用录制坐标（分页桌面会点到错误页同坐标）
        if (node != null) {
            // 勾选意图：禁止点文案中心（易命中协议超链接），必须点 checkable 或标签左侧
            if (checkIntent) {
                return performCheckToggleAction(node, step, wantChecked)
            }

            // 输入框占位：直接 FOCUS + CLICK 可编辑节点
            if (node.isEditable) {
                node.performAction(AccessibilityNodeInfo.ACTION_FOCUS)
                val clicked = node.performAction(AccessibilityNodeInfo.ACTION_CLICK)
                val focused = node.isFocused || clicked
                if (focused || clicked) {
                    return StepResult(success = true, actualStrategy = "EDITABLE_FOCUS_CLICK")
                }
            }
            if (!node.isEnabled) {
                return StepResult(
                    success = false,
                    errorMessage = "目标控件当前不可用（disabled）: '${step.locator.text.ifBlank { step.description }}'",
                    actualStrategy = "TARGET_DISABLED"
                )
            }
            val clickable = findClickableNode(node)
            if (clickable != null) {
                if (!clickable.isEnabled) {
                    return StepResult(
                        success = false,
                        errorMessage = "可点击目标当前不可用（disabled）",
                        actualStrategy = "TARGET_DISABLED"
                    )
                }
                val success = clickable.performAction(AccessibilityNodeInfo.ACTION_CLICK)
                android.util.Log.i(
                    "AssistantA11y",
                    "TAP NODE_CLICK success=$success desc='${step.description}' text='${step.locator.text}'"
                )
                if (success) {
                    return finalizeTapSuccess(step, "NODE_CLICK")
                }
            }
            // 节点不可点：用该节点当前 bounds 中心（非勾选意图）
            val rect = Rect()
            node.getBoundsInScreen(rect)
            if (rect.width() > 0 && rect.height() > 0) {
                val ok = performClickSync(rect.centerX().toFloat(), rect.centerY().toFloat())
                if (ok) {
                    return finalizeTapSuccess(
                        step,
                        "NODE_BOUNDS_CLICK",
                        ScreenCoordinate(rect.centerX(), rect.centerY())
                    )
                }
            }
        }

        if (hasSelector) {
            val label = step.locator.text.ifBlank {
                step.locator.contentDesc.ifBlank { step.locator.resourceId }
            }
            return StepResult(
                success = false,
                errorMessage = "未找到「$label」。应用内不会用坐标/翻页兜底。",
                actualStrategy = "SELECTOR_NOT_FOUND"
            )
        }

        // 无 selector 才允许录制坐标兜底
        val coord = step.screenCoordinate
        if (coord != null && coord.isValid) {
            android.util.Log.i(
                "AssistantA11y",
                "TAP COORDINATE_CLICK at (${coord.x},${coord.y}) desc='${step.description}'"
            )
            val ok = performClickSync(coord.x.toFloat(), coord.y.toFloat())
            if (ok) {
                return finalizeTapSuccess(step, "COORDINATE_CLICK", coord)
            }
            return StepResult(
                success = false,
                errorMessage = "点击手势未完成或被取消 ($coord)",
                actualStrategy = "COORDINATE_CLICK_FAILED",
                actualCoordinate = coord
            )
        }
        return StepResult(
            success = false,
            errorMessage = "无有效点击目标（无文本/id 且无坐标）: '${step.description}'",
            actualStrategy = "TAP_NO_TARGET"
        )
    }

    private fun performCheckToggleAction(
        anchorOrCheckable: AccessibilityNodeInfo,
        step: Step,
        wantChecked: Boolean
    ): StepResult {
        fun verifiedState(node: AccessibilityNodeInfo?): Boolean? {
            if (node == null || !node.isCheckable) return null
            try {
                node.refresh()
            } catch (_: Exception) {
            }
            return node.isChecked
        }

        fun clickNode(node: AccessibilityNodeInfo): Boolean {
            if (!node.isEnabled) return false
            return when {
                node.isCheckable || node.isClickable ->
                    node.performAction(AccessibilityNodeInfo.ACTION_CLICK)
                else -> {
                    val r = Rect()
                    node.getBoundsInScreen(r)
                    r.width() > 0 && performClickSync(r.centerX().toFloat(), r.centerY().toFloat())
                }
            }
        }

        fun confirmAfterClick(strategy: String): StepResult? {
            try {
                Thread.sleep(280)
            } catch (_: InterruptedException) {
            }
            val root = rootInActiveWindow ?: return null
            try {
                val again = resolveCheckableTarget(root, anchorOrCheckable)
                val state = verifiedState(again)
                try {
                    again?.recycle()
                } catch (_: Exception) {
                }
                if (state == null) {
                    return StepResult(
                        success = false,
                        errorMessage = "已尝试勾选，但无法确认勾选框状态",
                        actualStrategy = "CHECK_UNVERIFIED"
                    )
                }
                if (state == wantChecked) {
                    return StepResult(success = true, actualStrategy = strategy)
                }
                return null
            } finally {
                try {
                    root.recycle()
                } catch (_: Exception) {
                }
            }
        }

        val target = if (anchorOrCheckable.isCheckable || isLikelyCheckboxClass(anchorOrCheckable)) {
            AccessibilityNodeInfo.obtain(anchorOrCheckable)
        } else {
            findCheckableNear(anchorOrCheckable)
        }

        if (target != null) {
            val before = verifiedState(target)
            if (before == wantChecked) {
                try {
                    target.recycle()
                } catch (_: Exception) {
                }
                return StepResult(success = true, actualStrategy = "CHECK_ALREADY")
            }
            val clicked = clickNode(target)
            try {
                target.recycle()
            } catch (_: Exception) {
            }
            if (clicked) {
                confirmAfterClick("CHECKABLE_CLICK")?.let { return it }
            }
        }

        if (clickLeftOfLabel(anchorOrCheckable)) {
            confirmAfterClick("CHECK_LEFT_OF_LABEL")?.let { return it }
        }

        return StepResult(
            success = false,
            errorMessage = "未能勾选目标（可能点到了协议链接而非勾选框）: '${step.locator.text.ifBlank { step.description }}'",
            actualStrategy = "CHECK_NOT_TOGGLED"
        )
    }

    /**
     * 提交类按钮：若同屏仍有「协议/同意」类未勾选项且按钮文案仍在，视为未真正推进。
     */
    private fun finalizeTapSuccess(
        step: Step,
        strategy: String,
        coord: ScreenCoordinate? = null
    ): StepResult {
        if (!looksLikeSubmitIntent(step)) {
            return StepResult(
                success = true,
                actualStrategy = strategy,
                actualCoordinate = coord
            )
        }
        try {
            Thread.sleep(450)
        } catch (_: InterruptedException) {
        }
        val root = rootInActiveWindow
            ?: return StepResult(success = true, actualStrategy = strategy, actualCoordinate = coord)
        try {
            val label = step.locator.text.trim()
            val stillThere = label.isBlank() || screenHasExactText(root, label)
            if (stillThere && hasBlockingUncheckedConsent(root)) {
                return StepResult(
                    success = false,
                    errorMessage = "点击已送达，但界面未推进：仍有未勾选的协议/同意项。请先勾选后再点「${label.ifBlank { step.description }}」。",
                    actualStrategy = "SUBMIT_BLOCKED_BY_UNCHECKED",
                    actualCoordinate = coord
                )
            }
        } finally {
            try {
                root.recycle()
            } catch (_: Exception) {
            }
        }
        return StepResult(
            success = true,
            actualStrategy = strategy,
            actualCoordinate = coord
        )
    }

    private fun findClickableNode(node: AccessibilityNodeInfo?): AccessibilityNodeInfo? {
        var cur = node
        while (cur != null) {
            if (cur.isClickable) return cur
            cur = try {
                cur.parent
            } catch (_: Exception) {
                null
            }
        }
        return null
    }

    private fun performLongPressAction(node: AccessibilityNodeInfo?, step: Step): StepResult {
        val clickable = findClickableNode(node)
        if (clickable != null && clickable.isLongClickable) {
            val success = clickable.performAction(AccessibilityNodeInfo.ACTION_LONG_CLICK)
            if (success) {
                return StepResult(success = true, actualStrategy = "NODE_LONG_CLICK")
            }
        }
        val coord = getActionCoordinate(node, step)
        if (coord != null && coord.isValid) {
            val path = Path().apply {
                moveTo(coord.x.toFloat(), coord.y.toFloat())
                lineTo(coord.x.toFloat() + 1f, coord.y.toFloat() + 1f)
            }
            val ok = performGestureSync(path, durationMs = 800)
            if (ok) {
                return StepResult(
                    success = true,
                    actualStrategy = "COORDINATE_LONG_PRESS",
                    actualCoordinate = coord
                )
            }
            return StepResult(
                success = false,
                errorMessage = "长按手势未完成或被取消",
                actualStrategy = "COORDINATE_LONG_PRESS_FAILED",
                actualCoordinate = coord
            )
        }
        return StepResult(success = false, errorMessage = "No valid long-press target")
    }

    private fun performInputAction(node: AccessibilityNodeInfo?, step: Step): StepResult {
        if (step.inputText.isBlank()) {
            return StepResult(success = false, errorMessage = "输入内容为空", actualStrategy = "INPUT_EMPTY")
        }

        // 等页面稳定；上一步常是点开登录表单
        try {
            Thread.sleep(400)
        } catch (_: InterruptedException) { }

        // 若 locator 带占位文案，优先按 hint 找 EditText
        var hintNode: AccessibilityNodeInfo? = null
        if (step.locator.text.isNotBlank() || step.locator.contentDesc.isNotBlank()) {
            val root = rootInActiveWindow
            if (root != null) {
                try {
                    val label = step.locator.text.ifBlank { step.locator.contentDesc }
                    hintNode = findNodeByHintOrEditableLabel(root, label)
                } finally {
                    try { root.recycle() } catch (_: Exception) {}
                }
            }
        }

        val editable = resolveEditableNode(hintNode ?: node, step)
            ?: return StepResult(
                success = false,
                errorMessage = "未找到可编辑输入框",
                actualStrategy = "INPUT_NO_EDITABLE"
            )

        editable.performAction(AccessibilityNodeInfo.ACTION_FOCUS)
        editable.performAction(AccessibilityNodeInfo.ACTION_CLICK)
        try {
            Thread.sleep(250)
        } catch (_: InterruptedException) { }

        // 先清空再写入，避免追加
        val clearArgs = android.os.Bundle().apply {
            putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, "")
        }
        editable.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, clearArgs)

        val args = android.os.Bundle().apply {
            putCharSequence(
                AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE,
                step.inputText
            )
        }
        if (editable.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)) {
            return StepResult(success = true, actualStrategy = "NODE_INPUT")
        }

        if (pasteViaClipboard(editable, step.inputText)) {
            return StepResult(success = true, actualStrategy = "CLIPBOARD_PASTE")
        }

        return StepResult(
            success = false,
            errorMessage = "SET_TEXT 与粘贴均失败",
            actualStrategy = "INPUT_FAILED"
        )
    }

    private fun resolveEditableNode(
        node: AccessibilityNodeInfo?,
        step: Step
    ): AccessibilityNodeInfo? {
        if (node != null && node.isEditable) return node

        // 子树中找可编辑
        if (node != null) {
            findEditableInSubtree(node)?.let { return it }
            // 父节点附近（hint 「输入手机号码」常是 TextView，EditText 在旁边/父级）
            var parent = node.parent
            var depth = 0
            while (parent != null && depth < 4) {
                findEditableInSubtree(parent)?.let { found ->
                    // 不 recycle parent 链上的 found
                    return found
                }
                val next = parent.parent
                parent.recycle()
                parent = next
                depth++
            }
            parent?.recycle()
        }

        // 按坐标附近找
        val coord = step.screenCoordinate
        val root = rootInActiveWindow
        if (root != null) {
            try {
                if (coord != null && coord.isValid) {
                    findEditableNear(root, coord.x, coord.y)?.let { return it }
                }
                // 全树第一个可编辑
                findEditableInSubtree(root)?.let { return it }
            } finally {
                try { root.recycle() } catch (_: Exception) {}
            }
        }
        return null
    }

    private fun findEditableInSubtree(node: AccessibilityNodeInfo): AccessibilityNodeInfo? {
        if (node.isEditable) return AccessibilityNodeInfo.obtain(node)
        for (i in 0 until node.childCount) {
            val child = node.getChild(i) ?: continue
            val found = findEditableInSubtree(child)
            child.recycle()
            if (found != null) return found
        }
        return null
    }

    private fun findEditableNear(root: AccessibilityNodeInfo, x: Int, y: Int): AccessibilityNodeInfo? {
        var best: AccessibilityNodeInfo? = null
        var bestDist = Int.MAX_VALUE
        val rect = Rect()
        fun walk(n: AccessibilityNodeInfo) {
            if (n.isEditable) {
                n.getBoundsInScreen(rect)
                val dist = kotlin.math.abs(rect.centerX() - x) + kotlin.math.abs(rect.centerY() - y)
                if (dist < bestDist) {
                    best?.recycle()
                    best = AccessibilityNodeInfo.obtain(n)
                    bestDist = dist
                }
            }
            for (i in 0 until n.childCount) {
                val c = n.getChild(i) ?: continue
                walk(c)
                c.recycle()
            }
        }
        walk(root)
        return best
    }

    private fun pasteViaClipboard(node: AccessibilityNodeInfo, text: String): Boolean {
        return try {
            val cm = getSystemService(CLIPBOARD_SERVICE) as android.content.ClipboardManager
            cm.setPrimaryClip(android.content.ClipData.newPlainText("testory_input", text))
            node.performAction(AccessibilityNodeInfo.ACTION_FOCUS)
            node.performAction(AccessibilityNodeInfo.ACTION_PASTE)
        } catch (e: Exception) {
            android.util.Log.w("AssistantA11y", "clipboard paste failed", e)
            false
        }
    }

    private fun performSwipeAction(step: Step): StepResult {
        val displayMetrics = resources.displayMetrics
        val w = displayMetrics.widthPixels
        val h = displayMetrics.heightPixels

        // 优先使用录制时的坐标；无效时回退到屏幕中心。
        val coord = step.screenCoordinate
        val cx: Float = if (coord != null && coord.isValid) coord.x.toFloat() else (w / 2).toFloat()
        val cy: Float = if (coord != null && coord.isValid) coord.y.toFloat() else (h / 2).toFloat()

        val path = Path()
        val swipeDistance = (h * 0.6).toFloat()

        when (step.swipeDirection) {
            SwipeDirection.UP -> {
                path.moveTo(cx, Math.min(cy + swipeDistance / 2, h.toFloat() - 50f))
                path.lineTo(cx, Math.max(cy - swipeDistance / 2, 50f))
            }
            SwipeDirection.DOWN -> {
                path.moveTo(cx, Math.max(cy - swipeDistance / 2, 50f))
                path.lineTo(cx, Math.min(cy + swipeDistance / 2, h.toFloat() - 50f))
            }
            SwipeDirection.LEFT -> {
                path.moveTo(Math.min(cx + swipeDistance / 2, w.toFloat() - 50f), cy)
                path.lineTo(Math.max(cx - swipeDistance / 2, 50f), cy)
            }
            SwipeDirection.RIGHT -> {
                path.moveTo(Math.max(cx - swipeDistance / 2, 50f), cy)
                path.lineTo(Math.min(cx + swipeDistance / 2, w.toFloat() - 50f), cy)
            }
            null -> {
                return StepResult(success = false, errorMessage = "No swipe direction specified")
            }
        }

        performGestureSync(path, durationMs = 300).let { ok ->
            return StepResult(
                success = ok,
                actualStrategy = if (ok) "GESTURE_SWIPE" else "GESTURE_SWIPE_FAILED",
                errorMessage = if (ok) "" else "滑动手势未完成或被取消"
            )
        }
    }

    private fun performWaitAction(step: Step): StepResult {
        Thread.sleep(step.waitDurationMs.coerceAtMost(30000))
        return StepResult(success = true, actualStrategy = "WAIT")
    }

    private fun performBackAction(): StepResult {
        val success = performGlobalAction(GLOBAL_ACTION_BACK)
        return StepResult(success = success, actualStrategy = "GLOBAL_BACK")
    }

    private fun performHomeAction(): StepResult {
        val success = performGlobalAction(GLOBAL_ACTION_HOME)
        return StepResult(success = success, actualStrategy = "GLOBAL_HOME")
    }

    /**
     * 打开应用（兼容旧用例中的 OPEN_APP）：优先按 package 启动 Intent，
     * 不再把 description 当 UI 文案去查找节点。
     */
    private fun performOpenAppAction(step: Step): StepResult {
        val packageName = step.locator.packageName.ifBlank {
            step.targetNode?.packageName.orEmpty()
        }
        val appText = step.locator.text.ifBlank { step.targetNode?.text.orEmpty() }

        // Strategy 1: package Intent（与「点击桌面图标」语义等价的可靠回放）
        if (packageName.isNotBlank()) {
            val intent = packageManager.getLaunchIntentForPackage(packageName)
            if (intent != null) {
                intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK)
                startActivity(intent)
                return StepResult(success = true, actualStrategy = "INTENT")
            }
        }

        // Strategy 2: 桌面图标 text 点击（无 package 的旧数据）
        if (appText.isNotBlank()) {
            val root = rootInActiveWindow
            if (root != null) {
                val results = root.findAccessibilityNodeInfosByText(appText)
                for (node in results) {
                    if (node.isClickable) {
                        val clicked = node.performAction(AccessibilityNodeInfo.ACTION_CLICK)
                        node.recycle()
                        if (clicked) {
                            results.filter { it != node }.forEach { it.recycle() }
                            return StepResult(success = true, actualStrategy = "TEXT_ICON_CLICK")
                        }
                        break
                    }
                    var parent = node.parent
                    node.recycle()
                    while (parent != null && !parent.isClickable) {
                        val p = parent.parent
                        if (p == null) parent.recycle()
                        parent = p
                    }
                    if (parent != null && parent.isClickable) {
                        val clicked = parent.performAction(AccessibilityNodeInfo.ACTION_CLICK)
                        parent.recycle()
                        if (clicked) {
                            results.forEach { it.recycle() }
                            return StepResult(success = true, actualStrategy = "TEXT_ICON_PARENT_CLICK")
                        }
                        break
                    }
                    parent?.recycle()
                }
                results.forEach { it.recycle() }
            }
        }

        // Strategy 3: coordinate click
        val coord = getActionCoordinate(null, step)
        if (coord != null && coord.isValid) {
            val ok = performClickSync(coord.x.toFloat(), coord.y.toFloat())
            return StepResult(
                success = ok,
                actualStrategy = if (ok) "COORDINATE_CLICK" else "COORDINATE_CLICK_FAILED",
                actualCoordinate = coord,
                errorMessage = if (ok) "" else "打开应用坐标点击未完成"
            )
        }

        return StepResult(
            success = false,
            errorMessage = "Cannot launch: text=$appText pkg=$packageName"
        )
    }

    private fun performAssertAction(node: AccessibilityNodeInfo?, step: Step): StepResult {
        val assertType = step.extras.assertType.ifBlank { "contains" }.lowercase()
        val expected = step.assertText

        when (assertType) {
            "visible" -> {
                val needle = expected.ifBlank { step.locator.text }.ifBlank { step.locator.contentDesc }
                if (needle.isBlank() && node != null) {
                    return StepResult(success = true, actualStrategy = "ASSERT_VISIBLE_NODE")
                }
                val found = node != null || screenContainsText(needle)
                return StepResult(
                    success = found,
                    errorMessage = if (found) "" else "元素不可见: '$needle'",
                    actualStrategy = "ASSERT_VISIBLE"
                )
            }
            "not_visible" -> {
                val needle = expected.ifBlank { step.locator.text }.ifBlank { step.locator.contentDesc }
                val found = if (needle.isBlank()) node != null else screenContainsText(needle)
                return StepResult(
                    success = !found,
                    errorMessage = if (!found) "" else "元素仍可见: '$needle'",
                    actualStrategy = "ASSERT_NOT_VISIBLE"
                )
            }
            "equals", "text_equals" -> {
                if (expected.isBlank()) {
                    return StepResult(success = true, actualStrategy = "ASSERT_SKIP_EMPTY")
                }
                val actual = nodeText(node).ifBlank { findExactScreenText(expected) }
                val ok = actual.equals(expected, ignoreCase = true)
                return StepResult(
                    success = ok,
                    errorMessage = if (ok) "" else "断言 equals 失败: expected='$expected' actual='$actual'",
                    actualStrategy = "ASSERT_EQUALS"
                )
            }
            else -> { // contains
                if (expected.isBlank()) {
                    return StepResult(success = true, actualStrategy = "ASSERT_SKIP_EMPTY")
                }
                if (node != null) {
                    val nodeText = nodeText(node)
                    val found = nodeText.contains(expected, ignoreCase = true) ||
                        runCatching { nodeText.matches(Regex(expected)) }.getOrDefault(false)
                    return StepResult(
                        success = found,
                        errorMessage = if (found) "" else "Assert failed: expected '$expected' not found in '$nodeText'",
                        actualStrategy = "ASSERT"
                    )
                }
                val found = screenContainsText(expected)
                return StepResult(
                    success = found,
                    errorMessage = if (found) "" else "Text '$expected' not found on screen",
                    actualStrategy = "ASSERT_SCREEN_WIDE"
                )
            }
        }
    }

    private fun nodeText(node: AccessibilityNodeInfo?): String {
        if (node == null) return ""
        return (node.text?.toString() ?: "") + (node.contentDescription?.toString() ?: "")
    }

    private fun screenContainsText(needle: String): Boolean {
        if (needle.isBlank()) return false
        val root = rootInActiveWindow ?: return false
        return try {
            val texts = mutableListOf<String>()
            collectAllTexts(root, texts)
            texts.any { it.contains(needle, ignoreCase = true) }
        } finally {
            try { root.recycle() } catch (_: Exception) {}
        }
    }

    private fun findExactScreenText(expected: String): String {
        val root = rootInActiveWindow ?: return ""
        return try {
            val texts = mutableListOf<String>()
            collectAllTexts(root, texts)
            texts.firstOrNull { it.equals(expected, ignoreCase = true) }.orEmpty()
        } finally {
            try { root.recycle() } catch (_: Exception) {}
        }
    }

    fun textVisibleOnScreen(needle: String): Boolean = screenContainsText(needle)

    private fun performExtractTextAction(node: AccessibilityNodeInfo?, step: Step): StepResult {
        val saveAs = step.extras.saveAs.ifBlank { "extracted_text" }
        val text = when {
            node != null -> nodeText(node).trim()
            step.locator.text.isNotBlank() -> {
                // 定位失败时尝试全屏匹配附近文本
                val root = rootInActiveWindow
                if (root != null) {
                    try {
                        val texts = mutableListOf<String>()
                        collectAllTexts(root, texts)
                        texts.firstOrNull { it.contains(step.locator.text, ignoreCase = true) }.orEmpty()
                    } finally {
                        try { root.recycle() } catch (_: Exception) {}
                    }
                } else ""
            }
            else -> ""
        }
        if (text.isBlank()) {
            return StepResult(
                success = false,
                errorMessage = "EXTRACT_TEXT 未取到文本",
                actualStrategy = "EXTRACT_TEXT_EMPTY"
            )
        }
        return StepResult(
            success = true,
            actualStrategy = "EXTRACT_TEXT",
            variables = mapOf(saveAs to text)
        )
    }

    private fun performWaitUntilAction(step: Step): StepResult {
        val needle = step.extras.untilAssertText.ifBlank { step.assertText }.ifBlank { step.locator.text }
        if (needle.isBlank()) {
            return performWaitAction(step)
        }
        val timeout = if (step.waitDurationMs > 0) step.waitDurationMs else 15000L
        val deadline = System.currentTimeMillis() + timeout
        while (System.currentTimeMillis() < deadline) {
            if (screenContainsText(needle)) {
                return StepResult(success = true, actualStrategy = "WAIT_UNTIL")
            }
            try {
                Thread.sleep(300)
            } catch (_: InterruptedException) {
                break
            }
        }
        return StepResult(
            success = false,
            errorMessage = "WAIT_UNTIL 超时: '$needle'",
            actualStrategy = "WAIT_UNTIL_TIMEOUT"
        )
    }

    private fun performCloseAppAction(step: Step): StepResult {
        val pkg = step.locator.packageName.ifBlank {
            step.targetNode?.packageName.orEmpty()
        }.ifBlank {
            activeWindowPackage()
        }
        if (pkg.isBlank() || pkg.startsWith("com.testory.assistant")) {
            performGlobalAction(GLOBAL_ACTION_HOME)
            return StepResult(success = true, actualStrategy = "CLOSE_APP_HOME")
        }
        return try {
            val am = getSystemService(android.content.Context.ACTIVITY_SERVICE) as android.app.ActivityManager
            @Suppress("DEPRECATION")
            am.killBackgroundProcesses(pkg)
            performGlobalAction(GLOBAL_ACTION_HOME)
            StepResult(success = true, actualStrategy = "CLOSE_APP")
        } catch (e: Exception) {
            performGlobalAction(GLOBAL_ACTION_RECENTS)
            StepResult(success = true, actualStrategy = "CLOSE_APP_RECENTS", errorMessage = e.message.orEmpty())
        }
    }

    private fun performPressKeyAction(step: Step): StepResult {
        val key = step.extras.keyCode.ifBlank { step.inputText }.uppercase()
        val ok = when (key) {
            "BACK", "KEYCODE_BACK", "4" -> performGlobalAction(GLOBAL_ACTION_BACK)
            "HOME", "KEYCODE_HOME", "3" -> performGlobalAction(GLOBAL_ACTION_HOME)
            "RECENTS", "KEYCODE_APP_SWITCH", "187" -> performGlobalAction(GLOBAL_ACTION_RECENTS)
            "NOTIFICATIONS" -> performGlobalAction(GLOBAL_ACTION_NOTIFICATIONS)
            else -> performGlobalAction(GLOBAL_ACTION_BACK)
        }
        return StepResult(
            success = ok,
            actualStrategy = "PRESS_KEY:$key",
            errorMessage = if (ok) "" else "PRESS_KEY 失败: $key"
        )
    }

    private fun performScrollAction(step: Step): StepResult {
        val dir = step.swipeDirection ?: SwipeDirection.UP
        val amount = step.extras.scrollAmount.takeIf { it > 0 } ?: 600
        val dm = resources.displayMetrics
        val cx = dm.widthPixels / 2f
        val cy = dm.heightPixels / 2f
        val (x1, y1, x2, y2) = when (dir) {
            SwipeDirection.UP -> listOf(cx, cy + amount / 2f, cx, cy - amount / 2f)
            SwipeDirection.DOWN -> listOf(cx, cy - amount / 2f, cx, cy + amount / 2f)
            SwipeDirection.LEFT -> listOf(cx + amount / 2f, cy, cx - amount / 2f, cy)
            SwipeDirection.RIGHT -> listOf(cx - amount / 2f, cy, cx + amount / 2f, cy)
        }
        val ok = performSwipeSync(x1, y1, x2, y2)
        return StepResult(
            success = ok,
            actualStrategy = "SCROLL_${dir.name}",
            errorMessage = if (ok) "" else "SCROLL 手势失败"
        )
    }

    private fun performScrollUntilAction(step: Step): StepResult {
        val needle = step.extras.untilAssertText.ifBlank { step.assertText }.ifBlank { step.locator.text }
        if (needle.isBlank()) {
            return StepResult(success = false, errorMessage = "SCROLL_UNTIL 缺少目标文本")
        }
        val maxSwipes = step.extras.repeatMax.takeIf { it > 0 } ?: 8
        if (screenContainsText(needle)) {
            return StepResult(success = true, actualStrategy = "SCROLL_UNTIL_ALREADY")
        }
        repeat(maxSwipes) {
            val swipeStep = step.copy(
                action = ActionType.SCROLL,
                swipeDirection = step.swipeDirection ?: SwipeDirection.UP
            )
            performScrollAction(swipeStep)
            try { Thread.sleep(400) } catch (_: InterruptedException) {}
            if (screenContainsText(needle)) {
                return StepResult(success = true, actualStrategy = "SCROLL_UNTIL")
            }
        }
        return StepResult(
            success = false,
            errorMessage = "SCROLL_UNTIL 未找到: '$needle'",
            actualStrategy = "SCROLL_UNTIL_MISS"
        )
    }

    private fun performScanQrAction(step: Step): StepResult {
        val png = captureScreenshotPng(step.extras.roi) ?: return StepResult(
            success = false,
            errorMessage = "截屏失败，无法扫码",
            actualStrategy = "SCAN_QR_NO_SHOT"
        )
        val decoded = decodeQr(png)
        if (decoded.isNullOrBlank()) {
            return StepResult(
                success = false,
                errorMessage = "未识别到二维码",
                actualStrategy = "SCAN_QR_EMPTY"
            )
        }
        val saveAs = step.extras.saveAs.ifBlank { "qr_text" }
        return StepResult(
            success = true,
            actualStrategy = "SCAN_QR",
            variables = mapOf(saveAs to decoded)
        )
    }

    private fun performScreenshotAction(step: Step): StepResult {
        val png = captureScreenshotPng(step.extras.roi)
        if (png == null) {
            return StepResult(
                success = false,
                errorMessage = "截图失败（需 API 30+ 或权限）",
                actualStrategy = "SCREENSHOT_FAILED"
            )
        }
        val dir = filesDir.resolve("screenshots").apply { mkdirs() }
        val file = dir.resolve("shot_${System.currentTimeMillis()}.png")
        return try {
            file.writeBytes(png)
            val b64Preview = android.util.Base64.encodeToString(
                png.take(64).toByteArray(),
                android.util.Base64.NO_WRAP
            )
            StepResult(
                success = true,
                actualStrategy = "SCREENSHOT",
                evidence = file.absolutePath,
                variables = if (step.extras.saveAs.isNotBlank()) {
                    mapOf(step.extras.saveAs to file.absolutePath)
                } else emptyMap()
            ).also {
                android.util.Log.i("AssistantA11y", "screenshot saved ${file.absolutePath} head=$b64Preview")
            }
        } catch (e: Exception) {
            StepResult(
                success = false,
                errorMessage = e.message ?: "写截图失败",
                actualStrategy = "SCREENSHOT_WRITE_FAILED"
            )
        }
    }

    /**
     * 截取全屏或 ROI PNG。API 30+ 使用 AccessibilityService.takeScreenshot。
     */
    fun captureScreenshotPng(roi: List<Int>? = null): ByteArray? {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.R) return null
        val latch = CountDownLatch(1)
        val holder = arrayOfNulls<ByteArray>(1)
        try {
            takeScreenshot(
                Display.DEFAULT_DISPLAY,
                mainExecutor,
                object : TakeScreenshotCallback {
                    override fun onSuccess(screenshot: ScreenshotResult) {
                        try {
                            val hw = screenshot.hardwareBuffer
                            val colorSpace = screenshot.colorSpace
                            val bitmap = android.graphics.Bitmap.wrapHardwareBuffer(hw, colorSpace)
                                ?.copy(android.graphics.Bitmap.Config.ARGB_8888, false)
                            hw.close()
                            if (bitmap != null) {
                                val cropped = cropBitmap(bitmap, roi)
                                val baos = java.io.ByteArrayOutputStream()
                                cropped.compress(android.graphics.Bitmap.CompressFormat.PNG, 100, baos)
                                if (cropped !== bitmap) cropped.recycle()
                                bitmap.recycle()
                                holder[0] = baos.toByteArray()
                            }
                        } catch (e: Exception) {
                            android.util.Log.w("AssistantA11y", "screenshot encode failed", e)
                        } finally {
                            latch.countDown()
                        }
                    }

                    override fun onFailure(errorCode: Int) {
                        android.util.Log.w("AssistantA11y", "takeScreenshot failed code=$errorCode")
                        latch.countDown()
                    }
                }
            )
        } catch (e: Exception) {
            android.util.Log.w("AssistantA11y", "takeScreenshot exception", e)
            latch.countDown()
        }
        latch.await(5, TimeUnit.SECONDS)
        return holder[0]
    }

    private fun cropBitmap(src: android.graphics.Bitmap, roi: List<Int>?): android.graphics.Bitmap {
        if (roi == null || roi.size < 4) return src
        val l = roi[0].coerceIn(0, src.width - 1)
        val t = roi[1].coerceIn(0, src.height - 1)
        val r = roi[2].coerceIn(l + 1, src.width)
        val b = roi[3].coerceIn(t + 1, src.height)
        return try {
            android.graphics.Bitmap.createBitmap(src, l, t, r - l, b - t)
        } catch (_: Exception) {
            src
        }
    }

    private fun decodeQr(png: ByteArray): String? {
        return try {
            val bitmap = android.graphics.BitmapFactory.decodeByteArray(png, 0, png.size) ?: return null
            val width = bitmap.width
            val height = bitmap.height
            val pixels = IntArray(width * height)
            bitmap.getPixels(pixels, 0, width, 0, 0, width, height)
            bitmap.recycle()
            val source = com.google.zxing.RGBLuminanceSource(width, height, pixels)
            val binary = com.google.zxing.BinaryBitmap(com.google.zxing.common.HybridBinarizer(source))
            val reader = com.google.zxing.MultiFormatReader()
            reader.decode(binary)?.text
        } catch (_: Exception) {
            null
        }
    }

    fun performSwipeSync(x1: Float, y1: Float, x2: Float, y2: Float, timeoutMs: Long = 3000L): Boolean {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.N) return false
        val path = Path().apply {
            moveTo(x1, y1)
            lineTo(x2, y2)
        }
        val stroke = GestureDescription.StrokeDescription(path, 0, 350)
        val gesture = GestureDescription.Builder().addStroke(stroke).build()
        val latch = CountDownLatch(1)
        val ok = AtomicBoolean(false)
        val mainHandler = Handler(Looper.getMainLooper())
        val dispatched = mainHandler.post {
            dispatchGesture(gesture, object : GestureResultCallback() {
                override fun onCompleted(gestureDescription: GestureDescription?) {
                    ok.set(true)
                    latch.countDown()
                }

                override fun onCancelled(gestureDescription: GestureDescription?) {
                    latch.countDown()
                }
            }, null)
        }
        if (!dispatched) {
            latch.countDown()
        }
        latch.await(timeoutMs, TimeUnit.MILLISECONDS)
        return ok.get()
    }

    private fun collectAllTexts(node: AccessibilityNodeInfo, texts: MutableList<String>) {
        node.text?.toString()?.takeIf { it.isNotBlank() }?.let { texts.add(it) }
        node.contentDescription?.toString()?.takeIf { it.isNotBlank() }?.let { texts.add(it) }
        for (i in 0 until node.childCount) {
            val child = node.getChild(i) ?: continue
            collectAllTexts(child, texts)
            child.recycle()
        }
    }

    private fun getActionCoordinate(node: AccessibilityNodeInfo?, step: Step): ScreenCoordinate? {
        // Priority 1: stored coordinate
        step.screenCoordinate?.let { if (it.isValid) return it }

        // Priority 2: live node bounds center (if node is available)
        if (node != null) {
            val rect = Rect()
            node.getBoundsInScreen(rect)
            if (rect.right > rect.left && rect.bottom > rect.top) {
                return ScreenCoordinate(rect.centerX(), rect.centerY())
            }
        }

        // Priority 3: derive from targetNode bounds (from recording data)
        val targetNode = step.targetNode
        if (targetNode != null && targetNode.bounds.isValid) {
            return targetNode.bounds.toScreenCoordinate()
        }

        // 禁止回退到屏幕中心：会「手势成功」但点到空白 → 进度空转、界面无操作
        return null
    }

    private fun needsNodeForAction(action: ActionType, step: Step): Boolean {
        val hasSelector = step.locator.text.isNotBlank()
                || step.locator.contentDesc.isNotBlank()
                || step.locator.resourceId.isNotBlank()
        if (action == ActionType.TAP || action == ActionType.LONG_PRESS) {
            return hasSelector
        }
        if (action == ActionType.INPUT || action == ActionType.EXTRACT_TEXT) {
            return false
        }
        if (action == ActionType.ASSERT) {
            val t = step.extras.assertType.lowercase()
            return hasSelector && t != "visible" && t != "not_visible" &&
                !(t == "contains" || t == "equals" || t == "text_equals" || t.isBlank())
        }
        return action != ActionType.WAIT && action != ActionType.BACK
                && action != ActionType.HOME && action != ActionType.SWIPE
                && action != ActionType.OPEN_APP && action != ActionType.SCREENSHOT
                && action != ActionType.WAIT_UNTIL && action != ActionType.CLOSE_APP
                && action != ActionType.PRESS_KEY && action != ActionType.SCROLL
                && action != ActionType.SCROLL_UNTIL && action != ActionType.SCAN_QR
                && action != ActionType.SOLVE_CAPTCHA && action != ActionType.HUMAN_GATE
                && action != ActionType.REPEAT && action != ActionType.WHILE
    }

    private fun AccessibilityNodeInfo.recycleChildren(keep: List<AccessibilityNodeInfo>) {
        for (i in 0 until childCount) {
            val child = getChild(i) ?: continue
            if (child !in keep) {
                child.recycle()
            }
        }
    }
}

/**
 * 会话状态 — 替代旧版 AssistantSession（全局单例）。
 */
data class SessionState(
    val armedMode: ArmedMode = ArmedMode.IDLE,
    val isRecording: Boolean = false,
    val isPaused: Boolean = false,
    val recordStartTime: Long = 0,
    val stepCount: Int = 0,
    val currentPackage: String = "",
    val currentActivity: String = "",
    val lastTouchEvent: TouchEventInfo? = null
)

enum class ArmedMode { IDLE, RECORDING, REPLAYING, CAPTURING }

data class TouchEventInfo(
    val x: Float = 0f,
    val y: Float = 0f,
    val action: Int = 0,  // MotionEvent.ACTION_DOWN/UP/MOVE
    val timestamp: Long = System.currentTimeMillis()
)

data class UiTree(
    val nodes: List<UiNode>,
    val timestamp: Long = System.currentTimeMillis()
)

data class UiNode(
    val className: String = "",
    val text: String = "",
    val contentDescription: String = "",
    val resourceId: String = "",
    val bounds: ScreenRect = ScreenRect(),
    val isClickable: Boolean = false,
    val isEditable: Boolean = false,
    val children: List<UiNode> = emptyList()
)
