package com.testory.assistant.v2.core.repository

import com.testory.assistant.v2.core.communication.PcSyncClient
import com.testory.assistant.v2.core.database.dao.CaseDao
import com.testory.assistant.v2.core.database.dao.RunHistoryDao
import com.testory.assistant.v2.core.database.dao.StepDao
import com.testory.assistant.v2.core.database.entity.CaseEntity
import com.testory.assistant.v2.core.database.entity.StepEntity
import com.testory.assistant.v2.core.model.*
import io.mockk.*
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test

class CaseRepositoryTest {

    private val caseDao: CaseDao = mockk()
    private val stepDao: StepDao = mockk()
    private val runHistoryDao: RunHistoryDao = mockk()
    private val pcSyncClient: PcSyncClient = mockk()
    private lateinit var repository: CaseRepository

    @Before
    fun setUp() {
        repository = CaseRepository(caseDao, stepDao, runHistoryDao, pcSyncClient)
    }

    @Test
    fun `getAllCases should return list from DAO`() = runBlocking {
        val caseEntities = listOf(
            CaseEntity(id = "c1", name = "用例1"),
            CaseEntity(id = "c2", name = "用例2")
        )
        coEvery { caseDao.getAllCasesFlow() } returns flowOf(caseEntities)

        val result = repository.getAllCases().first()
        assertEquals(2, result.size)
        assertEquals("c1", result[0].id)
        assertEquals("用例1", result[0].name)
        assertEquals("c2", result[1].id)
        assertEquals("用例2", result[1].name)
    }

    @Test
    fun `getCaseById should return mapped TestCase`() = runBlocking {
        val entity = CaseEntity(id = "c1", name = "登录测试")
        val stepEntities = listOf(
            StepEntity(id = "s1", caseId = "c1", action = "tap", description = "点击按钮",
                xCoordinate = 100, yCoordinate = 200, stepOrder = 0)
        )
        coEvery { caseDao.getCaseById("c1") } returns entity
        coEvery { stepDao.getStepsForCase("c1") } returns stepEntities

        val result = repository.getCaseById("c1")
        assertNotNull(result)
        assertEquals("c1", result!!.id)
        assertEquals("登录测试", result.name)
        assertEquals(1, result.steps.size)
        assertEquals("s1", result.steps[0].id)
    }

    @Test
    fun `getCaseById should return null for missing case`() = runBlocking {
        coEvery { caseDao.getCaseById("nonexistent") } returns null

        val result = repository.getCaseById("nonexistent")
        assertNull(result)
    }

    @Test
    fun `deleteCase should cascade delete steps`() = runBlocking {
        coEvery { stepDao.deleteStepsForCase("c1") } just Runs
        coEvery { caseDao.deleteCase("c1") } just Runs

        repository.deleteCase("c1")

        coVerifySequence {
            stepDao.deleteStepsForCase("c1")
            caseDao.deleteCase("c1")
        }
    }
}
