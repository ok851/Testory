package com.testory.assistant.v2.feature.replay

import android.app.Application
import android.content.Intent
import android.util.Base64
import android.util.Log
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.testory.assistant.v2.core.model.*
import com.testory.assistant.v2.core.repository.CaseRepository
import com.testory.assistant.v2.service.accessibility.AccessibilityServiceHolder
import com.testory.assistant.v2.service.accessibility.AssistantAccessibilityService
import com.testory.assistant.v2.service.accessibility.PhoneExecutionGate
import com.testory.assistant.v2.service.accessibility.ReplaySessionController
import com.testory.assistant.v2.service.foreground.FloatingControlService
import com.testory.assistant.v2.service.foreground.RecorderForegroundService
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.*
import kotlinx.serialization.json.Json
import java.util.UUID
import javax.inject.Inject

/**
 * 回放引擎 — 步间真暂停、maxRetries、变量替换、数据驱动、验证码技能。
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
    private var dataRows: List<Map<String, String>> = emptyList()
    private val runtimeVariables = linkedMapOf<String, String>()

    suspend fun loadCase(caseId: String) {
        val testCase = withContext(Dispatchers.IO) {
            caseRepository.getCase(caseId)
        }
        caseName = testCase?.name ?: ""
        dataRows = testCase?.dataRows.orEmpty()
        steps = testCase?.steps?.mapIndexed { idx, step ->
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
        if (!PhoneExecutionGate.tryAcquire()) {
            _uiState.update {
                it.copy(
                    replayState = ReplayState.FAILED,
                    errorMessage = "本机正在执行 PC 任务，请稍后再试"
                )
            }
            return
        }

        replayJob = viewModelScope.launch(Dispatchers.Default) {
            try {
            ReplaySessionController.reset()
            runtimeVariables.clear()
            _uiState.update {
                it.copy(
                    replayState = ReplayState.RUNNING,
                    currentStep = 0,
                    passedCount = 0,
                    failedCount = 0,
                    elapsedMs = 0,
                    stepResults = emptyList(),
                    errorMessage = ""
                )
            }

            try {
                val notiIntent = Intent(application, RecorderForegroundService::class.java).apply {
                    putExtra(RecorderForegroundService.EXTRA_MODE, "replaying")
                }
                application.startForegroundService(notiIntent)
                val floatIntent = Intent(application, FloatingControlService::class.java).apply {
                    putExtra(FloatingControlService.EXTRA_MODE, "replaying")
                    putExtra(FloatingControlService.EXTRA_TOTAL_STEPS, steps.size)
                    putExtra(FloatingControlService.EXTRA_CURRENT_STEP, 0)
                }
                application.startForegroundService(floatIntent)
                sendReplayProgress(0, steps.size)
            } catch (_: Exception) { }

            val service = AccessibilityServiceHolder.instance
            if (service == null) {
                _uiState.update {
                    it.copy(
                        replayState = ReplayState.FAILED,
                        errorMessage = "无障碍服务未开启，请在设置中开启后重试"
                    )
                }
                stopForegroundServices()
                return@launch
            }

            val startTime = System.currentTimeMillis()
            val results = mutableListOf<StepResult>()
            var passed = 0
            var failed = 0

            leaveTestoryUi(service)
            val left = waitUntilLeftSelf(service, timeoutMs = 4000L)
            if (!left) {
                _uiState.update {
                    it.copy(
                        replayState = ReplayState.FAILED,
                        errorMessage = "无法离开 Testory 界面，回放已中止。请手动回到桌面后重试。"
                    )
                }
                stopForegroundServices()
                return@launch
            }

            val rows = if (dataRows.isEmpty()) listOf(emptyMap()) else dataRows
            var globalIndex = 0
            for ((rowIdx, row) in rows.withIndex()) {
                if (_uiState.value.replayState == ReplayState.CANCELLED) break
                runtimeVariables.putAll(row)
                if (rows.size > 1) {
                    Log.i(TAG, "data row ${rowIdx + 1}/${rows.size}: $row")
                }

                for ((index, step) in steps.withIndex()) {
                    if (_uiState.value.replayState == ReplayState.CANCELLED) break

                    // 真暂停：步间挂起
                    if (ReplaySessionController.isPaused()) {
                        _uiState.update { it.copy(replayState = ReplayState.PAUSED) }
                        ReplaySessionController.awaitIfPaused()
                        if (_uiState.value.replayState == ReplayState.CANCELLED) break
                        _uiState.update { it.copy(replayState = ReplayState.RUNNING) }
                    }

                    globalIndex++
                    _uiState.update {
                        it.copy(
                            currentStep = index + 1,
                            elapsedMs = System.currentTimeMillis() - startTime
                        )
                    }

                    val preWait = if (step.preWaitMs > 0) step.preWaitMs else 500L
                    delay(preWait)

                    ensureTargetPackage(service, step)

                    val substituted = StepVariables.applyToStep(step, runtimeVariables)
                    val enrichedStep = enrichStep(substituted)

                    val result = executeWithRetries(service, enrichedStep)
                    results.add(result)
                    if (result.variables.isNotEmpty()) {
                        runtimeVariables.putAll(result.variables)
                    }

                    Log.i(
                        TAG,
                        "step ${index + 1}/${steps.size} success=${result.success} " +
                            "strategy=${result.actualStrategy} err=${result.errorMessage}"
                    )

                    if (result.success) {
                        passed++
                        // HUMAN_GATE：成功但需等人确认后继续
                        if (enrichedStep.action == ActionType.HUMAN_GATE ||
                            result.actualStrategy == "HUMAN_GATE" ||
                            result.errorMessage == "await_human"
                        ) {
                            ReplaySessionController.requestPause()
                            _uiState.update {
                                it.copy(
                                    replayState = ReplayState.PAUSED,
                                    errorMessage = "验证码操作：请处理后点继续"
                                )
                            }
                            ReplaySessionController.awaitIfPaused()
                            _uiState.update {
                                it.copy(replayState = ReplayState.RUNNING, errorMessage = "")
                            }
                        }
                        delay(400)
                    } else {
                        failed++
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
                            sendReplayProgress(index + 1, steps.size)
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
                    totalSteps = steps.size * rows.size,
                    passedSteps = passed,
                    failedStepIndex = if (allPassed) -1 else results.indexOfFirst { !it.success } + 1,
                    durationMs = elapsed,
                    runAt = startTime,
                    stepResultsJson = stepResultsJson
                )
            )

            if (caseRepository.isPcConnected()) {
                try {
                    val deviceInfo = caseRepository.getDeviceInfo()
                    caseRepository.reportReplayResult(caseId, runId, results, deviceInfo)
                } catch (_: Exception) { }
            }

            stopForegroundServices()
            sendReplayComplete(allPassed)
            ReplaySessionController.reset()
            } finally {
                PhoneExecutionGate.release()
            }
        }
    }

    private suspend fun executeWithRetries(
        service: AssistantAccessibilityService,
        step: Step
    ): StepResult {
        val retries = step.maxRetries.coerceAtLeast(1)
        var last: StepResult? = null
        repeat(retries) { attempt ->
            if (ReplaySessionController.isPaused()) {
                _uiState.update { it.copy(replayState = ReplayState.PAUSED) }
                ReplaySessionController.awaitIfPaused()
                _uiState.update { it.copy(replayState = ReplayState.RUNNING) }
            }
            val result = try {
                when (step.action) {
                    ActionType.SOLVE_CAPTCHA -> executeSolveCaptcha(service, step)
                    ActionType.HUMAN_GATE -> StepResult(
                        success = true,
                        actualStrategy = "HUMAN_GATE",
                        errorMessage = "await_human",
                        stepDescription = step.description
                    )
                    else -> service.executeStep(step)
                }
            } catch (e: Exception) {
                Log.e(TAG, "executeStep crashed", e)
                StepResult(
                    stepIndex = step.index,
                    stepId = step.id,
                    success = false,
                    errorMessage = e.message ?: "executeStep exception",
                    stepDescription = step.description
                )
            }
            last = result
            if (result.success) return result
            if (attempt < retries - 1) {
                delay(400L * (attempt + 1))
            }
        }
        return last ?: StepResult(success = false, errorMessage = "unknown")
    }

    private suspend fun executeSolveCaptcha(
        service: AssistantAccessibilityService,
        step: Step
    ): StepResult {
        val png = withContext(Dispatchers.Default) {
            service.captureScreenshotPng(step.extras.roi)
        }
        if (png == null) {
            return fallbackCaptcha(step, "截屏失败")
        }
        val b64 = Base64.encodeToString(png, Base64.NO_WRAP)
        val solved = try {
            caseRepository.solveCaptcha(b64, step.extras.captchaHint, step.description)
        } catch (e: Exception) {
            return fallbackCaptcha(step, e.message ?: "PC 调用失败")
        }
        if (!solved.success) {
            return fallbackCaptcha(step, solved.error ?: "未识别验证码")
        }

        val dm = application.resources.displayMetrics
        val originX = step.extras.roi?.getOrNull(0) ?: 0
        val originY = step.extras.roi?.getOrNull(1) ?: 0
        when (solved.solutionType.lowercase()) {
            "slider", "curve" -> {
                val y = (originY + (step.extras.roi?.let { (it.getOrNull(3) ?: 0) - (it.getOrNull(1) ?: 0) }
                    ?: 80) / 2).toFloat()
                val x1 = (originX + 40).toFloat()
                val x2 = x1 + solved.distance.coerceAtLeast(40)
                val ok = service.performSwipeSync(x1, y.coerceAtMost(dm.heightPixels - 10f), x2, y)
                return StepResult(
                    success = ok,
                    actualStrategy = "SOLVE_CAPTCHA_${solved.solutionType}",
                    errorMessage = if (ok) "" else "滑块手势失败"
                )
            }
            "click" -> {
                var allOk = true
                for ((px, py) in solved.points) {
                    val ok = service.performClickSync(
                        (originX + px).toFloat(),
                        (originY + py).toFloat()
                    )
                    if (!ok) allOk = false
                    delay(200)
                }
                return StepResult(
                    success = allOk && solved.points.isNotEmpty(),
                    actualStrategy = "SOLVE_CAPTCHA_CLICK",
                    errorMessage = if (allOk) "" else "点击验证码点失败"
                )
            }
            "rotate" -> {
                // 旋转类：降级人工
                return fallbackCaptcha(step, "旋转验证码需人工")
            }
            else -> return fallbackCaptcha(step, "未知验证码类型: ${solved.solutionType}")
        }
    }

    private fun fallbackCaptcha(step: Step, reason: String): StepResult {
        val fb = step.extras.captchaFallback.ifBlank { "human_gate" }
        return if (fb == "human_gate") {
            StepResult(
                success = true,
                actualStrategy = "HUMAN_GATE",
                errorMessage = "await_human",
                stepDescription = "验证码降级人工: $reason"
            )
        } else {
            StepResult(
                success = false,
                errorMessage = "SOLVE_CAPTCHA 失败: $reason",
                actualStrategy = "SOLVE_CAPTCHA_FAIL"
            )
        }
    }

    fun pauseReplay() {
        ReplaySessionController.requestPause()
        _uiState.update { it.copy(replayState = ReplayState.PAUSED) }
    }

    fun resumeReplay() {
        ReplaySessionController.requestResume()
        _uiState.update { it.copy(replayState = ReplayState.RUNNING) }
    }

    fun cancelReplay() {
        ReplaySessionController.requestResume()
        replayJob?.cancel()
        _uiState.update { it.copy(replayState = ReplayState.CANCELLED) }
        stopForegroundServices()
        ReplaySessionController.reset()
        PhoneExecutionGate.release()
    }

    private fun enrichStep(step: Step): Step {
        val hasLocator = !step.locator.isEmpty
        val hasCoordinate = step.screenCoordinate?.isValid == true
        if (hasLocator || hasCoordinate) return step
        val node = step.targetNode
        if (node != null && node.bounds.isValid) {
            return step.copy(
                screenCoordinate = node.bounds.toScreenCoordinate(),
                locationSource = LocationSource.COORDINATE
            )
        }
        return step
    }

    private fun leaveTestoryUi(service: AssistantAccessibilityService) {
        try {
            val home = Intent(Intent.ACTION_MAIN).apply {
                addCategory(Intent.CATEGORY_HOME)
                flags = Intent.FLAG_ACTIVITY_NEW_TASK
            }
            application.startActivity(home)
        } catch (e: Exception) {
            Log.w(TAG, "start HOME intent failed", e)
        }
        try {
            service.performGlobalAction(
                android.accessibilityservice.AccessibilityService.GLOBAL_ACTION_HOME
            )
        } catch (e: Exception) {
            Log.w(TAG, "GLOBAL_ACTION_HOME failed", e)
        }
    }

    private suspend fun waitUntilLeftSelf(
        service: AssistantAccessibilityService,
        timeoutMs: Long
    ): Boolean {
        val deadline = System.currentTimeMillis() + timeoutMs
        while (System.currentTimeMillis() < deadline) {
            val pkg = try {
                service.activeWindowPackage()
            } catch (_: Exception) {
                ""
            }
            if (pkg.isNotBlank() && !pkg.startsWith("com.testory.assistant")) {
                Log.i(TAG, "left Testory, active=$pkg")
                return true
            }
            delay(200)
        }
        Log.w(TAG, "still on Testory after ${timeoutMs}ms, pkg=${service.activeWindowPackage()}")
        return false
    }

    private suspend fun ensureTargetPackage(
        service: AssistantAccessibilityService,
        step: Step
    ) {
        when (step.action) {
            ActionType.TAP, ActionType.LONG_PRESS, ActionType.SWIPE,
            ActionType.HOME, ActionType.BACK, ActionType.WAIT,
            ActionType.OPEN_APP, ActionType.SCREENSHOT,
            ActionType.WAIT_UNTIL, ActionType.SCROLL, ActionType.SCROLL_UNTIL,
            ActionType.PRESS_KEY, ActionType.HUMAN_GATE, ActionType.SCAN_QR,
            ActionType.SOLVE_CAPTCHA, ActionType.REPEAT, ActionType.WHILE -> return
            else -> {}
        }

        val target = step.locator.packageName.ifBlank {
            step.targetNode?.packageName.orEmpty()
        }
        if (target.isBlank()) return
        if (target.startsWith("com.testory.assistant")) return
        val lower = target.lowercase()
        if ("launcher" in lower || target == "com.android.systemui") return

        val current = try {
            service.activeWindowPackage()
        } catch (_: Exception) {
            ""
        }
        if (current == target) return

        Log.i(TAG, "ensureTargetPackage launch $target (current=$current) for ${step.action}")
        if (service.launchPackage(target)) {
            delay(1500)
        }
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

    companion object {
        private const val TAG = "ReplayVM"
    }
}
