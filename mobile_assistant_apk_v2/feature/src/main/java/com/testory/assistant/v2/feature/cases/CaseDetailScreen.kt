package com.testory.assistant.v2.feature.cases

import android.content.Intent
import android.provider.Settings
import android.widget.Toast
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectDragGesturesAfterLongPress
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowForward
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import com.testory.assistant.v2.core.model.*
import com.testory.assistant.v2.service.accessibility.CaptureSessionController
import com.testory.assistant.v2.service.accessibility.PickedElement
import com.testory.assistant.v2.service.foreground.ElementCaptureService

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun CaseDetailScreen(
    caseId: String,
    onBack: () -> Unit,
    onStartReplay: (String) -> Unit,
    onNavigateToHistory: (String) -> Unit,
    viewModel: CaseDetailViewModel = hiltViewModel()
) {
    val testCase by viewModel.case.collectAsState()
    val runHistory by viewModel.runHistory.collectAsState()
    val deleted by viewModel.deleted.collectAsState()
    val message by viewModel.message.collectAsState()
    val capturePending by CaptureSessionController.pending.collectAsState()
    val snackbarHostState = remember { SnackbarHostState() }
    val context = LocalContext.current

    var editingStep by remember { mutableStateOf<Step?>(null) }
    var pendingPicked by remember { mutableStateOf<Pair<Int, PickedElement>?>(null) }
    var confirmDeleteStepId by remember { mutableStateOf<String?>(null) }
    var showAddMenu by remember { mutableStateOf(false) }

    var draftSteps by remember { mutableStateOf<List<Step>>(emptyList()) }
    var draggingIndex by remember { mutableStateOf<Int?>(null) }
    var dragOffsetY by remember { mutableStateOf(0f) }
    var listScrollEnabled by remember { mutableStateOf(true) }
    val density = LocalDensity.current
    val listState = rememberLazyListState()

    LaunchedEffect(caseId) { viewModel.loadCase(caseId) }
    LaunchedEffect(deleted) { if (deleted) onBack() }
    LaunchedEffect(message) {
        message?.let {
            snackbarHostState.showSnackbar(it)
            viewModel.clearMessage()
        }
    }
    LaunchedEffect(testCase?.steps) {
        if (draggingIndex == null) {
            draftSteps = testCase?.steps.orEmpty()
        }
    }

    fun startCapture(kind: CaptureSessionController.Kind, afterIndex: Int = -1, stepId: String = "") {
        editingStep = null
        if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.M &&
            !Settings.canDrawOverlays(context)
        ) {
            Toast.makeText(context, "请先开启悬浮窗权限", Toast.LENGTH_LONG).show()
            try {
                context.startActivity(
                    Intent(
                        Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                        android.net.Uri.parse("package:${context.packageName}")
                    ).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                )
            } catch (_: Exception) { }
            return
        }
        val intent = Intent(context, ElementCaptureService::class.java).apply {
            putExtra(ElementCaptureService.EXTRA_CASE_ID, caseId)
            putExtra(ElementCaptureService.EXTRA_AFTER_INDEX, afterIndex)
            putExtra(
                ElementCaptureService.EXTRA_KIND,
                if (kind == CaptureSessionController.Kind.REPICK) "REPICK" else "CREATE"
            )
            putExtra(ElementCaptureService.EXTRA_STEP_ID, stepId)
        }
        try {
            context.startForegroundService(intent)
            Toast.makeText(context, "请打开目标页，再点悬浮窗「捕获」", Toast.LENGTH_LONG).show()
        } catch (e: Exception) {
            Toast.makeText(context, "无法启动捕获：${e.message}", Toast.LENGTH_SHORT).show()
        }
    }

    LaunchedEffect(capturePending) {
        val result = capturePending ?: return@LaunchedEffect
        if (result.request.caseId != caseId) return@LaunchedEffect
        CaptureSessionController.consume()
        val picked = result.picked
        if (picked == null) {
            snackbarHostState.showSnackbar("已取消捕获")
            return@LaunchedEffect
        }
        when (result.request.kind) {
            CaptureSessionController.Kind.REPICK -> {
                viewModel.applyPickedLocator(result.request.stepId, picked.locator, picked.label)
            }
            CaptureSessionController.Kind.CREATE -> {
                pendingPicked = result.request.afterIndex to picked
            }
        }
    }

    Scaffold(
        snackbarHost = { SnackbarHost(snackbarHostState) },
        topBar = {
            TopAppBar(
                title = { Text(testCase?.name ?: "用例详情") },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.Filled.ArrowBack, contentDescription = "返回")
                    }
                },
                actions = {
                    if (testCase != null) {
                        IconButton(onClick = { viewModel.deleteCase() }) {
                            Icon(
                                Icons.Filled.Delete, contentDescription = "删除",
                                tint = MaterialTheme.colorScheme.error
                            )
                        }
                    }
                }
            )
        },
        floatingActionButton = {
            Column(horizontalAlignment = Alignment.End) {
                if (testCase != null) {
                    Box {
                        SmallFloatingActionButton(
                            onClick = { showAddMenu = true },
                            containerColor = MaterialTheme.colorScheme.secondaryContainer
                        ) {
                            Icon(Icons.Filled.Add, contentDescription = "添加步骤")
                        }
                        DropdownMenu(
                            expanded = showAddMenu,
                            onDismissRequest = { showAddMenu = false }
                        ) {
                            DropdownMenuItem(
                                text = { Text("新建") },
                                onClick = {
                                    showAddMenu = false
                                    startCapture(CaptureSessionController.Kind.CREATE, afterIndex = -1)
                                },
                                leadingIcon = { Icon(Icons.Filled.Add, null) }
                            )
                            HorizontalDivider()
                            listOf(
                                "等待" to ActionType.WAIT,
                                "截图" to ActionType.SCREENSHOT,
                                "验证码操作" to ActionType.HUMAN_GATE,
                                "返回" to ActionType.BACK,
                                "桌面" to ActionType.HOME
                            ).forEach { (label, type) ->
                                DropdownMenuItem(
                                    text = { Text("插入$label") },
                                    onClick = {
                                        showAddMenu = false
                                        viewModel.insertSimpleStep(-1, type)
                                    }
                                )
                            }
                        }
                    }
                    Spacer(Modifier.height(12.dp))
                }
                if (testCase != null && testCase!!.steps.isNotEmpty()) {
                    ExtendedFloatingActionButton(
                        onClick = { onStartReplay(caseId) },
                        icon = { Icon(Icons.Filled.PlayArrow, contentDescription = null) },
                        text = { Text("运行测试") }
                    )
                }
            }
        }
    ) { padding ->
        testCase?.let { tc ->
            val steps = draftSteps.ifEmpty { tc.steps }
            LazyColumn(
                state = listState,
                userScrollEnabled = listScrollEnabled,
                modifier = Modifier
                    .fillMaxSize()
                    .padding(padding),
                contentPadding = PaddingValues(16.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                item {
                    Card(
                        modifier = Modifier.fillMaxWidth(),
                        colors = CardDefaults.cardColors(
                            containerColor = MaterialTheme.colorScheme.surfaceVariant
                        )
                    ) {
                        Column(modifier = Modifier.padding(16.dp)) {
                            Text(
                                text = tc.name,
                                style = MaterialTheme.typography.titleLarge,
                                fontWeight = FontWeight.Bold
                            )
                            if (tc.description.isNotBlank()) {
                                Spacer(modifier = Modifier.height(8.dp))
                                Text(tc.description, style = MaterialTheme.typography.bodyMedium)
                            }
                            Spacer(modifier = Modifier.height(8.dp))
                            Text(
                                "${steps.size} 步 · 点击编辑 · 长按右侧拖柄排序 · + 新建",
                                style = MaterialTheme.typography.labelMedium
                            )
                        }
                    }
                }

                item {
                    Spacer(modifier = Modifier.height(8.dp))
                    Text(
                        text = "测试步骤 (${steps.size})",
                        style = MaterialTheme.typography.titleMedium,
                        fontWeight = FontWeight.Bold
                    )
                }

                itemsIndexed(steps, key = { _, s -> s.id.ifBlank { "i${s.index}" } }) { index, step ->
                    val isDragging = draggingIndex == index
                    val itemOffset = if (isDragging) with(density) { dragOffsetY.toDp() } else 0.dp
                    Card(
                        modifier = Modifier
                            .fillMaxWidth()
                            .offset(y = itemOffset)
                            .clickable(enabled = draggingIndex == null) { editingStep = step },
                        colors = CardDefaults.cardColors(
                            containerColor = if (isDragging) {
                                MaterialTheme.colorScheme.primaryContainer
                            } else {
                                MaterialTheme.colorScheme.surface
                            }
                        ),
                        elevation = CardDefaults.cardElevation(
                            defaultElevation = if (isDragging) 6.dp else 1.dp
                        )
                    ) {
                        Row(
                            modifier = Modifier
                                .fillMaxWidth()
                                .padding(12.dp),
                            verticalAlignment = Alignment.CenterVertically
                        ) {
                            Surface(
                                color = MaterialTheme.colorScheme.primaryContainer,
                                shape = MaterialTheme.shapes.small
                            ) {
                                Text(
                                    text = "${index + 1}",
                                    modifier = Modifier.padding(horizontal = 8.dp, vertical = 4.dp),
                                    style = MaterialTheme.typography.labelMedium,
                                    fontWeight = FontWeight.Bold
                                )
                            }
                            Spacer(modifier = Modifier.width(12.dp))
                            Column(modifier = Modifier.weight(1f)) {
                                Text(
                                    text = "${actionLabelZh(step.action)} — ${step.description.ifEmpty { step.action.name }}",
                                    style = MaterialTheme.typography.bodyMedium,
                                    fontWeight = FontWeight.Medium
                                )
                                val sub = buildList {
                                    if (step.locator.text.isNotBlank()) add("text=\"${step.locator.text}\"")
                                    if (step.inputText.isNotBlank()) add("输入: ${step.inputText}")
                                    if (step.assertText.isNotBlank()) add("断言: ${step.assertText}")
                                    if (step.optional) add("可选")
                                }.joinToString(" · ")
                                if (sub.isNotBlank()) {
                                    Text(
                                        text = sub,
                                        style = MaterialTheme.typography.bodySmall,
                                        color = MaterialTheme.colorScheme.outline,
                                        fontFamily = FontFamily.Monospace,
                                        maxLines = 2
                                    )
                                }
                            }
                            Icon(
                                Icons.Filled.DragHandle,
                                contentDescription = "长按拖动排序",
                                modifier = Modifier
                                    .size(36.dp)
                                    .pointerInput(steps.size, index) {
                                        detectDragGesturesAfterLongPress(
                                            onDragStart = {
                                                listScrollEnabled = false
                                                draggingIndex = index
                                                dragOffsetY = 0f
                                            },
                                            onDragCancel = {
                                                listScrollEnabled = true
                                                draggingIndex = null
                                                dragOffsetY = 0f
                                                draftSteps = tc.steps
                                            },
                                            onDragEnd = {
                                                listScrollEnabled = true
                                                val from = draggingIndex
                                                draggingIndex = null
                                                dragOffsetY = 0f
                                                if (from != null) {
                                                    viewModel.reorderSteps(draftSteps)
                                                }
                                            },
                                            onDrag = { change, dragAmount ->
                                                change.consume()
                                                dragOffsetY += dragAmount.y
                                                val from = draggingIndex ?: return@detectDragGesturesAfterLongPress
                                                val threshold = with(density) { 40.dp.toPx() }
                                                if (dragOffsetY > threshold && from < draftSteps.lastIndex) {
                                                    draftSteps = draftSteps.toMutableList().also {
                                                        val item = it.removeAt(from)
                                                        it.add(from + 1, item)
                                                    }
                                                    draggingIndex = from + 1
                                                    dragOffsetY -= threshold
                                                } else if (dragOffsetY < -threshold && from > 0) {
                                                    draftSteps = draftSteps.toMutableList().also {
                                                        val item = it.removeAt(from)
                                                        it.add(from - 1, item)
                                                    }
                                                    draggingIndex = from - 1
                                                    dragOffsetY += threshold
                                                }
                                            }
                                        )
                                    },
                                tint = MaterialTheme.colorScheme.onSurfaceVariant
                            )
                        }
                    }
                }

                item {
                    Spacer(modifier = Modifier.height(16.dp))
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Text(
                            text = "运行记录",
                            style = MaterialTheme.typography.titleMedium,
                            fontWeight = FontWeight.Bold,
                            modifier = Modifier.weight(1f)
                        )
                        TextButton(onClick = { onNavigateToHistory(caseId) }) {
                            Text("查看全部")
                            Icon(
                                Icons.AutoMirrored.Filled.ArrowForward,
                                contentDescription = null,
                                modifier = Modifier.size(16.dp)
                            )
                        }
                    }
                }
                if (runHistory.isNotEmpty()) {
                    items(runHistory.take(3).size) { idx ->
                        val run = runHistory[idx]
                        Card(
                            modifier = Modifier
                                .fillMaxWidth()
                                .clickable { onNavigateToHistory(caseId) }
                        ) {
                            Row(
                                modifier = Modifier.padding(12.dp),
                                verticalAlignment = Alignment.CenterVertically
                            ) {
                                Icon(
                                    if (run.success) Icons.Filled.CheckCircle else Icons.Filled.Cancel,
                                    null,
                                    tint = if (run.success) MaterialTheme.colorScheme.primary
                                    else MaterialTheme.colorScheme.error
                                )
                                Spacer(modifier = Modifier.width(12.dp))
                                Text(
                                    if (run.success) "通过" else "失败",
                                    modifier = Modifier.weight(1f)
                                )
                                Text(
                                    "${run.passedSteps}/${run.totalSteps}",
                                    style = MaterialTheme.typography.labelSmall
                                )
                            }
                        }
                    }
                }
                item { Spacer(modifier = Modifier.height(120.dp)) }
            }
        } ?: run {
            Box(
                Modifier.fillMaxSize().padding(padding),
                contentAlignment = Alignment.Center
            ) {
                if (deleted) Text("用例已删除") else CircularProgressIndicator()
            }
        }
    }

    editingStep?.let { step ->
        val stepIndex = testCase?.steps?.indexOfFirst { it.id == step.id } ?: -1
        StepEditSheet(
            step = step,
            onDismiss = { editingStep = null },
            onSave = {
                viewModel.updateStep(it)
                editingStep = null
            },
            onDelete = {
                confirmDeleteStepId = step.id
                editingStep = null
            },
            onInsertSimple = { action ->
                viewModel.insertSimpleStep(stepIndex, action)
                editingStep = null
            },
            onInsertWithPick = {
                startCapture(
                    CaptureSessionController.Kind.CREATE,
                    afterIndex = stepIndex
                )
            },
            onRepick = {
                startCapture(
                    CaptureSessionController.Kind.REPICK,
                    stepId = step.id
                )
            }
        )
    }

    pendingPicked?.let { (afterIndex, picked) ->
        ActionPickDialog(
            title = "对「${picked.label.ifBlank { "已选控件" }}」做什么？",
            onDismiss = { pendingPicked = null },
            onChoose = { action ->
                viewModel.createStepFromPick(afterIndex, action, picked.locator, picked.label)
                pendingPicked = null
            }
        )
    }

    confirmDeleteStepId?.let { sid ->
        AlertDialog(
            onDismissRequest = { confirmDeleteStepId = null },
            title = { Text("删除步骤？") },
            text = { Text("删除后可用「+」重新拾取或插入。") },
            confirmButton = {
                TextButton(onClick = {
                    viewModel.deleteStep(sid)
                    confirmDeleteStepId = null
                }) { Text("删除") }
            },
            dismissButton = {
                TextButton(onClick = { confirmDeleteStepId = null }) { Text("取消") }
            }
        )
    }
}

