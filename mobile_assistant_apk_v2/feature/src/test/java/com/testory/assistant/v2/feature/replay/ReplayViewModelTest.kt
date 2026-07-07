package com.testory.assistant.v2.feature.replay

import com.testory.assistant.v2.core.model.*
import io.mockk.*
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.*
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class ReplayViewModelTest {

    private lateinit var viewModel: ReplayViewModel
    private val testDispatcher = StandardTestDispatcher()

    @Before
    fun setUp() {
        Dispatchers.setMain(testDispatcher)
        viewModel = ReplayViewModel()
    }

    @After
    fun tearDown() {
        Dispatchers.resetMain()
    }

    @Test
    fun `initial state should be idle`() = runTest {
        val state = viewModel.uiState.value
        assertEquals(ReplayStatus.IDLE, state.status)
        assertEquals(0, state.currentStepIndex)
    }

    @Test
    fun `startReplay should set initial state`() = runTest {
        val steps = listOf(
            Step(id = "s1", action = StepAction.TAP, description = "步骤1",
                screenCoordinate = ScreenCoordinate(100, 100)),
            Step(id = "s2", action = StepAction.TAP, description = "步骤2",
                screenCoordinate = ScreenCoordinate(200, 200))
        )

        viewModel.startReplay("case1", steps)
        val state = viewModel.uiState.value
        assertEquals(ReplayStatus.RUNNING, state.status)
        assertEquals(0, state.currentStepIndex)
        assertEquals(2, state.totalSteps)
        assertTrue(state.passedSteps.isEmpty())
        assertTrue(state.failedSteps.isEmpty())
    }

    @Test
    fun `step passed should increment progress`() = runTest {
        val steps = listOf(
            Step(id = "s1", action = StepAction.TAP, description = "步骤1",
                screenCoordinate = ScreenCoordinate(100, 100))
        )
        viewModel.startReplay("case1", steps)
        viewModel.stepPassed("s1")

        val state = viewModel.uiState.value
        assertEquals(1, state.currentStepIndex)
        assertEquals(1, state.passedSteps.size)
        assertTrue(state.passedSteps.contains("s1"))
    }

    @Test
    fun `step failed should record failure`() = runTest {
        val steps = listOf(
            Step(id = "s1", action = StepAction.TAP, description = "步骤1",
                screenCoordinate = ScreenCoordinate(100, 100))
        )
        viewModel.startReplay("case1", steps)
        viewModel.stepFailed("s1", "元素未找到")

        val state = viewModel.uiState.value
        assertEquals(1, state.failedSteps.size)
        assertTrue(state.failedSteps.contains("s1"))
        assertEquals(ReplayStatus.FAILED, state.status)
    }

    @Test
    fun `replay complete should set COMPLETED status`() = runTest {
        val steps = listOf(
            Step(id = "s1", action = StepAction.TAP, description = "步骤1",
                screenCoordinate = ScreenCoordinate(100, 100))
        )
        viewModel.startReplay("case1", steps)
        viewModel.stepPassed("s1")

        val state = viewModel.uiState.value
        assertEquals(ReplayStatus.COMPLETED, state.status)
    }

    @Test
    fun `stop replay should set STOPPED status`() = runTest {
        val steps = listOf(
            Step(id = "s1", action = StepAction.TAP, description = "步骤1",
                screenCoordinate = ScreenCoordinate(100, 100)),
            Step(id = "s2", action = StepAction.TAP, description = "步骤2",
                screenCoordinate = ScreenCoordinate(200, 200))
        )
        viewModel.startReplay("case1", steps)
        viewModel.stopReplay()

        val state = viewModel.uiState.value
        assertEquals(ReplayStatus.STOPPED, state.status)
    }
}
