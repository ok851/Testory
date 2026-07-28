package com.testory.assistant.v2.feature.home

import androidx.compose.animation.animateColorAsState
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material.icons.outlined.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import com.testory.assistant.v2.core.model.DeviceInfo
import com.testory.assistant.v2.core.model.PcConnectionState

/**
 * 首页：就绪清单 + 本机录制主 CTA + 次要「用例 / AI」。
 * 录制与执行在本机；PC 仅同步与 AI 推理。
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun HomeScreen(
    onNavigateToRecorder: () -> Unit,
    onNavigateToAIBridge: () -> Unit,
    onNavigateToCases: () -> Unit,
    onNavigateToSettings: () -> Unit,
    viewModel: HomeViewModel = hiltViewModel()
) {
    val uiState by viewModel.uiState.collectAsState()

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Column {
                        Text("Testory", fontWeight = FontWeight.Bold)
                        Text(
                            "手机端录制与执行",
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant
                        )
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MaterialTheme.colorScheme.surface
                ),
                actions = {
                    ConnectionIndicator(state = uiState.pcConnectionState)
                    IconButton(onClick = {
                        viewModel.refresh()
                        onNavigateToSettings()
                    }) {
                        Icon(Icons.Filled.Settings, contentDescription = "设置")
                    }
                }
            )
        }
    ) { padding ->
        HomeScreenContent(
            modifier = Modifier.padding(padding),
            uiState = uiState,
            onStartRecording = onNavigateToRecorder,
            onStartAI = onNavigateToAIBridge,
            onViewCases = onNavigateToCases,
            onOpenSettings = onNavigateToSettings
        )
    }
}

@Composable
fun HomeScreenContent(
    uiState: HomeUiState,
    onStartRecording: () -> Unit,
    onStartAI: () -> Unit,
    onViewCases: () -> Unit,
    onOpenSettings: () -> Unit,
    modifier: Modifier = Modifier,
    onStartReplay: () -> Unit = onViewCases,
    onRefreshConnection: () -> Unit = {}
) {
    val readyCount = listOf(
        uiState.isAccessibilityEnabled,
        uiState.canDrawOverlays,
        uiState.pcConnectionState == PcConnectionState.CONNECTED
    ).count { it }

    Column(
        modifier = modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp)
    ) {
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .clip(RoundedCornerShape(20.dp))
                .background(
                    Brush.linearGradient(
                        listOf(
                            MaterialTheme.colorScheme.primaryContainer,
                            MaterialTheme.colorScheme.surfaceVariant
                        )
                    )
                )
                .padding(20.dp)
        ) {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(
                    text = uiState.deviceInfo.model.ifBlank { "Android 设备" },
                    style = MaterialTheme.typography.titleLarge,
                    fontWeight = FontWeight.Bold
                )
                Text(
                    text = "录制与回放在本机完成 · PC 负责同步与 AI",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
                Text(
                    text = "就绪 $readyCount / 3",
                    style = MaterialTheme.typography.labelLarge,
                    color = MaterialTheme.colorScheme.primary
                )
            }
        }

        Text("就绪清单", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
        ReadinessRow(
            ok = uiState.isAccessibilityEnabled,
            title = "无障碍服务",
            subtitle = if (uiState.isAccessibilityEnabled) "已开启，可录制控件" else "去设置开启 Testory 无障碍"
        )
        ReadinessRow(
            ok = uiState.canDrawOverlays,
            title = "悬浮窗权限",
            subtitle = if (uiState.canDrawOverlays) "录制时可显示控制条" else "建议开启以便本机控制录跑"
        )
        ReadinessRow(
            ok = uiState.pcConnectionState == PcConnectionState.CONNECTED,
            title = "已配对 PC",
            subtitle = when (uiState.pcConnectionState) {
                PcConnectionState.CONNECTED ->
                    if (uiState.aiReady && uiState.aiModelLabel.isNotBlank())
                        "已连接 · AI ${uiState.aiModelLabel}"
                    else if (uiState.pcConnectionState == PcConnectionState.CONNECTED)
                        "已连接 · 可同步用例（AI 需在 PC 绑定模型）"
                    else "已连接"
                else -> "未连接 · 在设置中用配对码连接"
            }
        )

        Button(
            onClick = onStartRecording,
            modifier = Modifier
                .fillMaxWidth()
                .height(56.dp),
            shape = RoundedCornerShape(16.dp)
        ) {
            Icon(Icons.Filled.FiberManualRecord, contentDescription = null)
            Spacer(Modifier.width(8.dp))
            Text("开始录制", style = MaterialTheme.typography.titleMedium)
        }

        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(12.dp)
        ) {
            SecondaryActionCard(
                modifier = Modifier.weight(1f),
                icon = Icons.Outlined.FolderOpen,
                title = "用例",
                subtitle = "${uiState.caseCount} 个 · 本机运行",
                onClick = onViewCases
            )
            SecondaryActionCard(
                modifier = Modifier.weight(1f),
                icon = Icons.Outlined.SmartToy,
                title = "AI 助手",
                subtitle = if (uiState.aiReady) "使用 PC 大模型" else "需先配对 PC",
                onClick = onStartAI,
                enabled = true
            )
        }

        TextButton(onClick = onOpenSettings, modifier = Modifier.align(Alignment.CenterHorizontally)) {
            Text("连接 PC / 权限设置")
        }
        Spacer(modifier = Modifier.height(24.dp))
    }
}

@Composable
private fun ReadinessRow(ok: Boolean, title: String, subtitle: String) {
    val tint by animateColorAsState(
        if (ok) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.outline,
        label = "ready"
    )
    Surface(
        shape = RoundedCornerShape(14.dp),
        color = MaterialTheme.colorScheme.surface,
        tonalElevation = 1.dp,
        modifier = Modifier.fillMaxWidth()
    ) {
        Row(
            modifier = Modifier.padding(14.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            Icon(
                if (ok) Icons.Filled.CheckCircle else Icons.Outlined.RadioButtonUnchecked,
                contentDescription = null,
                tint = tint
            )
            Spacer(Modifier.width(12.dp))
            Column {
                Text(title, fontWeight = FontWeight.SemiBold)
                Text(
                    subtitle,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
            }
        }
    }
}

@Composable
private fun SecondaryActionCard(
    modifier: Modifier,
    icon: ImageVector,
    title: String,
    subtitle: String,
    onClick: () -> Unit,
    enabled: Boolean = true
) {
    Card(
        onClick = onClick,
        enabled = enabled,
        modifier = modifier.height(110.dp),
        shape = RoundedCornerShape(16.dp)
    ) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(14.dp),
            verticalArrangement = Arrangement.SpaceBetween
        ) {
            Icon(icon, contentDescription = null, tint = MaterialTheme.colorScheme.primary)
            Column {
                Text(title, fontWeight = FontWeight.SemiBold)
                Text(
                    subtitle,
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
            }
        }
    }
}

@Composable
private fun ConnectionIndicator(state: PcConnectionState) {
    val (color, label) = when (state) {
        PcConnectionState.CONNECTED -> MaterialTheme.colorScheme.primary to "已配对"
        PcConnectionState.CONNECTING -> MaterialTheme.colorScheme.tertiary to "连接中"
        PcConnectionState.RECONNECTING -> MaterialTheme.colorScheme.error to "重连中"
        PcConnectionState.DISCONNECTED -> MaterialTheme.colorScheme.outline to "未配对"
    }
    Surface(
        color = color.copy(alpha = 0.12f),
        shape = RoundedCornerShape(999.dp),
        modifier = Modifier.padding(end = 4.dp)
    ) {
        Row(
            modifier = Modifier.padding(horizontal = 10.dp, vertical = 4.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            Box(
                modifier = Modifier
                    .size(8.dp)
                    .clip(CircleShape)
                    .background(color)
            )
            Spacer(Modifier.width(6.dp))
            Text(label, style = MaterialTheme.typography.labelSmall, color = color)
        }
    }
}

data class HomeUiState(
    val deviceInfo: DeviceInfo = DeviceInfo(),
    val isAccessibilityEnabled: Boolean = false,
    val canDrawOverlays: Boolean = false,
    val pcConnectionState: PcConnectionState = PcConnectionState.DISCONNECTED,
    val caseCount: Int = 0,
    val aiReady: Boolean = false,
    val aiModelLabel: String = "",
    val aiMessage: String = ""
)

/** 兼容旧仪器测试命名 */
enum class ConnectionStatus { CONNECTED, CONNECTING, DISCONNECTED, ERROR }