@Composable
private fun ActionPickDialog(
    title: String,
    onDismiss: () -> Unit,
    onChoose: (ActionType) -> Unit
) {
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(title) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                pickActionChoices().forEach { (label, type) ->
                    TextButton(
                        onClick = { onChoose(type) },
                        modifier = Modifier.fillMaxWidth()
                    ) { Text(label) }
                }
            }
        },
        confirmButton = {
            TextButton(onClick = onDismiss) { Text("取消") }
        }
    )
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun StepEditSheet(
    step: Step,
    onDismiss: () -> Unit,
    onSave: (Step) -> Unit,
    onDelete: () -> Unit,
    onInsertSimple: (ActionType) -> Unit,
    onInsertWithPick: () -> Unit,
    onRepick: () -> Unit
) {
    var description by remember(step.id) { mutableStateOf(step.description) }
    var inputText by remember(step.id) { mutableStateOf(step.inputText) }
    var assertText by remember(step.id) { mutableStateOf(step.assertText) }
    var locatorText by remember(step.id) { mutableStateOf(step.locator.text) }
    var locatorId by remember(step.id) { mutableStateOf(step.locator.resourceId) }
    var locatorDesc by remember(step.id) { mutableStateOf(step.locator.contentDesc) }
    var waitMs by remember(step.id) { mutableStateOf(step.waitDurationMs.toString()) }
    var preWaitMs by remember(step.id) { mutableStateOf(step.preWaitMs.toString()) }
    var maxRetries by remember(step.id) { mutableStateOf(step.maxRetries.toString()) }
    var optional by remember(step.id) { mutableStateOf(step.optional) }
    var assertType by remember(step.id) { mutableStateOf(step.extras.assertType.ifBlank { "contains" }) }
    var saveAs by remember(step.id) { mutableStateOf(step.extras.saveAs) }
    var action by remember(step.id) { mutableStateOf(step.action) }
    var showInsertMenu by remember { mutableStateOf(false) }
    var showActionMenu by remember { mutableStateOf(false) }

    ModalBottomSheet(onDismissRequest = onDismiss) {
        Column(
            Modifier
                .fillMaxWidth()
                .padding(horizontal = 20.dp)
                .padding(bottom = 32.dp)
                .verticalScroll(rememberScrollState()),
            verticalArrangement = Arrangement.spacedBy(10.dp)
        ) {
            Text("编辑步骤", style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)

            Text(
                "动作类型",
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.outline
            )
            AssistChip(
                onClick = { showActionMenu = true },
                label = { Text(actionLabelZh(action)) },
                leadingIcon = { Icon(Icons.Filled.Tune, null, Modifier.size(16.dp)) }
            )
            DropdownMenu(expanded = showActionMenu, onDismissRequest = { showActionMenu = false }) {
                actionChoices().forEach { (label, type) ->
                    DropdownMenuItem(
                        text = { Text(label) },
                        onClick = {
                            action = type
                            showActionMenu = false
                        }
                    )
                }
            }

            OutlinedTextField(
                value = description, onValueChange = { description = it },
                label = { Text("描述") }, modifier = Modifier.fillMaxWidth(), singleLine = true
            )
            OutlinedTextField(
                value = locatorText, onValueChange = { locatorText = it },
                label = { Text("定位 text") }, modifier = Modifier.fillMaxWidth(), singleLine = true
            )
            OutlinedTextField(
                value = locatorId, onValueChange = { locatorId = it },
                label = { Text("resource-id") }, modifier = Modifier.fillMaxWidth(), singleLine = true
            )
            OutlinedTextField(
                value = locatorDesc, onValueChange = { locatorDesc = it },
                label = { Text("content-desc") }, modifier = Modifier.fillMaxWidth(), singleLine = true
            )
            if (action == ActionType.INPUT) {
                OutlinedTextField(
                    value = inputText, onValueChange = { inputText = it },
                    label = { Text("输入文本（支持 {{var}}）") },
                    modifier = Modifier.fillMaxWidth()
                )
            }
            if (action == ActionType.ASSERT || action == ActionType.WAIT_UNTIL ||
                action == ActionType.SCROLL_UNTIL
            ) {
                OutlinedTextField(
                    value = assertText, onValueChange = { assertText = it },
                    label = { Text("断言/等待文本") }, modifier = Modifier.fillMaxWidth()
                )
            }
            if (action == ActionType.ASSERT) {
                OutlinedTextField(
                    value = assertType, onValueChange = { assertType = it },
                    label = { Text("断言类型 contains/equals/visible/not_visible") },
                    modifier = Modifier.fillMaxWidth(), singleLine = true
                )
            }
            if (action == ActionType.EXTRACT_TEXT || action == ActionType.SCAN_QR) {
                OutlinedTextField(
                    value = saveAs, onValueChange = { saveAs = it },
                    label = { Text("保存变量名") }, modifier = Modifier.fillMaxWidth(), singleLine = true
                )
            }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(
                    value = waitMs, onValueChange = { waitMs = it },
                    label = { Text("等待 ms") }, modifier = Modifier.weight(1f), singleLine = true
                )
                OutlinedTextField(
                    value = preWaitMs, onValueChange = { preWaitMs = it },
                    label = { Text("前置等待") }, modifier = Modifier.weight(1f), singleLine = true
                )
                OutlinedTextField(
                    value = maxRetries, onValueChange = { maxRetries = it },
                    label = { Text("重试") }, modifier = Modifier.weight(1f), singleLine = true
                )
            }
            Row(verticalAlignment = Alignment.CenterVertically) {
                Checkbox(checked = optional, onCheckedChange = { optional = it })
                Text("可选步骤（失败不中断）")
            }

            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedButton(onClick = onRepick) { Text("重新捕获定位") }
                OutlinedButton(onClick = { showInsertMenu = true }) { Text("在此后插入") }
                DropdownMenu(expanded = showInsertMenu, onDismissRequest = { showInsertMenu = false }) {
                    DropdownMenuItem(
                        text = { Text("捕获元素后插入…") },
                        onClick = {
                            showInsertMenu = false
                            onInsertWithPick()
                        }
                    )
                    HorizontalDivider()
                    listOf(
                        "等待" to ActionType.WAIT,
                        "截图" to ActionType.SCREENSHOT,
                        "验证码操作" to ActionType.HUMAN_GATE,
                        "返回" to ActionType.BACK,
                        "桌面" to ActionType.HOME
                    ).forEach { (label, type) ->
                        DropdownMenuItem(text = { Text(label) }, onClick = {
                            showInsertMenu = false
                            onInsertSimple(type)
                        })
                    }
                }
            }

            Text(
                "交互类步骤请用「捕获元素后插入」（悬浮窗捕获）；等待/截图等可直接插入。",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.outline
            )

            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween
            ) {
                TextButton(
                    onClick = onDelete,
                    colors = ButtonDefaults.textButtonColors(contentColor = MaterialTheme.colorScheme.error)
                ) { Text("删除") }
                Row {
                    TextButton(onClick = onDismiss) { Text("取消") }
                    Button(onClick = {
                        onSave(
                            step.copy(
                                action = action,
                                description = description,
                                inputText = inputText,
                                assertText = assertText,
                                locator = step.locator.copy(
                                    text = locatorText,
                                    resourceId = locatorId,
                                    contentDesc = locatorDesc
                                ),
                                waitDurationMs = waitMs.toLongOrNull() ?: step.waitDurationMs,
                                preWaitMs = preWaitMs.toLongOrNull() ?: step.preWaitMs,
                                maxRetries = maxRetries.toIntOrNull() ?: step.maxRetries,
                                optional = optional,
                                extras = step.extras.copy(
                                    assertType = assertType,
                                    saveAs = saveAs,
                                    untilAssertText = if (action == ActionType.WAIT_UNTIL ||
                                        action == ActionType.SCROLL_UNTIL
                                    ) assertText else step.extras.untilAssertText
                                )
                            )
                        )
                    }) { Text("保存") }
                }
            }
        }
    }
}

