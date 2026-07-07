package com.testory.assistant.v2.feature.onboarding

import android.content.Context
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.testory.assistant.v2.core.util.AndroidUtils
import dagger.hilt.android.lifecycle.HiltViewModel
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import javax.inject.Inject

data class OnboardingStep(
    val title: String,
    val description: String,
    val iconEmoji: String,
    val actionLabel: String? = null,
    val isCompleted: Boolean = false
)

data class OnboardingUiState(
    val currentStep: Int = 0,
    val steps: List<OnboardingStep> = listOf(
        OnboardingStep(
            title = "开启无障碍服务",
            description = "Testory 需要无障碍权限来录制和回放你的操作。\n请前往设置开启「Testory Assistant」无障碍服务。",
            iconEmoji = "⚙️",
            actionLabel = "去开启"
        ),
        OnboardingStep(
            title = "允许悬浮窗",
            description = "录制时需要悬浮窗来显示控制按钮和步骤预览。\n请允许 Testory 显示悬浮窗。",
            iconEmoji = "🪟",
            actionLabel = "去设置"
        ),
        OnboardingStep(
            title = "连接 PC 端",
            description = "请在 PC 上启动 Testory 平台，\n然后在此处输入 PC 的 IP 地址完成连接。",
            iconEmoji = "🔗",
            actionLabel = null
        )
    ),
    val accessibilityEnabled: Boolean = false,
    val overlayGranted: Boolean = false,
    val pcAddress: String = "",
    val allDone: Boolean = false
)

@HiltViewModel
class OnboardingViewModel @Inject constructor(
    @ApplicationContext private val appContext: Context
) : ViewModel() {

    private val _uiState = MutableStateFlow(OnboardingUiState())
    val uiState: StateFlow<OnboardingUiState> = _uiState.asStateFlow()

    fun checkPermissions() {
        val accEnabled = AndroidUtils.isAccessibilityServiceEnabled(
            appContext,
            "com.testory.assistant.v2.service.accessibility.AssistantAccessibilityService"
        )
        val overlayGranted = android.provider.Settings.canDrawOverlays(appContext)

        _uiState.value = _uiState.value.copy(
            accessibilityEnabled = accEnabled,
            overlayGranted = overlayGranted,
            allDone = accEnabled && overlayGranted
        )
    }

    fun nextStep() {
        val state = _uiState.value
        if (state.currentStep < state.steps.size - 1) {
            _uiState.value = state.copy(currentStep = state.currentStep + 1)
        }
    }

    fun prevStep() {
        val state = _uiState.value
        if (state.currentStep > 0) {
            _uiState.value = state.copy(currentStep = state.currentStep - 1)
        }
    }

    fun openAccessibilitySettings() {
        AndroidUtils.openAccessibilitySettings(appContext)
    }

    fun openOverlaySettings() {
        AndroidUtils.openAppSettings(appContext)
    }

    fun setPcAddress(address: String) {
        _uiState.value = _uiState.value.copy(pcAddress = address)
    }
}
