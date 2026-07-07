package com.testory.assistant.v2.core.util

import java.time.Instant
import java.time.LocalDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter

/**
 * Time formatting utilities.
 */
object TimeUtils {

    private val timeFormatter = DateTimeFormatter.ofPattern("HH:mm")
    private val dateFormatter = DateTimeFormatter.ofPattern("MM-dd HH:mm")
    private val fullFormatter = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss")

    fun formatTime(epochMillis: Long): String {
        val ldt = LocalDateTime.ofInstant(Instant.ofEpochMilli(epochMillis), ZoneId.systemDefault())
        return ldt.format(timeFormatter)
    }

    fun formatDate(epochMillis: Long): String {
        val ldt = LocalDateTime.ofInstant(Instant.ofEpochMilli(epochMillis), ZoneId.systemDefault())
        val now = LocalDateTime.now()
        return when {
            ldt.toLocalDate() == now.toLocalDate() -> ldt.format(timeFormatter)
            ldt.year == now.year -> ldt.format(dateFormatter)
            else -> ldt.format(fullFormatter)
        }
    }

    fun formatDuration(seconds: Int): String {
        val m = seconds / 60
        val s = seconds % 60
        return "%d:%02d".format(m, s)
    }

    fun relativeTime(epochMillis: Long): String {
        val diff = System.currentTimeMillis() - epochMillis
        return when {
            diff < 60_000 -> "刚刚"
            diff < 3_600_000 -> "${diff / 60_000}分钟前"
            diff < 86_400_000 -> "${diff / 3_600_000}小时前"
            diff < 604_800_000 -> "${diff / 86_400_000}天前"
            else -> formatDate(epochMillis)
        }
    }
}
