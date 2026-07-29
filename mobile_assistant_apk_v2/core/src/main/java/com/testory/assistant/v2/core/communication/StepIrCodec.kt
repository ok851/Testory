package com.testory.assistant.v2.core.communication

import com.testory.assistant.v2.core.model.ActionType
import com.testory.assistant.v2.core.model.Locator
import com.testory.assistant.v2.core.model.LocationSource
import com.testory.assistant.v2.core.model.ScreenCoordinate
import com.testory.assistant.v2.core.model.Step
import com.testory.assistant.v2.core.model.StepExtras
import com.testory.assistant.v2.core.model.SwipeDirection
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.longOrNull

/**
 * Unified Step IR ↔ PC sync JSON / mobile_spec 映射。
 */
object StepIrCodec {

    fun buildMobileSpec(step: Step, viewportCoord: List<Int>?, bounds: List<Int>?): MobileSpecDto {
        val ex = step.extras
        return MobileSpecDto(
            viewportCoord = viewportCoord,
            bounds = bounds,
            resourceId = step.locator.resourceId.takeIf { it.isNotBlank() },
            packageName = step.locator.packageName.takeIf { it.isNotBlank() }
                ?: step.targetNode?.packageName?.takeIf { it.isNotBlank() },
            contentDesc = step.locator.contentDesc.takeIf { it.isNotBlank() },
            className = step.locator.className.takeIf { it.isNotBlank() },
            text = step.locator.text.takeIf { it.isNotBlank() },
            locationSource = step.locationSource.name.lowercase(),
            isWebView = step.locator.isWebView,
            assertText = step.assertText.takeIf { it.isNotBlank() },
            waitDurationMs = step.waitDurationMs.takeIf { it > 0 },
            preWaitMs = step.preWaitMs.takeIf { it != 500L },
            maxRetries = step.maxRetries.takeIf { it != 3 },
            optional = step.optional.takeIf { it },
            assertType = ex.assertType.takeIf { it.isNotBlank() && it != "contains" },
            saveAs = ex.saveAs.takeIf { it.isNotBlank() },
            keyCode = ex.keyCode.takeIf { it.isNotBlank() },
            repeatMax = ex.repeatMax.takeIf { it > 0 },
            untilAssertText = ex.untilAssertText.takeIf { it.isNotBlank() },
            captchaHint = ex.captchaHint.takeIf { it.isNotBlank() },
            captchaFallback = ex.captchaFallback.takeIf { it.isNotBlank() && it != "human_gate" },
            roi = ex.roi,
            scrollAmount = ex.scrollAmount.takeIf { it > 0 },
            swipeDirection = step.swipeDirection?.name?.lowercase()
        )
    }

    fun parseFromSyncJson(
        s: JsonObject,
        caseId: String,
        fallbackIndex: Int
    ): Step {
        val actionType = ActionType.fromWire(s.str("action"))
        val selType = s.str("selector_type").lowercase()
        val selValue = s.str("selector_value")
        val ms = s["mobile_spec"]?.let { parseMobileSpecElement(it) }

        val locator = Locator(
            text = when {
                selType == "text" -> selValue
                !ms?.text.isNullOrBlank() -> ms!!.text!!
                else -> ""
            },
            contentDesc = when {
                selType == "content_desc" -> selValue
                !ms?.contentDesc.isNullOrBlank() -> ms!!.contentDesc!!
                else -> ""
            },
            resourceId = when {
                selType == "resource_id" -> selValue
                !ms?.resourceId.isNullOrBlank() -> ms!!.resourceId!!
                else -> ""
            },
            className = when {
                selType == "class_name" -> selValue
                !ms?.className.isNullOrBlank() -> ms!!.className!!
                else -> ""
            },
            xpath = if (selType == "xpath") selValue else "",
            packageName = ms?.packageName.orEmpty(),
            isWebView = ms?.isWebView == true
        )

        val coord = when {
            selType == "coordinate" && selValue.contains(",") -> {
                val parts = selValue.split(",")
                val x = parts.getOrNull(0)?.trim()?.toIntOrNull() ?: 0
                val y = parts.getOrNull(1)?.trim()?.toIntOrNull() ?: 0
                if (x > 0 || y > 0) ScreenCoordinate(x, y) else null
            }
            ms?.viewportCoord != null && ms.viewportCoord.size >= 2 ->
                ScreenCoordinate(ms.viewportCoord[0], ms.viewportCoord[1])
            else -> null
        }

        val assertText = s.str("assert_text").ifBlank {
            ms?.assertText.orEmpty()
        }.ifBlank {
            // 兼容：部分 PC 步骤把期望写在 input_value
            if (actionType == ActionType.ASSERT) s.str("input_value") else ""
        }

        val waitMs = s.long("wait_duration_ms").takeIf { it > 0 }
            ?: ms?.waitDurationMs
            ?: 0L
        val preWait = s.long("pre_wait_ms").takeIf { it > 0 }
            ?: ms?.preWaitMs
            ?: 500L
        val maxRetries = s.int("max_retries").takeIf { it > 0 }
            ?: ms?.maxRetries
            ?: 3
        val optional = s.bool("optional") || (ms?.optional == true)

        val extras = StepExtras(
            assertType = s.str("assert_type").ifBlank { ms?.assertType.orEmpty() }.ifBlank { "contains" },
            saveAs = s.str("save_as").ifBlank { ms?.saveAs.orEmpty() },
            keyCode = s.str("key_code").ifBlank { ms?.keyCode.orEmpty() },
            repeatMax = s.int("repeat_max").takeIf { it > 0 } ?: (ms?.repeatMax ?: 0),
            untilAssertText = s.str("until_assert_text").ifBlank { ms?.untilAssertText.orEmpty() },
            captchaHint = s.str("captcha_hint").ifBlank { ms?.captchaHint.orEmpty() },
            captchaFallback = s.str("captcha_fallback").ifBlank {
                ms?.captchaFallback.orEmpty()
            }.ifBlank { "human_gate" },
            roi = ms?.roi,
            scrollAmount = ms?.scrollAmount ?: 0
        )

        val swipeDir = (ms?.swipeDirection ?: s.str("swipe_direction")).uppercase().let {
            try {
                if (it.isBlank()) null else SwipeDirection.valueOf(it)
            } catch (_: Exception) {
                null
            }
        }

        return Step(
            id = s.str("id").ifBlank { "${caseId}_s$fallbackIndex" },
            caseId = caseId,
            index = s.int("step_order").takeIf { it > 0 } ?: (fallbackIndex + 1),
            action = actionType,
            description = s.str("description"),
            locator = locator,
            screenCoordinate = coord,
            locationSource = when {
                !selValue.isBlank() && selType != "coordinate" -> LocationSource.SELECTOR
                coord != null -> LocationSource.COORDINATE
                else -> LocationSource.UNKNOWN
            },
            inputText = s.str("input_value"),
            swipeDirection = swipeDir,
            waitDurationMs = waitMs,
            assertText = assertText,
            extras = extras,
            preWaitMs = preWait,
            maxRetries = maxRetries,
            optional = optional
        )
    }

