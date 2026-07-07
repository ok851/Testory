package com.testory.assistant.v2.core.database.entity

import androidx.room.ColumnInfo
import androidx.room.Entity
import androidx.room.ForeignKey
import androidx.room.Index
import androidx.room.PrimaryKey
import com.testory.assistant.v2.core.model.ActionType
import com.testory.assistant.v2.core.model.Locator
import com.testory.assistant.v2.core.model.LocationSource
import com.testory.assistant.v2.core.model.NodeInfo
import com.testory.assistant.v2.core.model.ScreenCoordinate
import com.testory.assistant.v2.core.model.ScreenRect
import com.testory.assistant.v2.core.model.Step

@Entity(
    tableName = "steps",
    foreignKeys = [
        ForeignKey(
            entity = CaseEntity::class,
            parentColumns = ["id"],
            childColumns = ["case_id"],
            onDelete = ForeignKey.CASCADE
        )
    ],
    indices = [Index(value = ["case_id"])]
)
data class StepEntity(
    @PrimaryKey
    @ColumnInfo(name = "id")
    val id: String,

    @ColumnInfo(name = "case_id")
    val caseId: String,

    @ColumnInfo(name = "step_index")
    val index: Int,

    @ColumnInfo(name = "action")
    val action: String,

    @ColumnInfo(name = "description")
    val description: String = "",

    // ── 定位器字段 ──
    @ColumnInfo(name = "locator_json")
    val locatorJson: String = "{}",

    // ── 节点信息 ──
    @ColumnInfo(name = "target_node_json")
    val targetNodeJson: String? = null,

    // ── 坐标 ──
    @ColumnInfo(name = "screen_x")
    val screenX: Int = 0,

    @ColumnInfo(name = "screen_y")
    val screenY: Int = 0,

    @ColumnInfo(name = "location_source")
    val locationSource: String = "UNKNOWN",

    // ── 动作参数 ──
    @ColumnInfo(name = "input_text")
    val inputText: String = "",

    @ColumnInfo(name = "swipe_direction")
    val swipeDirection: String? = null,

    @ColumnInfo(name = "wait_duration_ms")
    val waitDurationMs: Long = 0,

    @ColumnInfo(name = "assert_text")
    val assertText: String = "",

    // ── 元数据 ──
    @ColumnInfo(name = "pre_wait_ms")
    val preWaitMs: Long = 500,

    @ColumnInfo(name = "max_retries")
    val maxRetries: Int = 3,

    @ColumnInfo(name = "optional")
    val optional: Boolean = false
)

// ── Common serializer for Locator (inline JSON) ──
// Uses kotlinx.serialization internally via a simple helper
private object LocatorSerializer {
    private val json = kotlinx.serialization.json.Json {
        ignoreUnknownKeys = true
        encodeDefaults = true
    }

    fun toJson(locator: Locator): String = json.encodeToString(Locator.serializer(), locator)

    fun fromJson(jsonStr: String): Locator = try {
        json.decodeFromString(Locator.serializer(), jsonStr)
    } catch (_: Exception) { Locator() }

    fun toNodeJson(node: NodeInfo): String = json.encodeToString(NodeInfo.serializer(), node)

    fun fromNodeJson(jsonStr: String): NodeInfo = try {
        json.decodeFromString(NodeInfo.serializer(), jsonStr)
    } catch (_: Exception) { NodeInfo() }
}

fun StepEntity.toDomain(): Step = Step(
    id = id,
    caseId = caseId,
    index = index,
    action = try { ActionType.valueOf(action) } catch (_: Exception) { ActionType.TAP },
    description = description,
    locator = LocatorSerializer.fromJson(locatorJson),
    targetNode = targetNodeJson?.let { LocatorSerializer.fromNodeJson(it) },
    screenCoordinate = if (screenX > 0 || screenY > 0) ScreenCoordinate(screenX, screenY) else null,
    locationSource = try { LocationSource.valueOf(locationSource) } catch (_: Exception) { LocationSource.UNKNOWN },
    inputText = inputText,
    swipeDirection = swipeDirection?.let {
        try { com.testory.assistant.v2.core.model.SwipeDirection.valueOf(it) } catch (_: Exception) { null }
    },
    waitDurationMs = waitDurationMs,
    assertText = assertText,
    preWaitMs = preWaitMs,
    maxRetries = maxRetries,
    optional = optional
)

fun Step.toEntity(): StepEntity = StepEntity(
    id = id.ifEmpty { java.util.UUID.randomUUID().toString() },
    caseId = caseId,
    index = index,
    action = action.name,
    description = description,
    locatorJson = LocatorSerializer.toJson(locator),
    targetNodeJson = targetNode?.let { LocatorSerializer.toNodeJson(it) },
    screenX = screenCoordinate?.x ?: 0,
    screenY = screenCoordinate?.y ?: 0,
    locationSource = locationSource.name,
    inputText = inputText,
    swipeDirection = swipeDirection?.name,
    waitDurationMs = waitDurationMs,
    assertText = assertText,
    preWaitMs = preWaitMs,
    maxRetries = maxRetries,
    optional = optional
)
