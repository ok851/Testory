package com.testory.assistant;

import android.app.Application;

public class AssistantApplication extends Application {

    @Override
    public void onCreate() {
        super.onCreate();
        AssistantApplicationHolder.init(this);
    }
}
