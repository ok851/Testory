package com.testory.assistant.v2.feature.ai_bridge

import androidx.compose.animation.*
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import com.testory.assistant.v2.core.model.ActionType
import com.testory.assistant.v2.core.model.PcConnectionState
import com.testory.assistant.v2.core.model.Step
import kotlinx.coroutines.launch

/**
 * AI 桥接对话面板 — 自然语言意图发往 PC，使用 PC 已绑定大模型推理；
 * 步骤保存与执行在手机本机完成。
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AIBridgeScreen(
    onBack: () -> Unit,
    viewModel: AIBridgeViewModel = hiltViewModel()
) {
    val uiState by viewModel.uiState.collectAsState()
    var inputText by remember { mutableStateOf("") }
    val listState = rememberLazyListState()
    val coroutineScope = rememberCoroutineScope()

    LaunchedEffect(Unit) { viewModel.refreshAiStatus() }

    LaunchedEffect(uiState.messages.size) {
        if (uiState.messages.isNotEmpty()) {
            coroutineScope.launch {
                listState.animateScrollToItem(uiState.messages.size)
            }
        }
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Column {
                        Text("AI 测试助手")
                        val sub = when {
                            uiState.pcConnectionState != PcConnectionState.CONNECTED -> "未配对 PC"
                            uiState.aiModelLabel.isNotBlank() -> "使用 PC · ${uiState.aiModelLabel}"
                            uiState.aiReady -> "使用 PC 已绑定大模型"
                            else -> "PC 已连 · 请绑定大模型"
                        }
                        Text(
                            sub,
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant
                        )
                    }
                },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.Filled.ArrowBack, contentDescription = "返回")
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MaterialTheme.colorScheme.surface
                ),
                actions = {
                    if (uiState.pcConnectionState != PcConnectionState.CONNECTED) {
                        Surface(
                            color = MaterialTheme.colorScheme.errorContainer,
                            shape = MaterialTheme.shapes.small
                        ) {
                            Text(
                                text = "PC 未连接",
                                modifier = Modifier.padding(horizontal = 8.dp, vertical = 4.dp),
                                style = MaterialTheme.typography.labelSmall,
                                color = MaterialTheme.colorScheme.onErrorContainer
                            )
                        }
                        Spacer(modifier = Modifier.width(8.dp))
                    }
                }
            )
        },
        bottomBar = {
            // Input area
            Surface(
                shadowElevation = 8.dp
            ) {
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .navigationBarsPadding()
                        .padding(horizontal = 12.dp, vertical = 8.dp)
                ) {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(8.dp)
                    ) {
                        FilterChip(
                            selected = uiState.aiMode == "agent",
                            onClick = { viewModel.setAiMode("agent") },
                            label = { Text("Agent") },
                            enabled = !uiState.isGenerating
                        )
                        FilterChip(
                            selected = uiState.aiMode == "chat",
                            onClick = { viewModel.setAiMode("chat") },
                            label = { Text("闲聊") },
                            enabled = !uiState.isGenerating
                        )
                        FilterChip(
                            selected = uiState.aiMode == "generate",
                            onClick = { viewModel.setAiMode("generate") },
                            label = { Text("生成用例") },
                            enabled = !uiState.isGenerating
                        )
                    }
                    Spacer(modifier = Modifier.height(6.dp))
                    Text(
                        text = when (uiState.aiMode) {
                            "generate" -> "仅生成手机可回放步骤"
                            "chat" -> "短回复，不调跨端工具"
                            else -> "与 PC 同一 Agent；已连接端可联动（桌面/本机）"
                        },
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                    Spacer(modifier = Modifier.height(6.dp))
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        OutlinedTextField(
                            value = inputText,
                            onValueChange = { inputText = it },
                            modifier = Modifier.weight(1f),
                            placeholder = {
                                Text(
                                    when (uiState.aiMode) {
                                        "generate" -> "描述要生成的测试场景…"
                                        "chat" -> "随便问…"
                                        else -> "例如：登录某应用并取验证码填写…"
                                    }
                                )
                            },
                            maxLines = 3,
                            enabled = uiState.pcConnectionState == PcConnectionState.CONNECTED
                                    && !uiState.isGenerating
                        )
                        Spacer(modifier = Modifier.width(8.dp))
                        IconButton(
                            onClick = {
                                if (inputText.isNotBlank()) {
                                    viewModel.sendMessage(inputText)
                                    inputText = ""
                                }
                            },
                            enabled = inputText.isNotBlank()
                                    && uiState.pcConnectionState == PcConnectionState.CONNECTED
                                    && !uiState.isGenerating
                        ) {
                            Icon(
                                if (uiState.isGenerating) Icons.Filled.Stop
                                else Icons.Filled.Send,
                                contentDescription = "发送"
                            )
                        }
                    }
                }
            }
        }
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
        ) {
            // PC connection warning
            if (uiState.pcConnectionState != PcConnectionState.CONNECTED) {
                Card(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(16.dp),
                    colors = CardDefaults.cardColors(
                        containerColor = MaterialTheme.colorScheme.errorContainer
                    )
                ) {
                    Row(
                        modifier = Modifier.padding(16.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Icon(
                            Icons.Filled.Warning,
                            contentDescription = null,
                            tint = MaterialTheme.colorScheme.onErrorContainer
                        )
                        Spacer(modifier = Modifier.width(12.dp))
                        Column {
                            Text(
                                text = "PC 端 Testory 未连接",
                                style = MaterialTheme.typography.bodyMedium,
                                fontWeight = FontWeight.Bold,
                                color = MaterialTheme.colorScheme.onErrorContainer
                            )
                            Text(
                                text = "AI 推理由已连接的 PC 端完成，请确保 PC 端 Testory 已启动并连接到本设备。",
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onErrorContainer.copy(alpha = 0.8f)
                            )
                        }
                    }
                }
            }

            // Empty state or messages
            if (uiState.messages.isEmpty()) {
                Box(
                    modifier = Modifier
                        .fillMaxSize()
                        .padding(32.dp),
                    contentAlignment = Alignment.Center
                ) {
                    Column(horizontalAlignment = Alignment.CenterHorizontally) {
                        Icon(
                            Icons.Filled.SmartToy,
                            contentDescription = null,
                            modifier = Modifier.size(64.dp),
                            tint = MaterialTheme.colorScheme.outline
                        )
                        Spacer(modifier = Modifier.height(16.dp))
                        Text(
                            text = "AI 测试助手",
                            style = MaterialTheme.typography.titleLarge,
                            fontWeight = FontWeight.Bold
                        )
                        Spacer(modifier = Modifier.height(8.dp))
                        Text(
                            text = "告诉我你想测试什么，我来生成测试步骤",
                            style = MaterialTheme.typography.bodyMedium,
                            color = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.6f)
                        )
                        Spacer(modifier = Modifier.height(16.dp))
                        // Example chips
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            AssistChip(
                                onClick = {
                                    viewModel.sendMessage("帮我测试登录功能")
                                },
                                label = { Text("测试登录") }
                            )
                            AssistChip(
                                onClick = {
                                    viewModel.sendMessage("帮我测试注册流程")
                                },
                                label = { Text("测试注册") }
                            )
                        }
                    }
                }
            } else {
                LazyColumn(
                    state = listState,
                    modifier = Modifier.fillMaxSize(),
                    contentPadding = PaddingValues(16.dp),
                    verticalArrangement = Arrangement.spacedBy(12.dp)
                ) {
                    itemsIndexed(uiState.messages) { index, message ->
                        when (message) {
                            is ChatMessage.User -> UserBubble(message.text)
                            is ChatMessage.Ai -> AiBubble(
                                message = message,
                                onRunStep = { viewModel.runGeneratedSteps(message.steps) },
                                onSaveSteps = { viewModel.saveGeneratedSteps(message.steps, message.caseName) }
                            )
                            is ChatMessage.System -> SystemBubble(message.text)
                        }
                    }

                    if (uiState.isGenerating) {
                        item {
                            Row(
                                modifier = Modifier.padding(vertical = 8.dp),
                                verticalAlignment = Alignment.CenterVertically
                            ) {
                                CircularProgressIndicator(
                                    modifier = Modifier.size(20.dp),
                                    strokeWidth = 2.dp
                                )
                                Spacer(modifier = Modifier.width(8.dp))
                                Text(
                                    text = "AI 正在思考...",
                                    style = MaterialTheme.typography.bodySmall,
                                    color = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.6f)
                                )
                            }
                        }
                    }

                    item { Spacer(modifier = Modifier.height(8.dp)) }
                }
            }
        }
    }
}

@Composable
private fun UserBubble(text: String) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.End
    ) {
        Surface(
            color = MaterialTheme.colorScheme.primaryContainer,
            shape = MaterialTheme.shapes.medium,
            modifier = Modifier.widthIn(max = 300.dp)
        ) {
            Text(
                text = text,
                modifier = Modifier.padding(12.dp),
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onPrimaryContainer
            )
        }
    }
}

@Composable
private fun AiBubble(
    message: ChatMessage.Ai,
    onRunStep: () -> Unit,
    onSaveSteps: () -> Unit
) {
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .padding(start = 8.dp)
    ) {
        Text(
            text = message.text,
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.8f)
        )

        if (message.steps.isNotEmpty()) {
            Spacer(modifier = Modifier.height(8.dp))
            Card(
                modifier = Modifier.fillMaxWidth(),
                colors = CardDefaults.cardColors(
                    containerColor = MaterialTheme.colorScheme.surfaceVariant
                )
            ) {
                Column(modifier = Modifier.padding(12.dp)) {
                    Text(
                        text = "生成的测试步骤:",
                        style = MaterialTheme.typography.labelMedium,
                        fontWeight = FontWeight.Bold
                    )
                    Spacer(modifier = Modifier.height(8.dp))

                    message.steps.forEachIndexed { index, step ->
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Surface(
                                color = MaterialTheme.colorScheme.primary,
                                shape = MaterialTheme.shapes.extraSmall
                            ) {
                                Text(
                                    text = "${index + 1}",
                                    modifier = Modifier.padding(horizontal = 6.dp, vertical = 2.dp),
                                    style = MaterialTheme.typography.labelSmall,
                                    color = MaterialTheme.colorScheme.onPrimary
                                )
                            }
                            Spacer(modifier = Modifier.width(8.dp))
                            Text(
                                text = step.description.ifEmpty { step.action.name },
                                style = MaterialTheme.typography.bodySmall,
                                fontFamily = FontFamily.Monospace
                            )
                        }
                        if (index < message.steps.lastIndex) {
                            Spacer(modifier = Modifier.height(4.dp))
                        }
                    }

                    Spacer(modifier = Modifier.height(12.dp))
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(8.dp)
                    ) {
                        OutlinedButton(
                            onClick = onSaveSteps,
                            modifier = Modifier.weight(1f)
                        ) {
                            Icon(Icons.Filled.Save, contentDescription = null, modifier = Modifier.size(16.dp))
                            Spacer(modifier = Modifier.width(4.dp))
                            Text("保存用例")
                        }
                        Button(
                            onClick = onRunStep,
                            modifier = Modifier.weight(1f)
                        ) {
                            Icon(Icons.Filled.PlayArrow, contentDescription = null, modifier = Modifier.size(16.dp))
                            Spacer(modifier = Modifier.width(4.dp))
                            Text("立即运行")
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun SystemBubble(text: String) {
    Text(
        text = text,
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.outline,
        modifier = Modifier.fillMaxWidth(),
        textAlign = androidx.compose.ui.text.style.TextAlign.Center
    )
}

// ── Chat message types ──

sealed class ChatMessage {
    data class User(val text: String) : ChatMessage()
    data class Ai(
        val text: String,
        val steps: List<Step> = emptyList(),
        val caseName: String = ""
    ) : ChatMessage()
    data class System(val text: String) : ChatMessage()
}

data class AIBridgeUiState(
    val messages: List<ChatMessage> = emptyList(),
    val pcConnectionState: PcConnectionState = PcConnectionState.DISCONNECTED,
    val isGenerating: Boolean = false,
    val aiReady: Boolean = false,
    val aiModelLabel: String = "",
    val aiMessage: String = "",
    /** agent=与 PC 同一大脑；chat=闲聊；generate=仅生成步骤 */
    val aiMode: String = "agent"
)
