package com.testory.assistant.v2.service.accessibility

import android.util.Log
import com.testory.assistant.v2.core.communication.PcSyncClient
import com.testory.assistant.v2.core.communication.PendingRunJob
import com.testory.assistant.v2.core.model.PcConnectionState
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import org.json.JSONObject
import javax.inject.Inject
import javax.inject.Singleton

/**
 * 轮询 PC sync pending job：
 * - extract_otp：通知栏取码并上报 variables.sms_otp
 * - run_steps：本机无障碍回放并上报 results + variables
 */
@Singleton
class PcRunJobPoller @Inject constructor(
    private val pcSyncClient: PcSyncClient,
    private val phoneJobExecutor: PhoneJobExecutor
) {
    private var job: Job? = null

    fun start(scope: CoroutineScope) {
        if (job?.isActive == true) return
        job = scope.launch(Dispatchers.IO) {
            while (isActive) {
                try {
                    if (pcSyncClient.state.value == PcConnectionState.CONNECTED) {
                        // 先 OTP（短任务），再 run_steps，避免互相误吞
                        val pending = pcSyncClient.fetchPendingRunJob("extract_otp")
                            ?: pcSyncClient.fetchPendingRunJob("run_steps")
                        if (pending != null) {
                            handleJob(pending)
                        }
                    }
                } catch (e: Exception) {
                    Log.w(TAG, "poll pending job failed", e)
                }
                delay(POLL_MS)
            }
        }
    }

    fun stop() {
        job?.cancel()
        job = null
    }

    private suspend fun handleJob(pending: PendingRunJob) {
        val kind = pending.jobKind.lowercase().ifBlank { "run_steps" }
        when {
            kind == "extract_otp" -> handleExtractOtp(pending)
            kind == "run_steps" || kind == "run_case" -> handleRunSteps(pending)
            else -> {
                val payload = JSONObject()
                    .put("status", "error")
                    .put("success", false)
                    .put("error", "unsupported job_kind: $kind")
                    .put("error_code", "MOBILE_JOB_KIND_UNSUPPORTED")
                pcSyncClient.reportJobResult(pending.jobId, payload.toString())
                Log.w(TAG, "unsupported job kind=$kind id=${pending.jobId}")
            }
        }
    }

    private suspend fun handleExtractOtp(pending: PendingRunJob) {
        val first = pending.steps.firstOrNull()
        val hint = first?.locator?.text.orEmpty()
        val pattern = first?.inputText.orEmpty()
        var otp: String? = null
        var raw: String? = null
        repeat(24) {
            val pair = NotificationTextBuffer.extractOtp(pattern, hint)
            otp = pair.first
            raw = pair.second
            if (!otp.isNullOrBlank()) return@repeat
            delay(2500)
        }
        val ok = !otp.isNullOrBlank()
        val saveAs = first?.extras?.saveAs?.ifBlank { "sms_otp" } ?: "sms_otp"
        val payload = JSONObject()
            .put("status", if (ok) "success" else "error")
            .put("success", ok)
            .put("sms_otp", otp ?: "")
            .put("variables", JSONObject().put(saveAs, otp ?: "").put("sms_otp", otp ?: ""))
            .put(
                "results",
                org.json.JSONArray().put(
                    JSONObject()
                        .put("stepIndex", 1)
                        .put("action", "extract_otp")
                        .put("success", ok)
                        .put("extractedText", raw ?: "")
                        .put("errorMessage", if (ok) "" else "未在通知中解析到验证码")
                )
            )
            .put("error", if (ok) "" else "未在通知中解析到验证码")
        pcSyncClient.reportJobResult(pending.jobId, payload.toString())
        Log.i(TAG, "extract_otp job=${pending.jobId} ok=$ok")
    }

    private suspend fun handleRunSteps(pending: PendingRunJob) {
        if (pending.steps.isEmpty()) {
            val payload = JSONObject()
                .put("status", "error")
                .put("success", false)
                .put("error", "steps 为空")
            pcSyncClient.reportJobResult(pending.jobId, payload.toString())
            return
        }
        if (!PhoneExecutionGate.tryAcquire()) {
            val payload = JSONObject()
                .put("status", "busy")
                .put("success", false)
                .put("error", "本机正在回放，请稍后重试")
                .put("error_code", "MOBILE_BUSY")
            pcSyncClient.reportJobResult(pending.jobId, payload.toString())
            Log.w(TAG, "run_steps busy → requeue job=${pending.jobId}")
            return
        }
        try {
            Log.i(TAG, "run_steps start job=${pending.jobId} steps=${pending.steps.size}")
            val outcome = phoneJobExecutor.executeSteps(pending.steps)
            val payload = phoneJobExecutor.toReportPayload(outcome)
            pcSyncClient.reportJobResult(pending.jobId, payload.toString())
            Log.i(TAG, "run_steps done job=${pending.jobId} ok=${outcome.success}")
        } catch (e: Exception) {
            Log.e(TAG, "run_steps crashed job=${pending.jobId}", e)
            val payload = JSONObject()
                .put("status", "error")
                .put("success", false)
                .put("error", e.message ?: "执行异常")
            pcSyncClient.reportJobResult(pending.jobId, payload.toString())
        } finally {
            PhoneExecutionGate.release()
        }
    }

    companion object {
        private const val TAG = "PcRunJobPoller"
        private const val POLL_MS = 2500L
    }
}
