package com.testory.assistant.v2.feature.home

import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.testory.assistant.v2.feature.home.HomeScreen
import com.testory.assistant.v2.feature.home.HomeUiState
import com.testory.assistant.v2.feature.home.ConnectionStatus
import com.testory.assistant.v2.ui.theme.TestoryTheme
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * Compose UI tests for HomeScreen.
 */
@RunWith(AndroidJUnit4::class)
class HomeScreenTest {

    @get:Rule
    val composeRule = createComposeRule()

    @Test
    fun `home screen should display main action cards`() {
        composeRule.setContent {
            TestoryTheme {
                HomeScreenContent(
                    uiState = HomeUiState(),
                    onStartRecording = {},
                    onStartAI = {},
                    onStartReplay = {},
                    onViewCases = {},
                    onOpenSettings = {},
                    onRefreshConnection = {}
                )
            }
        }
        composeRule.onNodeWithText("录制测试").assertIsDisplayed()
        composeRule.onNodeWithText("AI创建测试").assertIsDisplayed()
        composeRule.onNodeWithText("运行测试").assertIsDisplayed()
        composeRule.onNodeWithText("用例管理").assertIsDisplayed()
    }

    @Test
    fun `home screen should show connection status`() {
        composeRule.setContent {
            TestoryTheme {
                HomeScreenContent(
                    uiState = HomeUiState(
                        connectionStatus = ConnectionStatus.CONNECTED,
                        deviceName = "Pixel 6"
                    ),
                    onStartRecording = {},
                    onStartAI = {},
                    onStartReplay = {},
                    onViewCases = {},
                    onOpenSettings = {},
                    onRefreshConnection = {}
                )
            }
        }
        composeRule.onNodeWithText("已连接").assertIsDisplayed()
    }
}
