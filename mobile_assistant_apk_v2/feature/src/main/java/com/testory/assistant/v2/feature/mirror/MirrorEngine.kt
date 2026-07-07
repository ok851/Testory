package com.testory.assistant.v2.feature.mirror

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.hardware.display.DisplayManager
import android.hardware.display.VirtualDisplay
import android.media.MediaCodec
import android.media.MediaCodecInfo
import android.media.MediaFormat
import android.media.projection.MediaProjection
import android.media.projection.MediaProjectionManager
import android.media.projection.MediaProjectionConfig
import android.os.Build
import android.view.Surface
import com.testory.assistant.v2.core.model.MirrorState
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.*
import java.net.ServerSocket
import java.net.Socket
import java.io.OutputStream
import javax.inject.Inject
import javax.inject.Singleton

/**
 * 投屏引擎 — APK 内置 MediaProjection + MediaCodec 编码 + TCP 推流。
 *
 * 借鉴 EasyClick 方案：屏幕采集、编码、推流全在 APK 同一进程内，
 * 不经过 ADB 隧道，彻底避免 scrcpy 的瓶颈问题。
 *
 * 数据流：
 *   MediaProjection → VirtualDisplay → Surface → MediaCodec(H.264) → TCP Server → PC
 */
@Singleton
class MirrorEngine @Inject constructor(
    @ApplicationContext private val context: Context
) {
    // ── Core components ──
    private var mediaProjection: MediaProjection? = null
    private var videoEncoder: MediaCodec? = null
    private var virtualDisplay: VirtualDisplay? = null
    private var serverSocket: ServerSocket? = null

    // ── State ──
    private val _mirrorState = MutableStateFlow(MirrorState.DISCONNECTED)
    val mirrorState: StateFlow<MirrorState> = _mirrorState.asStateFlow()

    private val qualityConfig = MutableStateFlow(StreamQuality.STANDARD)
    private var scope: CoroutineScope? = null
    private var clientSocket: Socket? = null
    private var outputStream: OutputStream? = null

    // ── Config ──
    companion object {
        const val DEFAULT_PORT = 8178  // EasyClick-style port
        const val TAG = "MirrorEngine"
    }

    /**
     * 请求投屏权限 — 返回需要 startActivityForResult 的 Intent。
     */
    fun requestProjection(): Intent {
        val projectionManager = context.getSystemService(Context.MEDIA_PROJECTION_SERVICE)
                as MediaProjectionManager

        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            projectionManager.createScreenCaptureIntent(
                MediaProjectionConfig.createConfigForDefaultDisplay()
            )
        } else {
            @Suppress("DEPRECATION")
            projectionManager.createScreenCaptureIntent()
        }
    }

    /**
     * 启动投屏流。
     *
     * @param resultCode Activity.RESULT_OK from onActivityResult
     * @param data Intent from onActivityResult
     */
    suspend fun startMirroring(
        resultCode: Int,
        data: Intent,
        port: Int = DEFAULT_PORT
    ): Flow<MirrorEvent> = callbackFlow {
        if (resultCode != Activity.RESULT_OK) {
            _mirrorState.value = MirrorState.ERROR
            send(MirrorEvent.Error("Permission denied"))
            close()
            return@callbackFlow
        }

        try {
            _mirrorState.value = MirrorState.CONNECTING
            val projectionManager = context.getSystemService(Context.MEDIA_PROJECTION_SERVICE)
                    as MediaProjectionManager

            mediaProjection = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
                projectionManager.getMediaProjection(resultCode, data)
            } else {
                @Suppress("DEPRECATION")
                projectionManager.getMediaProjection(resultCode, data)
            }

            // Register callback for projection stop
            mediaProjection?.registerCallback(object : MediaProjection.Callback() {
                override fun onStop() {
                    scope?.launch {
                        _mirrorState.value = MirrorState.DISCONNECTED
                    }
                }
            }, null)

            // Start TCP server
            serverSocket = ServerSocket(port)
            send(MirrorEvent.StatusUpdate("TCP server started on port $port"))

            // Setup video encoder
            setupVideoEncoder()

            // Start streaming
            _mirrorState.value = MirrorState.STREAMING
            send(MirrorEvent.Started)

            // Accept client connection (blocking, on IO thread)
            withContext(Dispatchers.IO) {
                serverSocket?.let { server ->
                    try {
                        clientSocket = server.accept()
                        outputStream = clientSocket?.getOutputStream()
                        send(MirrorEvent.ClientConnected(clientSocket?.inetAddress?.hostAddress ?: "unknown"))
                    } catch (e: Exception) {
                        if (!server.isClosed) {
                            send(MirrorEvent.Error("Client connection failed: ${e.message}"))
                        }
                    }
                }
            }

            // Keep alive
            while (isActive && _mirrorState.value == MirrorState.STREAMING) {
                delay(1000)
            }

        } catch (e: Exception) {
            _mirrorState.value = MirrorState.ERROR
            send(MirrorEvent.Error(e.message ?: "Unknown error"))
        }
    }

    /**
     * 停止投屏。
     */
    suspend fun stopMirroring() {
        _mirrorState.value = MirrorState.DISCONNECTED

        withContext(Dispatchers.Main) {
            virtualDisplay?.release()
            virtualDisplay = null
        }

        withContext(Dispatchers.IO) {
            try {
                outputStream?.close()
                clientSocket?.close()
                serverSocket?.close()
            } catch (_: Exception) { }

            videoEncoder?.stop()
            videoEncoder?.release()
            videoEncoder = null
        }

        mediaProjection?.stop()
        mediaProjection = null
        scope?.cancel()
        scope = null
    }

    /**
     * 设置画质 — 根据网络状况自适应。
     */
    fun setQuality(quality: StreamQuality) {
        qualityConfig.value = quality
    }

    /**
     * 获取当前帧率配置。
     */
    fun getCurrentFps(): Int = when (qualityConfig.value) {
        StreamQuality.HIGH -> 24
        StreamQuality.STANDARD -> 15
        StreamQuality.LOW -> 5
    }

    /**
     * 发送编码后的帧数据到 PC 端。
     */
    suspend fun sendFrame(data: ByteArray) {
        withContext(Dispatchers.IO) {
            try {
                outputStream?.let { stream ->
                    // Prepend frame size (4 bytes big-endian)
                    val size = data.size
                    stream.write(size shr 24)
                    stream.write(size shr 16)
                    stream.write(size shr 8)
                    stream.write(size)
                    stream.write(data)
                    stream.flush()
                }
            } catch (e: Exception) {
                _mirrorState.value = MirrorState.ERROR
            }
        }
    }

    // ── Private ──

    private fun setupVideoEncoder() {
        val quality = qualityConfig.value
        val displayMetrics = context.resources.displayMetrics
        val width = (displayMetrics.widthPixels * quality.scaleFactor).toInt()
        val height = (displayMetrics.heightPixels * quality.scaleFactor).toInt()

        // Ensure dimensions are even (H.264 requirement)
        val adjustedWidth = width.and(1.inv())
        val adjustedHeight = height.and(1.inv())

        val format = MediaFormat.createVideoFormat(
            "video/avc",  // H.264 (MIME_TYPE_AVC constant was removed in API 34)
            adjustedWidth,
            adjustedHeight
        ).apply {
            setInteger(MediaFormat.KEY_COLOR_FORMAT,
                MediaCodecInfo.CodecCapabilities.COLOR_FormatSurface)
            setInteger(MediaFormat.KEY_BIT_RATE, quality.bitRate)
            setInteger(MediaFormat.KEY_FRAME_RATE, getCurrentFps())
            setInteger(MediaFormat.KEY_I_FRAME_INTERVAL, 2)  // I-frame every 2 seconds
        }

        videoEncoder = MediaCodec.createEncoderByType("video/avc")
        videoEncoder?.configure(format, null, null, MediaCodec.CONFIGURE_FLAG_ENCODE)
        val inputSurface = videoEncoder?.createInputSurface()
        videoEncoder?.start()

        // Create VirtualDisplay
        inputSurface?.let { surface ->
            virtualDisplay = mediaProjection?.createVirtualDisplay(
                "TestoryMirror",
                adjustedWidth,
                adjustedHeight,
                displayMetrics.densityDpi,
                DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR,
                surface,
                null,
                null
            )
        }
    }
}

/**
 * 投屏画质配置。
 */
enum class StreamQuality(
    val scaleFactor: Float,
    val bitRate: Int
) {
    /** WiFi/USB 良好环境 */
    HIGH(1.0f, 4_000_000),      // 1080p, 4Mbps
    /** WiFi 一般环境 */
    STANDARD(0.75f, 2_000_000),  // 720p, 2Mbps
    /** 蜂窝网络/低速环境 */
    LOW(0.5f, 500_000),          // 480p, 500Kbps
}

/**
 * 投屏事件。
 */
sealed class MirrorEvent {
    data object Started : MirrorEvent()
    data class StatusUpdate(val message: String) : MirrorEvent()
    data class ClientConnected(val address: String) : MirrorEvent()
    data class FrameSent(val size: Int) : MirrorEvent()
    data class Error(val message: String) : MirrorEvent()
    data object Stopped : MirrorEvent()
}
