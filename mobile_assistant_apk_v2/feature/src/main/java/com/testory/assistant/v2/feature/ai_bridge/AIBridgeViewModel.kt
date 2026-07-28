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
 * AI 桥接：手机只发自然语言意图；推理在 PC（已绑定大模型）；
 * 保存与执行仍在手机本机。
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
                if (state == PcConnectionState.CONNECTED) {
                    refreshAiStatus()
                } else {
                    _uiState.update {
                        it.copy(aiReady = false, aiModelLabel = "", aiMessage = "")
                    }
                }
            }
        }
    }

    fun refreshAiStatus() {
        viewModelScope.launch {
            if (_uiState.value.pcConnectionState != PcConnectionState.CONNECTED) return@launch
            val st = pcSyncClient.fetchAiStatus()
            val label = listOf(st.provider, st.model).filter { it.isNotBlank() }.joinToString(" · ")
            _uiState.update {
                it.copy(
                    aiReady = st.success && st.ready,
                    aiModelLabel = label,
                    aiMessage = st.message.ifBlank { st.error ?: "" }
                )
            }
        }
    }

    fun setAiMode(mode: String) {
        val normalized = if (mode == "generate") "generate" else "chat"
        _uiState.update { it.copy(aiMode = normalized) }
    }

    fun sendMessage(text: String) {
        if (text.isBlank()) return
        val mode = _uiState.value.aiMode

        _uiState.update {
            it.copy(
                messages = it.messages + ChatMessage.User(text),
                isGenerating = true
            )
        }

        viewModelScope.launch {
            if (_uiState.value.pcConnectionState != PcConnectionState.CONNECTED) {
                _uiState.update {
                    it.copy(
                        messages = it.messages + ChatMessage.System(
                            "未配对 PC。请先在设置中连接，AI 推理在 PC 端完成。"
                        ),
                        isGenerating = false
                    )
                }
                return@launch
            }

            try {
                val result = pcSyncClient.aiGenerateSteps(text, mode = mode)
                if (result.model.isNotBlank() || result.provider.isNotBlank()) {
                    val label = listOf(result.provider, result.model)
                        .filter { it.isNotBlank() }
                        .joinToString(" · ")
                    _uiState.update {
                        it.copy(aiReady = true, aiModelLabel = label)
                    }
                }

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
                } else if (result.success) {
                    val reply = result.description.ifBlank {
                        if (mode == "generate") {
                            "未生成步骤。可换更具体的场景描述，或先用「对话」模式讨论。"
                        } else {
                            "已收到。需要可回放步骤时，请切换到「生成用例」模式。"
                        }
                    }
                    _uiState.update {
                        it.copy(
                            messages = it.messages + ChatMessage.Ai(
                                text = reply,
                                steps = emptyList(),
                                caseName = ""
                            ),
                            isGenerating = false
                        )
                    }
                } else {
                    val errMsg = result.error ?: "PC 端请求失败"
                    _uiState.update {
                        it.copy(
                            messages = it.messages + ChatMessage.System(
                                "AI 失败: $errMsg\n\n" +
                                    "请确认:\n" +
                                    "1. 已配对 PC（同网，能访问 Flask）\n" +
                                    "2. PC 端已绑定并激活大模型\n" +
                                    "3. 模型接口可从 PC 访问"
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
                description = "由 AI 对话生成（PC 大模型）",
                steps = steps.mapIndexed { index, step ->
                    step.copy(index = index + 1)
                },
                source = CaseSource.AI_GENERATED
            )
            caseRepository.saveCase(testCase)
            _uiState.update {
                it.copy(
                    messages = it.messages + ChatMessage.System(
                        "✅ 用例「${name}」已保存到本机，请在用例列表中本机回放"
                    )
                )
            }
        }
    }

    fun runGeneratedSteps(steps: List<Step>) {
        _uiState.update {
            it.copy(
                messages = it.messages + ChatMessage.System(
                    "▶️ 请先保存用例，再到用例列表中选择本机回放（PC 不参与执行）"
                )
            )
        }
    }
}
