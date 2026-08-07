package com.testory.assistant.v2.core.communication

import android.content.Context
import android.os.Build
import android.provider.Settings
import com.testory.assistant.v2.core.model.*
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
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

    /** AI：慢因在 PC 推理链路；客户端 90s 上限，寒暄/精简 prompt 后通常远低于此 */
    private val aiClient = client.newBuilder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(90, TimeUnit.SECONDS)
        .writeTimeout(30, TimeUnit.SECONDS)
        .callTimeout(100, TimeUnit.SECONDS)
        .build()

    private val _state = MutableStateFlow(PcConnectionState.DISCONNECTED)
    override val state: StateFlow<PcConnectionState> = _state.asStateFlow()

    private var baseUrl: String = ""
    private var deviceToken: String = ""
    private val JSON_MEDIA = "application/json; charset=utf-8".toMediaType()

    private val prefs by lazy {
        context.getSharedPreferences("testory_pc_sync", Context.MODE_PRIVATE)
    }

    private fun resolveStableDeviceId(): String {
        // Build.SERIAL 在 Android 10+ 常为 "unknown"，PC 若按 device_id 过滤会导致永远领不到任务
        val androidId = try {
            Settings.Secure.getString(context.contentResolver, Settings.Secure.ANDROID_ID)
        } catch (_: Exception) {
            null
        }?.trim().orEmpty()
        if (androidId.isNotBlank() && !androidId.equals("unknown", ignoreCase = true)) {
            return androidId
        }
        val serial = try {
            Build.SERIAL
        } catch (_: Exception) {
            ""
        }?.trim().orEmpty()
        if (serial.isNotBlank() && !serial.equals("unknown", ignoreCase = true)) {
            return serial
        }
        return Build.MODEL.replace(" ", "_").ifBlank { "android-device" }
    }

    private val deviceInfo: DeviceInfo by lazy {
        DeviceInfo(
            deviceId = resolveStableDeviceId(),
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
        val stepsPayload = testCase.steps.mapIndexed { idx, s ->
            val hasText = s.locator.text.isNotBlank()
            val hasId = s.locator.resourceId.isNotBlank()
            val hasDesc = s.locator.contentDesc.isNotBlank()
            val hasClass = s.locator.className.isNotBlank()
            val coord = s.screenCoordinate
            val hasCoord = coord != null && coord.isValid

            val selectorType = when {
                hasText -> "text"
                hasId -> "resource_id"
                hasDesc -> "content_desc"
                hasClass -> "class_name"
                hasCoord -> "coordinate"
                else -> "text"
            }
            val selectorValue = when (selectorType) {
                "text" -> s.locator.text
                "resource_id" -> s.locator.resourceId
                "content_desc" -> s.locator.contentDesc
                "class_name" -> s.locator.className
                "coordinate" -> "${coord!!.x},${coord.y}"
                else -> s.locator.text
            }

            val bounds = s.targetNode?.bounds
            val (parsedFrom, parsedTo) = parseSwipeCoords(s.description).let { pair ->
                if (pair.first != null) pair
                else parseSwipePipe(s.inputText)
            }
            val mobileSpec = StepIrCodec.buildMobileSpec(
                step = s,
                viewportCoord = if (hasCoord) listOf(coord!!.x, coord.y) else null,
                bounds = if (bounds != null && bounds.isValid) {
                    listOf(bounds.left, bounds.top, bounds.right, bounds.bottom)
                } else null
            ).copy(
                swipeFrom = parsedFrom,
                swipeTo = parsedTo ?: if (s.action == ActionType.SWIPE && hasCoord) {
                    listOf(coord!!.x, coord.y)
                } else null
            )

            PushStepDto(
                stepOrder = idx + 1,
                action = s.action.toWire(),
                selectorType = selectorType,
                selectorValue = selectorValue,
                inputValue = s.inputText,
                description = s.description,
                automationLayer = "android",
                mobileSpec = mobileSpec,
                assertText = s.assertText,
                waitDurationMs = s.waitDurationMs,
                preWaitMs = s.preWaitMs,
                maxRetries = s.maxRetries,
                optional = s.optional,
                assertType = s.extras.assertType,
                saveAs = s.extras.saveAs
            )
        }
        val pushBody = PushCaseRequest(
            projectId = testCase.projectId.toIntOrNull() ?: 0,
            name = testCase.name,
            description = testCase.description,
            platform = "android",
            steps = stepsPayload,
            remoteCaseId = testCase.remoteId?.toIntOrNull(),
            replace = testCase.remoteId != null
        )
        val bodyJson = json.encodeToString(PushCaseRequest.serializer(), pushBody)
        return post("/api/mobile/sync/cases/push", bodyJson)
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

    override suspend fun pullCaseSummaries(): List<SyncCaseSummary> {
        return try {
            val resp = get("/api/mobile/sync/cases")
            if (resp != null) {
                val jsonEl: JsonElement = json.parseToJsonElement(resp)
                val obj: JsonObject = jsonEl.jsonObject
                val casesArr: JsonArray = obj["cases"]?.jsonArray ?: return emptyList()
                val result = mutableListOf<SyncCaseSummary>()
                for (i in 0 until casesArr.size) {
                    val c = casesArr[i].jsonObject
                    result.add(SyncCaseSummary(
                        id = c["id"]?.toString()?.trim('"') ?: "",
                        name = c["name"]?.toString()?.trim('"') ?: "",
                        projectId = c["project_id"]?.toString()?.trim('"') ?: "",
                        projectName = c["project_name"]?.toString()?.trim('"') ?: ""
                    ))
                }
                result
            } else emptyList()
        } catch (_: Exception) { emptyList() }
    }

    override suspend fun pullCasesByIds(ids: List<String>): List<TestCase> {
        return try {
            val body = json.encodeToString(PullBatchRequest.serializer(), PullBatchRequest(ids))
            val resp = postForBody("/api/mobile/sync/cases/pull-batch", body) ?: return emptyList()
            val jsonEl: JsonElement = json.parseToJsonElement(resp)
            val root = jsonEl.jsonObject
            val success = root["success"]?.jsonPrimitive?.boolean ?: false
            if (!success) return emptyList()
            val bundles = root["bundles"]?.jsonArray ?: return emptyList()

            val result = mutableListOf<TestCase>()
            for (i in 0 until bundles.size) {
                val bundle = bundles[i].jsonObject
                val caseObj = bundle["case"]?.jsonObject ?: continue
                val stepsArr = bundle["steps"]?.jsonArray ?: json.parseToJsonElement("[]").jsonArray

                val caseId = caseObj["id"]?.toString()?.trim('"') ?: ""
                val steps = mutableListOf<Step>()
                for (j in 0 until stepsArr.size) {
                    val s = stepsArr[j].jsonObject
                    steps.add(StepIrCodec.parseFromSyncJson(s, caseId, j))
                }

                val createdAtStr = caseObj["created_at"]?.toString()?.trim('"') ?: ""
                val createdAt = parseIsoToEpoch(createdAtStr)
                val projectIdVal = caseObj["project_id"]?.toString()?.trim('"') ?: ""
                val description = caseObj["description"]?.toString()?.trim('"') ?: ""

                result.add(
                    TestCase(
                        id = caseId,
                        name = caseObj["name"]?.toString()?.trim('"') ?: "",
                        description = description,
                        steps = steps,
                        source = CaseSource.MANUAL,
                        createdAt = createdAt,
                        updatedAt = createdAt,
                        remoteId = caseId,
                        syncStatus = SyncStatus.SYNCED,
                        projectId = projectIdVal,
                        projectName = "",
                        targetPackage = ""
                    )
                )
            }
            result
        } catch (_: Exception) { emptyList() }
    }

    private fun parseIsoToEpoch(iso: String): Long {
        if (iso.isBlank()) return System.currentTimeMillis()
        return try {
            val cleaned = iso.replace("T", " ").substringBefore(".")
            val sdf = java.text.SimpleDateFormat("yyyy-MM-dd HH:mm:ss", java.util.Locale.US)
            sdf.parse(cleaned)?.time ?: System.currentTimeMillis()
        } catch (_: Exception) { System.currentTimeMillis() }
    }

    @Deprecated("Use pullCaseSummaries() + pullCasesByIds()")
    override suspend fun pullAllCases(): List<TestCase> {
        return try {
            val summaries = pullCaseSummaries()
            if (summaries.isEmpty()) return emptyList()
            pullCasesByIds(summaries.map { it.id })
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

    override suspend fun reportReplayResult(
        caseId: String, runId: String,
        deviceModel: String, androidVersion: String, deviceName: String,
        success: Boolean, totalSteps: Int, passedSteps: Int, durationMs: Long,
        results: List<StepResult>
    ): SyncResult {
        val events = results.mapIndexed { idx, r ->
            RunStepEvent(
                step_index = idx + 1,
                step_id = r.stepId,
                action = r.actualStrategy.ifEmpty { "unknown" },
                status = if (r.success) "success" else "error",
                error = r.errorMessage,
                duration_ms = r.durationMs,
                description = r.stepDescription
            )
        }
        val payload = json.encodeToString(
            RunEventsRequest.serializer(),
            RunEventsRequest(
                case_id = caseId,
                run_id = runId,
                device_model = deviceModel,
                android_version = androidVersion,
                device_name = deviceName,
                success = success,
                total_steps = totalSteps,
                passed_steps = passedSteps,
                duration_ms = durationMs,
                results = events
            )
        )
        return post("/api/mobile/sync/run/events", payload)
    }

    override suspend fun getDeviceInfo(): DeviceInfo = deviceInfo

    private fun parseSwipeCoords(description: String): Pair<List<Int>?, List<Int>?> {
        val re = Regex("""\((\d+),\s*(\d+)\)\s*→\s*\((\d+),\s*(\d+)\)""")
        val m = re.find(description) ?: return null to null
        return listOf(m.groupValues[1].toInt(), m.groupValues[2].toInt()) to
            listOf(m.groupValues[3].toInt(), m.groupValues[4].toInt())
    }

    private fun parseSwipePipe(input: String): Pair<List<Int>?, List<Int>?> {
        // 格式: "x1,y1|x2,y2"
        val parts = input.split("|")
        if (parts.size != 2) return null to null
        fun parseXY(s: String): List<Int>? {
            val xy = s.split(",")
            if (xy.size != 2) return null
            val x = xy[0].trim().toIntOrNull() ?: return null
            val y = xy[1].trim().toIntOrNull() ?: return null
            return listOf(x, y)
        }
        return parseXY(parts[0]) to parseXY(parts[1])
    }

    override suspend fun aiGenerateSteps(
        message: String,
        mode: String,
        sessionId: String
    ): AiGenerateResult {
        if (baseUrl.isEmpty()) {
            return AiGenerateResult(success = false, error = "未连接 PC 端")
        }
        return withContext(Dispatchers.IO) {
            try {
                val body = json.encodeToString(
                    AiGenerateRequest.serializer(),
                    AiGenerateRequest(
                        message = message,
                        mode = mode,
                        sessionId = sessionId,
                        agentSessionId = sessionId
                    )
                )
                val requestBody = body.toRequestBody(JSON_MEDIA)
                val request = buildRequest("$baseUrl/api/mobile/sync/ai/generate") {
                    post(requestBody)
                }
                val response = aiClient.newCall(request).execute()
                if (response.isSuccessful) {
                    val respBody = response.body?.string() ?: ""
                    val aiResp = json.decodeFromString(AiGenerateResponse.serializer(), respBody)
                    if (aiResp.success) {
                        val coreSteps = aiResp.steps.map { dto ->
                            com.testory.assistant.v2.core.model.Step(
                                id = "",
                                index = 0,
                                action = ActionType.fromWire(dto.action),
                                description = dto.description,
                                locator = com.testory.assistant.v2.core.model.Locator(
                                    text = dto.selector_value,
                                    contentDesc = "",
                                    resourceId = "",
                                    className = ""
                                ),
                                inputText = dto.input_value
                            )
                        }
                        AiGenerateResult(
                            success = true,
                            caseName = aiResp.case_name ?: "AI生成用例",
                            description = aiResp.description ?: "",
                            expectedResult = aiResp.expected_result ?: "",
                            reply = aiResp.reply ?: aiResp.description ?: "",
                            mode = aiResp.mode ?: mode,
                            steps = coreSteps,
                            provider = aiResp.ai_status?.provider ?: "",
                            model = aiResp.ai_status?.model ?: "",
                            sessionId = aiResp.sessionId.orEmpty(),
                            variables = aiResp.variables,
                            toolsUsed = aiResp.toolsUsed,
                            stepsDigest = aiResp.stepsDigest
                        )
                    } else {
                        AiGenerateResult(
                            success = false,
                            error = aiResp.error ?: "AI 生成失败"
                        )
                    }
                } else {
                    val errBody = response.body?.string() ?: ""
                    AiGenerateResult(
                        success = false,
                        error = "服务器错误 (${response.code}): $errBody"
                    )
                }
            } catch (e: java.net.SocketTimeoutException) {
                AiGenerateResult(
                    success = false,
                    error = "PC 大模型推理超时。请检查 custom_openai 接口是否可达、模型是否过慢；可在 PC 端 AI 配置中换更快模型后重试"
                )
            } catch (e: IOException) {
                AiGenerateResult(success = false, error = "网络错误: ${e.message}")
            } catch (e: Exception) {
                AiGenerateResult(success = false, error = "请求失败: ${e.message}")
            }
        }
    }

    override suspend fun fetchAiStatus(): AiStatusResult {
        if (baseUrl.isEmpty()) {
            return AiStatusResult(success = false, error = "未连接 PC 端")
        }
        return withContext(Dispatchers.IO) {
            try {
                val request = buildRequest("$baseUrl/api/mobile/sync/ai/status") { get() }
                val response = client.newCall(request).execute()
                val respBody = response.body?.string() ?: ""
                if (!response.isSuccessful) {
                    return@withContext AiStatusResult(
                        success = false,
                        error = "服务器错误 (${response.code})"
                    )
                }
                val st = json.decodeFromString(AiStatusResponse.serializer(), respBody)
                AiStatusResult(
                    success = st.success,
                    ready = st.ready,
                    provider = st.provider,
                    model = st.model,
                    message = st.message,
                    error = st.error
                )
            } catch (e: Exception) {
                AiStatusResult(success = false, error = e.message)
            }
        }
    }

    override suspend fun solveCaptcha(
        imageBase64: String,
        hint: String,
        instruction: String
    ): CaptchaSolveResult {
        if (baseUrl.isEmpty()) {
            return CaptchaSolveResult(success = false, error = "未连接 PC 端")
        }
        return withContext(Dispatchers.IO) {
            try {
                val payload = buildJsonObject {
                    put("image_base64", imageBase64)
                    put("captcha_hint", hint)
                    put("instruction", instruction)
                }.toString()
                val requestBody = payload.toRequestBody(JSON_MEDIA)
                val request = buildRequest("$baseUrl/api/mobile/sync/captcha/solve") {
                    post(requestBody)
                }
                val response = aiClient.newCall(request).execute()
                val respBody = response.body?.string() ?: ""
                if (!response.isSuccessful) {
                    return@withContext CaptchaSolveResult(
                        success = false,
                        error = "服务器错误 (${response.code}): $respBody"
                    )
                }
                val root = json.parseToJsonElement(respBody).jsonObject
                val success = root["success"]?.jsonPrimitive?.booleanOrNull == true
                val solution = root["solution"]?.jsonObject
                val type = solution?.get("type")?.jsonPrimitive?.contentOrNull.orEmpty()
                val distance = solution?.get("distance")?.jsonPrimitive?.intOrNull ?: 0
                val angle = solution?.get("angle")?.jsonPrimitive?.intOrNull ?: 0
                val points = mutableListOf<Pair<Int, Int>>()
                solution?.get("points")?.jsonArray?.forEach { p ->
                    val o = p.jsonObject
                    val x = o["x"]?.jsonPrimitive?.intOrNull ?: return@forEach
                    val y = o["y"]?.jsonPrimitive?.intOrNull ?: return@forEach
                    points.add(x to y)
                }
                CaptchaSolveResult(
                    success = success,
                    solutionType = type,
                    distance = distance,
                    angle = angle,
                    points = points,
                    raw = root["raw"]?.jsonPrimitive?.contentOrNull.orEmpty(),
                    error = if (success) null else (root["error"]?.jsonPrimitive?.contentOrNull
                        ?: "验证码未识别")
                )
            } catch (e: Exception) {
                CaptchaSolveResult(success = false, error = e.message)
            }
        }
    }

    override suspend fun fetchPendingRunJob(jobKind: String): PendingRunJob? {
        if (baseUrl.isEmpty() || deviceToken.isEmpty()) return null
        return withContext(Dispatchers.IO) {
            try {
                val q = if (jobKind.isNotBlank()) "?job_kind=${jobKind.trim()}" else ""
                val request = buildRequest("$baseUrl/api/mobile/sync/run/pending$q") { get() }
                val response = client.newCall(request).execute()
                val body = response.body?.string() ?: return@withContext null
                if (!response.isSuccessful) return@withContext null
                val root = json.parseToJsonElement(body).jsonObject
                val success = root["success"]?.jsonPrimitive?.booleanOrNull == true
                val hasJob = root["has_job"]?.jsonPrimitive?.booleanOrNull == true
                val jobId = root["job_id"]?.jsonPrimitive?.contentOrNull.orEmpty()
                if (!success || !hasJob || jobId.isBlank()) return@withContext null
                val kind = root["job_kind"]?.jsonPrimitive?.contentOrNull
                    ?.ifBlank { jobKind.ifBlank { "run_steps" } }
                    ?: jobKind.ifBlank { "run_steps" }
                val caseId = root["case_id"]?.jsonPrimitive?.intOrNull ?: 0
                val stepsArr = root["steps"]?.jsonArray
                val steps = mutableListOf<Step>()
                if (stepsArr != null) {
                    for (i in 0 until stepsArr.size) {
                        val el = stepsArr[i]
                        val obj = el as? JsonObject ?: continue
                        // extract_otp：保留 hint/pattern
                        val actionWire = obj["action"]?.jsonPrimitive?.contentOrNull.orEmpty()
                        if (actionWire.equals("extract_otp", ignoreCase = true)) {
                            steps.add(
                                Step(
                                    id = "otp_$i",
                                    index = i + 1,
                                    action = ActionType.EXTRACT_TEXT, // 占位；执行仍看 jobKind
                                    description = obj["description"]?.jsonPrimitive?.contentOrNull.orEmpty(),
                                    locator = Locator(
                                        text = obj["selector_value"]?.jsonPrimitive?.contentOrNull.orEmpty()
                                    ),
                                    inputText = obj["input_value"]?.jsonPrimitive?.contentOrNull.orEmpty(),
                                    extras = StepExtras(
                                        saveAs = obj["store_as"]?.jsonPrimitive?.contentOrNull
                                            ?.ifBlank { "sms_otp" } ?: "sms_otp"
                                    )
                                )
                            )
                        } else {
                            steps.add(StepIrCodec.parseFromSyncJson(obj, "job_$jobId", i))
                        }
                    }
                }
                PendingRunJob(
                    jobId = jobId,
                    caseId = caseId,
                    jobKind = kind,
                    steps = steps
                )
            } catch (_: Exception) {
                null
            }
        }
    }

    override suspend fun reportJobResult(jobId: String, payloadJson: String): SyncResult {
        if (baseUrl.isEmpty() || jobId.isBlank()) {
            return SyncResult.Error("未连接或缺少 jobId")
        }
        return withContext(Dispatchers.IO) {
            try {
                val requestBody = payloadJson.toRequestBody(JSON_MEDIA)
                val request = buildRequest("$baseUrl/api/mobile/sync/run/$jobId/events") {
                    post(requestBody)
                }
                val response = client.newCall(request).execute()
                if (response.isSuccessful) SyncResult.Success
                else SyncResult.Error("上报失败 (${response.code})")
            } catch (e: IOException) {
                SyncResult.NetworkUnavailable
            } catch (e: Exception) {
                SyncResult.Error(e.message ?: "上报失败")
            }
        }
    }

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

    private suspend fun postForBody(path: String, body: String): String? = withContext(Dispatchers.IO) {
        if (baseUrl.isEmpty()) return@withContext null
        try {
            val requestBody = body.toRequestBody(JSON_MEDIA)
            val request = buildRequest("$baseUrl$path") { post(requestBody) }
            val response = client.newCall(request).execute()
            if (response.isSuccessful) response.body?.string() else null
        } catch (_: Exception) { null }
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
data class BatchPullResponse(
    val success: Boolean,
    val bundles: List<BundleItem> = emptyList()
)

@kotlinx.serialization.Serializable
data class BundleItem(
    val `case`: TestCase? = null,
    val steps: List<Step>? = null
)

@kotlinx.serialization.Serializable
data class PushCaseRequest(
    @kotlinx.serialization.SerialName("project_id") val projectId: Int,
    @kotlinx.serialization.SerialName("name") val name: String,
    @kotlinx.serialization.SerialName("description") val description: String = "",
    @kotlinx.serialization.SerialName("platform") val platform: String = "android",
    @kotlinx.serialization.SerialName("steps") val steps: List<PushStepDto> = emptyList(),
    @kotlinx.serialization.SerialName("remote_case_id") val remoteCaseId: Int? = null,
    @kotlinx.serialization.SerialName("replace") val replace: Boolean = true
)

@kotlinx.serialization.Serializable
data class PushStepDto(
    @kotlinx.serialization.SerialName("step_order") val stepOrder: Int,
    @kotlinx.serialization.SerialName("action") val action: String,
    @kotlinx.serialization.SerialName("selector_type") val selectorType: String = "",
    @kotlinx.serialization.SerialName("selector_value") val selectorValue: String = "",
    @kotlinx.serialization.SerialName("input_value") val inputValue: String = "",
    @kotlinx.serialization.SerialName("description") val description: String = "",
    @kotlinx.serialization.SerialName("automation_layer") val automationLayer: String = "android",
    @kotlinx.serialization.SerialName("mobile_spec") val mobileSpec: MobileSpecDto? = null,
    @kotlinx.serialization.SerialName("assert_text") val assertText: String = "",
    @kotlinx.serialization.SerialName("wait_duration_ms") val waitDurationMs: Long = 0,
    @kotlinx.serialization.SerialName("pre_wait_ms") val preWaitMs: Long = 0,
    @kotlinx.serialization.SerialName("max_retries") val maxRetries: Int = 0,
    @kotlinx.serialization.SerialName("optional") val optional: Boolean = false,
    @kotlinx.serialization.SerialName("assert_type") val assertType: String = "",
    @kotlinx.serialization.SerialName("save_as") val saveAs: String = ""
)

@kotlinx.serialization.Serializable
data class MobileSpecDto(
    @kotlinx.serialization.SerialName("viewport_coord") val viewportCoord: List<Int>? = null,
    @kotlinx.serialization.SerialName("bounds") val bounds: List<Int>? = null,
    @kotlinx.serialization.SerialName("resource_id") val resourceId: String? = null,
    @kotlinx.serialization.SerialName("package") val packageName: String? = null,
    @kotlinx.serialization.SerialName("content_desc") val contentDesc: String? = null,
    @kotlinx.serialization.SerialName("class_name") val className: String? = null,
    @kotlinx.serialization.SerialName("text") val text: String? = null,
    @kotlinx.serialization.SerialName("location_source") val locationSource: String? = null,
    @kotlinx.serialization.SerialName("is_webview") val isWebView: Boolean = false,
    @kotlinx.serialization.SerialName("swipe_from") val swipeFrom: List<Int>? = null,
    @kotlinx.serialization.SerialName("swipe_to") val swipeTo: List<Int>? = null,
    @kotlinx.serialization.SerialName("swipe_direction") val swipeDirection: String? = null,
    @kotlinx.serialization.SerialName("assert_text") val assertText: String? = null,
    @kotlinx.serialization.SerialName("wait_duration_ms") val waitDurationMs: Long? = null,
    @kotlinx.serialization.SerialName("pre_wait_ms") val preWaitMs: Long? = null,
    @kotlinx.serialization.SerialName("max_retries") val maxRetries: Int? = null,
    @kotlinx.serialization.SerialName("optional") val optional: Boolean? = null,
    @kotlinx.serialization.SerialName("assert_type") val assertType: String? = null,
    @kotlinx.serialization.SerialName("save_as") val saveAs: String? = null,
    @kotlinx.serialization.SerialName("key_code") val keyCode: String? = null,
    @kotlinx.serialization.SerialName("repeat_max") val repeatMax: Int? = null,
    @kotlinx.serialization.SerialName("until_assert_text") val untilAssertText: String? = null,
    @kotlinx.serialization.SerialName("captcha_hint") val captchaHint: String? = null,
    @kotlinx.serialization.SerialName("captcha_fallback") val captchaFallback: String? = null,
    @kotlinx.serialization.SerialName("roi") val roi: List<Int>? = null,
    @kotlinx.serialization.SerialName("scroll_amount") val scrollAmount: Int? = null,
    @kotlinx.serialization.SerialName("prefer_checkable") val preferCheckable: Boolean? = null
)
