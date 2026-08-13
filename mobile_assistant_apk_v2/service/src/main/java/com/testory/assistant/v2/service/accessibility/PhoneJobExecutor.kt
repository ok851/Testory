package com.testory.assistant.v2.service.accessibility

import android.content.Context
import android.content.Intent
import android.util.Base64
import android.util.Log
import com.testory.assistant.v2.core.model.ActionType
import com.testory.assistant.v2.core.model.Step
import com.testory.assistant.v2.core.model.StepResult
import com.testory.assistant.v2.core.model.StepVariables
import com.testory.assistant.v2.core.communication.PcSyncClient
import com.testory.assistant.v2.core.repository.CaseRepository
import com.testory.assistant.v2.service.foreground.FloatingControlService
import com.testory.assistant.v2.service.foreground.RecorderForegroundService
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import org.json.JSONArray
import org.json.JSONObject
import javax.inject.Inject
import javax.inject.Singleton

/**
 * PC 下发 run_steps 的本机执行引擎（无障碍 + 变量 + 重试 + 验证码）。
 */
@Singleton
class PhoneJobExecutor @Inject constructor(
    @ApplicationContext private val context: Context,
    private val caseRepository: CaseRepository,
    private val pcSyncClient: PcSyncClient
) {
    data class RunOutcome(
        val success: Boolean,
        val variables: Map<String, String>,
        val results: List<StepResult>,
        val error: String = ""
    )

    suspend fun executeSteps(steps: List<Step>, jobId: String = ""): RunOutcome {
        val service = AccessibilityServiceHolder.instance
            ?: return RunOutcome(false, emptyMap(), emptyList(), "无障碍服务未开启")

        ReplaySessionController.reset()
        startForeground(steps.size)
        try {
            leaveTestoryUiIfNeeded(service)

            val runtimeVariables = linkedMapOf<String, String>()
            val results = mutableListOf<StepResult>()
            var failed = 0
            for ((index, step) in steps.withIndex()) {
                // ── PC 取消检测：每步前轮询 job 状态 ──
                if (jobId.isNotBlank()) {
                    val status = try { pcSyncClient.fetchJobStatus(jobId) } catch (_: Exception) { null }
                    if (status != null && status.shouldAbort) {
                        Log.w(TAG, "job=$jobId aborted by PC: ${status.abortReason}")
                        stopForeground()
                        return RunOutcome(
                            success = false,
                            variables = runtimeVariables.toMap(),
                            results = results,
                            error = "PC 端已取消任务: ${status.abortReason.ifBlank { "user_pause" }}"
                        )
                    }
                }

                updateProgress(index + 1, steps.size)
                val preWait = if (step.preWaitMs > 0) step.preWaitMs else 500L
                delay(preWait)
                ensureTargetPackage(service, step)

                val substituted = StepVariables.applyToStep(step, runtimeVariables)
                val enriched = enrichStep(substituted)
                val raw = executeWithRetries(service, enriched)
                val result = raw.copy(
                    stepIndex = if (enriched.index > 0) enriched.index else index + 1,
                    stepId = enriched.id,
                    stepDescription = enriched.description.ifBlank {
                        raw.stepDescription.ifBlank { resultStepDesc(enriched) }
                    }
                )
                results.add(result)
                if (result.variables.isNotEmpty()) {
                    runtimeVariables.putAll(result.variables)
                }

                if (result.success) {
                    if (isHumanGate(enriched, result)) {
                        awaitHumanGate(timeoutMs = HUMAN_GATE_TIMEOUT_MS)
                    }
                    delay(400)
                } else {
                    failed++
                    if (!step.optional) {
                        stopForeground()
                        return RunOutcome(
                            success = false,
                            variables = runtimeVariables.toMap(),
                            results = results,
                            error = result.errorMessage.ifBlank { "步骤失败" }
                        )
                    }
                }
            }

            stopForeground()
            return RunOutcome(
                success = failed == 0,
                variables = runtimeVariables.toMap(),
                results = results,
                error = if (failed == 0) "" else "部分可选步骤失败"
            )
        } catch (e: Exception) {
            Log.e(TAG, "executeSteps failed", e)
            stopForeground()
            return RunOutcome(false, emptyMap(), emptyList(), e.message ?: "执行异常")
        } finally {
            ReplaySessionController.reset()
        }
    }

    fun toReportPayload(outcome: RunOutcome): JSONObject {
        val vars = JSONObject()
        outcome.variables.forEach { (k, v) -> vars.put(k, v) }
        val arr = JSONArray()
        outcome.results.forEach { r ->
            val storeAs = r.variables.keys.firstOrNull().orEmpty()
            val extracted = r.variables.values.firstOrNull().orEmpty()
            val rowVars = JSONObject()
            r.variables.forEach { (k, v) -> rowVars.put(k, v) }
            arr.put(
                JSONObject()
                    .put("stepIndex", r.stepIndex)
                    .put("stepId", r.stepId)
                    .put("action", r.actualStrategy)
                    .put("success", r.success)
                    .put("errorMessage", r.errorMessage)
                    .put("stepDescription", r.stepDescription)
                    .put("store_as", storeAs)
                    .put("extracted_text", extracted)
                    .put("extractedText", extracted)
                    .put("variables", rowVars)
            )
        }
        return JSONObject()
            .put("status", if (outcome.success) "success" else "error")
            .put("success", outcome.success)
            .put("variables", vars)
            .put("results", arr)
            .put("error", outcome.error)
    }

    private suspend fun executeWithRetries(
        service: AssistantAccessibilityService,
        step: Step
    ): StepResult {
        val retries = step.maxRetries.coerceAtLeast(1)
        var last: StepResult? = null
        repeat(retries) { attempt ->
            if (ReplaySessionController.isPaused()) {
                awaitHumanGate(timeoutMs = HUMAN_GATE_TIMEOUT_MS)
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
                StepResult(
                    success = false,
                    errorMessage = e.message ?: "executeStep exception",
                    stepDescription = step.description
                )
            }
            last = result
            if (result.success) return result
            if (attempt < retries - 1) {
                // 元素未找到（NODE_LOOKUP_FAILED）通常意味着广告/弹窗/启动页遮挡，
                // 用更长的间隔等待遮挡层消失（广告通常 3-5 秒）
                val isElementMissing = result.actualStrategy == "NODE_LOOKUP_FAILED"
                        || (result.errorMessage ?: "").contains("Element not found")
                val delayMs = if (isElementMissing) ELEMENT_MISSING_RETRY_DELAY_MS else 400L * (attempt + 1)
                delay(delayMs)
            }
        }
        // 最终失败时截屏记录当前画面，便于后续分析（广告/弹窗/异常页面）
        if (last != null && !last!!.success) {
            try {
                val png = service.captureScreenshotPng(null)
                if (png != null) {
                    val b64 = Base64.encodeToString(png, Base64.NO_WRAP)
                    Log.w(TAG, "step failed, screenshot captured (${png.size} bytes)")
                    last = last!!.copy(
                        errorMessage = (last!!.errorMessage ?: "") +
                                " [screenshot_captured=true, screen_data_len=${b64.length}]"
                    )
                }
            } catch (e: Exception) {
                Log.w(TAG, "failure screenshot capture failed", e)
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
        } ?: return fallbackCaptcha(step, "截屏失败")
        val b64 = Base64.encodeToString(png, Base64.NO_WRAP)
        val solved = try {
            caseRepository.solveCaptcha(b64, step.extras.captchaHint, step.description)
        } catch (e: Exception) {
            return fallbackCaptcha(step, e.message ?: "PC 调用失败")
        }
        if (!solved.success) {
            return fallbackCaptcha(step, solved.error ?: "未识别验证码")
        }
        val dm = context.resources.displayMetrics
        val originX = step.extras.roi?.getOrNull(0) ?: 0
        val originY = step.extras.roi?.getOrNull(1) ?: 0
        return when (solved.solutionType.lowercase()) {
            "slider", "curve" -> {
                val y = (originY + (step.extras.roi?.let { (it.getOrNull(3) ?: 0) - (it.getOrNull(1) ?: 0) }
                    ?: 80) / 2).toFloat()
                val x1 = (originX + 40).toFloat()
                val x2 = x1 + solved.distance.coerceAtLeast(40)
                val ok = service.performSwipeSync(x1, y.coerceAtMost(dm.heightPixels - 10f), x2, y)
                StepResult(
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
                StepResult(
                    success = allOk && solved.points.isNotEmpty(),
                    actualStrategy = "SOLVE_CAPTCHA_CLICK",
                    errorMessage = if (allOk) "" else "点击验证码点失败"
                )
            }
            else -> fallbackCaptcha(step, "验证码类型需人工: ${solved.solutionType}")
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

    private fun isHumanGate(step: Step, result: StepResult): Boolean =
        step.action == ActionType.HUMAN_GATE ||
            result.actualStrategy == "HUMAN_GATE" ||
            result.errorMessage == "await_human"

    private suspend fun awaitHumanGate(timeoutMs: Long) {
        ReplaySessionController.requestPause()
        val resumed = withTimeoutOrNull(timeoutMs) {
            ReplaySessionController.awaitIfPaused()
            true
        }
        if (resumed != true) {
            ReplaySessionController.requestResume()
        }
    }

    private fun enrichStep(step: Step): Step {
        if (!step.locator.isEmpty || step.screenCoordinate?.isValid == true) return step
        val node = step.targetNode
        return if (node != null && node.bounds.isValid) {
            step.copy(
                screenCoordinate = node.bounds.toScreenCoordinate(),
                locationSource = com.testory.assistant.v2.core.model.LocationSource.COORDINATE
            )
        } else step
    }

    /**
     * 仅在当前界面确实停留在 Testory 自身 UI 时才回桌面；
     * 若已在目标 App 或其他第三方 App 中，不做任何操作，直接让步骤在当前上下文执行。
     */
    private suspend fun leaveTestoryUiIfNeeded(service: AssistantAccessibilityService) {
        val currentPkg = try {
            service.activeWindowPackage()
        } catch (_: Exception) {
            ""
        }
        // 不在自身 UI 中，无需处理
        if (currentPkg.isNotBlank() && !currentPkg.startsWith("com.testory.assistant")) {
            return
        }
        Log.i(TAG, "currently in Testory UI ($currentPkg), navigating away before replay")
        try {
            val home = Intent(Intent.ACTION_MAIN).apply {
                addCategory(Intent.CATEGORY_HOME)
                flags = Intent.FLAG_ACTIVITY_NEW_TASK
            }
            context.startActivity(home)
        } catch (_: Exception) { }
        try {
            service.performGlobalAction(
                android.accessibilityservice.AccessibilityService.GLOBAL_ACTION_HOME
            )
        } catch (_: Exception) { }
        // 等待离开自身 UI
        val deadline = System.currentTimeMillis() + 4000L
        while (System.currentTimeMillis() < deadline) {
            val pkg = try { service.activeWindowPackage() } catch (_: Exception) { "" }
            if (pkg.isNotBlank() && !pkg.startsWith("com.testory.assistant")) return
            delay(200)
        }
        Log.w(TAG, "failed to leave Testory UI within timeout")
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
        if (target.isBlank() || target.startsWith("com.testory.assistant")) return
        val lower = target.lowercase()
        if ("launcher" in lower || target == "com.android.systemui") return
        val current = try {
            service.activeWindowPackage()
        } catch (_: Exception) {
            ""
        }
        if (current == target) return
        if (service.launchPackage(target)) delay(1500)
    }

    private fun startForeground(total: Int) {
        try {
            context.startForegroundService(
                Intent(context, RecorderForegroundService::class.java).apply {
                    putExtra(RecorderForegroundService.EXTRA_MODE, "replaying")
                }
            )
            context.startForegroundService(
                Intent(context, FloatingControlService::class.java).apply {
                    putExtra(FloatingControlService.EXTRA_MODE, "replaying")
                    putExtra(FloatingControlService.EXTRA_TOTAL_STEPS, total)
                    putExtra(FloatingControlService.EXTRA_CURRENT_STEP, 0)
                }
            )
        } catch (_: Exception) { }
    }

    private fun updateProgress(current: Int, total: Int) {
        try {
            context.startService(
                Intent(context, FloatingControlService::class.java).apply {
                    action = FloatingControlService.ACTION_UPDATE_REPLAY_PROGRESS
                    putExtra(FloatingControlService.EXTRA_CURRENT_STEP, current)
                    putExtra(FloatingControlService.EXTRA_TOTAL_STEPS, total)
                }
            )
        } catch (_: Exception) { }
    }

    private fun stopForeground() {
        try {
            context.stopService(Intent(context, RecorderForegroundService::class.java))
            context.stopService(Intent(context, FloatingControlService::class.java))
        } catch (_: Exception) { }
    }

    private fun resultStepDesc(step: Step): String =
        step.action.name.lowercase()

    companion object {
        private const val TAG = "PhoneJobExecutor"
        private const val HUMAN_GATE_TIMEOUT_MS = 120_000L
        /** 元素未找到时的重试间隔（广告/弹窗通常 3-5 秒后消失） */
        private const val ELEMENT_MISSING_RETRY_DELAY_MS = 2500L
    }
}
