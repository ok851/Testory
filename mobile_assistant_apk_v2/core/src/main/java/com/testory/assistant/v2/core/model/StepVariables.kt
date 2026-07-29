package com.testory.assistant.v2.core.model

/**
 * 运行时变量替换：`{{name}}` → variables[name]
 */
object StepVariables {
    private val PATTERN = Regex("\\{\\{\\s*([a-zA-Z_][a-zA-Z0-9_]*)\\s*\\}\\}")

    fun substitute(template: String, variables: Map<String, String>): String {
        if (template.isEmpty() || !template.contains("{{")) return template
        return PATTERN.replace(template) { m ->
            val key = m.groupValues[1]
            variables[key] ?: m.value
        }
    }

    fun applyToStep(step: Step, variables: Map<String, String>): Step {
        if (variables.isEmpty()) return step
        return step.copy(
            inputText = substitute(step.inputText, variables),
            assertText = substitute(step.assertText, variables),
            description = substitute(step.description, variables),
            locator = step.locator.copy(
                text = substitute(step.locator.text, variables),
                contentDesc = substitute(step.locator.contentDesc, variables),
                resourceId = substitute(step.locator.resourceId, variables),
                textRegex = substitute(step.locator.textRegex, variables)
            ),
            extras = step.extras.copy(
                untilAssertText = substitute(step.extras.untilAssertText, variables),
                captchaHint = substitute(step.extras.captchaHint, variables),
                saveAs = step.extras.saveAs,
                keyCode = step.extras.keyCode
            )
        )
    }
}
