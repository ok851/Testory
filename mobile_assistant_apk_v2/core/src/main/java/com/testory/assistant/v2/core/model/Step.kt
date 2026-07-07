package com.testory.assistant.v2.core.model

import kotlinx.serialization.Serializable

/**
 * 测试步骤 — 录制/回放的核心数据单元。
 *
 * 相比 v1 (Java) 的关键修复:
 * - node 与 operation_node 严格分离，不再混用
 * - viewport_coord 总是写入真实坐标 (不再丢失)
 * - selector 与 coordinate 来源互斥标记，避免回放时歧义
 */
@Serializable
data class Step(
    val id: String = "",
    val caseId: String = "",
    /** 步骤序号 (1-based) */
    val index: Int = 0,
    /** 动作类型: tap / long_press / swipe / input / assert / wait / open_app */
    val action: ActionType = ActionType.TAP,
    /** 人类可读描述，AI/录制自动生成 */
    val description: String = "",

    // ── 定位信息 ──
    /** 分层定位器 (多策略回退) */
    val locator: Locator = Locator(),
    /** 操作目标节点信息 (录制时记录的原始无障碍节点) */
    val targetNode: NodeInfo? = null,
    /** 操作时屏幕坐标 (相对于屏幕左上角) */
    val screenCoordinate: ScreenCoordinate? = null,
    /** 定位来源标记: 标明该步骤是由 selector 还是坐标定位的 */
    val locationSource: LocationSource = LocationSource.UNKNOWN,

    // ── 动作参数 ──
    /** 输入文本 (action=input 时使用) */
    val inputText: String = "",
    /** 滑动方向 (action=swipe 时使用) */
    val swipeDirection: SwipeDirection? = null,
    /** 等待时长(ms) (action=wait 时使用) */
    val waitDurationMs: Long = 0,
    /** 断言期望文本 (action=assert 时使用) */
    val assertText: String = "",

    // ── 元数据 ──
    /** 执行前等待(ms)，给页面切换留时间 */
    val preWaitMs: Long = 500,
    /** 重试次数 */
    val maxRetries: Int = 3,
    /** 步骤是否被标记为可选 (失败不中断) */
    val optional: Boolean = false
)

@Serializable
enum class ActionType {
    TAP,
    LONG_PRESS,
    SWIPE,
    INPUT,
    ASSERT,
    WAIT,
    OPEN_APP,
    BACK,
    HOME,
    SCREENSHOT
}

@Serializable
enum class SwipeDirection { UP, DOWN, LEFT, RIGHT }

@Serializable
enum class LocationSource {
    /** 未知来源 (回放时走多级回退) */
    UNKNOWN,
    /** 由 selector (text/content-desc/resource-id) 定位 */
    SELECTOR,
    /** 由屏幕绝对坐标定位 */
    COORDINATE,
    /** 由视觉 (OCR/模板匹配) 定位 */
    VISUAL
}

/**
 * 分层定位器 — 支持多策略回退。
 * 参照旧 StepNormalizer 的多级定位逻辑，但更清晰分层。
 */
@Serializable
data class Locator(
    /** 优先级1: 文本匹配 */
    val text: String = "",
    /** 优先级1: 文本正则 */
    val textRegex: String = "",
    /** 优先级2: content-description */
    val contentDesc: String = "",
    /** 优先级3: resource-id */
    val resourceId: String = "",
    /** 优先级4: class name + index */
    val className: String = "",
    val classIndex: Int = 0,
    /** 优先级5: XPath (懒加载时生成) */
    val xpath: String = "",
    /** 依赖的包名 */
    val packageName: String = "",
    /** 是否在 WebView 中定位 */
    val isWebView: Boolean = false
) {
    val isEmpty: Boolean get() = text.isEmpty() && contentDesc.isEmpty()
            && resourceId.isEmpty() && xpath.isEmpty() && className.isEmpty()
}

/**
 * 无障碍节点信息 — 录制时从 AccessibilityNodeInfo 提取。
 * 此为纯数据模型，不再持有原生 AccessibilityNodeInfo 引用 (避免内存泄漏)。
 */
@Serializable
data class NodeInfo(
    val className: String = "",
    val text: String = "",
    val contentDescription: String = "",
    val resourceId: String = "",
    val packageName: String = "",
    /** 节点在屏幕上的边界 */
    val bounds: ScreenRect = ScreenRect(),
    /** 是否可点击 */
    val isClickable: Boolean = false,
    /** 是否可编辑 */
    val isEditable: Boolean = false,
    /** 是否已选中 */
    val isChecked: Boolean = false,
    /** 窗口层级 */
    val windowId: Int = -1,
    /** 子节点数量 */
    val childCount: Int = 0,
    /** 节点深度 */
    val depth: Int = 0
)

@Serializable
data class ScreenCoordinate(
    val x: Int = 0,
    val y: Int = 0
) {
    companion object {
        val ZERO = ScreenCoordinate(0, 0)
    }
    val isValid: Boolean get() = x > 0 || y > 0
}

@Serializable
data class ScreenRect(
    val left: Int = 0,
    val top: Int = 0,
    val right: Int = 0,
    val bottom: Int = 0
) {
    val centerX: Int get() = (left + right) / 2
    val centerY: Int get() = (top + bottom) / 2
    val isValid: Boolean get() = right > left && bottom > top
    fun toScreenCoordinate() = ScreenCoordinate(centerX, centerY)
}
