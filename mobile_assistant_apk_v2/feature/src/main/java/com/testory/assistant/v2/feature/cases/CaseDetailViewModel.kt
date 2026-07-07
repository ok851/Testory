package com.testory.assistant.v2.feature.cases

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.testory.assistant.v2.core.model.RunResultSummary
import com.testory.assistant.v2.core.model.TestCase
import com.testory.assistant.v2.core.repository.CaseRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.*
import kotlinx.coroutines.launch
import javax.inject.Inject

@HiltViewModel
class CaseDetailViewModel @Inject constructor(
    private val caseRepository: CaseRepository
) : ViewModel() {

    private val _case = MutableStateFlow<TestCase?>(null)
    val case: StateFlow<TestCase?> = _case.asStateFlow()

    private val _runHistory = MutableStateFlow<List<RunResultSummary>>(emptyList())
    val runHistory: StateFlow<List<RunResultSummary>> = _runHistory.asStateFlow()

    fun loadCase(caseId: String) {
        viewModelScope.launch {
            caseRepository.observeCase(caseId).collect { testCase ->
                _case.value = testCase
            }
        }
        viewModelScope.launch {
            caseRepository.observeRunHistory(caseId).collect { history ->
                _runHistory.value = history
            }
        }
    }

    fun deleteCase() {
        _case.value?.let { testCase ->
            viewModelScope.launch {
                caseRepository.deleteCase(testCase.id)
            }
        }
    }
}
