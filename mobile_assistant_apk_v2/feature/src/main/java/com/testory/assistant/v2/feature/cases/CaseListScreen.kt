package com.testory.assistant.v2.feature.cases

import androidx.compose.animation.*
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
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import com.testory.assistant.v2.core.model.CaseSource
import com.testory.assistant.v2.core.model.TestCase

/**
 * 用例列表 — 管理所有测试用例。
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun CaseListScreen(
    onCaseClick: (String) -> Unit,
    onBack: () -> Unit,
    viewModel: CaseListViewModel = hiltViewModel()
) {
    val uiState by viewModel.uiState.collectAsState()
    var searchQuery by remember { mutableStateOf("") }
    var showDeleteDialog by remember { mutableStateOf<String?>(null) }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("用例管理") },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.Filled.ArrowBack, contentDescription = "返回")
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MaterialTheme.colorScheme.surface
                )
            )
        }
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
        ) {
            // Search bar
            OutlinedTextField(
                value = searchQuery,
                onValueChange = {
                    searchQuery = it
                    viewModel.search(it)
                },
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp, vertical = 8.dp),
                placeholder = { Text("搜索用例...") },
                leadingIcon = { Icon(Icons.Filled.Search, contentDescription = null) },
                trailingIcon = {
                    if (searchQuery.isNotBlank()) {
                        IconButton(onClick = {
                            searchQuery = ""
                            viewModel.search("")
                        }) {
                            Icon(Icons.Filled.Clear, contentDescription = "清除")
                        }
                    }
                },
                singleLine = true
            )

            // Source filter chips
            if (searchQuery.isBlank()) {
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(horizontal = 16.dp),
                    horizontalArrangement = Arrangement.spacedBy(8.dp)
                ) {
                    FilterChip(
                        selected = uiState.selectedFilter == null,
                        onClick = { viewModel.filterBy(null) },
                        label = { Text("全部 (${uiState.allCount})") }
                    )
                    FilterChip(
                        selected = uiState.selectedFilter == CaseSource.RECORDED,
                        onClick = { viewModel.filterBy(CaseSource.RECORDED) },
                        label = { Text("录制 (${uiState.recordedCount})") }
                    )
                    FilterChip(
                        selected = uiState.selectedFilter == CaseSource.AI_GENERATED,
                        onClick = { viewModel.filterBy(CaseSource.AI_GENERATED) },
                        label = { Text("AI (${uiState.aiCount})") }
                    )
                }
                Spacer(modifier = Modifier.height(8.dp))
            }

            // Case list
            if (uiState.cases.isEmpty()) {
                Box(
                    modifier = Modifier.fillMaxSize(),
                    contentAlignment = Alignment.Center
                ) {
                    Column(horizontalAlignment = Alignment.CenterHorizontally) {
                        Icon(
                            Icons.Filled.FolderOpen,
                            contentDescription = null,
                            modifier = Modifier.size(64.dp),
                            tint = MaterialTheme.colorScheme.outline
                        )
                        Spacer(modifier = Modifier.height(16.dp))
                        Text(
                            text = "还没有测试用例",
                            style = MaterialTheme.typography.bodyLarge,
                            color = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.6f)
                        )
                        Text(
                            text = "点击主页的录制或AI按钮创建",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.4f)
                        )
                    }
                }
            } else {
                LazyColumn(
                    contentPadding = PaddingValues(16.dp),
                    verticalArrangement = Arrangement.spacedBy(8.dp)
                ) {
                    items(
                        items = uiState.cases,
                        key = { it.id }
                    ) { testCase ->
                        CaseCard(
                            testCase = testCase,
                            onClick = { onCaseClick(testCase.id) },
                            onDelete = { showDeleteDialog = testCase.id }
                        )
                    }
                    item { Spacer(modifier = Modifier.height(16.dp)) }
                }
            }
        }

        // Delete confirmation dialog
        showDeleteDialog?.let { caseId ->
            AlertDialog(
                onDismissRequest = { showDeleteDialog = null },
                title = { Text("删除用例") },
                text = { Text("确定要删除这个用例吗？此操作不可撤销。") },
                confirmButton = {
                    Button(
                        onClick = {
                            viewModel.deleteCase(caseId)
                            showDeleteDialog = null
                        },
                        colors = ButtonDefaults.buttonColors(
                            containerColor = MaterialTheme.colorScheme.error
                        )
                    ) {
                        Text("删除")
                    }
                },
                dismissButton = {
                    TextButton(onClick = { showDeleteDialog = null }) {
                        Text("取消")
                    }
                }
            )
        }
    }
}

@Composable
private fun CaseCard(
    testCase: TestCase,
    onClick: () -> Unit,
    onDelete: () -> Unit
) {
    Card(
        modifier = Modifier
            .fillMaxWidth()
            .clickable(onClick = onClick),
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.surfaceVariant
        )
    ) {
        Column(
            modifier = Modifier.padding(16.dp)
        ) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Text(
                    text = testCase.name,
                    style = MaterialTheme.typography.titleSmall,
                    fontWeight = FontWeight.Bold,
                    modifier = Modifier.weight(1f),
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis
                )
                Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                    // Source badge
                    val sourceLabel = when (testCase.source) {
                        CaseSource.RECORDED -> "录制"
                        CaseSource.AI_GENERATED -> "AI"
                        CaseSource.MANUAL -> "手动"
                        CaseSource.TEMPLATE -> "模板"
                    }
                    val sourceColor = when (testCase.source) {
                        CaseSource.RECORDED -> MaterialTheme.colorScheme.primary
                        CaseSource.AI_GENERATED -> MaterialTheme.colorScheme.tertiary
                        else -> MaterialTheme.colorScheme.secondary
                    }
                    SuggestionChip(
                        onClick = {},
                        label = { Text(sourceLabel, style = MaterialTheme.typography.labelSmall) },
                        colors = SuggestionChipDefaults.suggestionChipColors(
                            containerColor = sourceColor.copy(alpha = 0.15f),
                            labelColor = sourceColor
                        )
                    )
                    IconButton(
                        onClick = onDelete,
                        modifier = Modifier.size(32.dp)
                    ) {
                        Icon(
                            Icons.Filled.Delete,
                            contentDescription = "删除",
                            modifier = Modifier.size(18.dp),
                            tint = MaterialTheme.colorScheme.outline
                        )
                    }
                }
            }

            if (testCase.description.isNotBlank()) {
                Spacer(modifier = Modifier.height(4.dp))
                Text(
                    text = testCase.description,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.6f),
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis
                )
            }

            Spacer(modifier = Modifier.height(8.dp))

            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween
            ) {
                Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    Text(
                        text = "${testCase.steps.size} 步",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.primary
                    )
                    testCase.lastRunResult?.let {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Icon(
                                if (it.success) Icons.Filled.CheckCircle else Icons.Filled.Cancel,
                                contentDescription = null,
                                modifier = Modifier.size(14.dp),
                                tint = if (it.success) MaterialTheme.colorScheme.primary
                                else MaterialTheme.colorScheme.error
                            )
                            Spacer(modifier = Modifier.width(2.dp))
                            Text(
                                text = "${it.passedSteps}/${it.totalSteps}",
                                style = MaterialTheme.typography.labelSmall
                            )
                        }
                    }
                }
                Text(
                    text = java.text.SimpleDateFormat(
                        "yyyy/MM/dd HH:mm",
                        java.util.Locale.getDefault()
                    ).format(java.util.Date(testCase.updatedAt)),
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.5f)
                )
            }
        }
    }
}

data class CaseListUiState(
    val cases: List<TestCase> = emptyList(),
    val selectedFilter: CaseSource? = null,
    val allCount: Int = 0,
    val recordedCount: Int = 0,
    val aiCount: Int = 0
)
