package com.testory.assistant.v2.core.util

import org.junit.Assert.*
import org.junit.Test

class TimeUtilsTest {

    @Test
    fun `formatDuration should format seconds correctly`() {
        assertEquals("0:00", TimeUtils.formatDuration(0))
        assertEquals("0:30", TimeUtils.formatDuration(30))
        assertEquals("1:00", TimeUtils.formatDuration(60))
        assertEquals("2:30", TimeUtils.formatDuration(150))
        assertEquals("10:05", TimeUtils.formatDuration(605))
    }

    @Test
    fun `relativeTime should return correct strings`() {
        val now = System.currentTimeMillis()

        assertEquals("刚刚", TimeUtils.relativeTime(now))
        assertEquals("1分钟前", TimeUtils.relativeTime(now - 60_000))
        assertEquals("5分钟前", TimeUtils.relativeTime(now - 5 * 60_000))
        assertEquals("1小时前", TimeUtils.relativeTime(now - 3_600_000))
        assertEquals("3小时前", TimeUtils.relativeTime(now - 3 * 3_600_000))
    }

    @Test
    fun `formatDate should not crash`() {
        val now = System.currentTimeMillis()
        val result = TimeUtils.formatDate(now)
        assertNotNull(result)
        assertTrue(result.isNotEmpty())
    }

    @Test
    fun `formatTime should not crash`() {
        val now = System.currentTimeMillis()
        val result = TimeUtils.formatTime(now)
        assertNotNull(result)
        assertTrue(result.isNotEmpty())
    }
}
