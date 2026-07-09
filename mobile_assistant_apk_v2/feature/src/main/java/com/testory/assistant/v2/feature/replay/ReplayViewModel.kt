package com.testory.assistant.v2.feature.replay

import android.app.Application
import android.content.Intent
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.testory.assistant.v2.core.model.*
import com.testory.assistant.v2.core.repository.CaseRepository
import com.testory.assistant.v2.service.accessibility.AccessibilityServiceHolder
import com.testory.assistant.v2.service.foreground.FloatingControlService
import com.testory.assistant.v2.service.foreground.RecorderForegroundService
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.*
import kotlinx.serialization.json.Json
import java.util.UUID
import javax.inject.Inject

/**
 * 回放引擎 ViewModel — 按步骤顺序执行测试用例。
 * 接入真实的 AssistantAccessibilityService 执行操作。
 */
@HiltViewModel
class ReplayViewModel @Inject constructor(
    private val application: Application,
    private val caseRepository: CaseRepository
) : ViewModel() {

    private val _uiState = MutableStateFlow(ReplayUiState())
    val uiState: StateFlow<ReplayUiState> = _uiState.asStateFlow()

    private var replayJob: Job? = null
    private var steps: List<Step> = emptyList()
    private var caseName: String = ""

    suspend fun loadCase(caseId: String) {
        val testCase = withContext(Dispatchers.IO) {
            caseRepository.getCase(caseId)
        }
        caseName = testCase?.name ?: ""
        steps = testCase?.steps?.mapIndexed { idx, step ->
            // Ensure step has index set for tracking
            if (step.index <= 0) step.copy(index = idx + 1) else step
        } ?: emptyList()
        _uiState.update {
            it.copy(
                testCase = testCase,
                totalSteps = steps.size,
                replayState = ReplayState.IDLE,
                stepResults = emptyList(),
                currentStep = 0,
                passedCount = 0,
                failedCount = 0,
                elapsedMs = 0
            )
        }
    }

    fun startReplay() {
        if (steps.isEmpty()) return
        if (replayJob?.isActive == true) return

        replayJob = viewModelScope.launch(Dispatchers.Default) {
            _uiState.update {
                it.copy(
                    replayState = ReplayState.RUNNING,
                    currentStep = 0,
                    passedCount = 0,
                    failedCount = 0,
                    elapsedMs = 0,
                    stepResults = emptyList()
                )
            }

            // Start foreground notification
            try {
                val notiIntent = Intent(application, RecorderForegroundService::class.java).apply {
                    putExtra(RecorderForegroundService.EXTRA_MODE, "replaying")
                }
                application.startForegroundService(notiIntent)
                val floatIntent = Intent(application, FloatingControlService::class.java).apply {
                    putExtra(FloatingControlService.EXTRA_MODE, "replaying")
                }
                application.startForegroundService(floatIntent)
            } catch (_: Exception) { }

            // Get accessibility service
            val service = AccessibilityServiceHolder.instance
            if (service == null) {
                _uiState.update {
                    it.copy(
                        replayState = ReplayState.FAILED,
                        errorMessage = "无障碍服务未开启，请在设置中开启后重试"
                    )
                }
                return@launch
            }

            val startTime = System.currentTimeMillis()
            val results = mutableListOf<StepResult>()
            var passed = 0
            var failed = 0

            // Go to home screen first so replay context matches recording context
            service.performGlobalAction(android.accessibilityservice.AccessibilityService.GLOBAL_ACTION_HOME)
            kotlinx.coroutines.delay(500)

            for ((index, step) in steps.withIndex()) {
                // Check if cancelled
                if (_uiState.value.replayState == ReplayState.CANCELLED) break

                // Wait before step
                if (step.preWaitMs > 0) {
                    delay(step.preWaitMs)
                }

                // Enrich step with coordinate fallback if needed
                val enrichedStep = enrichStep(step)

                // Execute step via accessibility service
                val result = service.executeStep(enrichedStep)
                results.add(result)

                if (result.success) {
                    passed++
                } else {
                    failed++
                    // Stop on failure unless step is optional
                    if (!step.optional) {
                        _uiState.update {
                            it.copy(
                                replayState = ReplayState.FAILED,
                                currentStep = index + 1,
                                passedCount = passed,
                                failedCount = failed,
                                elapsedMs = System.currentTimeMillis() - startTime,
                                stepResults = results.toList(),
                                errorMessage = result.errorMessage
                            )
                        }
                        // Stop foreground
                        stopForegroundServices()
                        return@launch
                    }
                }

                _uiState.update {
                    it.copy(
                        currentStep = index + 1,
                        passedCount = passed,
                        failedCount = failed,
                        elapsedMs = System.currentTimeMillis() - startTime,
                        stepResults = results.toList()
                    )
                }
                sendReplayProgress(index + 1, steps.size)
            }

            val elapsed = System.currentTimeMillis() - startTime
            val allPassed = failed == 0
            _uiState.update {
                it.copy(
                    replayState = if (allPassed) ReplayState.COMPLETED else ReplayState.FAILED,
                    elapsedMs = elapsed,
                    stepResults = results.toList()
                )
            }

            // Save run result
            val runId = UUID.randomUUID().toString()
            val stepResultsJson = Json.encodeToString(
                kotlinx.serialization.builtins.ListSerializer(StepResult.serializer()),
                results
            )
            val testCase = _uiState.value.testCase
            val caseId = testCase?.id ?: ""
            caseRepository.saveRunResult(
                caseId = caseId,
                caseName = caseName,
                run = RunResultSummary(
                    runId = runId,
                    success = allPassed,
                    totalSteps = steps.size,
                    passedSteps = passed,
                    failedStepIndex = if (allPassed) -1 else results.indexOfFirst { !it.success } + 1,
                    durationMs = elapsed,
                    runAt = startTime,
                    stepResultsJson = stepResultsJson
                )
            )

            // Auto-sync run result to PC if connected
            if (caseRepository.isPcConnected()) {
                try {
                    val deviceInfo = caseRepository.getDeviceInfo()
                    caseRepository.reportReplayResult(caseId, runId, results, deviceInfo)
                } catch (_: Exception) { }
            }

            stopForegroundServices()
            sendReplayComplete(allPassed)
        }
    }

    fun resumeReplay() {
        _uiState.update { it.copy(replayState = ReplayState.RUNNING) }
    }

    fun cancelReplay() {
        replayJob?.cancel()
        _uiState.update { it.copy(replayState = ReplayState.CANCELLED) }
        stopForegroundServices()
    }

    /**
     * 补充步骤的坐标信息。
     * 当 selector 和 coordinate 都为空时，尝试从 targetNode bounds 推导坐标。
     */
    private fun enrichStep(step: Step): Step {
        val hasLocator = !step.locator.isEmpty
        val hasCoordinate = step.screenCoordinate?.isValid == true

        if (hasLocator || hasCoordinate) return step

        // Fallback: try to derive coordinate from targetNode bounds
        val node = step.targetNode
        if (node != null && node.bounds.isValid) {
            val coord = node.bounds.toScreenCoordinate()
            return step.copy(
                screenCoordinate = coord,
                locationSource = LocationSource.COORDINATE
            )
        }

        return step
    }

    private fun stopForegroundServices() {
        try {
            application.stopService(Intent(application, RecorderForegroundService::class.java))
            application.stopService(Intent(application, FloatingControlService::class.java))
        } catch (_: Exception) { }
    }

    private fun sendReplayProgress(current: Int, total: Int) {
        try {
            val intent = Intent(application, FloatingControlService::class.java).apply {
                action = FloatingControlService.ACTION_UPDATE_REPLAY_PROGRESS
                putExtra(FloatingControlService.EXTRA_CURRENT_STEP, current)
                putExtra(FloatingControlService.EXTRA_TOTAL_STEPS, total)
            }
            application.startService(intent)
        } catch (_: Exception) { }
    }

    private fun sendReplayComplete(allPassed: Boolean) {
        try {
            val result = if (allPassed) "回放完成 ✅" else "回放失败 ❌"
            val intent = Intent(application, FloatingControlService::class.java).apply {
                action = FloatingControlService.ACTION_REPLAY_COMPLETE
                putExtra(FloatingControlService.EXTRA_COMPLETE_RESULT, result)
            }
            application.startService(intent)
        } catch (_: Exception) { }
    }
}
