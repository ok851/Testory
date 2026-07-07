package com.testory.assistant.v2.feature.cases

import com.testory.assistant.v2.core.model.*
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
class CaseListViewModelTest {

    private val caseRepository: CaseRepository = mockk()
    private lateinit var viewModel: CaseListViewModel
    private val testDispatcher = StandardTestDispatcher()

    @Before
    fun setUp() {
        Dispatchers.setMain(testDispatcher)
        coEvery { caseRepository.getAllCases() } returns flowOf(emptyList())
        viewModel = CaseListViewModel(caseRepository)
    }

    @After
    fun tearDown() {
        Dispatchers.resetMain()
    }

    @Test
    fun `initial state should have empty list`() = runTest {
        val state = viewModel.uiState.value
        assertTrue(state.cases.isEmpty())
        assertFalse(state.isLoading)
    }

    @Test
    fun `cases should be sorted by updatedAt descending`() = runTest {
        val cases = listOf(
            com.testory.assistant.v2.core.database.entity.CaseEntity(
                id = "c1", name = "A", updatedAt = 1000),
            com.testory.assistant.v2.core.database.entity.CaseEntity(
                id = "c2", name = "B", updatedAt = 3000),
            com.testory.assistant.v2.core.database.entity.CaseEntity(
                id = "c3", name = "C", updatedAt = 2000)
        )
        coEvery { caseRepository.getAllCases() } returns flowOf(cases)

        viewModel = CaseListViewModel(caseRepository)
        advanceUntilIdle()

        val state = viewModel.uiState.value
        assertEquals("B", state.cases[0].name)
        assertEquals("C", state.cases[1].name)
        assertEquals("A", state.cases[2].name)
    }

    @Test
    fun `deleteCase should call repository`() = runTest {
        coEvery { caseRepository.deleteCase("c1") } just Runs

        viewModel.deleteCase("c1")

        coVerify { caseRepository.deleteCase("c1") }
    }
}
