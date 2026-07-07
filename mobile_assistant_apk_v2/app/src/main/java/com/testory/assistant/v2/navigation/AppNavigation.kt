package com.testory.assistant.v2.navigation

import androidx.compose.runtime.Composable
import androidx.navigation.NavHostController
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import com.testory.assistant.v2.feature.ai_bridge.AIBridgeScreen
import com.testory.assistant.v2.feature.cases.CaseDetailScreen
import com.testory.assistant.v2.feature.cases.CaseListScreen
import com.testory.assistant.v2.feature.home.HomeScreen
import com.testory.assistant.v2.feature.onboarding.OnboardingScreen
import com.testory.assistant.v2.feature.recorder.RecorderScreen
import com.testory.assistant.v2.feature.replay.ReplayScreen
import com.testory.assistant.v2.feature.settings.SettingsScreen

/**
 * 导航路由定义。
 */
object NavRoutes {
    const val HOME = "home"
    const val RECORDER = "recorder"
    const val AI_BRIDGE = "ai_bridge"
    const val CASES = "cases"
    const val CASE_DETAIL = "case_detail/{caseId}"
    const val REPLAY = "replay/{caseId}"
    const val SETTINGS = "settings"
    const val ONBOARDING = "onboarding"

    fun caseDetail(caseId: String) = "case_detail/$caseId"
    fun replay(caseId: String) = "replay/$caseId"
}

@Composable
fun AppNavigation(
    navController: NavHostController = rememberNavController()
) {
    NavHost(
        navController = navController,
        startDestination = NavRoutes.HOME
    ) {
        // 首页
        composable(NavRoutes.HOME) {
            HomeScreen(
                onNavigateToRecorder = {
                    navController.navigate(NavRoutes.RECORDER)
                },
                onNavigateToAIBridge = {
                    navController.navigate(NavRoutes.AI_BRIDGE)
                },
                onNavigateToCases = {
                    navController.navigate(NavRoutes.CASES)
                },
                onNavigateToSettings = {
                    navController.navigate(NavRoutes.SETTINGS)
                }
            )
        }

        // 首次使用引导
        composable(NavRoutes.ONBOARDING) {
            OnboardingScreen(
                onComplete = {
                    navController.popBackStack(NavRoutes.ONBOARDING, inclusive = true)
                    navController.navigate(NavRoutes.HOME)
                }
            )
        }

        // 录制中心
        composable(NavRoutes.RECORDER) {
            RecorderScreen(
                onBack = { navController.popBackStack() }
            )
        }

        // AI 桥接对话面板
        composable(NavRoutes.AI_BRIDGE) {
            AIBridgeScreen(
                onBack = { navController.popBackStack() }
            )
        }

        // 用例管理
        composable(NavRoutes.CASES) {
            CaseListScreen(
                onCaseClick = { caseId ->
                    navController.navigate(NavRoutes.caseDetail(caseId))
                },
                onBack = { navController.popBackStack() }
            )
        }

        // 用例详情
        composable(NavRoutes.CASE_DETAIL) { backStackEntry ->
            val caseId = backStackEntry.arguments?.getString("caseId") ?: return@composable
            CaseDetailScreen(
                caseId = caseId,
                onBack = { navController.popBackStack() },
                onStartReplay = { id ->
                    navController.navigate(NavRoutes.replay(id))
                }
            )
        }

        // 回放执行
        composable(NavRoutes.REPLAY) { backStackEntry ->
            val caseId = backStackEntry.arguments?.getString("caseId") ?: return@composable
            ReplayScreen(
                caseId = caseId,
                onBack = { navController.popBackStack() }
            )
        }

        // 设置
        composable(NavRoutes.SETTINGS) {
            SettingsScreen(
                onBack = { navController.popBackStack() }
            )
        }
    }
}
