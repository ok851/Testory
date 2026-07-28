package com.testory.assistant.v2.service.accessibility

import java.util.concurrent.CopyOnWriteArrayList
import java.util.regex.Pattern

/**
 * 最近通知文本缓冲 — 供跨端 mobile_extract_otp 本机取码。
 * 无障碍 TYPE_NOTIFICATION_STATE_CHANGED 写入；取码时按「验证码」关键词解析。
 */
object NotificationTextBuffer {
    private const val MAX = 40
    private val lines = CopyOnWriteArrayList<String>()

    private val otpNearKeyword: Pattern = Pattern.compile(
        "(?:验证码|校验码|动态码|code|Code)[^\\d]{0,12}(\\d{4,8})" +
            "|(\\d{4,8})[^\\d]{0,8}(?:为您的验证码|是您的验证码)",
        Pattern.CASE_INSENSITIVE
    )
    private val digitFallback: Pattern = Pattern.compile("(?<!\\d)(\\d{4,8})(?!\\d)")

    fun push(text: String, packageName: String = "") {
        val t = text.trim()
        if (t.isEmpty()) return
        val line = if (packageName.isNotBlank()) "[$packageName] $t" else t
        lines.add(0, line)
        while (lines.size > MAX) {
            lines.removeAt(lines.size - 1)
        }
    }

    fun snapshot(): List<String> = lines.toList()

    fun extractOtp(pattern: String = "", senderHint: String = ""): Pair<String?, String?> {
        val hint = senderHint.trim()
        val candidates = if (hint.isNotBlank()) {
            lines.filter { it.contains(hint, ignoreCase = true) }.ifEmpty { lines }
        } else {
            lines
        }
        val custom = pattern.trim().takeIf { it.isNotEmpty() }?.let {
            try {
                Pattern.compile(it)
            } catch (_: Exception) {
                null
            }
        }
        for (line in candidates) {
            if (custom != null) {
                val m = custom.matcher(line)
                if (m.find()) {
                    val g = if (m.groupCount() >= 1) m.group(1) else m.group()
                    if (!g.isNullOrBlank()) return g to line
                }
            }
            val m1 = otpNearKeyword.matcher(line)
            if (m1.find()) {
                val g = sequenceOf(m1.group(1), m1.group(2)).firstOrNull { !it.isNullOrBlank() }
                if (!g.isNullOrBlank()) return g to line
            }
            if (line.contains("验证码") || line.contains("校验码") ||
                line.contains("动态码") || line.contains("OTP", ignoreCase = true)
            ) {
                val m2 = digitFallback.matcher(line)
                if (m2.find()) return m2.group(1) to line
            }
        }
        return null to null
    }
}
