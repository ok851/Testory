package com.testory.assistant.v2.core.model

import org.junit.Assert.*
import org.junit.Test

class StepTest {

    @Test
    fun `step with valid coordinates should return isValid true`() {
        val step = Step(
            id = "1",
            action = StepAction.TAP,
            description = "点击登录按钮",
            screenCoordinate = ScreenCoordinate(100, 200)
        )
        assertTrue(step.isValid())
        assertEquals(100, step.screenCoordinate!!.xc)
        assertEquals(200, step.screenCoordinate!!.yc)
    }

    @Test
    fun `step with zero coordinates should return isValid false`() {
        val step = Step(
            id = "2",
            action = StepAction.TAP,
            description = "点击 - 坐标无效",
            screenCoordinate = ScreenCoordinate(0, 0)
        )
        assertFalse(step.isValid())
    }

    @Test
    fun `step with negative x should return isValid false`() {
        val step = Step(
            id = "3",
            action = StepAction.TAP,
            description = "坐标x为负",
            screenCoordinate = ScreenCoordinate(-1, 200)
        )
        assertFalse(step.isValid())
    }

    @Test
    fun `step with negative y should return isValid false`() {
        val step = Step(
            id = "4",
            action = StepAction.TAP,
            description = "坐标y为负",
            screenCoordinate = ScreenCoordinate(100, -1)
        )
        assertFalse(step.isValid())
    }

    @Test
    fun `step without coordinates but with locator should be valid`() {
        val step = Step(
            id = "5",
            action = StepAction.TAP,
            description = "通过文本定位",
            node = Locator(type = LocatorType.TEXT, value = "登录")
        )
        assertTrue(step.isValid())
    }

    @Test
    fun `step with coordLocationSource equals DIRECT should use screenCoordinate`() {
        val step = Step(
            id = "6",
            action = StepAction.TAP,
            screenCoordinate = ScreenCoordinate(50, 100),
            coordLocationSource = LocationSource.DIRECT
        )
        assertEquals(LocationSource.DIRECT, step.coordLocationSource)
        assertTrue(step.isValid())
    }

    @Test
    fun `step with coordLocationSource equals VIEWPORT should use viewport_coord`() {
        val step = Step(
            id = "7",
            action = StepAction.TAP,
            viewportCoordinate = ScreenCoordinate(50, 100),
            coordLocationSource = LocationSource.VIEWPORT
        )
        assertEquals(LocationSource.VIEWPORT, step.coordLocationSource)
        assertTrue(step.isValid())
    }

    @Test
    fun `input action should require inputValue`() {
        val step = Step(
            id = "8",
            action = StepAction.INPUT,
            description = "输入用户名",
            inputValue = "testuser"
        )
        assertNotNull(step.inputValue)
        assertEquals("testuser", step.inputValue)
    }

    @Test
    fun `swipe action should include direction`() {
        val step = Step(
            id = "9",
            action = StepAction.SWIPE,
            description = "向下滑动",
            swipeDirection = SwipeDirection.DOWN
        )
        assertEquals(SwipeDirection.DOWN, step.swipeDirection)
    }

    @Test
    fun `step should serialize and deserialize correctly`() {
        val step = Step(
            id = "10",
            action = StepAction.TAP,
            description = "测试序列化",
            screenCoordinate = ScreenCoordinate(300, 400),
            node = Locator(type = LocatorType.ID, value = "btn_login"),
            coordLocationSource = LocationSource.DIRECT
        )

        val json = step.toJson()
        val restored = Step.fromJson(json)

        assertEquals(step.id, restored.id)
        assertEquals(step.action, restored.action)
        assertEquals(step.screenCoordinate?.xc, restored.screenCoordinate?.xc)
        assertEquals(step.screenCoordinate?.yc, restored.screenCoordinate?.yc)
        assertEquals(step.node?.type, restored.node?.type)
        assertEquals(step.node?.value, restored.node?.value)
    }

    @Test
    fun `ScreenCoordinate isValid should validate coordinates`() {
        assertTrue(ScreenCoordinate(1, 1).isValid)
        assertTrue(ScreenCoordinate(100, 200).isValid)
        assertFalse(ScreenCoordinate(0, 0).isValid)
        assertFalse(ScreenCoordinate(-1, 0).isValid)
        assertFalse(ScreenCoordinate(0, -1).isValid)
        assertFalse(ScreenCoordinate(-1, -1).isValid)
    }
}
