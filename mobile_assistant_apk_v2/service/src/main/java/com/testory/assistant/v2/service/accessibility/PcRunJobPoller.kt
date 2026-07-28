package com.testory.assistant.v2.service.accessibility

import android.util.Log
import com.testory.assistant.v2.core.communication.PcConnectionState
import com.testory.assistant.v2.core.communication.PcSyncClient
import com.testory.assistant.v2.core.communication.PendingRunJob
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
 * 轮询 PC sync pending job：extract_otp 本机取通知验证码并上报；
 * 其它 job_kind 暂记录日志（回放仍由用例页主动触发）。
 */
@Singleton
class PcRunJobPoller @Inject constructor(
    private val pcSyncClient: PcSyncClient
) {
    private var job: Job? = null

    fun start(scope: CoroutineScope) {
        if (job?.isActive == true) return
        job = scope.launch(Dispatchers.IO) {
            while (isActive) {
                try {
                    if (pcSyncClient.state.value == PcConnectionState.CONNECTED) {
                        val pending = pcSyncClient.fetchPendingRunJob()
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
        val kind = pending.jobKind.lowercase()
        val isOtp = kind == "extract_otp" || pending.steps.any {
            it.action.equals("extract_otp", ignoreCase = true)
        }
        if (!isOtp) {
            // 不应发生：客户端已按 job_kind=extract_otp 过滤；若仍收到则上报 error 避免卡死
            val payload = JSONObject()
                .put("status", "error")
                .put("success", false)
                .put("error", "unsupported job_kind for background poller: $kind")
                .put("error_code", "MOBILE_JOB_KIND_UNSUPPORTED")
            pcSyncClient.reportJobResult(pending.jobId, payload.toString())
            Log.w(TAG, "unexpected job kind=$kind id=${pending.jobId}")
            return
        }
        val hint = pending.steps.firstOrNull()?.selector_value.orEmpty()
        val pattern = pending.steps.firstOrNull()?.input_value.orEmpty()
        // 短暂等待短信到达
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
        val payload = JSONObject()
            .put("status", if (ok) "success" else "error")
            .put("success", ok)
            .put("sms_otp", otp ?: "")
            .put(
                "variables",
                JSONObject().put("sms_otp", otp ?: "")
            )
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

    companion object {
        private const val TAG = "PcRunJobPoller"
        private const val POLL_MS = 4000L
    }
}
