package com.testory.assistant.v2.feature.home

import com.testory.assistant.v2.core.database.entity.CaseEntity
import com.testory.assistant.v2.core.repository.CaseRepository
import io.mockk.*
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.test.*
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class HomeViewModelTest {

    private val caseRepository: CaseRepository = mockk()
    private lateinit var viewModel: HomeViewModel
    private val testDispatcher = StandardTestDispatcher()

    @Before
    fun setUp() {
        Dispatchers.setMain(testDispatcher)
        coEvery { caseRepository.getAllCases() } returns flowOf(emptyList())
        viewModel = HomeViewModel(caseRepository)
    }

    @After
    fun tearDown() {
        Dispatchers.resetMain()
    }

    @Test
    fun `initial state should be idle`() = runTest {
        val state = viewModel.uiState.value
        assertEquals(ConnectionStatus.DISCONNECTED, state.connectionStatus)
        assertTrue(state.recentCases.isEmpty())
    }

    @Test
    fun `loadCases should populate recent cases`() = runTest {
        val cases = listOf(
            CaseEntity(id = "c1", name = "登录测试", updatedAt = System.currentTimeMillis()),
            CaseEntity(id = "c2", name = "注册测试", updatedAt = System.currentTimeMillis() - 1000)
        )
        coEvery { caseRepository.getAllCases() } returns flowOf(cases)

        viewModel = HomeViewModel(caseRepository)
        advanceUntilIdle()

        val state = viewModel.uiState.value
        assertEquals(2, state.recentCases.size)
        assertEquals("登录测试", state.recentCases[0].name)
    }

    @Test
    fun `recentCases should be sorted by updatedAt descending`() = runTest {
        val older = CaseEntity(id = "c1", name = "旧用例", updatedAt = 1000)
        val newer = CaseEntity(id = "c2", name = "新用例", updatedAt = 2000)
        coEvery { caseRepository.getAllCases() } returns flowOf(listOf(older, newer))

        viewModel = HomeViewModel(caseRepository)
        advanceUntilIdle()

        val state = viewModel.uiState.value
        assertEquals("新用例", state.recentCases[0].name)
        assertEquals("旧用例", state.recentCases[1].name)
    }
}
