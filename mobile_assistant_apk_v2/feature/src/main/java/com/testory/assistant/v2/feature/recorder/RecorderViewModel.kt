package com.testory.assistant.v2.feature.recorder

import android.app.Application
import android.content.Intent
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.testory.assistant.v2.core.model.*
import com.testory.assistant.v2.core.repository.CaseRepository
import com.testory.assistant.v2.service.accessibility.AccessibilityServiceHolder
import com.testory.assistant.v2.service.accessibility.AssistantAccessibilityService
import com.testory.assistant.v2.service.accessibility.EventPipeline
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

    init {
        viewModelScope.launch {
            eventPipeline.stepFlow.collect { step ->
                val newSteps = _uiState.value.steps + step
                _uiState.update { it.copy(steps = newSteps) }
                accessibilityService?.updateFloatingStepCount(newSteps.size)
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
        if (_uiState.value.recordingState == RecordingState.RECORDING ||
            _uiState.value.recordingState == RecordingState.PAUSED) {
            stopRecording()
        }
    }

    fun startRecording() {
        val service = getAccessibilityService()
        if (service == null) return
        accessibilityService = service
        _uiState.update { it.copy(steps = emptyList()) }

        service.startRecording()

        try {
            val intent = Intent(application, RecorderForegroundService::class.java).apply {
                putExtra(RecorderForegroundService.EXTRA_MODE, "recording")
            }
            application.startForegroundService(intent)
        } catch (_: Exception) { }

        service.performGlobalAction(android.accessibilityservice.AccessibilityService.GLOBAL_ACTION_HOME)
    }

    fun stopRecording() {
        val service = getAccessibilityService() ?: return
        service.stopRecording()
        _uiState.update { it.copy(showSaveDialog = true) }
        stopForegroundServices()

        service.performGlobalAction(android.accessibilityservice.AccessibilityService.GLOBAL_ACTION_HOME)
    }

    fun pauseRecording() {
        getAccessibilityService()?.pauseRecording()
    }

    fun resumeRecording() {
        getAccessibilityService()?.resumeRecording()
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

    private fun getAccessibilityService(): AssistantAccessibilityService? {
        return accessibilityService ?: AccessibilityServiceHolder.instance
    }

    private fun stopForegroundServices() {
        try {
            application.stopService(Intent(application, RecorderForegroundService::class.java))
        } catch (_: Exception) { }
    }
}
