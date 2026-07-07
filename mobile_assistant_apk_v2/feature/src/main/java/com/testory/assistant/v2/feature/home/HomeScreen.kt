package com.testory.assistant.v2.feature.home

import androidx.compose.animation.*
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material.icons.outlined.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import com.testory.assistant.v2.core.model.DeviceInfo
import com.testory.assistant.v2.core.model.PcConnectionState
import com.testory.assistant.v2.core.model.TestCase

/**
 * 首页 — 用户进入后看到的主界面。
 * 两种模式入口：
 *   1. 录制测试 (本地执行)
 *   2. AI 创建测试 (PC 桥接)
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
                title = { Text("Testory · 智测工坊") },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MaterialTheme.colorScheme.primaryContainer,
                    titleContentColor = MaterialTheme.colorScheme.onPrimaryContainer
                ),
                actions = {
                    // PC 连接状态指示器
                    ConnectionIndicator(
                        state = uiState.pcConnectionState
                    )
                    IconButton(onClick = onNavigateToSettings) {
                        Icon(Icons.Filled.Settings, contentDescription = "设置")
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
                .padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp)
        ) {
            // Device info card
            DeviceStatusCard(
                deviceInfo = uiState.deviceInfo,
                isAccessibilityEnabled = uiState.isAccessibilityEnabled
            )

            // Quick actions grid
            Text(
                text = "快速操作",
                style = MaterialTheme.typography.titleMedium,
                fontWeight = FontWeight.Bold
            )

            // Row 1: 录制 + AI 创建
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(12.dp)
            ) {
                ActionCard(
                    modifier = Modifier.weight(1f),
                    icon = Icons.Filled.Videocam,
                    title = "录制测试",
                    subtitle = "操作 App 自动记录",
                    containerColor = MaterialTheme.colorScheme.primaryContainer,
                    onClick = onNavigateToRecorder
                )
                ActionCard(
                    modifier = Modifier.weight(1f),
                    icon = Icons.Filled.SmartToy,
                    title = "AI 创建",
                    subtitle = "说出需求自动生成",
                    containerColor = MaterialTheme.colorScheme.secondaryContainer,
                    onClick = onNavigateToAIBridge
                )
            }

            // Row 2: 运行 + 用例
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(12.dp)
            ) {
                ActionCard(
                    modifier = Modifier.weight(1f),
                    icon = Icons.Filled.PlayArrow,
                    title = "运行测试",
                    subtitle = "一键回放验证",
                    containerColor = MaterialTheme.colorScheme.tertiaryContainer,
                    onClick = onNavigateToCases
                )
                ActionCard(
                    modifier = Modifier.weight(1f),
                    icon = Icons.Filled.FolderOpen,
                    title = "用例管理",
                    subtitle = "${uiState.caseCount} 个用例",
                    containerColor = MaterialTheme.colorScheme.surfaceVariant,
                    onClick = onNavigateToCases
                )
            }

            // Recent cases
            if (uiState.recentCases.isNotEmpty()) {
                Spacer(modifier = Modifier.height(8.dp))
                Text(
                    text = "最近用例",
                    style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.Bold
                )
                uiState.recentCases.take(3).forEach { testCase ->
                    RecentCaseCard(
                        testCase = testCase,
                        onClick = onNavigateToCases
                    )
                }
            }

            Spacer(modifier = Modifier.height(32.dp))
        }
    }
}

@Composable
private fun ConnectionIndicator(state: PcConnectionState) {
    val (color, label) = when (state) {
        PcConnectionState.CONNECTED -> MaterialTheme.colorScheme.primary to "已连接"
        PcConnectionState.CONNECTING -> MaterialTheme.colorScheme.tertiary to "连接中"
        PcConnectionState.RECONNECTING -> MaterialTheme.colorScheme.error to "重连中"
        PcConnectionState.DISCONNECTED -> MaterialTheme.colorScheme.outline to "未连接"
    }

    Surface(
        color = color.copy(alpha = 0.15f),
        shape = MaterialTheme.shapes.small
    ) {
        Row(
            modifier = Modifier.padding(horizontal = 8.dp, vertical = 4.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            Box(
                modifier = Modifier
                    .size(8.dp)
                    .padding(0.dp)
            ) {
                Surface(
                    modifier = Modifier.fillMaxSize(),
                    color = color,
                    shape = MaterialTheme.shapes.extraLarge
                ) {}
            }
            Spacer(modifier = Modifier.width(4.dp))
            Text(
                text = label,
                style = MaterialTheme.typography.labelSmall,
                color = color
            )
        }
    }
}

@Composable
private fun DeviceStatusCard(
    deviceInfo: DeviceInfo,
    isAccessibilityEnabled: Boolean
) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.surfaceVariant
        )
    ) {
        Column(modifier = Modifier.padding(16.dp)) {
            Text(
                text = deviceInfo.deviceName.ifEmpty { "Android 设备" },
                style = MaterialTheme.typography.titleSmall,
                fontWeight = FontWeight.Bold
            )
            Spacer(modifier = Modifier.height(4.dp))
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(16.dp)
            ) {
                Text(
                    text = "Android ${deviceInfo.androidVersion}",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
                Text(
                    text = "${deviceInfo.screenWidth}×${deviceInfo.screenHeight}",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
            }
            Spacer(modifier = Modifier.height(8.dp))
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(4.dp)
            ) {
                val (color, label) = if (isAccessibilityEnabled) {
                    MaterialTheme.colorScheme.primary to "无障碍服务已开启"
                } else {
                    MaterialTheme.colorScheme.error to "无障碍服务未开启"
                }
                Icon(
                    Icons.Filled.Accessibility,
                    contentDescription = null,
                    modifier = Modifier.size(16.dp),
                    tint = color
                )
                Text(
                    text = label,
                    style = MaterialTheme.typography.labelSmall,
                    color = color
                )
            }
        }
    }
}

@Composable
private fun ActionCard(
    modifier: Modifier = Modifier,
    icon: ImageVector,
    title: String,
    subtitle: String,
    containerColor: androidx.compose.ui.graphics.Color,
    onClick: () -> Unit
) {
    Card(
        modifier = modifier.height(120.dp),
        onClick = onClick,
        colors = CardDefaults.cardColors(containerColor = containerColor)
    ) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(16.dp),
            verticalArrangement = Arrangement.Center
        ) {
            Icon(
                icon,
                contentDescription = null,
                modifier = Modifier.size(32.dp),
                tint = MaterialTheme.colorScheme.onSurface
            )
            Spacer(modifier = Modifier.height(8.dp))
            Text(
                text = title,
                style = MaterialTheme.typography.titleSmall,
                fontWeight = FontWeight.Bold
            )
            Text(
                text = subtitle,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.7f)
            )
        }
    }
}

@Composable
private fun RecentCaseCard(
    testCase: TestCase,
    onClick: () -> Unit
) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        onClick = onClick,
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.surface
        )
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(16.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = testCase.name,
                    style = MaterialTheme.typography.bodyMedium,
                    fontWeight = FontWeight.Medium
                )
                Text(
                    text = "${testCase.steps.size} 步 · ${
                        java.text.SimpleDateFormat("MM/dd HH:mm", java.util.Locale.getDefault())
                            .format(java.util.Date(testCase.updatedAt))
                    }",
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

/**
 * Home screen UI state
 */
data class HomeUiState(
    val deviceInfo: DeviceInfo = DeviceInfo(),
    val isAccessibilityEnabled: Boolean = false,
    val pcConnectionState: PcConnectionState = PcConnectionState.DISCONNECTED,
    val caseCount: Int = 0,
    val recentCases: List<TestCase> = emptyList()
)
