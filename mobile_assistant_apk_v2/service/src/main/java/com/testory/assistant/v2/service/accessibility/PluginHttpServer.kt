package com.testory.assistant.v2.service.accessibility

import android.content.Context
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.util.Log
import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedReader
import java.io.File
import java.io.InputStreamReader
import java.io.OutputStreamWriter
import java.net.InetSocketAddress
import java.net.ServerSocket
import java.net.Socket
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

/**
 * 设备端 JSON-RPC HTTP 服务，供 PC 经 adb forward 调用。
 * 兼容 mobile_automation_gateway/plugin_rpc.py：POST /  body = {jsonrpc, id, method, params}
 */
class PluginHttpServer(
    private val context: Context,
    private val serviceProvider: () -> AssistantAccessibilityService?
) {
    private val tag = "PluginHttpServer"
    private val running = AtomicBoolean(false)
    private var serverSocket: ServerSocket? = null
    private var acceptThread: Thread? = null
    private val executor = Executors.newCachedThreadPool()
    private val mainHandler = Handler(Looper.getMainLooper())
    @Volatile var listenPort: Int = 0
        private set

    fun start(preferredPort: Int = 0): Int {
        if (running.get()) return listenPort
        val ss = ServerSocket()
        ss.reuseAddress = true
        ss.bind(InetSocketAddress("127.0.0.1", preferredPort.coerceAtLeast(0)))
        serverSocket = ss
        listenPort = ss.localPort
        running.set(true)
        writePortFile(listenPort)
        acceptThread = Thread({
            while (running.get()) {
                try {
                    val client = ss.accept()
                    executor.execute { handleClient(client) }
                } catch (_: Exception) {
                    if (!running.get()) break
                }
            }
        }, "testory-plugin-rpc").also { it.isDaemon = true; it.start() }
        Log.i(tag, "JSON-RPC listening on 127.0.0.1:$listenPort")
        return listenPort
    }

    fun stop() {
        running.set(false)
        try { serverSocket?.close() } catch (_: Exception) {}
        serverSocket = null
        acceptThread = null
        listenPort = 0
    }

    private fun writePortFile(port: Int) {
        try {
            val dir = context.getExternalFilesDir(null) ?: context.filesDir
            File(dir, "plugin_port.txt").writeText(port.toString())
        } catch (e: Exception) {
            Log.w(tag, "write plugin_port.txt failed: ${e.message}")
        }
        try {
            // 兼容旧路径探测：/sdcard/Android/data/<pkg>/files/plugin_port.txt
            val external = context.getExternalFilesDir(null)
            if (external != null) {
                File(external, "plugin_port.txt").writeText(port.toString())
            }
        } catch (_: Exception) {}
    }

    private fun handleClient(socket: Socket) {
        socket.soTimeout = 15000
        try {
            val input = BufferedReader(InputStreamReader(socket.getInputStream(), Charsets.UTF_8))
            val requestLine = input.readLine() ?: return
            var contentLength = 0
            while (true) {
                val line = input.readLine() ?: break
                if (line.isEmpty()) break
                val lower = line.lowercase()
                if (lower.startsWith("content-length:")) {
                    contentLength = lower.substringAfter(":").trim().toIntOrNull() ?: 0
                }
            }
            val body = CharArray(contentLength.coerceAtLeast(0))
            var read = 0
            while (read < contentLength) {
                val n = input.read(body, read, contentLength - read)
                if (n < 0) break
                read += n
            }
            val raw = String(body, 0, read)
            val responseJson = dispatch(raw)
            val out = OutputStreamWriter(socket.getOutputStream(), Charsets.UTF_8)
            val bytes = responseJson.toByteArray(Charsets.UTF_8)
            out.write("HTTP/1.1 200 OK\r\n")
            out.write("Content-Type: application/json; charset=utf-8\r\n")
            out.write("Content-Length: ${bytes.size}\r\n")
            out.write("Connection: close\r\n\r\n")
            out.write(responseJson)
            out.flush()
        } catch (e: Exception) {
            Log.w(tag, "handleClient error: ${e.message}")
        } finally {
            try { socket.close() } catch (_: Exception) {}
        }
    }

    private fun dispatch(raw: String): String {
        val reqId: Any = JSONObject.NULL
        return try {
            val req = JSONObject(raw.ifBlank { "{}" })
            val id = if (req.has("id")) req.get("id") else JSONObject.NULL
            val method = req.optString("method", "")
            val params = req.optJSONObject("params") ?: JSONObject()
            val result = handleMethod(method, params)
            JSONObject()
                .put("jsonrpc", "2.0")
                .put("id", id)
                .put("result", result)
                .toString()
        } catch (e: Exception) {
            JSONObject()
                .put("jsonrpc", "2.0")
                .put("id", reqId)
                .put("error", JSONObject().put("code", -32000).put("message", e.message ?: "error"))
                .toString()
        }
    }

    private fun handleMethod(method: String, params: JSONObject): JSONObject {
        val svc = serviceProvider()
        return when (method) {
            "ping", "getPort" -> JSONObject()
                .put("ok", true)
                .put("port", listenPort)
                .put("package", context.packageName)

            "getStatus" -> JSONObject()
                .put("reachable", true)
                .put("recording", svc?.sessionState?.value?.isRecording == true)
                .put("package", context.packageName)
                .put("port", listenPort)

            "startRecording" -> {
                val ok = runOnMain { svc?.startRecording() == true }
                JSONObject().put("ok", ok).put("recording_active", ok)
            }

            "stopRecording" -> {
                runOnMain { svc?.stopRecording() }
                JSONObject().put("ok", true).put("recording_active", false)
            }

            "pauseRecording" -> {
                runOnMain { svc?.pauseRecording() }
                JSONObject().put("ok", true)
            }

            "resumeRecording" -> {
                runOnMain { svc?.resumeRecording() }
                JSONObject().put("ok", true)
            }

            "pollSteps" -> {
                // 正式路径：手机本地录制 + LAN sync；RPC poll 恒为空（非正式录制引擎）
                JSONObject()
                    .put("steps", JSONArray())
                    .put("recording_active", svc?.sessionState?.value?.isRecording == true)
                    .put("note", "phone_local_recording_only")
            }

            "getPageSource" -> {
                val tree = runOnMain { svc?.getCurrentUiTree() }
                uiTreeToPageSource(tree)
            }

            "pickAtPoint", "getNodeAt" -> {
                val x = params.optInt("x", 0)
                val y = params.optInt("y", 0)
                val node = runOnMain { svc?.pickNodeAt(x, y) }
                if (node == null) {
                    JSONObject().put("ok", false).put("error", "no_node")
                } else {
                    JSONObject()
                        .put("ok", true)
                        .put("text", node.text)
                        .put("contentDesc", node.contentDescription)
                        .put("resourceId", node.resourceId)
                        .put("className", node.className)
                        .put("packageName", node.packageName)
                        .put("clickable", node.isClickable)
                        .put("bounds", JSONArray()
                            .put(node.bounds.left).put(node.bounds.top)
                            .put(node.bounds.right).put(node.bounds.bottom))
                        .put("centerX", node.bounds.centerX)
                        .put("centerY", node.bounds.centerY)
                }
            }

            "takeScreenshot" -> takeScreenshotResult(svc)

            "tap" -> {
                val x = params.optInt("x", 0).toFloat()
                val y = params.optInt("y", 0).toFloat()
                val ok = runOnMainAwaitGesture { cb ->
                    svc?.performClick(x, y, cb) ?: cb(false)
                }
                JSONObject().put("ok", ok).put("x", x.toInt()).put("y", y.toInt())
            }

            "swipe" -> {
                val x1 = params.optInt("x1", 0).toFloat()
                val y1 = params.optInt("y1", 0).toFloat()
                val x2 = params.optInt("x2", 0).toFloat()
                val y2 = params.optInt("y2", 0).toFloat()
                val path = android.graphics.Path().apply {
                    moveTo(x1, y1)
                    lineTo(x2, y2)
                }
                val ok = runOnMainAwaitGesture { cb ->
                    svc?.performGesture(path, 300, cb) ?: cb(false)
                }
                JSONObject().put("ok", ok)
            }

            "input" -> {
                // 无障碍输入：聚焦后粘贴文本能力有限，返回提示由 PC 侧兜底
                val text = params.optString("text", "")
                JSONObject()
                    .put("ok", text.isNotBlank())
                    .put("text", text)
                    .put("note", "input via a11y; prefer selector focus then type")
            }

            "dismissDialogs" -> JSONObject().put("ok", false).put("error", "not_implemented")

            else -> throw IllegalArgumentException("unknown method: $method")
        }
    }

    private fun uiTreeToPageSource(tree: UiTree?): JSONObject {
        if (tree == null) {
            return JSONObject().put("ok", false).put("error", "no_active_window")
        }
        val nodes = JSONArray()
        val xml = StringBuilder()
        xml.append("<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>\n")
        xml.append("<hierarchy rotation=\"0\">\n")

        fun esc(s: String): String = s
            .replace("&", "&amp;")
            .replace("\"", "&quot;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")

        fun walk(node: UiNode, depth: Int) {
            val bounds = "[${node.bounds.left},${node.bounds.top}][${node.bounds.right},${node.bounds.bottom}]"
            val obj = JSONObject()
                .put("class", node.className)
                .put("text", node.text)
                .put("content-desc", node.contentDescription)
                .put("resource-id", node.resourceId)
                .put("clickable", node.isClickable)
                .put("bounds", bounds)
                .put("depth", depth)
            nodes.put(obj)

            val indent = "  ".repeat(depth + 1)
            xml.append(indent)
            xml.append("<node")
            xml.append(" index=\"0\"")
            xml.append(" text=\"").append(esc(node.text)).append("\"")
            xml.append(" resource-id=\"").append(esc(node.resourceId)).append("\"")
            xml.append(" class=\"").append(esc(node.className)).append("\"")
            xml.append(" content-desc=\"").append(esc(node.contentDescription)).append("\"")
            xml.append(" clickable=\"").append(node.isClickable).append("\"")
            xml.append(" bounds=\"").append(esc(bounds)).append("\"")
            if (node.children.isEmpty()) {
                xml.append(" />\n")
            } else {
                xml.append(">\n")
                node.children.forEach { walk(it, depth + 1) }
                xml.append(indent).append("</node>\n")
            }
        }
        tree.nodes.forEach { walk(it, 0) }
        xml.append("</hierarchy>\n")

        return JSONObject()
            .put("ok", true)
            .put("source", "accessibility")
            .put("node_count", nodes.length())
            .put("nodes", nodes)
            .put("hierarchy", nodes)
            .put("xml", xml.toString())
            .put("page_source", xml.toString())
    }

    private fun takeScreenshotResult(svc: AssistantAccessibilityService?): JSONObject {
        if (svc == null || Build.VERSION.SDK_INT < Build.VERSION_CODES.R) {
            return JSONObject().put("ok", false).put("error", "screenshot_unavailable")
        }
        // AccessibilityService.takeScreenshot 需要 API 30+；此处返回占位，PC 可用 adb screencap 兜底
        return JSONObject().put("ok", false).put("error", "use_adb_screencap")
    }

    private fun <T> runOnMain(block: () -> T): T {
        if (Looper.myLooper() == Looper.getMainLooper()) return block()
        var result: T? = null
        var error: Throwable? = null
        val latch = CountDownLatch(1)
        mainHandler.post {
            try {
                result = block()
            } catch (t: Throwable) {
                error = t
            } finally {
                latch.countDown()
            }
        }
        latch.await(8, TimeUnit.SECONDS)
        error?.let { throw it }
        @Suppress("UNCHECKED_CAST")
        return result as T
    }

    private fun runOnMainAwaitGesture(start: ((Boolean) -> Unit) -> Unit): Boolean {
        val latch = CountDownLatch(1)
        var ok = false
        mainHandler.post {
            try {
                start { success ->
                    ok = success
                    latch.countDown()
                }
            } catch (_: Exception) {
                ok = false
                latch.countDown()
            }
        }
        latch.await(8, TimeUnit.SECONDS)
        return ok
    }
}
