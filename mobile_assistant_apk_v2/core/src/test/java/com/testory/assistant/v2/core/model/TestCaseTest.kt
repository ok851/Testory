package com.testory.assistant.v2.core.model

import org.junit.Assert.*
import org.junit.Test

class TestCaseTest {

    @Test
    fun `testCase should generate valid ID`() {
        val tc = TestCase(
            name = "登录测试",
            description = "验证用户登录流程"
        )
        assertNotNull(tc.id)
        assertTrue(tc.id.isNotEmpty())
    }

    @Test
    fun `testCase should store metadata correctly`() {
        val tc = TestCase(
            name = "注册测试",
            description = "验证注册流程",
            targetPackage = "com.example.app"
        )
        assertEquals("注册测试", tc.name)
        assertEquals("验证注册流程", tc.description)
        assertEquals("com.example.app", tc.targetPackage)
    }

    @Test
    fun `testCase should generate valid json`() {
        val tc = TestCase(
            id = "test-id-001",
            name = "JSON 测试",
            steps = listOf(
                Step(id = "s1", action = StepAction.TAP, description = "步骤1",
                    screenCoordinate = ScreenCoordinate(100, 200)),
                Step(id = "s2", action = StepAction.INPUT, description = "步骤2",
                    inputValue = "hello", node = Locator(LocatorType.TEXT, "输入框"))
            )
        )

        val json = tc.toJson()
        assertTrue(json.contains("test-id-001"))
        assertTrue(json.contains("JSON 测试"))
        assertTrue(json.contains("s1"))
        assertTrue(json.contains("s2"))
    }

    @Test
    fun `empty testCase should have no steps`() {
        val tc = TestCase(name = "空用例")
        assertTrue(tc.steps.isEmpty())
        assertNotNull(tc.createdAt)
        assertNotNull(tc.updatedAt)
    }

    @Test
    fun `testCase timestamp should be consistent`() {
        val now = System.currentTimeMillis()
        val tc = TestCase(
            name = "时间戳测试",
            createdAt = now,
            updatedAt = now
        )
        assertEquals(now, tc.createdAt)
        assertEquals(now, tc.updatedAt)
    }
}
