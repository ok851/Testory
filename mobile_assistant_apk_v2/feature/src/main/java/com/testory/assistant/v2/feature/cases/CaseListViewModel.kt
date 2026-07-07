package com.testory.assistant.v2.feature.cases

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.testory.assistant.v2.core.model.CaseSource
import com.testory.assistant.v2.core.model.TestCase
import com.testory.assistant.v2.core.repository.CaseRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.*
import kotlinx.coroutines.launch
import javax.inject.Inject

@HiltViewModel
class CaseListViewModel @Inject constructor(
    private val caseRepository: CaseRepository
) : ViewModel() {

    private val _uiState = MutableStateFlow(CaseListUiState())
    val uiState: StateFlow<CaseListUiState> = _uiState.asStateFlow()

    private var allCases: List<TestCase> = emptyList()

    init {
        viewModelScope.launch {
            caseRepository.observeAllCases().collect { cases ->
                allCases = cases
                val recordedCases = cases.filter { it.source == CaseSource.RECORDED }
                val aiCases = cases.filter { it.source == CaseSource.AI_GENERATED }

                val filter = _uiState.value.selectedFilter
                val filtered = if (filter != null) {
                    cases.filter { it.source == filter }
                } else cases

                _uiState.update {
                    it.copy(
                        cases = filtered,
                        allCount = cases.size,
                        recordedCount = recordedCases.size,
                        aiCount = aiCases.size
                    )
                }
            }
        }
    }

    fun search(query: String) {
        viewModelScope.launch {
            if (query.isBlank()) {
                filterBy(_uiState.value.selectedFilter)
            } else {
                caseRepository.searchCases(query).collect { cases ->
                    _uiState.update { it.copy(cases = cases) }
                }
            }
        }
    }

    fun filterBy(source: CaseSource?) {
        _uiState.update {
            it.copy(
                selectedFilter = source,
                cases = if (source == null) allCases
                else allCases.filter { c -> c.source == source }
            )
        }
    }

    fun deleteCase(caseId: String) {
        viewModelScope.launch {
            caseRepository.deleteCase(caseId)
        }
    }
}
