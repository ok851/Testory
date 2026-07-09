package com.testory.assistant.v2.feature.ai_bridge

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.testory.assistant.v2.core.communication.PcSyncClient
import com.testory.assistant.v2.core.model.*
import com.testory.assistant.v2.core.repository.CaseRepository
import dagger.hilt.android.lifecycle.HiltViewModel
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
                val result = pcSyncClient.aiGenerateSteps(text)

                if (result.success && result.steps.isNotEmpty()) {
                    val aiText = buildString {
                        appendLine("**${result.caseName}**")
                        if (result.description.isNotEmpty()) {
                            appendLine()
                            appendLine(result.description)
                        }
                        if (result.expectedResult.isNotEmpty()) {
                            appendLine()
                            appendLine("预期结果: ${result.expectedResult}")
                        }
                    }
                    _uiState.update {
                        it.copy(
                            messages = it.messages + ChatMessage.Ai(
                                text = aiText.trim(),
                                steps = result.steps,
                                caseName = result.caseName
                            ),
                            isGenerating = false
                        )
                    }
                } else {
                    val errMsg = result.error ?: "PC 端 AI 服务返回了空步骤，请检查 Ollama 是否已启动并安装模型"
                    _uiState.update {
                        it.copy(
                            messages = it.messages + ChatMessage.System(
                                "AI 生成失败: $errMsg\n\n" +
                                "请确认:\n" +
                                "1. PC 端 Ollama 已启动\n" +
                                "2. 已安装并运行模型\n" +
                                "3. 在 PC 端 AI 配置中启用了移动端推理"
                            ),
                            isGenerating = false
                        )
                    }
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

    fun saveGeneratedSteps(steps: List<Step>, caseName: String = "") {
        viewModelScope.launch {
            val name = caseName.ifBlank { "AI生成_${System.currentTimeMillis()}" }
            val testCase = TestCase(
                id = UUID.randomUUID().toString(),
                name = name,
                description = "由 AI 对话生成",
                steps = steps.mapIndexed { index, step ->
                    step.copy(index = index + 1)
                },
                source = CaseSource.AI_GENERATED
            )
            caseRepository.saveCase(testCase)
            _uiState.update {
                it.copy(
                    messages = it.messages + ChatMessage.System("✅ 用例「${name}」已保存")
                )
            }
        }
    }

    fun runGeneratedSteps(steps: List<Step>) {
        _uiState.update {
            it.copy(
                messages = it.messages + ChatMessage.System("▶️ 请先保存用例，再到用例列表中选择回放")
            )
        }
    }
}
