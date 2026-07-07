package com.testory.assistant.v2.feature.settings

import android.content.Context
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.testory.assistant.v2.core.communication.PcSyncClient
import com.testory.assistant.v2.core.util.AndroidUtils
import dagger.hilt.android.lifecycle.HiltViewModel
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import javax.inject.Inject

data class SettingsUiState(
    val accessibilityEnabled: Boolean = false,
    val overlayGranted: Boolean = false,
    val pcAddress: String = "192.168.1.100",
    val pcPort: String = "5000",
    val connectionStatus: ConnectionStatus = ConnectionStatus.DISCONNECTED,
    val connectionError: String? = null,
    val appVersion: String = "2.0.0",
    val recordingQuality: RecordingQuality = RecordingQuality.HIGH,
    val enableSound: Boolean = true,
    val enableVibration: Boolean = true,
    val enableOfflineMode: Boolean = false
)

enum class ConnectionStatus { DISCONNECTED, CONNECTING, CONNECTED, ERROR }

enum class RecordingQuality(val label: String, val fps: Int) {
    LOW("省流量 (480p, 5fps)", 5),
    MEDIUM("均衡 (720p, 15fps)", 15),
    HIGH("高清 (1080p, 30fps)", 30)
}

@HiltViewModel
class SettingsViewModel @Inject constructor(
    @ApplicationContext private val appContext: Context,
    private val pcSyncClient: PcSyncClient
) : ViewModel() {

    private val prefs by lazy {
        appContext.getSharedPreferences("testory_assistant_settings", Context.MODE_PRIVATE)
    }

    private val _uiState = MutableStateFlow(
        SettingsUiState(
            pcAddress = prefs.getString("pc_address", "192.168.1.100") ?: "192.168.1.100",
            pcPort = prefs.getString("pc_port", "5000") ?: "5000"
        )
    )
    val uiState: StateFlow<SettingsUiState> = _uiState.asStateFlow()

    init {
        viewModelScope.launch {
            pcSyncClient.state.collect { state ->
                val status = when (state) {
                    com.testory.assistant.v2.core.model.PcConnectionState.CONNECTED -> ConnectionStatus.CONNECTED
                    com.testory.assistant.v2.core.model.PcConnectionState.CONNECTING -> ConnectionStatus.CONNECTING
                    com.testory.assistant.v2.core.model.PcConnectionState.RECONNECTING -> ConnectionStatus.ERROR
                    com.testory.assistant.v2.core.model.PcConnectionState.DISCONNECTED -> ConnectionStatus.DISCONNECTED
                }
                _uiState.value = _uiState.value.copy(connectionStatus = status)
            }
        }
    }

    fun refreshPermissions() {
        val accEnabled = AndroidUtils.isAccessibilityServiceEnabled(
            appContext,
            "com.testory.assistant.v2.service.accessibility.AssistantAccessibilityService"
        )
        val overlayGranted = android.provider.Settings.canDrawOverlays(appContext)
        _uiState.value = _uiState.value.copy(
            accessibilityEnabled = accEnabled,
            overlayGranted = overlayGranted
        )
    }

    fun updatePcAddress(address: String) {
        _uiState.value = _uiState.value.copy(pcAddress = address)
    }

    fun updatePcPort(port: String) {
        _uiState.value = _uiState.value.copy(pcPort = port)
    }

    fun setRecordingQuality(quality: RecordingQuality) {
        _uiState.value = _uiState.value.copy(recordingQuality = quality)
    }

    fun toggleSound(enabled: Boolean) {
        _uiState.value = _uiState.value.copy(enableSound = enabled)
    }

    fun toggleVibration(enabled: Boolean) {
        _uiState.value = _uiState.value.copy(enableVibration = enabled)
    }

    fun toggleOfflineMode(enabled: Boolean) {
        _uiState.value = _uiState.value.copy(enableOfflineMode = enabled)
    }

    fun openAccessibilitySettings() {
        AndroidUtils.openAccessibilitySettings(appContext)
    }

    fun connectToPc(pairCode: String = "") {
        val host = _uiState.value.pcAddress.trim()
        val port = _uiState.value.pcPort.trim().toIntOrNull() ?: 5000
        if (host.isBlank()) return

        prefs.edit()
            .putString("pc_address", host)
            .putString("pc_port", port.toString())
            .apply()

        viewModelScope.launch {
            _uiState.value = _uiState.value.copy(
                connectionStatus = ConnectionStatus.CONNECTING,
                connectionError = null
            )
            val success = if (pairCode.isNotBlank()) {
                pcSyncClient.connectWithPairCode(host, port, pairCode)
            } else {
                pcSyncClient.connect(host, port)
            }
            if (!success) {
                val errMsg = if (pairCode.isNotBlank()) {
                    "无法连接到 $host:$port，请检查：\n① PC 端服务是否已启动\n② IP/端口是否正确\n③ 配对码是否有效且未过期（有效期 2 分钟）\n④ 手机与 PC 是否在同一局域网"
                } else {
                    "无法连接到 $host:$port，请检查 PC 端服务是否已启动，IP/端口是否正确"
                }
                _uiState.value = _uiState.value.copy(
                    connectionStatus = ConnectionStatus.ERROR,
                    connectionError = errMsg
                )
            } else {
                _uiState.value = _uiState.value.copy(connectionError = null)
            }
        }
    }

    fun disconnectFromPc() {
        viewModelScope.launch {
            pcSyncClient.disconnect()
        }
    }
}
