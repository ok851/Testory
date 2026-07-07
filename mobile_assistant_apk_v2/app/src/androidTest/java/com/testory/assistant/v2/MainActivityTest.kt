package com.testory.assistant.v2

import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.testory.assistant.v2.ui.theme.TestoryTheme
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * Smoke tests for the main app composable tree.
 * Verifies that basic components render without crashes.
 */
@RunWith(AndroidJUnit4::class)
class MainActivityTest {

    @get:Rule
    val composeRule = createComposeRule()

    @Test
    fun `app theme should render content`() {
        composeRule.setContent {
            TestoryTheme {
                // Minimal render test
                androidx.compose.material3.Text("Testory")
            }
        }
        composeRule.onNodeWithText("Testory").assertIsDisplayed()
    }
}
