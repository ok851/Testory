package com.testory.assistant.v2.feature.sync

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.testory.assistant.v2.core.communication.SyncCaseSummary
import com.testory.assistant.v2.core.model.SyncStatus
import com.testory.assistant.v2.core.model.TestCase
import com.testory.assistant.v2.core.repository.CaseRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.*
import kotlinx.coroutines.launch
import javax.inject.Inject

data class SyncUiState(
    val selectedTab: Int = 0,
    val localCases: List<TestCase> = emptyList(),
    val remoteSummaries: List<SyncCaseSummary> = emptyList(),
    val groupedRemoteSummaries: Map<String, List<SyncCaseSummary>> = emptyMap(),
    val selectedLocalIds: Set<String> = emptySet(),
    val selectedRemoteIds: Set<String> = emptySet(),
    val isSyncing: Boolean = false,
    val syncResult: String? = null,
    val pcConnected: Boolean = false
)

@HiltViewModel
class SyncViewModel @Inject constructor(
    private val caseRepository: CaseRepository
) : ViewModel() {

    private val _uiState = MutableStateFlow(SyncUiState())
    val uiState: StateFlow<SyncUiState> = _uiState.asStateFlow()

    init {
        viewModelScope.launch {
            _uiState.update { it.copy(pcConnected = caseRepository.isPcConnected()) }
        }
    }

    fun loadData() {
        viewModelScope.launch {
            // Load local unsynced cases
            caseRepository.observeAllCases().collect { cases ->
                val unsynced = cases.filter { it.syncStatus != SyncStatus.SYNCED }
                _uiState.update { it.copy(localCases = unsynced) }
            }
        }
    }

    fun loadRemoteSummaries() {
        viewModelScope.launch {
            try {
                val summaries = caseRepository.pullCaseSummaries()
                val grouped = buildGroupedSummaries(summaries)
                _uiState.update { it.copy(remoteSummaries = summaries, groupedRemoteSummaries = grouped) }
            } catch (_: Exception) {
                _uiState.update { it.copy(syncResult = "加载 PC 端用例失败，请检查连接") }
            }
        }
    }

    private fun buildGroupedSummaries(summaries: List<SyncCaseSummary>): Map<String, List<SyncCaseSummary>> {
        val map = linkedMapOf<String, MutableList<SyncCaseSummary>>()
        val projectOrder = summaries.map { it.projectName }.filter { it.isNotBlank() }.distinct()
        for (p in projectOrder) {
            map[p] = mutableListOf()
        }
        for (s in summaries) {
            val key = s.projectName.ifBlank { "未分类" }
            map.getOrPut(key) { mutableListOf() }.add(s)
        }
        return map
    }

    fun selectTab(tab: Int) {
        _uiState.update { it.copy(selectedTab = tab) }
        if (tab == 1) loadRemoteSummaries()
        if (tab == 0) loadData()
    }

    fun toggleLocalSelection(caseId: String) {
        _uiState.update {
            val newSet = it.selectedLocalIds.toMutableSet()
            if (newSet.contains(caseId)) newSet.remove(caseId) else newSet.add(caseId)
            it.copy(selectedLocalIds = newSet)
        }
    }

    fun toggleRemoteSelection(caseId: String) {
        _uiState.update {
            val newSet = it.selectedRemoteIds.toMutableSet()
            if (newSet.contains(caseId)) newSet.remove(caseId) else newSet.add(caseId)
            it.copy(selectedRemoteIds = newSet)
        }
    }

    fun selectAllLocal() {
        _uiState.update {
            if (it.selectedLocalIds.size == it.localCases.size) {
                it.copy(selectedLocalIds = emptySet())
            } else {
                it.copy(selectedLocalIds = it.localCases.map { c -> c.id }.toSet())
            }
        }
    }

    fun selectAllRemote() {
        _uiState.update {
            if (it.selectedRemoteIds.size == it.remoteSummaries.size) {
                it.copy(selectedRemoteIds = emptySet())
            } else {
                it.copy(selectedRemoteIds = it.remoteSummaries.map { s -> s.id }.toSet())
            }
        }
    }

    fun pushSelected(onResult: (Boolean, String) -> Unit) {
        val selectedIds = _uiState.value.selectedLocalIds
        if (selectedIds.isEmpty()) {
            onResult(false, "请先选择要推送的用例")
            return
        }
        _uiState.update { it.copy(isSyncing = true) }
        viewModelScope.launch {
            try {
                caseRepository.pushCasesByIds(selectedIds)
                _uiState.update {
                    it.copy(
                        isSyncing = false,
                        selectedLocalIds = emptySet(),
                        syncResult = "推送成功：${selectedIds.size} 个用例"
                    )
                }
                onResult(true, "推送成功：${selectedIds.size} 个用例")
                loadData()
            } catch (e: Exception) {
                _uiState.update { it.copy(isSyncing = false) }
                onResult(false, "推送失败：${e.message}")
            }
        }
    }

    fun pullSelected(onResult: (Boolean, String) -> Unit) {
        val selectedIds = _uiState.value.selectedRemoteIds
        if (selectedIds.isEmpty()) {
            onResult(false, "请先选择要拉取的用例")
            return
        }
        _uiState.update { it.copy(isSyncing = true) }
        viewModelScope.launch {
            try {
                val pulled = caseRepository.pullCasesByIds(selectedIds.toList())
                _uiState.update {
                    it.copy(
                        isSyncing = false,
                        selectedRemoteIds = emptySet(),
                        syncResult = "拉取成功：${pulled.size} 个用例"
                    )
                }
                onResult(true, "拉取成功：${pulled.size} 个用例")
            } catch (e: Exception) {
                _uiState.update { it.copy(isSyncing = false) }
                onResult(false, "拉取失败：${e.message}")
            }
        }
    }

    fun clearResult() {
        _uiState.update { it.copy(syncResult = null) }
    }
}
