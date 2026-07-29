package com.testory.assistant.v2.feature.cases

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.testory.assistant.v2.core.model.ActionType
import com.testory.assistant.v2.core.model.LocationSource
import com.testory.assistant.v2.core.model.Locator
import com.testory.assistant.v2.core.model.RunResultSummary
import com.testory.assistant.v2.core.model.Step
import com.testory.assistant.v2.core.model.StepExtras
import com.testory.assistant.v2.core.model.TestCase
import com.testory.assistant.v2.core.repository.CaseRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.*
import kotlinx.coroutines.launch
import java.util.UUID
import javax.inject.Inject

@HiltViewModel
class CaseDetailViewModel @Inject constructor(
    private val caseRepository: CaseRepository
) : ViewModel() {

    private val _case = MutableStateFlow<TestCase?>(null)
    val case: StateFlow<TestCase?> = _case.asStateFlow()

    private val _runHistory = MutableStateFlow<List<RunResultSummary>>(emptyList())
    val runHistory: StateFlow<List<RunResultSummary>> = _runHistory.asStateFlow()

    private val _deleted = MutableStateFlow(false)
    val deleted: StateFlow<Boolean> = _deleted.asStateFlow()

    private val _message = MutableStateFlow<String?>(null)
    val message: StateFlow<String?> = _message.asStateFlow()

    fun loadCase(caseId: String) {
        _deleted.value = false
        viewModelScope.launch {
            caseRepository.observeCase(caseId).collect { testCase ->
                _case.value = testCase
            }
        }
        viewModelScope.launch {
            caseRepository.observeRunHistory(caseId).collect { history ->
                _runHistory.value = history
            }
        }
    }

    fun deleteCase() {
        _case.value?.let { testCase ->
            viewModelScope.launch {
                caseRepository.deleteCase(testCase.id)
                _deleted.value = true
            }
        }
    }

    fun clearMessage() {
        _message.value = null
    }

    fun updateStep(updated: Step) {
        val tc = _case.value ?: return
        val steps = tc.steps.toMutableList()
        val idx = steps.indexOfFirst { it.id == updated.id }
        if (idx < 0) return
        steps[idx] = updated.copy(caseId = tc.id, index = idx + 1)
        persistSteps(tc.id, steps)
    }

    fun deleteStep(stepId: String) {
        val tc = _case.value ?: return
        val steps = tc.steps.filterNot { it.id == stepId }
            .mapIndexed { i, s -> s.copy(index = i + 1) }
        persistSteps(tc.id, steps)
    }

    /**
     * 插入无需拾取的步骤（等待/截图/验证码操作等）。
     * @param afterIndex -1 表示插到末尾；否则插在该下标之后
     */
    fun insertSimpleStep(afterIndex: Int, action: ActionType) {
        val tc = _case.value ?: return
        val steps = tc.steps.toMutableList()
        val insertAt = when {
            afterIndex < 0 -> steps.size
            else -> (afterIndex + 1).coerceIn(0, steps.size)
        }
        val newStep = buildSimpleStep(tc.id, action)
        steps.add(insertAt, newStep)
        persistSteps(tc.id, steps.mapIndexed { i, s -> s.copy(index = i + 1) })
        _message.value = "已插入「${actionLabelZh(action)}」"
    }

    /**
     * 拾取元素后新建/插入步骤。
     * @param afterIndex -1 表示追加到末尾；否则插在该下标之后
     */
    fun createStepFromPick(
        afterIndex: Int,
        action: ActionType,
        locator: Locator,
        label: String
    ) {
        val tc = _case.value ?: return
        val steps = tc.steps.toMutableList()
        val insertAt = when {
            afterIndex < 0 -> steps.size
            else -> (afterIndex + 1).coerceIn(0, steps.size)
        }
        val step = buildPickedStep(tc.id, action, locator, label)
        steps.add(insertAt, step)
        persistSteps(tc.id, steps.mapIndexed { i, s -> s.copy(index = i + 1) })
        _message.value = "已添加「${actionLabelZh(action)}」"
    }

    fun moveStep(fromIndex: Int, toIndex: Int) {
        val tc = _case.value ?: return
        if (fromIndex == toIndex) return
        if (fromIndex !in tc.steps.indices || toIndex !in tc.steps.indices) return
        val steps = tc.steps.toMutableList()
        val item = steps.removeAt(fromIndex)
        steps.add(toIndex, item)
        persistSteps(tc.id, steps.mapIndexed { i, s -> s.copy(index = i + 1) })
    }

    fun reorderSteps(ordered: List<Step>) {
        val tc = _case.value ?: return
        persistSteps(tc.id, ordered.mapIndexed { i, s -> s.copy(caseId = tc.id, index = i + 1) })
    }

    fun applyPickedLocator(stepId: String, locator: Locator, description: String = "") {
        val tc = _case.value ?: return
        val step = tc.steps.find { it.id == stepId } ?: return
        updateStep(
            step.copy(
                locator = locator,
                description = description.ifBlank { step.description },
                locationSource = LocationSource.SELECTOR
            )
        )
        _message.value = "已更新定位"
    }

    private fun buildSimpleStep(caseId: String, action: ActionType): Step {
        val id = UUID.randomUUID().toString()
        return when (action) {
            ActionType.SCREENSHOT -> Step(
                id = id, caseId = caseId, action = ActionType.SCREENSHOT, description = "截图"
            )
            ActionType.WAIT -> Step(
                id = id, caseId = caseId, action = ActionType.WAIT,
                description = "等待", waitDurationMs = 1000L
            )
            ActionType.WAIT_UNTIL -> Step(
                id = id, caseId = caseId, action = ActionType.WAIT_UNTIL,
                description = "等待出现", waitDurationMs = 10000L
            )
            ActionType.HUMAN_GATE -> Step(
                id = id, caseId = caseId, action = ActionType.HUMAN_GATE,
                description = "验证码操作"
            )
            ActionType.BACK -> Step(
                id = id, caseId = caseId, action = ActionType.BACK, description = "返回"
            )
            ActionType.HOME -> Step(
                id = id, caseId = caseId, action = ActionType.HOME, description = "桌面"
            )
            else -> Step(
                id = id, caseId = caseId, action = action,
                description = actionLabelZh(action)
            )
        }
    }

    private fun buildPickedStep(
        caseId: String,
        action: ActionType,
        locator: Locator,
        label: String
    ): Step {
        val id = UUID.randomUUID().toString()
        val desc = label.ifBlank { actionLabelZh(action) }
        return when (action) {
            ActionType.ASSERT -> Step(
                id = id,
                caseId = caseId,
                action = ActionType.ASSERT,
                description = "断言: $desc",
                locator = locator,
                assertText = locator.text.ifBlank { label },
                locationSource = LocationSource.SELECTOR,
                extras = StepExtras(assertType = "contains")
            )
            ActionType.EXTRACT_TEXT -> Step(
                id = id,
                caseId = caseId,
                action = ActionType.EXTRACT_TEXT,
                description = "提取: $desc",
                locator = locator,
                locationSource = LocationSource.SELECTOR,
                extras = StepExtras(saveAs = "extracted_text")
            )
            ActionType.INPUT -> Step(
                id = id,
                caseId = caseId,
                action = ActionType.INPUT,
                description = "输入: $desc",
                locator = locator,
                locationSource = LocationSource.SELECTOR
            )
            ActionType.WAIT_UNTIL, ActionType.SCROLL_UNTIL -> Step(
                id = id,
                caseId = caseId,
                action = action,
                description = "${actionLabelZh(action)}: $desc",
                locator = locator,
                assertText = locator.text.ifBlank { label },
                waitDurationMs = 10000L,
                locationSource = LocationSource.SELECTOR,
                extras = StepExtras(untilAssertText = locator.text.ifBlank { label })
            )
            else -> Step(
                id = id,
                caseId = caseId,
                action = action,
                description = "${actionLabelZh(action)} $desc",
                locator = locator,
                locationSource = LocationSource.SELECTOR
            )
        }
    }

    private fun persistSteps(caseId: String, steps: List<Step>) {
        viewModelScope.launch {
            try {
                caseRepository.updateSteps(caseId, steps)
            } catch (e: Exception) {
                _message.value = "保存失败: ${e.message}"
            }
        }
    }
}

internal fun actionLabelZh(action: ActionType): String = when (action) {
    ActionType.TAP -> "点击"
    ActionType.LONG_PRESS -> "长按"
    ActionType.SWIPE -> "滑动"
    ActionType.INPUT -> "输入"
    ActionType.OPEN_APP -> "打开"
    ActionType.BACK -> "返回"
    ActionType.HOME -> "桌面"
    ActionType.WAIT -> "等待"
    ActionType.ASSERT -> "断言"
    ActionType.SCREENSHOT -> "截图"
    ActionType.EXTRACT_TEXT -> "提取文本"
    ActionType.WAIT_UNTIL -> "等待出现"
    ActionType.CLOSE_APP -> "关闭应用"
    ActionType.PRESS_KEY -> "按键"
    ActionType.SCROLL -> "滚动"
    ActionType.REPEAT -> "重复"
    ActionType.WHILE -> "循环"
    ActionType.SCAN_QR -> "扫码"
    ActionType.SCROLL_UNTIL -> "滚动直到"
    ActionType.SOLVE_CAPTCHA -> "自动解验证码"
    ActionType.HUMAN_GATE -> "验证码操作"
}
