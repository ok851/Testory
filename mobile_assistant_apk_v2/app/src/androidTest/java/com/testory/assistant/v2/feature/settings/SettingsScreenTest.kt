package com.testory.assistant.v2.feature.settings

import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.testory.assistant.v2.feature.settings.SettingsScreen
import com.testory.assistant.v2.feature.settings.SettingsUiState
import com.testory.assistant.v2.feature.settings.ConnectionStatus
import com.testory.assistant.v2.feature.settings.RecordingQuality
import com.testory.assistant.v2.ui.theme.TestoryTheme
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class SettingsScreenTest {

    @get:Rule
    val composeRule = createComposeRule()

    @Test
    fun `settings should display version`() {
        composeRule.setContent {
            TestoryTheme {
                SettingsScreen(
                    uiState = SettingsUiState(),
                    onBack = {},
                    onRefreshPermissions = {},
                    onPcAddressChange = {},
                    onPcPortChange = {},
                    onRecordingQualityChange = {},
                    onToggleSound = {},
                    onToggleVibration = {},
                    onToggleOfflineMode = {},
                    onOpenAccessibility = {}
                )
            }
        }
        composeRule.onNodeWithText("设置").assertIsDisplayed()
        composeRule.onNodeWithText("2.0.0").assertIsDisplayed()
    }
}
