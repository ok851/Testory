package com.testory.assistant.v2.core.communication

import android.content.Context
import android.os.Build
import com.testory.assistant.v2.core.model.*
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.serialization.json.Json
import okhttp3.*
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.IOException
import java.util.concurrent.TimeUnit
import javax.inject.Inject
import javax.inject.Singleton

@Singleton
class OkHttpPcSyncClient @Inject constructor(
    @ApplicationContext private val context: Context
) : PcSyncClient {

    private val json = Json {
        ignoreUnknownKeys = true
        encodeDefaults = true
        prettyPrint = false
    }

    private val client = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS)
        .writeTimeout(30, TimeUnit.SECONDS)
        .build()

    private val _state = MutableStateFlow(PcConnectionState.DISCONNECTED)
    override val state: StateFlow<PcConnectionState> = _state.asStateFlow()

    private var baseUrl: String = ""
    private var deviceToken: String = ""
    private val JSON_MEDIA = "application/json; charset=utf-8".toMediaType()

    private val prefs by lazy {
        context.getSharedPreferences("testory_pc_sync", Context.MODE_PRIVATE)
    }

    private val deviceInfo: DeviceInfo by lazy {
        DeviceInfo(
            deviceId = Build.SERIAL.ifEmpty { Build.MODEL.replace(" ", "_") },
            deviceName = Build.MODEL,
            model = Build.MODEL,
            manufacturer = Build.MANUFACTURER,
            androidVersion = Build.VERSION.RELEASE,
            sdkInt = Build.VERSION.SDK_INT,
            screenWidth = context.resources.displayMetrics.widthPixels,
            screenHeight = context.resources.displayMetrics.heightPixels,
            densityDpi = context.resources.displayMetrics.densityDpi
        )
    }

    init {
        // Restore cached token
        deviceToken = prefs.getString("device_token", "") ?: ""
        val savedHost = prefs.getString("pc_host", "") ?: ""
        val savedPort = prefs.getInt("pc_port", 0)
        if (savedHost.isNotBlank() && savedPort > 0) {
            baseUrl = "http://$savedHost:$savedPort"
        }
    }

    override suspend fun connect(host: String, port: Int): Boolean {
        _state.value = PcConnectionState.CONNECTING
        baseUrl = "http://$host:$port"
        prefs.edit()
            .putString("pc_host", host)
            .putInt("pc_port", port)
            .apply()

        return try {
            withContext(Dispatchers.IO) {
                val request = Request.Builder()
                    .url("$baseUrl/api/ping")
                    .get()
                    .build()
                val response = client.newCall(request).execute()
                if (response.isSuccessful) {
                    _state.value = PcConnectionState.CONNECTED
                    true
                } else {
                    _state.value = PcConnectionState.DISCONNECTED
                    false
                }
            }
        } catch (e: IOException) {
            _state.value = PcConnectionState.DISCONNECTED
            false
        }
    }

    override suspend fun connectWithPairCode(host: String, port: Int, pairCode: String): Boolean {
        _state.value = PcConnectionState.CONNECTING
        baseUrl = "http://$host:$port"
        prefs.edit()
            .putString("pc_host", host)
            .putInt("pc_port", port)
            .apply()

        return try {
            withContext(Dispatchers.IO) {
                // Step 1: 先检查服务器可达性
                val pingRequest = Request.Builder()
                    .url("$baseUrl/api/ping")
                    .get()
                    .build()
                val pingResponse = client.newCall(pingRequest).execute()
                if (!pingResponse.isSuccessful) {
                    _state.value = PcConnectionState.DISCONNECTED
                    return@withContext false
                }

                // Step 2: 服务器可达，发送配对码确认请求
                val pairBody = json.encodeToString(
                    PairConfirmRequest.serializer(),
                    PairConfirmRequest(code = pairCode, device_id = deviceInfo.deviceId)
                )
                val pairRequest = Request.Builder()
                    .url("$baseUrl/api/mobile/sync/pair/confirm")
                    .post(pairBody.toRequestBody(JSON_MEDIA))
                    .build()

                val pairResponse = client.newCall(pairRequest).execute()
                if (!pairResponse.isSuccessful) {
                    _state.value = PcConnectionState.DISCONNECTED
                    return@withContext false
                }

                val respBody = pairResponse.body?.string() ?: ""
                val pairResult = json.decodeFromString(PairConfirmResponse.serializer(), respBody)
                if (!pairResult.success || pairResult.device_token.isNullOrBlank()) {
                    _state.value = PcConnectionState.DISCONNECTED
                    return@withContext false
                }

                // Save token
                deviceToken = pairResult.device_token
                prefs.edit().putString("device_token", deviceToken).apply()

                _state.value = PcConnectionState.CONNECTED
                true
            }
        } catch (e: IOException) {
            _state.value = PcConnectionState.DISCONNECTED
            false
        }
    }

    override suspend fun disconnect() {
        _state.value = PcConnectionState.DISCONNECTED
        deviceToken = ""
        prefs.edit().remove("device_token").apply()
    }

    override suspend fun getDeviceToken(): String? {
        return deviceToken.ifBlank { null }
    }

    override suspend fun pushCase(testCase: TestCase): SyncResult {
        return post("/api/mobile/sync/cases/push", json.encodeToString(TestCase.serializer(), testCase))
    }

    override suspend fun pullCase(caseId: String): TestCase? {
        return try {
            val resp = get("/api/mobile/sync/cases/$caseId/bundle")
            resp?.let { body ->
                val bundle = json.decodeFromString(CaseBundleResponse.serializer(), body)
                bundle.case
            }
        } catch (_: Exception) { null }
    }

    override suspend fun pullAllCases(): List<TestCase> {
        return try {
            val resp = get("/api/mobile/sync/cases")
            if (resp != null) {
                val listResp = json.decodeFromString(CaseListResponse.serializer(), resp)
                listResp.cases
            } else emptyList()
        } catch (_: Exception) { emptyList() }
    }

    override suspend fun pushStep(step: Step): SyncResult {
        return post("/api/mobile/sync/steps", json.encodeToString(Step.serializer(), step))
    }

    override suspend fun pullSteps(caseId: String): List<Step> {
        return try {
            val resp = get("/api/mobile/sync/cases/$caseId/bundle")
            if (resp != null) {
                val bundle = json.decodeFromString(CaseBundleResponse.serializer(), resp)
                bundle.steps ?: emptyList()
            } else emptyList()
        } catch (_: Exception) { emptyList() }
    }

    override suspend fun reportReplayResult(runId: String, results: List<StepResult>): SyncResult {
        val payload = json.encodeToString(
            RunEventsRequest.serializer(),
            RunEventsRequest(
                runId = runId,
                status = if (results.all { it.success }) "success" else "error",
                results = results
            )
        )
        return post("/api/mobile/sync/run/$runId/events", payload)
    }

    override suspend fun getDeviceInfo(): DeviceInfo = deviceInfo

    // ── HTTP helpers ──

    private fun buildRequest(url: String, builder: Request.Builder.() -> Request.Builder): Request {
        val b = Request.Builder().url(url)
        val withAuth = if (deviceToken.isNotBlank()) {
            b.addHeader("X-Mobile-Device-Token", deviceToken)
        } else b
        return builder(withAuth).build()
    }

    private suspend fun get(path: String): String? = withContext(Dispatchers.IO) {
        if (baseUrl.isEmpty()) return@withContext null
        try {
            val request = buildRequest("$baseUrl$path") { get() }
            val response = client.newCall(request).execute()
            if (response.isSuccessful) response.body?.string() else null
        } catch (_: Exception) { null }
    }

    private suspend fun post(path: String, body: String): SyncResult = withContext(Dispatchers.IO) {
        if (baseUrl.isEmpty()) return@withContext SyncResult.NetworkUnavailable
        try {
            val requestBody = body.toRequestBody(JSON_MEDIA)
            val request = buildRequest("$baseUrl$path") { post(requestBody) }
            val response = client.newCall(request).execute()
            if (response.isSuccessful) {
                val respBody = response.body?.string() ?: ""
                val syncResp = json.decodeFromString(SyncResponse.serializer(), respBody)
                if (syncResp.success) SyncResult.Success
                else SyncResult.Error(syncResp.message)
            } else {
                SyncResult.Error("HTTP ${response.code}: ${response.message}", response.code)
            }
        } catch (e: IOException) {
            SyncResult.Error(e.message ?: "Network error")
        }
    }
}

// ── Pairing DTOs ──
@kotlinx.serialization.Serializable
data class PairConfirmRequest(val code: String, val device_id: String)

@kotlinx.serialization.Serializable
data class PairConfirmResponse(
    val success: Boolean,
    val device_token: String? = null,
    val error: String? = null
)

@kotlinx.serialization.Serializable
data class CaseBundleResponse(
    val success: Boolean,
    val `case`: TestCase? = null,
    val steps: List<Step>? = null
)

@kotlinx.serialization.Serializable
data class RunEventsRequest(
    val runId: String,
    val status: String,
    val results: List<StepResult>
)
