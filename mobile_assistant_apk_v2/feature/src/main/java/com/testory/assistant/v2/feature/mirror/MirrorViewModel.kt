package com.testory.assistant.v2.feature.mirror

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.media.projection.MediaProjectionManager
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import dagger.hilt.android.lifecycle.HiltViewModel
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import javax.inject.Inject

sealed class MirrorState {
    object Idle : MirrorState()
    object WaitingPermission : MirrorState()
    object Connecting : MirrorState()
    data class Streaming(val address: String, val port: Int) : MirrorState()
    data class Error(val message: String) : MirrorState()
}

@HiltViewModel
class MirrorViewModel @Inject constructor(
    @ApplicationContext private val appContext: Context
) : ViewModel() {

    private val _state = MutableStateFlow<MirrorState>(MirrorState.Idle)
    val state: StateFlow<MirrorState> = _state.asStateFlow()

    private val _events = MutableSharedFlow<MirrorEvent>(extraBufferCapacity = 32)
    val events: SharedFlow<MirrorEvent> = _events.asSharedFlow()

    private val _connectedClients = MutableStateFlow(0)
    val connectedClients: StateFlow<Int> = _connectedClients.asStateFlow()

    var mirrorEngine: MirrorEngine? = null
        private set

    fun createProjectionIntent(): Intent {
        val manager = appContext.getSystemService(Context.MEDIA_PROJECTION_SERVICE)
                as MediaProjectionManager
        return manager.createScreenCaptureIntent()
    }

    fun startMirroring(activity: Activity, resultCode: Int, data: Intent) {
        viewModelScope.launch {
            _state.value = MirrorState.Connecting
            try {
                val engine = MirrorEngine(appContext)
                mirrorEngine = engine
                engine.startMirroring(resultCode, data).collect { event ->
                    when (event) {
                        is MirrorEvent.Started -> {
                            _state.value = MirrorState.Streaming(
                                address = "localhost",
                                port = MirrorEngine.DEFAULT_PORT
                            )
                            _events.emit(event)
                        }
                        is MirrorEvent.Error -> {
                            _state.value = MirrorState.Error(event.message)
                            _events.emit(event)
                        }
                        is MirrorEvent.ClientConnected -> {
                            _connectedClients.value = _connectedClients.value + 1
                            _events.emit(event)
                        }
                        is MirrorEvent.Stopped -> {
                            _state.value = MirrorState.Idle
                            _events.emit(event)
                        }
                        is MirrorEvent.StatusUpdate -> {
                            _events.emit(event)
                        }
                        is MirrorEvent.FrameSent -> {
                            // Frame sent, no state change needed
                        }
                    }
                }
            } catch (e: Exception) {
                _state.value = MirrorState.Error("启动失败: ${e.message}")
                _events.emit(MirrorEvent.Error(e.message ?: "未知错误"))
            }
        }
    }

    fun stopMirroring() {
        viewModelScope.launch {
            mirrorEngine?.stopMirroring()
            mirrorEngine = null
            _state.value = MirrorState.Idle
            _events.emit(MirrorEvent.Stopped)
        }
    }

    override fun onCleared() {
        mirrorEngine?.let {
            viewModelScope.launch { it.stopMirroring() }
        }
        super.onCleared()
    }
}
