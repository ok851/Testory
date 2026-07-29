package com.testory.assistant.v2.feature.recorder

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
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import com.testory.assistant.v2.core.model.ActionType
import com.testory.assistant.v2.core.model.RecordingState
import com.testory.assistant.v2.core.model.Step

/**
 * 录制中心 — 核心录制界面。
 *
 * 用户点击"开始录制"后返回桌面/目标 App 操作，
 * 录制到的步骤实时在此页面显示。
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun RecorderScreen(
    onBack: () -> Unit,
    viewModel: RecorderViewModel = hiltViewModel()
) {
    val uiState by viewModel.uiState.collectAsState()

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Text(
                        text = when (uiState.recordingState) {
                            RecordingState.IDLE -> "录制测试"
                            RecordingState.RECORDING -> "正在录制..."
                            RecordingState.PAUSED -> "录制已暂停"
                            RecordingState.SAVING -> "保存中..."
                        }
                    )
                },
                navigationIcon = {
                    IconButton(onClick = {
                        if (uiState.recordingState == RecordingState.IDLE ||
                            uiState.recordingState == RecordingState.SAVING
                        ) {
                            onBack()
                        }
                    }) {
                        Icon(Icons.Filled.ArrowBack, contentDescription = "返回")
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = if (uiState.recordingState == RecordingState.RECORDING)
                        MaterialTheme.colorScheme.errorContainer
                    else
                        MaterialTheme.colorScheme.surface
                ),
                actions = {
                    if (uiState.steps.isNotEmpty()) {
                        IconButton(onClick = { viewModel.clearSteps() }) {
                            Icon(Icons.Filled.Delete, contentDescription = "清空")
                        }
                    }
                }
            )
        },
        floatingActionButton = {
            when (uiState.recordingState) {
                RecordingState.IDLE -> {
                    ExtendedFloatingActionButton(
                        onClick = { viewModel.startRecording() },
                        icon = { Icon(Icons.Filled.FiberManualRecord, contentDescription = null) },
                        text = { Text("开始录制") },
                        containerColor = MaterialTheme.colorScheme.error
                    )
                }
                RecordingState.RECORDING -> {
                    Row(
                        horizontalArrangement = Arrangement.spacedBy(12.dp)
                    ) {
                        FloatingActionButton(
                            onClick = { viewModel.pauseRecording() },
                            containerColor = MaterialTheme.colorScheme.secondaryContainer
                        ) {
                            Icon(Icons.Filled.Pause, contentDescription = "暂停")
                        }
                        FloatingActionButton(
                            onClick = { viewModel.stopRecording() },
                            containerColor = MaterialTheme.colorScheme.error
                        ) {
                            Icon(Icons.Filled.Stop, contentDescription = "停止")
                        }
                    }
                }
                RecordingState.PAUSED -> {
                    ExtendedFloatingActionButton(
                        onClick = { viewModel.resumeRecording() },
                        icon = { Icon(Icons.Filled.PlayArrow, contentDescription = null) },
                        text = { Text("继续录制") },
                        containerColor = MaterialTheme.colorScheme.primary
                    )
                }
                RecordingState.SAVING -> {
                    // Saving indicator
                    LinearProgressIndicator(modifier = Modifier.fillMaxWidth())
                }
            }
        }
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .padding(16.dp)
        ) {
            // Step count indicator
            if (uiState.steps.isNotEmpty()) {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Text(
                        text = "已录制 ${uiState.steps.size} 步",
                        style = MaterialTheme.typography.titleSmall,
                        fontWeight = FontWeight.Bold
                    )
                    if (uiState.recordingState == RecordingState.IDLE) {
                        TextButton(onClick = { viewModel.saveCase() }) {
                            Icon(Icons.Filled.Save, contentDescription = null, modifier = Modifier.size(16.dp))
                            Spacer(modifier = Modifier.width(4.dp))
                            Text("保存用例")
                        }
                    }
                }
                Spacer(modifier = Modifier.height(8.dp))
            } else if (uiState.recordingState == RecordingState.IDLE) {
                // Empty state
                Box(
                    modifier = Modifier
                        .fillMaxSize()
                        .padding(32.dp),
                    contentAlignment = Alignment.Center
                ) {
                    Column(horizontalAlignment = Alignment.CenterHorizontally) {
                        Icon(
                            Icons.Filled.Videocam,
                            contentDescription = null,
                            modifier = Modifier.size(64.dp),
                            tint = MaterialTheme.colorScheme.outline
                        )
                        Spacer(modifier = Modifier.height(16.dp))
                        Text(
                            text = "点击下方按钮开始录制",
                            style = MaterialTheme.typography.bodyLarge,
                            color = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.6f)
                        )
                        Spacer(modifier = Modifier.height(8.dp))
                        Text(
                            text = "录制过程中可在手机上自由操作\n步骤将自动记录",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.4f)
                        )
                    }
                }
            }

            // Step list
            if (uiState.steps.isNotEmpty()) {
                LazyColumn(
                    verticalArrangement = Arrangement.spacedBy(8.dp)
                ) {
                    itemsIndexed(
                        items = uiState.steps,
                        key = { index, step -> step.id.ifEmpty { "$index" } }
                    ) { index, step ->
                        StepCard(index = index + 1, step = step)
                    }
                    item { Spacer(modifier = Modifier.height(80.dp)) } // FAB space
                }
            }

            // Save dialog
            if (uiState.showSaveDialog) {
                SaveCaseDialog(
                    onConfirm = { name, description ->
                        viewModel.confirmSave(name, description)
                    },
                    onDismiss = { viewModel.dismissSaveDialog() }
                )
            }
        }
    }
}

@Composable
private fun StepCard(index: Int, step: Step) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.surfaceVariant
        )
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(12.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            // Step index badge
            Surface(
                color = MaterialTheme.colorScheme.primaryContainer,
                shape = MaterialTheme.shapes.small
            ) {
                Text(
                    text = "$index",
                    modifier = Modifier.padding(horizontal = 10.dp, vertical = 4.dp),
                    style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.onPrimaryContainer,
                    fontWeight = FontWeight.Bold
                )
            }

            Spacer(modifier = Modifier.width(12.dp))

            // Action icon
            val actionIcon = getActionIcon(step.action)
            val actionColor = getActionColor(step.action)
            Icon(
                actionIcon,
                contentDescription = null,
                modifier = Modifier.size(20.dp),
                tint = actionColor
            )

            Spacer(modifier = Modifier.width(8.dp))

            // Description
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = step.description.ifEmpty { step.action.name.lowercase() },
                    style = MaterialTheme.typography.bodyMedium,
                    maxLines = 2
                )
                if (step.locator.text.isNotBlank()) {
                    Text(
                        text = "目标: \"${step.locator.text.take(30)}\"",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.5f),
                        fontFamily = FontFamily.Monospace,
                        maxLines = 1
                    )
                }
            }

            Spacer(modifier = Modifier.width(4.dp))

            Icon(
                Icons.Filled.DragHandle,
                contentDescription = "拖动排序",
                modifier = Modifier.size(16.dp),
                tint = MaterialTheme.colorScheme.outline
            )
        }
    }
}

@Composable
private fun getActionIcon(action: ActionType) = when (action) {
    ActionType.TAP, ActionType.LONG_PRESS -> Icons.Filled.TouchApp
    ActionType.INPUT -> Icons.Filled.Keyboard
    ActionType.SWIPE, ActionType.SCROLL, ActionType.SCROLL_UNTIL -> Icons.Filled.Swipe
    ActionType.WAIT, ActionType.WAIT_UNTIL, ActionType.REPEAT, ActionType.WHILE -> Icons.Filled.Timer
    ActionType.ASSERT -> Icons.Filled.CheckCircle
    ActionType.BACK -> Icons.Filled.ArrowBack
    ActionType.HOME -> Icons.Filled.Home
    ActionType.OPEN_APP -> Icons.Filled.OpenInNew
    ActionType.SCREENSHOT -> Icons.Filled.CameraAlt
    ActionType.EXTRACT_TEXT, ActionType.SCAN_QR -> Icons.Filled.TextFields
    ActionType.SOLVE_CAPTCHA, ActionType.HUMAN_GATE -> Icons.Filled.Security
    ActionType.CLOSE_APP, ActionType.PRESS_KEY -> Icons.Filled.Settings
}

@Composable
private fun getActionColor(action: ActionType) = when (action) {
    ActionType.TAP, ActionType.LONG_PRESS -> MaterialTheme.colorScheme.primary
    ActionType.INPUT -> MaterialTheme.colorScheme.tertiary
    ActionType.SWIPE, ActionType.SCROLL, ActionType.SCROLL_UNTIL -> MaterialTheme.colorScheme.secondary
    ActionType.ASSERT -> MaterialTheme.colorScheme.error
    else -> MaterialTheme.colorScheme.outline
}

@Composable
private fun SaveCaseDialog(
    onConfirm: (String, String) -> Unit,
    onDismiss: () -> Unit
) {
    var caseName by remember { mutableStateOf("") }
    var description by remember { mutableStateOf("") }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("保存用例") },
        text = {
            Column {
                OutlinedTextField(
                    value = caseName,
                    onValueChange = { caseName = it },
                    label = { Text("用例名称") },
                    placeholder = { Text("例如: 登录流程测试") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth()
                )
                Spacer(modifier = Modifier.height(8.dp))
                OutlinedTextField(
                    value = description,
                    onValueChange = { description = it },
                    label = { Text("描述 (可选)") },
                    placeholder = { Text("描述这个测试用例的功能") },
                    maxLines = 3,
                    modifier = Modifier.fillMaxWidth()
                )
            }
        },
        confirmButton = {
            Button(
                onClick = { onConfirm(caseName, description) },
                enabled = caseName.isNotBlank()
            ) {
                Text("保存")
            }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) {
                Text("取消")
            }
        }
    )
}

data class RecorderUiState(
    val recordingState: RecordingState = RecordingState.IDLE,
    val steps: List<Step> = emptyList(),
    val showSaveDialog: Boolean = false
)
