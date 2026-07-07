package com.testory.assistant.v2.feature.ai_bridge

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.testory.assistant.v2.core.communication.PcSyncClient
import com.testory.assistant.v2.core.model.*
import com.testory.assistant.v2.core.repository.CaseRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.*
import kotlinx.coroutines.launch
import java.util.UUID
import javax.inject.Inject

/**
 * AI 桥接 ViewModel — 收集用户意图 → 发送 PC 推理 → 接收步骤 → 展示预览。
 */
@HiltViewModel
class AIBridgeViewModel @Inject constructor(
    private val caseRepository: CaseRepository,
    private val pcSyncClient: PcSyncClient
) : ViewModel() {

    private val _uiState = MutableStateFlow(AIBridgeUiState())
    val uiState: StateFlow<AIBridgeUiState> = _uiState.asStateFlow()

    init {
        viewModelScope.launch {
            pcSyncClient.state.collect { state ->
                _uiState.update { it.copy(pcConnectionState = state) }
            }
        }
    }

    fun sendMessage(text: String) {
        if (text.isBlank()) return

        // Add user message
        _uiState.update {
            it.copy(
                messages = it.messages + ChatMessage.User(text),
                isGenerating = true
            )
        }

        viewModelScope.launch {
            // Check PC connection
            if (_uiState.value.pcConnectionState != PcConnectionState.CONNECTED) {
                _uiState.update {
                    it.copy(
                        messages = it.messages + ChatMessage.System(
                            "无法连接到 PC 端，请确保 PC 端 Testory 已启动"
                        ),
                        isGenerating = false
                    )
                }
                return@launch
            }

            try {
                // TODO: In Phase 3, implement gRPC AIBridgeService protocol
                // For now, use HTTP fallback to PC agent endpoint
                val deviceInfo = pcSyncClient.getDeviceInfo()

                // Simulate AI response with template steps
                // In production, this makes gRPC call to PC Ollama
                delay(1500) // Simulate processing time

                val steps = generateDemoSteps(text)

                _uiState.update {
                    it.copy(
                        messages = it.messages + ChatMessage.Ai(
                            text = "好的！我建议以下测试步骤：",
                            steps = steps
                        ),
                        isGenerating = false
                    )
                }
            } catch (e: Exception) {
                _uiState.update {
                    it.copy(
                        messages = it.messages + ChatMessage.System(
                            "AI 推理出错: ${e.message}"
                        ),
                        isGenerating = false
                    )
                }
            }
        }
    }

    fun saveGeneratedSteps(steps: List<Step>) {
        viewModelScope.launch {
            val testCase = TestCase(
                id = UUID.randomUUID().toString(),
                name = "AI生成_${System.currentTimeMillis()}",
                description = "由 AI 对话生成",
                steps = steps.mapIndexed { index, step ->
                    step.copy(index = index + 1)
                },
                source = CaseSource.AI_GENERATED
            )
            caseRepository.saveCase(testCase)
            _uiState.update {
                it.copy(
                    messages = it.messages + ChatMessage.System("✅ 用例已保存")
                )
            }
        }
    }

    fun runGeneratedSteps(steps: List<Step>) {
        // Navigate to replay screen (handled by navigation)
        // For now, show message
        _uiState.update {
            it.copy(
                messages = it.messages + ChatMessage.System("▶️ 运行功能将在回放模块中实现")
            )
        }
    }

    /**
     * 生成演示步骤 — Phase 1 占位符，Phase 3 替换为真实 gRPC AI 调用。
     */
    private fun generateDemoSteps(userIntent: String): List<Step> {
        val normalized = userIntent.lowercase()

        return when {
            normalized.contains("登录") || normalized.contains("login") -> listOf(
                Step(action = ActionType.TAP, description = "打开应用"),
                Step(action = ActionType.TAP, description = "点击\"登录\"按钮"),
                Step(action = ActionType.INPUT, description = "输入用户名", inputText = "test_user"),
                Step(action = ActionType.INPUT, description = "输入密码", inputText = "******"),
                Step(action = ActionType.TAP, description = "点击\"确认登录\""),
                Step(action = ActionType.ASSERT, description = "验证显示\"欢迎\"", assertText = "欢迎")
            )
            normalized.contains("注册") || normalized.contains("register") -> listOf(
                Step(action = ActionType.TAP, description = "打开应用"),
                Step(action = ActionType.TAP, description = "点击\"注册\""),
                Step(action = ActionType.INPUT, description = "输入手机号", inputText = "13800138000"),
                Step(action = ActionType.INPUT, description = "输入验证码", inputText = "123456"),
                Step(action = ActionType.TAP, description = "点击\"下一步\""),
                Step(action = ActionType.INPUT, description = "设置密码", inputText = "******"),
                Step(action = ActionType.TAP, description = "点击\"完成注册\""),
                Step(action = ActionType.ASSERT, description = "验证注册成功")
            )
            normalized.contains("滚动") || normalized.contains("列表") || normalized.contains("scroll") -> listOf(
                Step(action = ActionType.OPEN_APP, description = "打开目标应用"),
                Step(action = ActionType.WAIT, description = "等待页面加载", waitDurationMs = 2000),
                Step(action = ActionType.SWIPE, description = "向下滑动列表", swipeDirection = SwipeDirection.UP),
                Step(action = ActionType.WAIT, description = "等待内容加载", waitDurationMs = 1000),
                Step(action = ActionType.SWIPE, description = "继续滑动", swipeDirection = SwipeDirection.UP),
                Step(action = ActionType.ASSERT, description = "验证新内容已加载")
            )
            else -> listOf(
                Step(action = ActionType.OPEN_APP, description = "打开目标应用"),
                Step(action = ActionType.WAIT, description = "等待页面加载", waitDurationMs = 2000),
                Step(action = ActionType.TAP, description = "点击主要按钮"),
                Step(action = ActionType.ASSERT, description = "验证页面内容正确")
            )
        }
    }
}