private fun actionChoices(): List<Pair<String, ActionType>> = listOf(
    "点击" to ActionType.TAP,
    "输入" to ActionType.INPUT,
    "滑动" to ActionType.SWIPE,
    "断言" to ActionType.ASSERT,
    "等待" to ActionType.WAIT,
    "等待出现" to ActionType.WAIT_UNTIL,
    "截图" to ActionType.SCREENSHOT,
    "提取文本" to ActionType.EXTRACT_TEXT,
    "滚动直到" to ActionType.SCROLL_UNTIL,
    "扫码" to ActionType.SCAN_QR,
    "自动解验证码" to ActionType.SOLVE_CAPTCHA,
    "验证码操作" to ActionType.HUMAN_GATE,
    "返回" to ActionType.BACK,
    "桌面" to ActionType.HOME,
    "关闭应用" to ActionType.CLOSE_APP
)

private fun pickActionChoices(): List<Pair<String, ActionType>> = listOf(
    "点击" to ActionType.TAP,
    "长按" to ActionType.LONG_PRESS,
    "输入" to ActionType.INPUT,
    "断言" to ActionType.ASSERT,
    "提取文本" to ActionType.EXTRACT_TEXT,
    "等待出现" to ActionType.WAIT_UNTIL,
    "滚动直到" to ActionType.SCROLL_UNTIL
)
