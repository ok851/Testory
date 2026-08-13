# -*- coding: utf-8 -*-
"""Add fetchJobStatus implementation to OkHttpPcSyncClient."""

FILE = r"D:\mkst_baixiang\Python_Code\NewUITestPlatform\NewUITestPlatform\mobile_assistant_apk_v2\core\src\main\java\com\testory\assistant\v2\core\communication\OkHttpPcSyncClient.kt"

with open(FILE, "r", encoding="utf-8") as f:
    content = f.read()

# Add fetchJobStatus after reportJobResult, before "// -- HTTP helpers --"
old = '''        }
    }

    // ── HTTP helpers ──'''

new = '''        }
    }

    override suspend fun fetchJobStatus(jobId: String): JobStatusInfo? {
        if (baseUrl.isEmpty() || jobId.isBlank()) return null
        return withContext(Dispatchers.IO) {
            try {
                val request = buildRequest("$baseUrl/api/mobile/sync/run/$jobId/status") { get() }
                val response = client.newCall(request).execute()
                if (!response.isSuccessful) {
                    return@withContext null
                }
                val body = response.body?.string() ?: return@withContext null
                val obj = json.parseToJsonElement(body).jsonObject
                if (obj["success"]?.jsonPrimitive?.boolean != true) return@withContext null
                JobStatusInfo(
                    jobId = jobId,
                    status = obj["status"]?.jsonPrimitive?.contentOrNull ?: "",
                    shouldAbort = obj["should_abort"]?.jsonPrimitive?.booleanOrNull ?: false,
                    abortReason = obj["abort_reason"]?.jsonPrimitive?.contentOrNull ?: "",
                    error = obj["error"]?.jsonPrimitive?.contentOrNull ?: ""
                )
            } catch (_: Exception) {
                null
            }
        }
    }

    // ── HTTP helpers ──'''

assert old in content, "old not found"
content = content.replace(old, new, 1)

with open(FILE, "w", encoding="utf-8") as f:
    f.write(content)

print("OK: OkHttpPcSyncClient.fetchJobStatus added")
