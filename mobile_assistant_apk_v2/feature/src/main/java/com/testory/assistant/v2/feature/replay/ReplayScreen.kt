package com.testory.assistant.v2.feature.replay

import androidx.compose.animation.*
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import com.testory.assistant.v2.core.model.ReplayState
import com.testory.assistant.v2.core.model.StepResult
import com.testory.assistant.v2.core.model.TestCase

/**
 * 回放界面 — 执行测试用例并实时显示进度。
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ReplayScreen(
    caseId: String,
    onBack: () -> Unit,
    viewModel: ReplayViewModel = hiltViewModel()
) {
    val uiState by viewModel.uiState.collectAsState()

    LaunchedEffect(caseId) {
        viewModel.loadCase(caseId)
        // 从用例详情页点击“运行测试”后自动开始回放，无需再点“开始回放”
        if (viewModel.uiState.value.replayState == ReplayState.IDLE) {
            viewModel.startReplay()
        }
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Text(
                        text = when (uiState.replayState) {
                            ReplayState.IDLE -> "回放测试"
                            ReplayState.RUNNING -> "正在回放..."
                            ReplayState.PAUSED -> "回放已暂停"
                            ReplayState.COMPLETED -> "✅ 回放完成"
                            ReplayState.FAILED -> "❌ 回放失败"
                            ReplayState.CANCELLED -> "回放已取消"
                        }
                    )
                },
                navigationIcon = {
                    IconButton(onClick = {
                        if (uiState.replayState == ReplayState.RUNNING) {
                            viewModel.cancelReplay()
                        }
                        onBack()
                    }) {
                        Icon(Icons.Filled.ArrowBack, contentDescription = "返回")
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = when (uiState.replayState) {
                        ReplayState.RUNNING -> MaterialTheme.colorScheme.primaryContainer
                        ReplayState.COMPLETED -> Color(0xFF4CAF50).copy(alpha = 0.15f)
                        ReplayState.FAILED -> MaterialTheme.colorScheme.errorContainer
                        else -> MaterialTheme.colorScheme.surface
                    }
                )
            )
        },
        floatingActionButton = {
            when (uiState.replayState) {
                ReplayState.IDLE, ReplayState.COMPLETED, ReplayState.FAILED -> {
                    ExtendedFloatingActionButton(
                        onClick = { viewModel.startReplay() },
                        icon = { Icon(Icons.Filled.PlayArrow, contentDescription = null) },
                        text = { Text(if (uiState.replayState == ReplayState.IDLE) "开始回放" else "重新回放") }
                    )
                }
                ReplayState.RUNNING -> {
                    FloatingActionButton(
                        onClick = { viewModel.cancelReplay() },
                        containerColor = MaterialTheme.colorScheme.error
                    ) {
                        Icon(Icons.Filled.Stop, contentDescription = "停止")
                    }
                }
                ReplayState.PAUSED -> {
                    ExtendedFloatingActionButton(
                        onClick = { viewModel.resumeReplay() },
                        icon = { Icon(Icons.Filled.PlayArrow, contentDescription = null) },
                        text = { Text("继续回放") }
                    )
                }
                else -> {}
            }
        }
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .padding(16.dp)
        ) {
            // Progress section
            if (uiState.totalSteps > 0 && uiState.replayState != ReplayState.IDLE) {
                Card(
                    modifier = Modifier.fillMaxWidth(),
                    colors = CardDefaults.cardColors(
                        containerColor = MaterialTheme.colorScheme.surfaceVariant
                    )
                ) {
                    Column(modifier = Modifier.padding(16.dp)) {
                        // Progress bar
                        Row(
                            modifier = Modifier.fillMaxWidth(),
                            horizontalArrangement = Arrangement.SpaceBetween,
                            verticalAlignment = Alignment.CenterVertically
                        ) {
                            Text(
                                text = "进度",
                                style = MaterialTheme.typography.titleSmall,
                                fontWeight = FontWeight.Bold
                            )
                            Text(
                                text = "${uiState.currentStep}/${uiState.totalSteps}",
                                style = MaterialTheme.typography.titleSmall,
                                color = MaterialTheme.colorScheme.primary,
                                fontWeight = FontWeight.Bold
                            )
                        }
                        Spacer(modifier = Modifier.height(8.dp))
                        LinearProgressIndicator(
                            progress = {
                                if (uiState.totalSteps > 0)
                                    uiState.currentStep.toFloat() / uiState.totalSteps
                                else 0f
                            },
                            modifier = Modifier.fillMaxWidth(),
                            trackColor = MaterialTheme.colorScheme.surfaceVariant
                        )
                        Spacer(modifier = Modifier.height(8.dp))

                        // Stats
                        Row(
                            modifier = Modifier.fillMaxWidth(),
                            horizontalArrangement = Arrangement.spacedBy(16.dp)
                        ) {
                            StatChip("成功", uiState.passedCount, MaterialTheme.colorScheme.primary)
                            StatChip("失败", uiState.failedCount, MaterialTheme.colorScheme.error)
                            StatChip("耗时", "${uiState.elapsedMs / 1000}s", MaterialTheme.colorScheme.outline)
                        }
                    }
                }
                Spacer(modifier = Modifier.height(16.dp))
            }

            // Case info
            uiState.testCase?.let { testCase ->
                Text(
                    text = testCase.name,
                    style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.Bold
                )
                if (testCase.description.isNotBlank()) {
                    Text(
                        text = testCase.description,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.6f)
                    )
                }
                Spacer(modifier = Modifier.height(16.dp))
            }

            // Step results
            if (uiState.stepResults.isEmpty() && uiState.replayState == ReplayState.IDLE) {
                Box(
                    modifier = Modifier.fillMaxSize(),
                    contentAlignment = Alignment.Center
                ) {
                    Column(horizontalAlignment = Alignment.CenterHorizontally) {
                        Icon(
                            Icons.Filled.PlayCircle,
                            contentDescription = null,
                            modifier = Modifier.size(64.dp),
                            tint = MaterialTheme.colorScheme.outline
                        )
                        Spacer(modifier = Modifier.height(16.dp))
                        Text(
                            text = "点击下方按钮开始回放",
                            style = MaterialTheme.typography.bodyLarge,
                            color = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.6f)
                        )
                    }
                }
            } else {
                LazyColumn(
                    verticalArrangement = Arrangement.spacedBy(8.dp)
                ) {
                    itemsIndexed(uiState.stepResults) { index, result ->
                        StepResultCard(result = result)
                    }
                    item { Spacer(modifier = Modifier.height(80.dp)) }
                }
            }
        }
    }
}

@Composable
private fun StatChip(
    label: String,
    value: Any,
    color: Color
) {
    Surface(
        color = color.copy(alpha = 0.1f),
        shape = MaterialTheme.shapes.small
    ) {
        Row(
            modifier = Modifier.padding(horizontal = 8.dp, vertical = 4.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            Text(
                text = label,
                style = MaterialTheme.typography.labelSmall,
                color = color.copy(alpha = 0.7f)
            )
            Spacer(modifier = Modifier.width(4.dp))
            Text(
                text = "$value",
                style = MaterialTheme.typography.labelSmall,
                color = color,
                fontWeight = FontWeight.Bold
            )
        }
    }
}

@Composable
private fun StepResultCard(result: StepResult) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(
            containerColor = if (result.success)
                MaterialTheme.colorScheme.surfaceVariant
            else
                MaterialTheme.colorScheme.errorContainer.copy(alpha = 0.3f)
        )
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(12.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            // Status icon
            val (icon, tint) = when {
                result.success -> Icons.Filled.CheckCircle to MaterialTheme.colorScheme.primary
                result.errorMessage.isNotEmpty() -> Icons.Filled.Cancel to MaterialTheme.colorScheme.error
                else -> Icons.Filled.HourglassEmpty to MaterialTheme.colorScheme.outline
            }
            Icon(
                icon,
                contentDescription = null,
                modifier = Modifier.size(24.dp),
                tint = tint
            )

            Spacer(modifier = Modifier.width(12.dp))

            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = "步骤 ${result.stepIndex}",
                    style = MaterialTheme.typography.labelMedium,
                    fontWeight = FontWeight.Bold
                )
                if (result.stepDescription.isNotBlank()) {
                    Text(
                        text = result.stepDescription,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.7f)
                    )
                }
                if (result.errorMessage.isNotEmpty()) {
                    Text(
                        text = result.errorMessage,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.error,
                        maxLines = 2
                    )
                }
            }

            Text(
                text = "${result.durationMs}ms",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.outline
            )
        }
    }
}

data class ReplayUiState(
    val testCase: TestCase? = null,
    val replayState: ReplayState = ReplayState.IDLE,
    val currentStep: Int = 0,
    val totalSteps: Int = 0,
    val passedCount: Int = 0,
    val failedCount: Int = 0,
    val elapsedMs: Long = 0,
    val stepResults: List<StepResult> = emptyList(),
    val errorMessage: String = ""
)
