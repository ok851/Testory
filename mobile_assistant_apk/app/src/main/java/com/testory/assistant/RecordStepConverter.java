package com.testory.assistant;

import org.json.JSONObject;

/** 将无障碍录制事件转为与 PC 对齐的可回放步骤。 */
final class RecordStepConverter {

    private RecordStepConverter() {
    }

    static JSONObject toDbStep(JSONObject raw, int order) throws Exception {
        return StepNormalizer.toDbStep(raw, order);
    }
}
