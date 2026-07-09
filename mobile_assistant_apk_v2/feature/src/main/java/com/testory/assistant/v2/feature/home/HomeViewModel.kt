package com.testory.assistant.v2.feature.home

import android.accessibilityservice.AccessibilityServiceInfo
import android.content.Context
import android.provider.Settings
import android.view.accessibility.AccessibilityManager
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.testory.assistant.v2.core.communication.PcSyncClient
import com.testory.assistant.v2.core.model.DeviceInfo
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
            // Collect device info
            val deviceInfo = pcSyncClient.getDeviceInfo()
            _uiState.update { it.copy(deviceInfo = deviceInfo) }

            // Observe cases
            caseRepository.observeAllCases().collect { cases ->
                _uiState.update {
                    it.copy(caseCount = cases.size)
                }
            }
        }

        viewModelScope.launch {
            // Observe PC connection state
            pcSyncClient.state.collect { state ->
                _uiState.update { it.copy(pcConnectionState = state) }
            }
        }

        // Check accessibility service status
        viewModelScope.launch {
            while (true) {
                _uiState.update {
                    it.copy(isAccessibilityEnabled = isAccessibilityServiceEnabled())
                }
                kotlinx.coroutines.delay(2000)
            }
        }
    }

    private fun isAccessibilityServiceEnabled(): Boolean {
        val am = context.getSystemService(Context.ACCESSIBILITY_SERVICE) as? AccessibilityManager
            ?: return false
        val expectedName = "${context.packageName}/com.testory.assistant.v2.service.accessibility.AssistantAccessibilityService"

        val enabledServices = Settings.Secure.getString(
            context.contentResolver,
            Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES
        ) ?: return false

        // Also check via AccessibilityManager
        val services = am.getEnabledAccessibilityServiceList(
            AccessibilityServiceInfo.FEEDBACK_ALL_MASK
        )

        return enabledServices.contains(expectedName) ||
            services.any { it.id.contains("testory", ignoreCase = true) }
    }

    fun refresh() {
        viewModelScope.launch {
            val deviceInfo = pcSyncClient.getDeviceInfo()
            _uiState.update {
                it.copy(
                    deviceInfo = deviceInfo,
                    isAccessibilityEnabled = isAccessibilityServiceEnabled()
                )
            }
        }
    }
}
