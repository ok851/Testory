package com.testory.assistant.v2.core.model

import kotlinx.serialization.Serializable

/**
 * 测试步骤 — 录制/回放的核心数据单元（Unified Step IR）。
 *
 * 与 PC sync 共用 action 名（小写）与 mobile_spec 扩展字段。
 */
@Serializable
data class Step(
    val id: String = "",
    val caseId: String = "",
    /** 步骤序号 (1-based) */
    val index: Int = 0,
    val action: ActionType = ActionType.TAP,
    /** 人类可读描述，AI/录制自动生成 */
    val description: String = "",

    // ── 定位信息 ──
    val locator: Locator = Locator(),
    val targetNode: NodeInfo? = null,
    val screenCoordinate: ScreenCoordinate? = null,
    val locationSource: LocationSource = LocationSource.UNKNOWN,

    // ── 动作参数 ──
    val inputText: String = "",
    val swipeDirection: SwipeDirection? = null,
    val waitDurationMs: Long = 0,
    val assertText: String = "",

    // ── IR 扩展（断言/变量/控制流/技能）──
    val extras: StepExtras = StepExtras(),

    // ── 元数据 ──
    val preWaitMs: Long = 500,
    val maxRetries: Int = 3,
    val optional: Boolean = false
)

/**
 * 步骤扩展字段 — 存 Room extras_json，并透传到 PC mobile_spec。
 */
@Serializable
data class StepExtras(
    /** contains | equals | visible | not_visible */
    val assertType: String = "contains",
    /** EXTRACT_TEXT / SCAN_QR 等写入变量名 */
    val saveAs: String = "",
    /** PRESS_KEY：如 BACK / HOME / ENTER 或 keycode 数字 */
    val keyCode: String = "",
    /** REPEAT / WHILE 最大次数 */
    val repeatMax: Int = 0,
    /** WAIT_UNTIL / SCROLL_UNTIL / WHILE 期望文本 */
    val untilAssertText: String = "",
    /** SOLVE_CAPTCHA 提示 */
    val captchaHint: String = "",
    /** human_gate | fail */
    val captchaFallback: String = "human_gate",
    /** ROI [left, top, right, bottom]，扫码/验证码裁剪 */
    val roi: List<Int>? = null,
    /** SCROLL 幅度像素，0=默认 */
    val scrollAmount: Int = 0,
    /**
     * 勾选/开关意图：优先命中 checkable 控件，避免点到协议链接文案。
     * 由 PC normalize 或描述启发式写入。
     */
    val preferCheckable: Boolean = false
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
    SCREENSHOT,
    EXTRACT_TEXT,
    WAIT_UNTIL,
    CLOSE_APP,
    PRESS_KEY,
    SCROLL,
    REPEAT,
    WHILE,
    SCAN_QR,
    SCROLL_UNTIL,
    SOLVE_CAPTCHA,
    HUMAN_GATE;

    fun toWire(): String = name.lowercase()

    companion object {
        fun fromWire(raw: String?): ActionType {
            val a = (raw ?: "tap").trim().lowercase()
            return when (a) {
                "tap", "click" -> TAP
                "check", "uncheck", "toggle_check", "check_box", "checkbox" -> TAP
                "long_press", "longpress" -> LONG_PRESS
                "swipe" -> SWIPE
                "input", "input_text", "type" -> INPUT
                "assert", "verify", "assert_text", "assert_element" -> ASSERT
                "wait", "sleep", "delay" -> WAIT
                "open_app", "launch_app", "start_app", "launch", "startapp",
                "am_start", "start_activity" -> OPEN_APP
                "back", "press_back" -> BACK
                "home", "press_home", "goto_home" -> HOME
                "screenshot" -> SCREENSHOT
                "extract_text" -> EXTRACT_TEXT
                "wait_until" -> WAIT_UNTIL
                "close_app" -> CLOSE_APP
                "press_key", "key_event", "keycode" -> PRESS_KEY
                "scroll" -> SCROLL
                "repeat" -> REPEAT
                "while" -> WHILE
                "scan_qr" -> SCAN_QR
                "scroll_until" -> SCROLL_UNTIL
                "solve_captcha" -> SOLVE_CAPTCHA
                "human_gate" -> HUMAN_GATE
                "find_and_tap", "tap_text", "click_text" -> TAP
                else -> try {
                    valueOf(a.uppercase())
                } catch (_: Exception) {
                    TAP
                }
            }
        }
    }
}

@Serializable
enum class SwipeDirection { UP, DOWN, LEFT, RIGHT }

@Serializable
enum class LocationSource {
    UNKNOWN,
    SELECTOR,
    COORDINATE,
    VISUAL
}

@Serializable
data class Locator(
    val text: String = "",
    val textRegex: String = "",
    val contentDesc: String = "",
    val resourceId: String = "",
    val className: String = "",
    val classIndex: Int = 0,
    val xpath: String = "",
    val packageName: String = "",
    val isWebView: Boolean = false
) {
    val isEmpty: Boolean get() = text.isEmpty() && contentDesc.isEmpty()
            && resourceId.isEmpty() && xpath.isEmpty() && className.isEmpty()
}

@Serializable
data class NodeInfo(
    val className: String = "",
    val text: String = "",
    val contentDescription: String = "",
    val resourceId: String = "",
    val packageName: String = "",
    val bounds: ScreenRect = ScreenRect(),
    val isClickable: Boolean = false,
    val isEditable: Boolean = false,
    val isChecked: Boolean = false,
    val windowId: Int = -1,
    val childCount: Int = 0,
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