    private fun parseMobileSpecElement(el: JsonElement): MobileSpecDto? {
        return try {
            when (el) {
                is JsonObject -> {
                    MobileSpecDto(
                        viewportCoord = el["viewport_coord"]?.jsonArray?.mapNotNull {
                            it.jsonPrimitive.intOrNull
                        },
                        bounds = el["bounds"]?.jsonArray?.mapNotNull { it.jsonPrimitive.intOrNull },
                        resourceId = el.str("resource_id").ifBlank { null },
                        packageName = el.str("package").ifBlank { el.str("package_name") }.ifBlank { null },
                        contentDesc = el.str("content_desc").ifBlank { null },
                        className = el.str("class_name").ifBlank { null },
                        text = el.str("text").ifBlank { null },
                        locationSource = el.str("location_source").ifBlank { null },
                        isWebView = el.bool("is_webview"),
                        assertText = el.str("assert_text").ifBlank { null },
                        waitDurationMs = el.long("wait_duration_ms").takeIf { it > 0 },
                        preWaitMs = el.long("pre_wait_ms").takeIf { it > 0 },
                        maxRetries = el.int("max_retries").takeIf { it > 0 },
                        optional = el["optional"]?.jsonPrimitive?.booleanOrNull,
                        assertType = el.str("assert_type").ifBlank { null },
                        saveAs = el.str("save_as").ifBlank { null },
                        keyCode = el.str("key_code").ifBlank { null },
                        repeatMax = el.int("repeat_max").takeIf { it > 0 },
                        untilAssertText = el.str("until_assert_text").ifBlank { null },
                        captchaHint = el.str("captcha_hint").ifBlank { null },
                        captchaFallback = el.str("captcha_fallback").ifBlank { null },
                        roi = el["roi"]?.jsonArray?.mapNotNull { it.jsonPrimitive.intOrNull },
                        scrollAmount = el.int("scroll_amount").takeIf { it > 0 },
                        swipeDirection = el.str("swipe_direction").ifBlank { null },
                        swipeFrom = el["swipe_from"]?.jsonArray?.mapNotNull { it.jsonPrimitive.intOrNull },
                        swipeTo = el["swipe_to"]?.jsonArray?.mapNotNull { it.jsonPrimitive.intOrNull }
                    )
                }
                is JsonPrimitive -> {
                    // PC DB 常存 mobile_spec 为 JSON 字符串
                    val raw = el.contentOrNull?.trim().orEmpty()
                    if (raw.isBlank() || (!raw.startsWith("{") && !raw.startsWith("["))) null
                    else {
                        val nested = Json { ignoreUnknownKeys = true }.parseToJsonElement(raw)
                        if (nested is JsonObject) parseMobileSpecElement(nested) else null
                    }
                }
                else -> null
            }
        } catch (_: Exception) {
            null
        }
    }

    private fun JsonObject.str(key: String): String =
        this[key]?.jsonPrimitive?.contentOrNull?.trim('"') ?: ""

    private fun JsonObject.int(key: String): Int =
        this[key]?.jsonPrimitive?.intOrNull
            ?: this[key]?.jsonPrimitive?.contentOrNull?.toIntOrNull()
            ?: 0

    private fun JsonObject.long(key: String): Long =
        this[key]?.jsonPrimitive?.longOrNull
            ?: this[key]?.jsonPrimitive?.contentOrNull?.toLongOrNull()
            ?: 0L

    private fun JsonObject.bool(key: String): Boolean =
        this[key]?.jsonPrimitive?.booleanOrNull == true
}
