package com.testory.assistant.v2.feature.history

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import com.testory.assistant.v2.core.model.ActionType
import com.testory.assistant.v2.core.model.RunResultSummary
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun RunHistoryScreen(
    caseId: String,
    onBack: () -> Unit,
    viewModel: RunHistoryViewModel = hiltViewModel()
) {
    val uiState by viewModel.uiState.collectAsState()

    LaunchedEffect(caseId) {
        viewModel.loadHistory(caseId)
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Text("运行记录 - ${uiState.caseName}".ifEmpty { "运行记录" })
                },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.Filled.ArrowBack, contentDescription = "返回")
                    }
                }
            )
        }
    ) { padding ->
        if (uiState.records.isEmpty()) {
            Box(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(padding),
                contentAlignment = Alignment.Center
            ) {
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Icon(
                        Icons.Filled.History,
                        contentDescription = null,
                        modifier = Modifier.size(64.dp),
                        tint = MaterialTheme.colorScheme.outline
                    )
                    Spacer(modifier = Modifier.height(16.dp))
                    Text(
                        "暂无运行记录",
                        style = MaterialTheme.typography.bodyLarge,
                        color = MaterialTheme.colorScheme.outline
                    )
                    Text(
                        "执行测试用例后会自动记录",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.outline.copy(alpha = 0.6f)
                    )
                }
            }
        } else {
            LazyColumn(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(padding),
                contentPadding = PaddingValues(16.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                // Overview card
                item {
                    Card(
                        modifier = Modifier.fillMaxWidth(),
                        colors = CardDefaults.cardColors(
                            containerColor = MaterialTheme.colorScheme.primaryContainer.copy(alpha = 0.3f)
                        )
                    ) {
                        Row(
                            modifier = Modifier
                                .fillMaxWidth()
                                .padding(16.dp),
                            horizontalArrangement = Arrangement.SpaceEvenly
                        ) {
                            StatItem("总次数", "${uiState.totalRuns}")
                            StatItem("通过", "${uiState.passCount}")
                            StatItem("成功率", "${(uiState.successRate * 100).toInt()}%")
                            StatItem("平均耗时", formatDuration(uiState.avgDurationMs))
                        }
                    }
                }

                item {
                    Spacer(modifier = Modifier.height(4.dp))
                }

                items(uiState.records, key = { it.runId }) { record ->
                    val isExpanded = uiState.expandedRecordIds.contains(record.runId)
                    val stepResults = viewModel.parseStepResults(record.stepResultsJson)

                    Card(
                        modifier = Modifier
                            .fillMaxWidth()
                            .clickable { viewModel.toggleExpanded(record.runId) },
                        colors = CardDefaults.cardColors(
                            containerColor = MaterialTheme.colorScheme.surface
                        )
                    ) {
                        Column(modifier = Modifier.padding(12.dp)) {
                            Row(
                                modifier = Modifier.fillMaxWidth(),
                                verticalAlignment = Alignment.CenterVertically
                            ) {
                                Icon(
                                    if (record.success) Icons.Filled.CheckCircle
                                    else Icons.Filled.Cancel,
                                    contentDescription = null,
                                    tint = if (record.success) MaterialTheme.colorScheme.primary
                                    else MaterialTheme.colorScheme.error,
                                    modifier = Modifier.size(24.dp)
                                )
                                Spacer(modifier = Modifier.width(12.dp))
                                Column(modifier = Modifier.weight(1f)) {
                                    Text(
                                        text = if (record.success) "全部通过" else "测试失败",
                                        style = MaterialTheme.typography.bodyMedium,
                                        fontWeight = FontWeight.Medium
                                    )
                                    Text(
                                        text = "${record.passedSteps}/${record.totalSteps} 步通过 · 耗时 ${formatDuration(record.durationMs)}",
                                        style = MaterialTheme.typography.bodySmall,
                                        color = MaterialTheme.colorScheme.outline
                                    )
                                }
                                Column(horizontalAlignment = Alignment.End) {
                                    Text(
                                        text = SimpleDateFormat(
                                            "MM/dd HH:mm",
                                            Locale.getDefault()
                                        ).format(Date(record.runAt)),
                                        style = MaterialTheme.typography.labelSmall,
                                        color = MaterialTheme.colorScheme.outline
                                    )
                                    if (stepResults.isNotEmpty()) {
                                        Icon(
                                            if (isExpanded) Icons.Filled.ExpandLess
                                            else Icons.Filled.ExpandMore,
                                            contentDescription = null,
                                            modifier = Modifier.size(20.dp),
                                            tint = MaterialTheme.colorScheme.outline
                                        )
                                    }
                                }
                            }

                            AnimatedVisibility(visible = isExpanded && stepResults.isNotEmpty()) {
                                Column(modifier = Modifier.padding(top = 8.dp)) {
                                    HorizontalDivider()
                                    Spacer(modifier = Modifier.height(8.dp))
                                    stepResults.forEachIndexed { idx, stepResult ->
                                        Row(
                                            modifier = Modifier
                                                .fillMaxWidth()
                                                .padding(vertical = 4.dp),
                                            verticalAlignment = Alignment.CenterVertically
                                        ) {
                                            Icon(
                                                if (stepResult.success) Icons.Filled.Check
                                                else Icons.Filled.Close,
                                                contentDescription = null,
                                                tint = if (stepResult.success) MaterialTheme.colorScheme.primary
                                                else MaterialTheme.colorScheme.error,
                                                modifier = Modifier.size(16.dp)
                                            )
                                            Spacer(modifier = Modifier.width(8.dp))
                                            Text(
                                                text = "第 ${idx + 1} 步",
                                                style = MaterialTheme.typography.labelMedium,
                                                fontWeight = FontWeight.Medium
                                            )
                                            Spacer(modifier = Modifier.width(8.dp))
                                            Text(
                                                text = stepResult.stepDescription.ifEmpty {
                                                    stepResult.actualStrategy.ifEmpty { "-" }
                                                },
                                                style = MaterialTheme.typography.bodySmall,
                                                color = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.7f),
                                                modifier = Modifier.weight(1f)
                                            )
                                            if (stepResult.durationMs > 0) {
                                                Text(
                                                    text = "${stepResult.durationMs}ms",
                                                    style = MaterialTheme.typography.labelSmall,
                                                    color = MaterialTheme.colorScheme.outline
                                                )
                                            }
                                        }
                                        if (stepResult.errorMessage.isNotBlank()) {
                                            Text(
                                                text = stepResult.errorMessage,
                                                style = MaterialTheme.typography.bodySmall,
                                                color = MaterialTheme.colorScheme.error,
                                                modifier = Modifier.padding(start = 24.dp, bottom = 2.dp)
                                            )
                                        }
                                    }
                                }
                            }
                        }
                    }
                }

                item { Spacer(modifier = Modifier.height(16.dp)) }
            }
        }
    }
}

@Composable
private fun StatItem(label: String, value: String) {
    Column(horizontalAlignment = Alignment.CenterHorizontally) {
        Text(
            text = value,
            style = MaterialTheme.typography.titleMedium,
            fontWeight = FontWeight.Bold,
            color = MaterialTheme.colorScheme.primary
        )
        Text(
            text = label,
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.outline
        )
    }
}

private fun formatDuration(ms: Long): String {
    return when {
        ms < 1000 -> "${ms}ms"
        ms < 60000 -> "${ms / 1000}.${(ms % 1000) / 100}s"
        else -> "${ms / 60000}m${(ms % 60000) / 1000}s"
    }
}
