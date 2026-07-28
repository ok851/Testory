package com.testory.assistant.v2.feature.home

import android.accessibilityservice.AccessibilityServiceInfo
import android.content.Context
import android.os.Build
import android.provider.Settings
import android.view.accessibility.AccessibilityManager
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.testory.assistant.v2.core.communication.PcSyncClient
import com.testory.assistant.v2.core.model.DeviceInfo
import com.testory.assistant.v2.core.model.PcConnectionState
import com.testory.assistant.v2.core.repository.CaseRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.flow.*
import kotlinx.coroutines.launch
import javax.inject.Inject

@HiltViewModel
class HomeViewModel @Inject constructor(
    @ApplicationContext private val context: Context,
    private val caseRepository: CaseRepository,
    private val pcSyncClient: PcSyncClient
) : ViewModel() {

    private val _uiState = MutableStateFlow(HomeUiState())
    val uiState: StateFlow<HomeUiState> = _uiState.asStateFlow()

    init {
        viewModelScope.launch {
            val deviceInfo = pcSyncClient.getDeviceInfo()
            _uiState.update { it.copy(deviceInfo = deviceInfo) }
            caseRepository.observeAllCases().collect { cases ->
                _uiState.update { it.copy(caseCount = cases.size) }
            }
        }

        viewModelScope.launch {
            pcSyncClient.state.collect { state ->
                _uiState.update { it.copy(pcConnectionState = state) }
                if (state == PcConnectionState.CONNECTED) {
                    refreshAiStatus()
                } else {
                    _uiState.update {
                        it.copy(aiReady = false, aiModelLabel = "", aiMessage = "")
                    }
                }
            }
        }

        viewModelScope.launch {
            while (true) {
                _uiState.update {
                    it.copy(
                        isAccessibilityEnabled = isAccessibilityServiceEnabled(),
                        canDrawOverlays = canDrawOverlays()
                    )
                }
                kotlinx.coroutines.delay(2000)
            }
        }
    }

    fun refresh() {
        viewModelScope.launch {
            val deviceInfo = pcSyncClient.getDeviceInfo()
            _uiState.update {
                it.copy(
                    deviceInfo = deviceInfo,
                    isAccessibilityEnabled = isAccessibilityServiceEnabled(),
                    canDrawOverlays = canDrawOverlays()
                )
            }
            if (_uiState.value.pcConnectionState == PcConnectionState.CONNECTED) {
                refreshAiStatus()
            }
        }
    }

    private suspend fun refreshAiStatus() {
        try {
            val st = pcSyncClient.fetchAiStatus()
            val label = listOf(st.provider, st.model)
                .filter { it.isNotBlank() }
                .joinToString(" · ")
            _uiState.update {
                it.copy(
                    aiReady = st.success && st.ready,
                    aiModelLabel = label,
                    aiMessage = st.message.ifBlank { st.error ?: "" }
                )
            }
        } catch (_: Exception) {
            _uiState.update {
                it.copy(aiReady = false, aiModelLabel = "", aiMessage = "无法读取 PC 模型状态")
            }
        }
    }

    private fun canDrawOverlays(): Boolean {
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            Settings.canDrawOverlays(context)
        } else {
            true
        }
    }

    private fun isAccessibilityServiceEnabled(): Boolean {
        val am = context.getSystemService(Context.ACCESSIBILITY_SERVICE) as? AccessibilityManager
            ?: return false
        val expectedName =
            "${context.packageName}/com.testory.assistant.v2.service.accessibility.AssistantAccessibilityService"
        val enabledServices = Settings.Secure.getString(
            context.contentResolver,
            Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES
        ) ?: return false
        val services = am.getEnabledAccessibilityServiceList(
            AccessibilityServiceInfo.FEEDBACK_ALL_MASK
        )
        return enabledServices.contains(expectedName) ||
            services.any { it.id.contains("testory", ignoreCase = true) }
    }
}
