package com.testory.assistant.v2.feature.cases

import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.testory.assistant.v2.feature.cases.CaseListScreen
import com.testory.assistant.v2.feature.cases.CaseListUiState
import com.testory.assistant.v2.ui.theme.TestoryTheme
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class CaseListScreenTest {

    @get:Rule
    val composeRule = createComposeRule()

    @Test
    fun `empty state should show placeholder`() {
        composeRule.setContent {
            TestoryTheme {
                CaseListScreen(
                    uiState = CaseListUiState(),
                    onCaseClick = {},
                    onDeleteCase = {},
                    onCreateCase = {},
                    onBack = {}
                )
            }
        }
        composeRule.onNodeWithText("暂无用例").assertIsDisplayed()
    }

    @Test
    fun `cases should be displayed in list`() {
        composeRule.setContent {
            TestoryTheme {
                CaseListScreen(
                    uiState = CaseListUiState(
                        cases = listOf(
                            com.testory.assistant.v2.core.database.entity.CaseEntity(
                                id = "c1", name = "登录测试", description = "验证登录功能"
                            ),
                            com.testory.assistant.v2.core.database.entity.CaseEntity(
                                id = "c2", name = "注册测试", description = "验证注册功能"
                            )
                        )
                    ),
                    onCaseClick = {},
                    onDeleteCase = {},
                    onCreateCase = {},
                    onBack = {}
                )
            }
        }
        composeRule.onNodeWithText("登录测试").assertIsDisplayed()
        composeRule.onNodeWithText("注册测试").assertIsDisplayed()
    }
}
