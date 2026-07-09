package com.testory.assistant.v2.feature.history

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.testory.assistant.v2.core.model.RunResultSummary
import com.testory.assistant.v2.core.model.StepResult
import com.testory.assistant.v2.core.repository.CaseRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.*
import kotlinx.coroutines.launch
import kotlinx.serialization.json.Json
import javax.inject.Inject

data class RunHistoryUiState(
    val caseName: String = "",
    val records: List<RunResultSummary> = emptyList(),
    val expandedRecordIds: Set<String> = emptySet(),
    val totalRuns: Int = 0,
    val passCount: Int = 0,
    val successRate: Float = 0f,
    val avgDurationMs: Long = 0
)

@HiltViewModel
class RunHistoryViewModel @Inject constructor(
    private val caseRepository: CaseRepository
) : ViewModel() {

    private val _uiState = MutableStateFlow(RunHistoryUiState())
    val uiState: StateFlow<RunHistoryUiState> = _uiState.asStateFlow()

    private val json = Json { ignoreUnknownKeys = true }

    fun loadHistory(caseId: String) {
        viewModelScope.launch {
            caseRepository.observeCase(caseId).collect { testCase ->
                _uiState.update { it.copy(caseName = testCase?.name ?: "") }
            }
        }
        viewModelScope.launch {
            caseRepository.observeRunHistory(caseId).collect { records ->
                val total = records.size
                val passed = records.count { it.success }
                val rate = if (total > 0) passed.toFloat() / total else 0f
                val avgDur = if (total > 0) records.sumOf { it.durationMs } / total else 0L
                _uiState.update {
                    it.copy(
                        records = records,
                        totalRuns = total,
                        passCount = passed,
                        successRate = rate,
                        avgDurationMs = avgDur
                    )
                }
            }
        }
    }

    fun toggleExpanded(runId: String) {
        _uiState.update {
            val newSet = it.expandedRecordIds.toMutableSet()
            if (newSet.contains(runId)) newSet.remove(runId) else newSet.add(runId)
            it.copy(expandedRecordIds = newSet)
        }
    }

    fun parseStepResults(stepResultsJson: String): List<StepResult> {
        if (stepResultsJson.isBlank() || stepResultsJson == "[]") return emptyList()
        return try {
            json.decodeFromString(
                kotlinx.serialization.builtins.ListSerializer(StepResult.serializer()),
                stepResultsJson
            )
        } catch (_: Exception) { emptyList() }
    }
}
