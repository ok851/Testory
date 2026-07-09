package com.testory.assistant.v2.feature.settings

import android.content.Intent
import android.net.Uri
import android.provider.Settings
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel

/**
 * 设置页面。
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsScreen(
    onBack: () -> Unit,
    onNavigateToSync: () -> Unit = {},
    viewModel: SettingsViewModel = hiltViewModel()
) {
    val context = LocalContext.current
    val uiState by viewModel.uiState.collectAsState()
    var showPcDialog by remember { mutableStateOf(false) }
    var showClearDialog by remember { mutableStateOf(false) }
    var showAboutDialog by remember { mutableStateOf(false) }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("设置") },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.Filled.ArrowBack, contentDescription = "返回")
                    }
                }
            )
        }
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .verticalScroll(rememberScrollState())
        ) {
            // ── 权限设置 ──
            SettingsSection(title = "权限设置") {
                SettingsItem(
                    icon = Icons.Filled.Accessibility,
                    title = "无障碍服务",
                    subtitle = "开启后可录制和回放操作",
                    onClick = {
                        context.startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
                    }
                )
                SettingsItem(
                    icon = Icons.Filled.Security,
                    title = "悬浮窗权限",
                    subtitle = "允许录制时显示悬浮控制条",
                    onClick = {
                        val intent = Intent(
                            Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                            Uri.parse("package:${context.packageName}")
                        )
                        context.startActivity(intent)
                    }
                )
            }

            HorizontalDivider(modifier = Modifier.padding(horizontal = 16.dp))

            // ── PC 连接 ──
            SettingsSection(title = "PC 连接") {
                val connSubtitle = when (uiState.connectionStatus) {
                    ConnectionStatus.CONNECTED -> "已连接到 ${uiState.pcAddress}:${uiState.pcPort}"
                    ConnectionStatus.CONNECTING -> "正在连接…"
                    ConnectionStatus.ERROR -> "连接失败，请检查 IP/端口/配对码"
                    ConnectionStatus.DISCONNECTED -> "未连接，点击配置 PC 地址"
                }
                SettingsItem(
                    icon = Icons.Filled.Computer,
                    title = "PC 端连接",
                    subtitle = connSubtitle,
                    onClick = { showPcDialog = true }
                )
                SettingsItem(
                    icon = Icons.Filled.Sync,
                    title = "手动同步",
                    subtitle = "选择用例同步到 PC 端或从 PC 端拉取",
                    onClick = { onNavigateToSync() }
                )
            }

            HorizontalDivider(modifier = Modifier.padding(horizontal = 16.dp))

            // ── 通用设置 ──
            SettingsSection(title = "通用") {
                SettingsItem(
                    icon = Icons.Filled.Storage,
                    title = "清理数据",
                    subtitle = if (uiState.isClearing) "正在清理…" else "清除录制的用例和运行记录",
                    onClick = { showClearDialog = true }
                )
                SettingsItem(
                    icon = Icons.Filled.Info,
                    title = "关于 Testory",
                    subtitle = "版本 ${uiState.appVersion} · 智测工坊",
                    onClick = { showAboutDialog = true }
                )
            }

            Spacer(modifier = Modifier.height(32.dp))
        }

        // ── Dialogs ──

        // PC connection dialog
        if (showPcDialog) {
            var pairCode by remember { mutableStateOf("") }
            AlertDialog(
                onDismissRequest = { showPcDialog = false },
                title = { Text("连接 PC 端") },
                text = {
                    Column {
                        Text(
                            text = "请在 PC 端「移动端测试」页面获取 6 位配对码",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            modifier = Modifier.padding(bottom = 12.dp)
                        )
                        OutlinedTextField(
                            value = uiState.pcAddress,
                            onValueChange = { viewModel.updatePcAddress(it) },
                            label = { Text("PC IP 地址") },
                            singleLine = true,
                            modifier = Modifier.fillMaxWidth()
                        )
                        Spacer(modifier = Modifier.height(8.dp))
                        OutlinedTextField(
                            value = uiState.pcPort,
                            onValueChange = { viewModel.updatePcPort(it) },
                            label = { Text("端口") },
                            singleLine = true,
                            modifier = Modifier.fillMaxWidth()
                        )
                        Spacer(modifier = Modifier.height(8.dp))
                        OutlinedTextField(
                            value = pairCode,
                            onValueChange = { value ->
                                if (value.length <= 6 && value.all { it.isDigit() }) {
                                    pairCode = value
                                }
                            },
                            label = { Text("6 位配对码") },
                            singleLine = true,
                            modifier = Modifier.fillMaxWidth(),
                            placeholder = { Text("请输入配对码") }
                        )
                        Spacer(modifier = Modifier.height(12.dp))
                        val statusText = when (uiState.connectionStatus) {
                            ConnectionStatus.CONNECTED -> "状态：已连接 ✅"
                            ConnectionStatus.CONNECTING -> "状态：连接中…"
                            ConnectionStatus.ERROR -> "状态：连接失败 ❌"
                            ConnectionStatus.DISCONNECTED -> "状态：未连接"
                        }
                        Text(
                            text = statusText,
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant
                        )
                        if (uiState.connectionStatus == ConnectionStatus.ERROR && uiState.connectionError != null) {
                            Spacer(modifier = Modifier.height(8.dp))
                            Text(
                                text = uiState.connectionError!!,
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.error,
                                lineHeight = MaterialTheme.typography.bodySmall.lineHeight
                            )
                        }
                    }
                },
                confirmButton = {
                    Button(
                        onClick = { viewModel.connectToPc(pairCode) },
                        enabled = uiState.connectionStatus != ConnectionStatus.CONNECTING
                    ) {
                        Text("连接")
                    }
                },
                dismissButton = {
                    TextButton(onClick = { showPcDialog = false }) {
                        Text("取消")
                    }
                }
            )
        }

        // Clear data confirmation dialog
        if (showClearDialog) {
            AlertDialog(
                onDismissRequest = { showClearDialog = false },
                title = { Text("确认清理数据") },
                text = { Text("将清除所有录制的用例和运行记录，此操作不可撤销。建议先同步到 PC 端。") },
                confirmButton = {
                    Button(
                        onClick = {
                            showClearDialog = false
                            viewModel.clearAllData { ok, msg ->
                                android.widget.Toast.makeText(context, msg, android.widget.Toast.LENGTH_SHORT).show()
                            }
                        },
                        colors = ButtonDefaults.buttonColors(
                            containerColor = MaterialTheme.colorScheme.error
                        )
                    ) {
                        Text("确认清除")
                    }
                },
                dismissButton = {
                    TextButton(onClick = { showClearDialog = false }) {
                        Text("取消")
                    }
                }
            )
        }

        // About dialog
        if (showAboutDialog) {
            AlertDialog(
                onDismissRequest = { showAboutDialog = false },
                title = { Text("关于 Testory") },
                text = {
                    Column {
                        Text(
                            text = "Testory 智测工坊",
                            style = MaterialTheme.typography.titleMedium
                        )
                        Spacer(modifier = Modifier.height(4.dp))
                        Text(
                            text = "版本 ${uiState.appVersion}",
                            style = MaterialTheme.typography.bodyMedium,
                            color = MaterialTheme.colorScheme.onSurfaceVariant
                        )
                        Spacer(modifier = Modifier.height(12.dp))
                        Text(
                            text = "移动端智能测试助手，支持录制回放、AI 生成用例、PC 端协同管理。\n\n" +
                                   "📱 无障碍服务录制回放\n" +
                                   "🤖 AI 对话创建测试用例\n" +
                                   "🔗 PC 端协同管理与执行\n" +
                                   "📊 测试报告自动生成",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            lineHeight = MaterialTheme.typography.bodySmall.lineHeight
                        )
                    }
                },
                confirmButton = {
                    TextButton(onClick = { showAboutDialog = false }) {
                        Text("关闭")
                    }
                }
            )
        }
    }
}

@Composable
private fun SettingsSection(
    title: String,
    content: @Composable ColumnScope.() -> Unit
) {
    Column(
        modifier = Modifier.padding(vertical = 8.dp)
    ) {
        Text(
            text = title,
            modifier = Modifier.padding(horizontal = 16.dp, vertical = 8.dp),
            style = MaterialTheme.typography.labelLarge,
            fontWeight = FontWeight.Bold,
            color = MaterialTheme.colorScheme.primary
        )
        content()
    }
}

@Composable
private fun SettingsItem(
    icon: androidx.compose.ui.graphics.vector.ImageVector,
    title: String,
    subtitle: String,
    onClick: () -> Unit
) {
    Surface(
        modifier = Modifier
            .fillMaxWidth()
            .clickable(onClick = onClick)
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 16.dp, vertical = 12.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            Icon(
                icon,
                contentDescription = null,
                modifier = Modifier.size(24.dp),
                tint = MaterialTheme.colorScheme.onSurfaceVariant
            )
            Spacer(modifier = Modifier.width(16.dp))
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = title,
                    style = MaterialTheme.typography.bodyLarge
                )
                Text(
                    text = subtitle,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.6f)
                )
            }
            Icon(
                Icons.Filled.ChevronRight,
                contentDescription = null,
                tint = MaterialTheme.colorScheme.outline
            )
        }
    }
}
