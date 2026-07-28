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
                    // source 缺失时，尝试用窗口中心附近不做盲点；保留 fromSource
                } finally {
                    try { root.recycle() } catch (_: Exception) {}
                }
            }
        }

        if (fromSource != null) return fromSource

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
            val targetNode = locateTarget(rootNode, step)
            if (targetNode == null && needsNodeForAction(step.action, step)) {
                return StepResult(
                    stepIndex = step.index,
                    stepId = step.id,
                    success = false,
                    errorMessage = "Element not found: '${step.description}'",
                    actualStrategy = "NODE_LOOKUP_FAILED",
                    stepDescription = step.description
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
        val ok = performGestureSync(path, durationMs)
        callback?.invoke(ok)
        return ok
    }

    /**
     * 执行点击手势（同步等待完成）。
     */
    fun performClick(x: Float, y: Float, callback: ((Boolean) -> Unit)? = null): Boolean {
        val ok = performClickSync(x, y)
        callback?.invoke(ok)
        return ok
    }

    fun performClickSync(x: Float, y: Float, timeoutMs: Long = 3000L): Boolean {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.N) return false
        val path = Path().apply { moveTo(x, y) }
        return performGestureSync(path, durationMs = 50L, timeoutMs = timeoutMs)
    }

    fun performGestureSync(
        path: Path,
        durationMs: Long = 300,
        timeoutMs: Long = 5000L
    ): Boolean {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.N) return false
        val latch = CountDownLatch(1)
        val completed = AtomicBoolean(false)
        val gesture = GestureDescription.Builder()
            .addStroke(GestureDescription.StrokeDescription(path, 0, durationMs.coerceAtLeast(1L)))
            .build()
        val mainHandler = Handler(Looper.getMainLooper())
        val accepted = dispatchGesture(gesture, object : GestureResultCallback() {
            override fun onCompleted(gestureDescription: GestureDescription?) {
                completed.set(true)
                latch.countDown()
            }
            override fun onCancelled(gestureDescription: GestureDescription?) {
                completed.set(false)
                latch.countDown()
            }
        }, mainHandler)
        if (!accepted) return false
        val waited = latch.await(timeoutMs, TimeUnit.MILLISECONDS)
        return waited && completed.get()
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
     * 多级定位 — text 优先 + 坐标兜底。
     * text 是最高级标识（如桌面图标名称），坐标仅在没有可匹配节点时使用。
     */
    private fun locateTarget(root: AccessibilityNodeInfo, step: Step): AccessibilityNodeInfo? {
        val locator = step.locator

        // Strategy 1: text — highest priority (works even for off-screen nodes)
        if (locator.text.isNotBlank()) {
            val results = root.findAccessibilityNodeInfosByText(locator.text)
            if (results.isNotEmpty()) return results[0]
        }

        // Strategy 2: content-description（findByText 可能漏，补树遍历）
        if (locator.contentDesc.isNotBlank()) {
            val results = root.findAccessibilityNodeInfosByText(locator.contentDesc)
            if (results.isNotEmpty()) return results[0]
            findNodeByContentDesc(root, locator.contentDesc)?.let { return it }
        }

        // Strategy 3: resource-id
        if (locator.resourceId.isNotBlank()) {
            val results = root.findAccessibilityNodeInfosByViewId(locator.resourceId)
            if (results.isNotEmpty()) return results[0]
        }

        // Strategy 4: coordinate — hit-test 最深叶子
        val coord = step.screenCoordinate
        if (coord != null && coord.isValid) {
            nodeAnalyzer.findBestNode(root, coord.x, coord.y)?.let { info ->
                // 回放需要原生节点：按坐标再取一次
                return findNodeAtCoordinate(root, coord) ?: findNodeAtOrNearCoordinate(root, coord)
            }
            return findNodeAtOrNearCoordinate(root, coord)
        }

        return null
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
            ActionType.SCREENSHOT -> performScreenshotAction()
        }
    }

    private fun performTapAction(node: AccessibilityNodeInfo?, step: Step): StepResult {
        // 坐标手势必须同步等待；原缺陷：dispatchGesture 异步返回后立刻 success=true，
        // 下一步立刻再派发会取消上一步 → 进度条狂奔、界面无操作、仍显示成功。
        val coord = getActionCoordinate(node, step)
        if (coord != null && coord.isValid) {
            val ok = performClickSync(coord.x.toFloat(), coord.y.toFloat())
            if (ok) {
                return StepResult(
                    success = true,
                    actualStrategy = "COORDINATE_CLICK",
                    actualCoordinate = coord
                )
            }
            // 手势失败时再试节点点击
            if (node != null && node.isClickable) {
                val success = node.performAction(AccessibilityNodeInfo.ACTION_CLICK)
                if (success) {
                    return StepResult(success = true, actualStrategy = "NODE_CLICK_FALLBACK")
                }
            }
            return StepResult(
                success = false,
                errorMessage = "点击手势未完成或被取消 ($coord)",
                actualStrategy = "COORDINATE_CLICK_FAILED",
                actualCoordinate = coord
            )
        }
        if (node != null && node.isClickable) {
            val success = node.performAction(AccessibilityNodeInfo.ACTION_CLICK)
            return StepResult(success = success, actualStrategy = "NODE_CLICK")
        }
        return StepResult(success = false, errorMessage = "No valid click target")
    }

    private fun performLongPressAction(node: AccessibilityNodeInfo?, step: Step): StepResult {
        val coord = getActionCoordinate(node, step)
        if (coord != null && coord.isValid) {
            val path = Path().apply { moveTo(coord.x.toFloat(), coord.y.toFloat()) }
            val ok = performGestureSync(path, durationMs = 800)
            if (ok) {
                return StepResult(
                    success = true,
                    actualStrategy = "COORDINATE_LONG_PRESS",
                    actualCoordinate = coord
                )
            }
            if (node != null && node.isLongClickable) {
                val success = node.performAction(AccessibilityNodeInfo.ACTION_LONG_CLICK)
                if (success) {
                    return StepResult(success = true, actualStrategy = "NODE_LONG_CLICK_FALLBACK")
                }
            }
            return StepResult(
                success = false,
                errorMessage = "长按手势未完成或被取消",
                actualStrategy = "COORDINATE_LONG_PRESS_FAILED",
                actualCoordinate = coord
            )
        }
        if (node != null && node.isLongClickable) {
            val success = node.performAction(AccessibilityNodeInfo.ACTION_LONG_CLICK)
            return StepResult(success = success, actualStrategy = "NODE_LONG_CLICK")
        }
        return StepResult(success = false, errorMessage = "No valid long-press target")
    }

    private fun performInputAction(node: AccessibilityNodeInfo?, step: Step): StepResult {
        if (step.inputText.isBlank()) {
            return StepResult(success = false, errorMessage = "输入内容为空", actualStrategy = "INPUT_EMPTY")
        }
        return if (node != null && node.isEditable) {
            val args = android.os.Bundle().apply {
                putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, step.inputText)
            }
            val success = node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)
            StepResult(
                success = success,
                actualStrategy = "NODE_INPUT",
                errorMessage = if (success) "" else "SET_TEXT 失败"
            )
        } else if (node != null) {
            node.performAction(AccessibilityNodeInfo.ACTION_FOCUS)
            val args = android.os.Bundle().apply {
                putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, step.inputText)
            }
            val success = node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)
            StepResult(
                success = success,
                actualStrategy = "FOCUS_INPUT",
                errorMessage = if (success) "" else "输入框不可编辑或 SET_TEXT 失败"
            )
        } else {
            StepResult(success = false, errorMessage = "未找到可输入控件", actualStrategy = "INPUT_NO_NODE")
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
        if (step.assertText.isBlank()) {
            return StepResult(success = true, actualStrategy = "ASSERT_SKIP_EMPTY")
        }
        if (node != null) {
            val nodeText = (node.text?.toString() ?: "") + (node.contentDescription?.toString() ?: "")
            val found = nodeText.contains(step.assertText, ignoreCase = true) ||
                nodeText.matches(Regex(step.assertText))
            return StepResult(
                success = found,
                errorMessage = if (found) "" else "Assert failed: expected '${step.assertText}' not found in '$nodeText'",
                actualStrategy = "ASSERT"
            )
        }
        // Try to find text in whole UI tree
        val root = rootInActiveWindow ?: return StepResult(
            success = false,
            errorMessage = "Cannot perform assert: no window root"
        )
        try {
            val texts = mutableListOf<String>()
            collectAllTexts(root, texts)
            val found = texts.any { it.contains(step.assertText, ignoreCase = true) }
            root.recycle()
            return StepResult(
                success = found,
                errorMessage = if (found) "" else "Text '${step.assertText}' not found on screen",
                actualStrategy = "ASSERT_SCREEN_WIDE"
            )
        } catch (_: Exception) {
            root.recycle()
            return StepResult(success = false, errorMessage = "Assert error")
        }
    }

    private fun performScreenshotAction(): StepResult {
        // Screenshot capability requires MediaProjection - handled in MirrorEngine
        return StepResult(success = true, actualStrategy = "SCREENSHOT_DEFERRED")
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

        // Priority 4: fallback to screen center (never return 0,0 → causes "invalid coordinate")
        val dm = resources.displayMetrics
        return ScreenCoordinate(dm.widthPixels / 2, dm.heightPixels / 2)
    }

    private fun needsNodeForAction(action: ActionType, step: Step): Boolean {
        // TAP / LONG_PRESS never require a node — coordinate dispatch always works as fallback.
        // Even if coordinate is (0,0), performTapAction will derive from targetNode bounds.
        if (action == ActionType.TAP || action == ActionType.LONG_PRESS) {
            return false
        }
        // INPUT: only needs node for set-text; clipboard-based input is fallback
        if (action == ActionType.INPUT) {
            return step.screenCoordinate?.isValid != true && step.inputText.isNotBlank()
        }
        return action != ActionType.WAIT && action != ActionType.BACK
                && action != ActionType.HOME && action != ActionType.SWIPE
                && action != ActionType.OPEN_APP && action != ActionType.SCREENSHOT
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
