package com.testory.assistant;

import android.content.Context;

/** 供无 Activity 上下文时访问 Application。 */
final class AssistantApplicationHolder {

    private static Context appContext;

    private AssistantApplicationHolder() {
    }

    static void init(Context ctx) {
        if (ctx != null) {
            appContext = ctx.getApplicationContext();
        }
    }

    static Context get() {
        return appContext;
    }
}
