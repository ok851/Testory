package com.testory.assistant.v2.feature.recorder

import android.app.Application
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.Build
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.testory.assistant.v2.core.model.*
import com.testory.assistant.v2.core.repository.CaseRepository
import com.testory.assistant.v2.service.accessibility.AccessibilityServiceHolder
import com.testory.assistant.v2.service.accessibility.AssistantAccessibilityService
import com.testory.assistant.v2.service.accessibility.EventPipeline
import com.testory.assistant.v2.service.foreground.FloatingControlService
import com.testory.assistant.v2.service.foreground.RecorderForegroundService
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.*
import kotlinx.coroutines.launch
import java.util.UUID
import javax.inject.Inject

@HiltViewModel
class RecorderViewModel @Inject constructor(
    private val application: Application,
    private val caseRepository: CaseRepository,
    private val eventPipeline: EventPipeline
) : ViewModel() {

    private val _uiState = MutableStateFlow(RecorderUiState())
    val uiState: StateFlow<RecorderUiState> = _uiState.asStateFlow()

    private var accessibilityService: AssistantAccessibilityService? = null

    private val floatingBroadcastReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context?, intent: Intent?) {
            when (intent?.action) {
                FloatingControlService.BROADCAST_PAUSE -> pauseRecording()
                FloatingControlService.BROADCAST_STOP -> stopRecording()
                FloatingControlService.BROADCAST_RESUME -> resumeRecording()
            }
        }
    }

    init {
        // Register broadcast receiver for floating window commands
        val filter = IntentFilter().apply {
            addAction(FloatingControlService.BROADCAST_PAUSE)
            addAction(FloatingControlService.BROADCAST_STOP)
            addAction(FloatingControlService.BROADCAST_RESUME)
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            application.registerReceiver(floatingBroadcastReceiver, filter, Context.RECEIVER_NOT_EXPORTED)
        } else {
            application.registerReceiver(floatingBroadcastReceiver, filter)
        }

        viewModelScope.launch {
            eventPipeline.stepFlow.collect { step ->
                val newSteps = _uiState.value.steps + step
                _uiState.update { it.copy(steps = newSteps) }
                // 实时更新悬浮窗和前台通知的步数
                updateFloatingStepCount(newSteps.size)
            }
        }

        viewModelScope.launch {
            eventPipeline.recordingState.collect { state ->
                _uiState.update { it.copy(recordingState = state) }
            }
        }
    }

    override fun onCleared() {
        super.onCleared()
        try { application.unregisterReceiver(floatingBroadcastReceiver) } catch (_: Exception) {}
        // 如果 ViewModel 被销毁时仍在录制，直接停止录制，防止后台继续录制
        if (_uiState.value.recordingState == RecordingState.RECORDING ||
            _uiState.value.recordingState == RecordingState.PAUSED) {
            stopRecording()
        }
    }

    fun startRecording() {
        val service = getAccessibilityService()
        if (service == null) {
            return
        }
        accessibilityService = service
        _uiState.update { it.copy(steps = emptyList()) }

        service.startRecording()

        // Start foreground notification
        try {
            val notiIntent = Intent(application, RecorderForegroundService::class.java).apply {
                putExtra(RecorderForegroundService.EXTRA_MODE, "recording")
                putExtra(RecorderForegroundService.EXTRA_STEP_COUNT, 0)
            }
            application.startForegroundService(notiIntent)
        } catch (_: Exception) { }

        // Start floating control overlay
        try {
            val floatIntent = Intent(application, FloatingControlService::class.java).apply {
                putExtra(FloatingControlService.EXTRA_MODE, "recording")
                putExtra(FloatingControlService.EXTRA_STEP_COUNT, 0)
            }
            application.startForegroundService(floatIntent)
        } catch (_: Exception) { }

        // Press HOME to go to desktop (so user can interact with target app)
        service.performGlobalAction(android.accessibilityservice.AccessibilityService.GLOBAL_ACTION_HOME)
    }

    fun stopRecording() {
        val service = getAccessibilityService() ?: return
        service.stopRecording()
        _uiState.update { it.copy(showSaveDialog = true) }
        stopForegroundServices()

        // Press HOME again to return to desktop
        service.performGlobalAction(android.accessibilityservice.AccessibilityService.GLOBAL_ACTION_HOME)
    }

    fun pauseRecording() {
        val service = getAccessibilityService() ?: return
        service.pauseRecording()
    }

    fun resumeRecording() {
        val service = getAccessibilityService() ?: return
        service.resumeRecording()
    }

    fun clearSteps() {
        _uiState.update { it.copy(steps = emptyList()) }
    }

    fun saveCase() {
        _uiState.update { it.copy(showSaveDialog = true) }
    }

    fun dismissSaveDialog() {
        _uiState.update { it.copy(showSaveDialog = false) }
    }

    fun confirmSave(name: String, description: String) {
        viewModelScope.launch {
            _uiState.update { it.copy(recordingState = RecordingState.SAVING) }
            try {
                val testCase = TestCase(
                    id = UUID.randomUUID().toString(),
                    name = name.ifBlank { "录制_${System.currentTimeMillis()}" },
                    description = description,
                    steps = _uiState.value.steps,
                    source = CaseSource.RECORDED
                )
                caseRepository.saveCase(testCase)
                _uiState.update {
                    it.copy(
                        recordingState = RecordingState.IDLE,
                        showSaveDialog = false,
                        steps = emptyList()
                    )
                }
            } catch (e: Exception) {
                _uiState.update {
                    it.copy(recordingState = RecordingState.IDLE, showSaveDialog = false)
                }
            }
        }
    }

    private fun updateFloatingStepCount(count: Int) {
        try {
            val floatIntent = Intent(application, FloatingControlService::class.java).apply {
                action = FloatingControlService.ACTION_UPDATE_STEP_COUNT
                putExtra(FloatingControlService.EXTRA_STEP_COUNT, count)
            }
            application.startService(floatIntent)

            val notiIntent = Intent(application, RecorderForegroundService::class.java).apply {
                action = RecorderForegroundService.ACTION_UPDATE_STEP_COUNT
                putExtra(RecorderForegroundService.EXTRA_STEP_COUNT, count)
            }
            application.startService(notiIntent)
        } catch (_: Exception) { }
    }

    private fun getAccessibilityService(): AssistantAccessibilityService? {
        return accessibilityService ?: AccessibilityServiceHolder.instance
    }

    private fun stopForegroundServices() {
        try {
            application.stopService(Intent(application, RecorderForegroundService::class.java))
            application.stopService(Intent(application, FloatingControlService::class.java))
        } catch (_: Exception) { }
    }
}
