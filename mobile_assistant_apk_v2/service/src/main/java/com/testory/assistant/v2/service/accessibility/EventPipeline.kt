package com.testory.assistant.v2.service.accessibility

import android.view.accessibility.AccessibilityEvent
import com.testory.assistant.v2.core.model.*
import com.testory.assistant.v2.core.repository.CaseRepository
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.*
import javax.inject.Inject
import javax.inject.Singleton
import java.util.UUID

/**
 * 事件处理管线 — 替代旧版 AssistantSession/RecordEventFilter/StepNormalizer 的分散逻辑。
 *
 * 管线阶段:
 *   原始事件 → 去重/过滤 → 手势分类 → 元素定位 → 步骤生成 → 持久化 → 通知
 *
 * 关键修复:
 * - node/operation_node 分离
 * - viewport_coord 真实坐标写入
 * - LocationSource 标记来源互斥
 * - 在主线程中提前提取 AccessibilityNodeInfo，避免跨线程后 source 失效导致坐标丢失
 */
@Singleton
class EventPipeline @Inject constructor(
    private val caseRepository: CaseRepository
) {
    // ── Pipeline state ──
    private val _stepFlow = MutableSharedFlow<Step>(extraBufferCapacity = 32)
    val stepFlow: SharedFlow<Step> = _stepFlow.asSharedFlow()

    private val _recordingState = MutableStateFlow(RecordingState.IDLE)
    val recordingState: StateFlow<RecordingState> = _recordingState.asStateFlow()

    // Event sequence tracking for gesture classification
    private val eventBuffer = mutableListOf<RecordedEvent>()
    private var lastEventTime: Long = 0
    private var lastEventHash: Int = 0
    private var lastScrollX: Int = 0
    private var lastScrollY: Int = 0
    private var currentGestureId: String = ""
    private var pendingSteps = mutableListOf<Step>()

    private var pendingSwipe: GestureInfo.Swipe? = null
    private var classifyJob: Job? = null
    private val classifyDebounceMs = 100L

    /** 输入框逐字 TEXT_CHANGED：合并为一次输入，停顿后再落步 */
    private var pendingTextInput: GestureInfo.TextInput? = null
    private var textInputJob: Job? = null
    private val textInputDebounceMs = 800L

    private var scope: CoroutineScope? = null

    /**
     * 启动事件管线。
     */
    suspend fun start(
        rawEventFlow: SharedFlow<RecordedEvent>,
        sessionState: StateFlow<SessionState>
    ) {
        scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)

        lastScrollX = 0
        lastScrollY = 0
        pendingSwipe = null
        pendingTextInput = null
        textInputJob?.cancel()
        textInputJob = null
        eventBuffer.clear()

        scope?.launch {
            // Mirror session recording state to pipeline recording state
            sessionState.collect { state ->
                _recordingState.value = when {
                    state.isRecording && state.isPaused -> RecordingState.PAUSED
                    state.isRecording -> RecordingState.RECORDING
                    else -> RecordingState.IDLE
                }
            }
        }

        scope?.launch {
            // Combine raw events with session state
            rawEventFlow
                .combine(sessionState) { event, state -> Pair(event, state) }
                .collect { (event, state) ->
                    if (state.isRecording && !state.isPaused) {
                        processEvent(event)
                    }
                }
        }
    }

    /**
     * 停止管线，返回未保存的步骤。
     */
    suspend fun stop(): List<Step> {
        classifyJob?.cancel()
        classifyJob = null
        textInputJob?.cancel()
        textInputJob = null
        flushPendingTextInput()
        // 冲刷未分类缓冲
        if (eventBuffer.isNotEmpty()) {
            val gesture = classifyGesture()
            if (gesture is GestureInfo.Swipe) {
                handleSwipeGesture(gesture)
            } else if (gesture is GestureInfo.TextInput) {
                handleTextInputGesture(gesture)
                flushPendingTextInput()
            } else if (gesture != null) {
                flushPendingSwipe()
                val step = generateStep(gesture)
                if (step != null) {
                    pendingSteps.add(step)
                    _stepFlow.tryEmit(step)
                }
            }
        }
        flushPendingSwipe()
        scope?.cancel()
        scope = null
        val remaining = pendingSteps.toList()
        pendingSteps.clear()
        eventBuffer.clear()
        lastScrollX = 0
        lastScrollY = 0
        pendingTextInput = null
        _recordingState.value = RecordingState.IDLE
        return remaining
    }

    fun currentStepCount(): Int = pendingSteps.size

    fun emitDirect(step: Step) {
        pendingSteps.add(step)
        _stepFlow.tryEmit(step)
    }

    // ── Pipeline stages ──

    private fun processEvent(event: RecordedEvent) {
        if (!filterEvent(event)) return

        val isTextEvent = event.eventType == AccessibilityEvent.TYPE_VIEW_TEXT_CHANGED
                || event.eventType == AccessibilityEvent.TYPE_VIEW_TEXT_SELECTION_CHANGED

        if (isTextEvent) {
            // 先落盘未决的 click/swipe，再合并输入
            classifyJob?.cancel()
            if (eventBuffer.isNotEmpty()) {
                val gesture = classifyGesture()
                if (gesture is GestureInfo.Swipe) {
                    handleSwipeGesture(gesture)
                } else if (gesture != null && gesture !is GestureInfo.TextInput) {
                    flushPendingSwipe()
                    val step = generateStep(gesture)
                    if (step != null) {
                        pendingSteps.add(step)
                        _stepFlow.tryEmit(step)
                    }
                }
            }
            flushPendingSwipe()
            handleTextInputGesture(
                GestureInfo.TextInput(text = event.text, sourceNode = event.sourceNode)
            )
            return
        }

        // 非输入事件：先冲刷合并中的输入
        flushPendingTextInput()

        eventBuffer.add(event)
        // 短窗口合并 click/scroll，避免一次点击被拆成 tap+swipe
        classifyJob?.cancel()
        classifyJob = scope?.launch {
            delay(classifyDebounceMs)
            val gesture = classifyGesture()
            if (gesture is GestureInfo.Swipe) {
                handleSwipeGesture(gesture)
            } else if (gesture is GestureInfo.TextInput) {
                handleTextInputGesture(gesture)
            } else if (gesture != null) {
                flushPendingSwipe()
                val step = generateStep(gesture)
                if (step != null) {
                    pendingSteps.add(step)
                    _stepFlow.tryEmit(step)
                }
            }
        }
    }

    private fun handleTextInputGesture(input: GestureInfo.TextInput) {
        // 保留最新累计文本（系统 TEXT_CHANGED 已是全文）
        if (input.text.isEmpty() && pendingTextInput == null) return
        pendingTextInput = GestureInfo.TextInput(
            text = input.text.ifEmpty { pendingTextInput?.text.orEmpty() },
            sourceNode = input.sourceNode ?: pendingTextInput?.sourceNode
        )
        textInputJob?.cancel()
        textInputJob = scope?.launch {
            delay(textInputDebounceMs)
            flushPendingTextInput()
        }
    }

    private fun flushPendingTextInput() {
        textInputJob?.cancel()
        textInputJob = null
        val input = pendingTextInput ?: return
        pendingTextInput = null
        if (input.text.isEmpty()) return

        val last = pendingSteps.lastOrNull()
        if (last != null && last.action == ActionType.INPUT && isSameInputField(last, input)) {
            val updated = last.copy(
                description = "输入: ${input.text.take(40)}",
                inputText = input.text,
                targetNode = input.sourceNode ?: last.targetNode,
                locator = buildLocator(input.sourceNode).takeUnless { it.isEmpty } ?: last.locator,
                screenCoordinate = input.sourceNode?.bounds?.let {
                    ScreenCoordinate(it.centerX, it.centerY)
                } ?: last.screenCoordinate
            )
            pendingSteps[pendingSteps.lastIndex] = updated
            _stepFlow.tryEmit(updated)
            return
        }

        val step = generateStep(input) ?: return
        pendingSteps.add(step)
        _stepFlow.tryEmit(step)
    }

    private fun isSameInputField(last: Step, input: GestureInfo.TextInput): Boolean {
        val a = last.targetNode
        val b = input.sourceNode ?: return true
        if (a == null) return true
        if (a.resourceId.isNotBlank() && a.resourceId == b.resourceId) return true
        if (a.bounds.isValid && b.bounds.isValid) {
            val dx = kotlin.math.abs(a.bounds.centerX - b.bounds.centerX)
            val dy = kotlin.math.abs(a.bounds.centerY - b.bounds.centerY)
            if (dx < 40 && dy < 40) return true
        }
        // 连续输入且无定位信息时，默认合并
        return a.resourceId.isBlank() && b.resourceId.isBlank()
    }

    private fun handleSwipeGesture(swipe: GestureInfo.Swipe) {
        val existing = pendingSwipe

        if (existing == null) {
            pendingSwipe = swipe
            return
        }

        if (existing.direction != swipe.direction) {
            flushPendingSwipe()
            pendingSwipe = swipe
        } else {
            pendingSwipe = swipe
        }
    }

    private fun flushPendingSwipe() {
        val s = pendingSwipe
        pendingSwipe = null
        if (s != null) {
            val step = generateStep(s)
            if (step != null) {
                pendingSteps.add(step)
                _stepFlow.tryEmit(step)
            }
        }
    }

    /**
     * 事件去重/过滤 — 移植自 RecordEventFilter。
     * 过滤规则:
     * - 过滤系统 UI 事件 (状态栏、导航栏)
     * - 过滤重复事件 (同一 hash 120ms 内)
     * - 过滤无障碍焦点事件 (不产生实际交互)
     * - 过滤 Testory 自身 UI 事件
     */
    private fun filterEvent(event: RecordedEvent): Boolean {
        val now = System.currentTimeMillis()
        val pkg = event.packageName

        // Filter self (Testory App) events
        if (pkg.contains("com.testory.assistant")) return false

        // Filter system UI
        if (pkg == "com.android.systemui") return false

        // 桌面图标点击按普通 TAP 录制（与 PC 一致），不再过滤后改写成 OPEN_APP

        // Filter non-interactive event types
        when (event.eventType) {
            AccessibilityEvent.TYPE_NOTIFICATION_STATE_CHANGED,
            AccessibilityEvent.TYPE_ANNOUNCEMENT,
            AccessibilityEvent.TYPE_WINDOWS_CHANGED -> return false
        }

        // Deduplicate by hash within 120ms window
        val eventHash = calculateEventHash(event)
        if (eventHash == lastEventHash && (now - lastEventTime) < 120) {
            return false
        }

        lastEventTime = now
        lastEventHash = eventHash
        return true
    }

    /**
     * 手势分类 — 将事件序列分类为 click / long-press / swipe / text-input。
     * 原缺陷：TYPE_VIEW_SCROLLED 与 TYPE_VIEW_CLICKED 同时到达时各自独立分类，
     * 导致一次点击被重复录制为 tap + swipe 两条步骤。
     * 修复：同时存在时只保留 click (scroll 作为点击副作用被过滤)。
     */
    private fun classifyGesture(): GestureInfo? {
        if (eventBuffer.isEmpty()) return null

        val events = eventBuffer.toList()
        eventBuffer.clear()

        val eventTypes = events.map { it.eventType }
        val hasClick = AccessibilityEvent.TYPE_VIEW_CLICKED in eventTypes
                || AccessibilityEvent.TYPE_VIEW_LONG_CLICKED in eventTypes
        val hasScroll = AccessibilityEvent.TYPE_VIEW_SCROLLED in eventTypes

        if (AccessibilityEvent.TYPE_VIEW_TEXT_CHANGED in eventTypes ||
            AccessibilityEvent.TYPE_VIEW_TEXT_SELECTION_CHANGED in eventTypes) {
            return buildTextInputInfo(events)
        }

        if (AccessibilityEvent.TYPE_VIEW_CLICKED in eventTypes) {
            return buildClickInfo(events, isLongPress = false)
        }

        if (AccessibilityEvent.TYPE_VIEW_LONG_CLICKED in eventTypes) {
            return buildClickInfo(events, isLongPress = true)
        }

        if (hasScroll && !hasClick) {
            return buildSwipeInfo(events)
        }

        // Default: capture as tap if there's a focused/clicked node
        val clickedEvent = events.firstOrNull {
            it.eventType == AccessibilityEvent.TYPE_VIEW_CLICKED
        } ?: events.firstOrNull {
            it.eventType == AccessibilityEvent.TYPE_VIEW_FOCUSED
        } ?: events.firstOrNull {
            it.eventType == AccessibilityEvent.TYPE_VIEW_SELECTED
        }

        return clickedEvent?.let { buildClickInfo(events, isLongPress = false) }
    }

    /**
     * 步骤生成 — 移植自 StepNormalizer。
     * 关键修复: node 与 operation_node 严格分离，
     * viewport_coord/operation_node_coords 总是写入真实坐标。
     */
    private fun generateStep(gesture: GestureInfo): Step? {
        val stepId = UUID.randomUUID().toString()
        val stepIndex = pendingSteps.size + 1

        return when (gesture) {
            is GestureInfo.Click -> {
                val nodeInfo = gesture.sourceNode
                val locator = buildLocator(nodeInfo)

                val screenCoord = ScreenCoordinate(
                    x = gesture.screenX,
                    y = gesture.screenY
                )

                Step(
                    id = stepId,
                    index = stepIndex,
                    action = if (gesture.isLongPress) ActionType.LONG_PRESS else ActionType.TAP,
                    description = generateDescription(nodeInfo, "点击", gesture.screenX, gesture.screenY),
                    locator = locator,
                    targetNode = nodeInfo,
                    screenCoordinate = screenCoord,
                    locationSource = when {
                        !locator.isEmpty -> LocationSource.SELECTOR
                        screenCoord.isValid -> LocationSource.COORDINATE
                        else -> LocationSource.UNKNOWN
                    }
                )
            }

            is GestureInfo.TextInput -> {
                Step(
                    id = stepId,
                    index = stepIndex,
                    action = ActionType.INPUT,
                    description = "输入: ${gesture.text.take(20)}",
                    inputText = gesture.text,
                    targetNode = gesture.sourceNode,
                    screenCoordinate = gesture.sourceNode?.bounds?.let {
                        ScreenCoordinate(it.centerX, it.centerY)
                    },
                    locator = buildLocator(gesture.sourceNode)
                )
            }

            is GestureInfo.Swipe -> {
                val screenCoord = ScreenCoordinate(
                    x = gesture.toX,
                    y = gesture.toY
                )
                Step(
                    id = stepId,
                    index = stepIndex,
                    action = ActionType.SWIPE,
                    description = generateSwipeDescription(
                        gesture.direction, gesture.fromX, gesture.fromY, gesture.toX, gesture.toY),
                    // 持久化起止坐标，供 PC sync 解析（无需改 Room schema）
                    inputText = "${gesture.fromX},${gesture.fromY}|${gesture.toX},${gesture.toY}",
                    swipeDirection = gesture.direction,
                    screenCoordinate = screenCoord,
                    locationSource = LocationSource.COORDINATE
                )
            }

            is GestureInfo.AppLaunch -> {
                Step(
                    id = stepId,
                    index = stepIndex,
                    action = ActionType.OPEN_APP,
                    description = if (gesture.appName.isNotBlank()) "打开「${gesture.appName}」应用"
                        else "打开应用: ${gesture.packageName}",
                    locator = Locator(
                        packageName = gesture.packageName,
                        text = gesture.appName
                    ),
                    targetNode = NodeInfo(
                        packageName = gesture.packageName,
                        text = gesture.appName
                    )
                )
            }
        }
    }

    // ── Locator building (from node info) ──

    private fun buildLocator(node: NodeInfo?): Locator {
        if (node == null) return Locator()

        val cls = node.className
        val webLike = cls.contains("WebView", ignoreCase = true) ||
            cls.contains("Flutter", ignoreCase = true) ||
            cls.contains("SurfaceView", ignoreCase = true)

        return Locator(
            text = node.text.takeIf { it.isNotBlank() } ?: "",
            contentDesc = node.contentDescription.takeIf { it.isNotBlank() } ?: "",
            resourceId = node.resourceId.takeIf { it.isNotBlank() } ?: "",
            className = cls.takeIf { it.isNotBlank() } ?: "",
            packageName = node.packageName.takeIf { it.isNotBlank() } ?: "",
            isWebView = webLike
        )
    }

    private fun generateDescription(node: NodeInfo?, actionPrefix: String, screenX: Int = 0, screenY: Int = 0): String {
        if (node != null) {
            // 优先使用节点文本
            val label = when {
                node.text.isNotBlank() -> "\"${node.text.take(30)}\""
                node.contentDescription.isNotBlank() -> "\"${node.contentDescription.take(30)}\""
                node.resourceId.isNotBlank() -> {
                    val id = node.resourceId.substringAfterLast("/")
                    if (id.isNotBlank() && id.length > 4 && !id.matches(Regex("[a-z0-9]{1,4}"))) "[$id]"
                    else ""
                }
                else -> ""
            }
            if (label.isNotBlank()) return "$actionPrefix $label"
        }
        // 无文本时显示坐标
        return if (screenX > 0 || screenY > 0) {
            "$actionPrefix ($screenX, $screenY)"
        } else {
            "$actionPrefix (坐标定位)"
        }
    }

    private fun generateSwipeDescription(direction: SwipeDirection, fromX: Int, fromY: Int, toX: Int, toY: Int): String {
        val dirLabel = when (direction) {
            SwipeDirection.UP -> "上滑"
            SwipeDirection.DOWN -> "下滑"
            SwipeDirection.LEFT -> "左滑"
            SwipeDirection.RIGHT -> "右滑"
        }
        return if (fromX > 0 || fromY > 0) {
            "$dirLabel ($fromX, $fromY) → ($toX, $toY)"
        } else {
            "滑动: $dirLabel"
        }
    }

    // ── Helpers ──

    private fun calculateEventHash(event: RecordedEvent): Int {
        var hash = 7
        hash = 31 * hash + event.eventType
        hash = 31 * hash + event.packageName.hashCode()
        hash = 31 * hash + event.className.hashCode()
        hash = 31 * hash + event.text.hashCode()
        return hash
    }

    private fun buildClickInfo(events: List<RecordedEvent>, isLongPress: Boolean): GestureInfo.Click {
        val event = events.firstOrNull { it.sourceNode != null } ?: events.first()
        var node = event.sourceNode

        // Flutter/Compose 常把可读文本放在 AccessibilityEvent.text，而 source 节点为空文本
        if (node != null && node.text.isBlank() && event.text.isNotBlank()) {
            val cleaned = event.text.trim()
            if (cleaned.isNotBlank()) {
                node = node.copy(text = cleaned.take(80))
            }
        }
        if (node != null && node.contentDescription.isBlank() && event.text.isNotBlank()
            && node.text.isBlank()
        ) {
            node = node.copy(contentDescription = event.text.trim().take(80))
        }

        val sx = node?.bounds?.centerX ?: event.sourceBounds?.centerX ?: 0
        val sy = node?.bounds?.centerY ?: event.sourceBounds?.centerY ?: 0

        return GestureInfo.Click(
            screenX = sx,
            screenY = sy,
            isLongPress = isLongPress,
            sourceNode = node
        )
    }

    private fun buildTextInputInfo(events: List<RecordedEvent>): GestureInfo.TextInput {
        val textEvent = events.firstOrNull {
            it.eventType == AccessibilityEvent.TYPE_VIEW_TEXT_CHANGED
        } ?: events.first()
        val node = textEvent.sourceNode
        return GestureInfo.TextInput(
            text = textEvent.text,
            sourceNode = node
        )
    }

    private fun buildSwipeInfo(events: List<RecordedEvent>): GestureInfo? {
        val scrollEvent = events.firstOrNull {
            it.eventType == AccessibilityEvent.TYPE_VIEW_SCROLLED
        } ?: events.first()

        val curX = scrollEvent.scrollX
        val curY = scrollEvent.scrollY
        val dx = curX - lastScrollX
        val dy = curY - lastScrollY
        lastScrollX = curX
        lastScrollY = curY

        // Ignore stationary scroll events (delta == 0, view initialization noise).
        if (dx == 0 && dy == 0) return null

        val direction = when {
            dy > 10 -> SwipeDirection.UP
            dy < -10 -> SwipeDirection.DOWN
            dx > 10 -> SwipeDirection.RIGHT
            dx < -10 -> SwipeDirection.LEFT
            else -> null
        }
        if (direction == null) return null

        val bounds = (scrollEvent.sourceNode?.bounds ?: scrollEvent.sourceBounds)
        // 无 bounds 时用屏幕中部作为滑动起点，避免 RecyclerView/WebView 滚动手势丢失
        val sx = bounds?.centerX ?: 540
        val sy = bounds?.centerY ?: 960

        val rectH = if (bounds != null) (bounds.bottom - bounds.top) else 0
        val rectW = if (bounds != null) (bounds.right - bounds.left) else 0
        val swipeDistY = if (rectH > 0) (rectH * 0.5).toInt().coerceIn(100, 500) else 200
        val swipeDistX = if (rectW > 0) (rectW * 0.5).toInt().coerceIn(100, 400) else 150

        val (toX, toY) = when (direction) {
            SwipeDirection.UP -> sx to (sy - swipeDistY)
            SwipeDirection.DOWN -> sx to (sy + swipeDistY)
            SwipeDirection.LEFT -> (sx - swipeDistX) to sy
            SwipeDirection.RIGHT -> (sx + swipeDistX) to sy
        }

        val totalDist = kotlin.math.sqrt(
            ((toX - sx).toDouble() * (toX - sx) + (toY - sy).toDouble() * (toY - sy))
        )
        if (totalDist < 30.0) return null

        return GestureInfo.Swipe(
            direction = direction,
            fromX = sx,
            fromY = sy,
            toX = toX,
            toY = toY
        )
    }
}

/**
 * 已提取的录制事件 — 在无障碍服务主线程中把 AccessibilityNodeInfo 转为纯数据，
 * 避免 source 在跨线程传递后失效。
 */
data class RecordedEvent(
    val eventType: Int,
    val packageName: String = "",
    val className: String = "",
    val text: String = "",
    val sourceNode: NodeInfo? = null,
    val sourceBounds: ScreenRect? = null,
    val eventTime: Long = 0,
    val scrollX: Int = 0,
    val scrollY: Int = 0
)

/**
 * 手势信息 — 管线中间数据结构。
 */
sealed class GestureInfo {
    data class Click(
        val screenX: Int,
        val screenY: Int,
        val isLongPress: Boolean = false,
        val sourceNode: NodeInfo? = null
    ) : GestureInfo()

    data class TextInput(
        val text: String,
        val sourceNode: NodeInfo? = null
    ) : GestureInfo()

    data class Swipe(
        val direction: SwipeDirection,
        val fromX: Int,
        val fromY: Int,
        val toX: Int,
        val toY: Int
    ) : GestureInfo()

    data class AppLaunch(
        val packageName: String,
        val appName: String
    ) : GestureInfo()
}
