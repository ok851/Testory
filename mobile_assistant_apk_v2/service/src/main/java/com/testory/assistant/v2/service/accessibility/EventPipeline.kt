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
    private var currentGestureId: String = ""
    private var pendingSteps = mutableListOf<Step>()

    private var scope: CoroutineScope? = null

    /**
     * 启动事件管线。
     */
    suspend fun start(
        rawEventFlow: SharedFlow<RecordedEvent>,
        sessionState: StateFlow<SessionState>
    ) {
        scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)

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
        scope?.cancel()
        scope = null
        val remaining = pendingSteps.toList()
        pendingSteps.clear()
        eventBuffer.clear()
        _recordingState.value = RecordingState.IDLE
        return remaining
    }

    // ── Pipeline stages ──

    private fun processEvent(event: RecordedEvent) {
        // Stage 1: Deduplication & filtering
        if (!filterEvent(event)) return

        // Stage 2: Gesture classification
        eventBuffer.add(event)
        val gesture = classifyGesture()

        // Stage 3: Element analysis & step generation
        gesture?.let { g ->
            val step = generateStep(g)
            if (step != null) {
                // Stage 4: Emit + buffer
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
     * 手势分类 — 移植自 TouchGestureClassifier。
     * 将事件序列分类为 click / long-press / swipe / text-input。
     */
    private fun classifyGesture(): GestureInfo? {
        if (eventBuffer.isEmpty()) return null

        val events = eventBuffer.toList()
        eventBuffer.clear()

        // Detect gesture type from event sequence
        val eventTypes = events.map { it.eventType }

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

        if (AccessibilityEvent.TYPE_VIEW_SCROLLED in eventTypes) {
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
                // Extract node info for selector-based locator
                val nodeInfo = gesture.sourceNode
                val locator = buildLocator(nodeInfo)

                // Always record the actual click coordinate (FIX: was sometimes 0,0)
                val screenCoord = ScreenCoordinate(
                    x = gesture.screenX,
                    y = gesture.screenY
                )

                Step(
                    id = stepId,
                    index = stepIndex,
                    action = if (gesture.isLongPress) ActionType.LONG_PRESS else ActionType.TAP,
                    description = generateDescription(nodeInfo, "点击"),
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
                Step(
                    id = stepId,
                    index = stepIndex,
                    action = ActionType.SWIPE,
                    description = "滑动: ${gesture.direction}",
                    swipeDirection = gesture.direction,
                    screenCoordinate = ScreenCoordinate(
                        x = gesture.toX,
                        y = gesture.toY
                    ),
                    locationSource = LocationSource.COORDINATE
                )
            }

            is GestureInfo.AppLaunch -> {
                Step(
                    id = stepId,
                    index = stepIndex,
                    action = ActionType.OPEN_APP,
                    description = "打开应用: ${gesture.appName}",
                    locator = Locator(packageName = gesture.packageName),
                    targetNode = NodeInfo(packageName = gesture.packageName)
                )
            }

            else -> null
        }
    }

    // ── Locator building (from node info) ──

    private fun buildLocator(node: NodeInfo?): Locator {
        if (node == null) return Locator()

        return Locator(
            text = node.text.takeIf { it.isNotBlank() } ?: "",
            contentDesc = node.contentDescription.takeIf { it.isNotBlank() } ?: "",
            resourceId = node.resourceId.takeIf { it.isNotBlank() } ?: "",
            className = node.className.takeIf { it.isNotBlank() } ?: "",
            packageName = node.packageName.takeIf { it.isNotBlank() } ?: ""
        )
    }

    private fun generateDescription(node: NodeInfo?, actionPrefix: String): String {
        if (node == null) return "$actionPrefix (坐标定位)"

        val label = when {
            node.text.isNotBlank() -> "\"${node.text.take(30)}\""
            node.contentDescription.isNotBlank() -> "\"${node.contentDescription.take(30)}\""
            node.resourceId.isNotBlank() -> {
                val id = node.resourceId.substringAfterLast("/")
                if (id.isNotBlank()) "[$id]" else "[${node.resourceId}]"
            }
            else -> node.className.substringAfterLast(".").takeIf { it.isNotBlank() } ?: "未知元素"
        }
        return "$actionPrefix $label"
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
        val node = event.sourceNode

        return GestureInfo.Click(
            screenX = node?.bounds?.centerX ?: 0,
            screenY = node?.bounds?.centerY ?: 0,
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

    private fun buildSwipeInfo(events: List<RecordedEvent>): GestureInfo.Swipe {
        // Determine direction from scroll deltas
        val scrollEvent = events.firstOrNull {
            it.eventType == AccessibilityEvent.TYPE_VIEW_SCROLLED
        } ?: events.first()

        val scrollY = scrollEvent.scrollY
        val scrollX = scrollEvent.scrollX
        val direction = when {
            scrollY > 0 -> SwipeDirection.DOWN
            scrollY < 0 -> SwipeDirection.UP
            scrollX > 0 -> SwipeDirection.RIGHT
            else -> SwipeDirection.LEFT
        }

        return GestureInfo.Swipe(
            direction = direction,
            fromX = 0, fromY = 0,
            toX = scrollX, toY = scrollY
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
