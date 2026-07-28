package com.testory.assistant.v2.feature.home

import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.testory.assistant.v2.core.model.PcConnectionState
import com.testory.assistant.v2.ui.theme.TestoryTheme
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class HomeScreenTest {

    @get:Rule
    val composeRule = createComposeRule()

    @Test
    fun `home screen should display main actions`() {
        composeRule.setContent {
            TestoryTheme {
                HomeScreenContent(
                    uiState = HomeUiState(),
                    onStartRecording = {},
                    onStartAI = {},
                    onViewCases = {},
                    onOpenSettings = {}
                )
            }
        }
        composeRule.onNodeWithText("开始录制").assertIsDisplayed()
        composeRule.onNodeWithText("用例").assertIsDisplayed()
        composeRule.onNodeWithText("AI 助手").assertIsDisplayed()
        composeRule.onNodeWithText("就绪清单").assertIsDisplayed()
    }

    @Test
    fun `home screen should show paired status`() {
        composeRule.setContent {
            TestoryTheme {
                HomeScreenContent(
                    uiState = HomeUiState(
                        pcConnectionState = PcConnectionState.CONNECTED,
                        isAccessibilityEnabled = true,
                        canDrawOverlays = true
                    ),
                    onStartRecording = {},
                    onStartAI = {},
                    onViewCases = {},
                    onOpenSettings = {}
                )
            }
        }
        composeRule.onNodeWithText("就绪清单").assertIsDisplayed()
        composeRule.onNodeWithText("无障碍服务").assertIsDisplayed()
    }
}
