package com.testory.assistant.v2.core.model

import org.junit.Assert.assertEquals
import org.junit.Test

class StepVariablesTest {
    @Test
    fun substituteReplacesKnownVars() {
        val out = StepVariables.substitute("hi {{name}} / {{x}}", mapOf("name" to "Ada", "x" to "1"))
        assertEquals("hi Ada / 1", out)
    }

    @Test
    fun applyToStepReplacesLocatorAndAssert() {
        val step = Step(
            action = ActionType.ASSERT,
            assertText = "{{msg}}",
            locator = Locator(text = "{{btn}}"),
            inputText = "{{phone}}"
        )
        val applied = StepVariables.applyToStep(
            step,
            mapOf("msg" to "ok", "btn" to "登录", "phone" to "138")
        )
        assertEquals("ok", applied.assertText)
        assertEquals("登录", applied.locator.text)
        assertEquals("138", applied.inputText)
    }
}
