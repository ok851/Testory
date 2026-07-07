package com.testory.assistant.v2.service.accessibility

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.content.Intent
import android.graphics.Path
import android.graphics.Rect
import android.os.Build
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import com.testory.assistant.v2.core.model.*
import dagger.hilt.android.AndroidEntryPoint
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.*
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

    // ── Session state ──
    private val _sessionState = MutableStateFlow(SessionState())
    val sessionState: StateFlow<SessionState> = _sessionState.asStateFlow()

    // ── Event processing pipeline steps ──
    private val rawEventFlow = MutableSharedFlow<RecordedEvent>(
        extraBufferCapacity = 64,
        onBufferOverflow = kotlinx.coroutines.channels.BufferOverflow.DROP_OLDEST
    )

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)

    override fun onServiceConnected() {
        super.onServiceConnected()
        AccessibilityServiceHolder.attach(this)
        scope.launch {
            eventPipeline.start(rawEventFlow, sessionState)
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

        val recordedEvent = RecordedEvent(
            eventType = event.eventType,
            packageName = event.packageName?.toString() ?: "",
            className = event.className?.toString() ?: "",
            text = event.text?.joinToString(" ") ?: "",
            sourceNode = sourceNode,
            eventTime = event.eventTime,
            scrollX = event.scrollX,
            scrollY = event.scrollY
        )

        // Feed event into pipeline (non-blocking)
        rawEventFlow.tryEmit(recordedEvent)

        // Handle special events
        when (event.eventType) {
            AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED,
            AccessibilityEvent.TYPE_WINDOW_CONTENT_CHANGED -> {
                handleWindowEvent(event)
            }
        }
    }

    /**
     * 提取事件源节点。
     * 优先使用 event.source；当 source 为 null 时，尝试使用当前窗口的聚焦节点作为回退，
     * 以补全部分控件不暴露 source 时丢失的坐标信息。
     */
    private fun extractSourceNode(event: AccessibilityEvent): NodeInfo? {
        // 1. Primary: event.source
        event.source?.let { node ->
            return nodeAnalyzer.extractNodeInfo(node).also { node.recycle() }
        }

        // 2. Fallback: currently focused node in active window
        if (event.eventType == AccessibilityEvent.TYPE_VIEW_CLICKED ||
            event.eventType == AccessibilityEvent.TYPE_VIEW_LONG_CLICKED ||
            event.eventType == AccessibilityEvent.TYPE_VIEW_FOCUSED ||
            event.eventType == AccessibilityEvent.TYPE_VIEW_SELECTED) {
            val root = rootInActiveWindow ?: return null
            try {
                val focused = root.findFocus(AccessibilityNodeInfo.FOCUS_INPUT)
                    ?: root.findFocus(AccessibilityNodeInfo.FOCUS_ACCESSIBILITY)
                if (focused != null) {
                    return nodeAnalyzer.extractNodeInfo(focused).also { focused.recycle() }
                }
            } finally {
                root.recycle()
            }
        }

        return null
    }

    override fun onInterrupt() {
        _sessionState.update { it.copy(armedMode = ArmedMode.IDLE) }
    }

    override fun onDestroy() {
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
    }

    fun pauseRecording() {
        _sessionState.update { it.copy(isPaused = true) }
    }

    fun resumeRecording() {
        _sessionState.update { it.copy(isPaused = false) }
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
                    actualStrategy = "NODE_LOOKUP_FAILED"
                )
            }

            val result = performAction(targetNode, step)
            val durationMs = System.currentTimeMillis() - startTime

            result.copy(
                stepIndex = step.index,
                stepId = step.id,
                durationMs = durationMs
            )
        } catch (e: Exception) {
            StepResult(
                stepIndex = step.index,
                stepId = step.id,
                success = false,
                errorMessage = e.message ?: "Unknown error",
                durationMs = System.currentTimeMillis() - startTime
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
     * 执行滑动手势。
     */
    fun performGesture(
        path: Path,
        durationMs: Long = 300,
        callback: ((Boolean) -> Unit)? = null
    ) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.N) {
            callback?.invoke(false)
            return
        }
        val gesture = GestureDescription.Builder()
            .addStroke(GestureDescription.StrokeDescription(path, 0, durationMs))
            .build()
        dispatchGesture(gesture, object : GestureResultCallback() {
            override fun onCompleted(gestureDescription: GestureDescription?) {
                callback?.invoke(true)
            }
            override fun onCancelled(gestureDescription: GestureDescription?) {
                callback?.invoke(false)
            }
        }, null)
    }

    /**
     * 执行点击手势。
     */
    fun performClick(x: Float, y: Float, callback: ((Boolean) -> Unit)? = null) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.N) {
            callback?.invoke(false)
            return
        }
        val path = Path().apply { moveTo(x, y) }
        val gesture = GestureDescription.Builder()
            .addStroke(GestureDescription.StrokeDescription(path, 0, 50))
            .build()
        dispatchGesture(gesture, object : GestureResultCallback() {
            override fun onCompleted(gestureDescription: GestureDescription?) {
                callback?.invoke(true)
            }
            override fun onCancelled(gestureDescription: GestureDescription?) {
                callback?.invoke(false)
            }
        }, null)
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
    }

    /**
     * 多级定位策略 — 修复 node/operation_node 混用问题。
     * 策略: text → content-desc → resource-id → class+index → 坐标
     */
    private fun locateTarget(root: AccessibilityNodeInfo, step: Step): AccessibilityNodeInfo? {
        val locator = step.locator

        // Strategy 1: text match
        if (locator.text.isNotBlank()) {
            val results = root.findAccessibilityNodeInfosByText(locator.text)
            if (results.isNotEmpty()) return results[0]
        }

        // Strategy 2: content-description
        if (locator.contentDesc.isNotBlank()) {
            val results = root.findAccessibilityNodeInfosByText(locator.contentDesc)
            if (results.isNotEmpty()) return results[0]
        }

        // Strategy 3: resource-id
        if (locator.resourceId.isNotBlank()) {
            val results = root.findAccessibilityNodeInfosByViewId(locator.resourceId)
            if (results.isNotEmpty()) return results[0]
        }

        // Strategy 4: coordinate fallback (only when coordinate is valid)
        val coord = step.screenCoordinate
        if (coord != null && coord.isValid) {
            return findNodeAtCoordinate(root, coord)
        }

        return null
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
        if (node != null && node.isClickable) {
            val success = node.performAction(AccessibilityNodeInfo.ACTION_CLICK)
            return StepResult(success = success, actualStrategy = "NODE_CLICK")
        }
        // Fallback: coordinate click
        val coord = getActionCoordinate(node, step)
        if (coord != null && coord.isValid) {
            performClick(coord.x.toFloat(), coord.y.toFloat())
            return StepResult(success = true, actualStrategy = "COORDINATE_CLICK",
                actualCoordinate = coord)
        }
        return StepResult(success = false, errorMessage = "No valid click target")
    }

    private fun performLongPressAction(node: AccessibilityNodeInfo?, step: Step): StepResult {
        if (node != null && node.isLongClickable) {
            val success = node.performAction(AccessibilityNodeInfo.ACTION_LONG_CLICK)
            return StepResult(success = success, actualStrategy = "NODE_LONG_CLICK")
        }
        // Fallback: gesture long press at coordinate
        val coord = getActionCoordinate(node, step)
        if (coord != null && coord.isValid) {
            val path = Path().apply { moveTo(coord.x.toFloat(), coord.y.toFloat()) }
            performGesture(path, durationMs = 800)
            return StepResult(success = true, actualStrategy = "COORDINATE_LONG_PRESS",
                actualCoordinate = coord)
        }
        return StepResult(success = false, errorMessage = "No valid long-press target")
    }

    private fun performInputAction(node: AccessibilityNodeInfo?, step: Step): StepResult {
        return if (node != null && node.isEditable) {
            val args = android.os.Bundle().apply {
                putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, step.inputText)
            }
            val success = node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)
            StepResult(success = success, actualStrategy = "NODE_INPUT")
        } else {
            // Focus + input text
            val success = node?.performAction(AccessibilityNodeInfo.ACTION_FOCUS) ?: false
            if (success && node != null) {
                val args = android.os.Bundle().apply {
                    putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, step.inputText)
                }
                node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)
            }
            StepResult(success = success, actualStrategy = "FOCUS_INPUT")
        }
    }

    private fun performSwipeAction(step: Step): StepResult {
        val displayMetrics = resources.displayMetrics
        val w = displayMetrics.widthPixels
        val h = displayMetrics.heightPixels

        val path = Path()
        val margin = 100

        when (step.swipeDirection) {
            SwipeDirection.UP -> {
                // Swipe from bottom to top
                path.moveTo((w / 2).toFloat(), (h * 0.8).toFloat())
                path.lineTo((w / 2).toFloat(), (h * 0.2).toFloat())
            }
            SwipeDirection.DOWN -> {
                path.moveTo((w / 2).toFloat(), (h * 0.2).toFloat())
                path.lineTo((w / 2).toFloat(), (h * 0.8).toFloat())
            }
            SwipeDirection.LEFT -> {
                path.moveTo((w - margin).toFloat(), (h / 2).toFloat())
                path.lineTo(margin.toFloat(), (h / 2).toFloat())
            }
            SwipeDirection.RIGHT -> {
                path.moveTo(margin.toFloat(), (h / 2).toFloat())
                path.lineTo((w - margin).toFloat(), (h / 2).toFloat())
            }
            null -> {
                return StepResult(success = false, errorMessage = "No swipe direction specified")
            }
        }

        performGesture(path, durationMs = 300)
        return StepResult(success = true, actualStrategy = "GESTURE_SWIPE")
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

    private fun performOpenAppAction(step: Step): StepResult {
        val packageName = step.locator.packageName
        if (packageName.isBlank()) {
            return StepResult(success = false, errorMessage = "No package name for OPEN_APP")
        }
        val intent = packageManager.getLaunchIntentForPackage(packageName)
        return if (intent != null) {
            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK)
            startActivity(intent)
            StepResult(success = true, actualStrategy = "OPEN_APP")
        } else {
            StepResult(success = false, errorMessage = "Cannot launch app: $packageName")
        }
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
        // Priority: stored coordinate > node bounds center
        step.screenCoordinate?.let { if (it.isValid) return it }

        if (node != null) {
            val rect = Rect()
            node.getBoundsInScreen(rect)
            if (rect.right > rect.left && rect.bottom > rect.top) {
                return ScreenCoordinate(rect.centerX(), rect.centerY())
            }
        }
        return null
    }

    private fun needsNodeForAction(action: ActionType, step: Step): Boolean {
        // TAP / LONG_PRESS can fall back to coordinate if a valid coordinate is recorded
        if (action == ActionType.TAP || action == ActionType.LONG_PRESS) {
            return step.screenCoordinate?.isValid != true
        }
        return action != ActionType.WAIT && action != ActionType.BACK
                && action != ActionType.HOME && action != ActionType.SWIPE
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
