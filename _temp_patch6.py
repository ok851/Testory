# -*- coding: utf-8 -*-
"""Update PhoneJobExecutor.kt: add cancellation polling + swipe-limit guard."""

FILE = r"D:\mkst_baixiang\Python_Code\NewUITestPlatform\NewUITestPlatform\mobile_assistant_apk_v2\service\src\main\java\com\testory\assistant\v2\service\accessibility\PhoneJobExecutor.kt"

with open(FILE, "r", encoding="utf-8") as f:
    content = f.read()

# 1. Add PcSyncClient import and inject
old_imports = '''import com.testory.assistant.v2.core.repository.CaseRepository
import com.testory.assistant.v2.service.foreground.FloatingControlService
import com.testory.assistant.v2.service.foreground.RecorderForegroundService
import dagger.hilt.android.qualifiers.ApplicationContext'''
new_imports = '''import com.testory.assistant.v2.core.communication.PcSyncClient
import com.testory.assistant.v2.core.repository.CaseRepository
import com.testory.assistant.v2.service.foreground.FloatingControlService
import com.testory.assistant.v2.service.foreground.RecorderForegroundService
import dagger.hilt.android.qualifiers.ApplicationContext'''
assert old_imports in content, "imports not found"
content = content.replace(old_imports, new_imports, 1)

# 2. Inject PcSyncClient into constructor
old_ctor = '''class PhoneJobExecutor @Inject constructor(
    @ApplicationContext private val context: Context,
    private val caseRepository: CaseRepository
) {'''
new_ctor = '''class PhoneJobExecutor @Inject constructor(
    @ApplicationContext private val context: Context,
    private val caseRepository: CaseRepository,
    private val pcSyncClient: PcSyncClient
) {'''
assert old_ctor in content, "ctor not found"
content = content.replace(old_ctor, new_ctor, 1)

# 3. Add jobId parameter and cancellation/swipe-limit logic to executeSteps
old_exec = '''    suspend fun executeSteps(steps: List<Step>): RunOutcome {
        val service = AccessibilityServiceHolder.instance
            ?: return RunOutcome(false, emptyMap(), emptyList(), "无障碍服务未开启")

        ReplaySessionController.reset()
        startForeground(steps.size)
        try {
            leaveTestoryUi(service)
            waitUntilLeftSelf(service, 4000L)

            val runtimeVariables = linkedMapOf<String, String>()
            val results = mutableListOf<StepResult>()
            var failed = 0

            for ((index, step) in steps.withIndex()) {
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
            }'''

new_exec = '''    suspend fun executeSteps(steps: List<Step>, jobId: String = ""): RunOutcome {
        val service = AccessibilityServiceHolder.instance
            ?: return RunOutcome(false, emptyMap(), emptyList(), "无障碍服务未开启")

        ReplaySessionController.reset()
        startForeground(steps.size)
        try {
            leaveTestoryUi(service)
            waitUntilLeftSelf(service, 4000L)

            val runtimeVariables = linkedMapOf<String, String>()
            val results = mutableListOf<StepResult>()
            var failed = 0
            var consecutiveSwipes = 0

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

                // ── 连续滑动保护：防止在桌面/错误页面无限滑动 ──
                if (step.action == ActionType.SWIPE || step.action == ActionType.SCROLL) {
                    consecutiveSwipes++
                    if (consecutiveSwipes > MAX_CONSECUTIVE_SWIPES) {
                        Log.w(TAG, "excessive consecutive swipes ($consecutiveSwipes), aborting")
                        stopForeground()
                        return RunOutcome(
                            success = false,
                            variables = runtimeVariables.toMap(),
                            results = results,
                            error = "连续滑动超过 ${MAX_CONSECUTIVE_SWIPES} 次，疑似在桌面或错误页面循环，已中止"
                        )
                    }
                } else {
                    consecutiveSwipes = 0
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
            }'''

assert old_exec in content, "executeSteps not found"
content = content.replace(old_exec, new_exec, 1)

# 4. Add MAX_CONSECUTIVE_SWIPES constant
old_companion = '''    companion object {
        private const val TAG = "PhoneJobExecutor"
        private const val HUMAN_GATE_TIMEOUT_MS = 120_000L
    }'''
new_companion = '''    companion object {
        private const val TAG = "PhoneJobExecutor"
        private const val HUMAN_GATE_TIMEOUT_MS = 120_000L
        private const val MAX_CONSECUTIVE_SWIPES = 5
    }'''
assert old_companion in content, "companion not found"
content = content.replace(old_companion, new_companion, 1)

with open(FILE, "w", encoding="utf-8") as f:
    f.write(content)

print("OK: PhoneJobExecutor.kt updated")
