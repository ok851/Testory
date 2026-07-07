package com.testory.assistant.v2.feature.recorder

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
class RecorderViewModelTest {

    private lateinit var viewModel: RecorderViewModel
    private val testDispatcher = StandardTestDispatcher()

    @Before
    fun setUp() {
        Dispatchers.setMain(testDispatcher)
        viewModel = RecorderViewModel()
    }

    @After
    fun tearDown() {
        Dispatchers.resetMain()
    }

    @Test
    fun `initial state should be idle`() = runTest {
        val state = viewModel.uiState.value
        assertEquals(RecorderStatus.IDLE, state.status)
        assertTrue(state.steps.isEmpty())
    }

    @Test
    fun `startRecording should change status to RECORDING`() = runTest {
        viewModel.startRecording("com.example.app")
        val state = viewModel.uiState.value
        assertEquals(RecorderStatus.RECORDING, state.status)
        assertEquals("com.example.app", state.targetPackage)
    }

    @Test
    fun `pauseRecording should change status to PAUSED`() = runTest {
        viewModel.startRecording("com.example.app")
        viewModel.pauseRecording()
        val state = viewModel.uiState.value
        assertEquals(RecorderStatus.PAUSED, state.status)
    }

    @Test
    fun `resumeRecording should change status back to RECORDING`() = runTest {
        viewModel.startRecording("com.example.app")
        viewModel.pauseRecording()
        viewModel.resumeRecording()
        val state = viewModel.uiState.value
        assertEquals(RecorderStatus.RECORDING, state.status)
    }

    @Test
    fun `stopRecording should change status to STOPPED`() = runTest {
        viewModel.startRecording("com.example.app")
        viewModel.stopRecording()
        val state = viewModel.uiState.value
        assertEquals(RecorderStatus.STOPPED, state.status)
    }

    @Test
    fun `addStep should append step with correct order`() = runTest {
        viewModel.startRecording("com.example.app")

        val step1 = Step(
            action = StepAction.TAP,
            description = "点击按钮",
            screenCoordinate = ScreenCoordinate(100, 200)
        )
        viewModel.addStep(step1)

        val state = viewModel.uiState.value
        assertEquals(1, state.steps.size)
        assertEquals("点击按钮", state.steps[0].description)
        assertEquals(0, state.steps[0].order)
    }

    @Test
    fun `addStep should not append when not recording`() = runTest {
        val step = Step(
            action = StepAction.TAP,
            description = "不应被添加",
            screenCoordinate = ScreenCoordinate(100, 200)
        )
        viewModel.addStep(step)

        val state = viewModel.uiState.value
        assertTrue(state.steps.isEmpty())
    }

    @Test
    fun `removeLastStep should remove the most recent step`() = runTest {
        viewModel.startRecording("com.example.app")
        viewModel.addStep(Step(action = StepAction.TAP, description = "步骤1",
            screenCoordinate = ScreenCoordinate(100, 100)))
        viewModel.addStep(Step(action = StepAction.TAP, description = "步骤2",
            screenCoordinate = ScreenCoordinate(200, 200)))

        viewModel.removeLastStep()
        val state = viewModel.uiState.value
        assertEquals(1, state.steps.size)
        assertEquals("步骤1", state.steps[0].description)
    }
}
