package com.testory.assistant;

import android.app.Application;

/** 应用启动时预连接平台桥，减少首次录制等待。 */
public class AssistantApplication extends Application {

    @Override
    public void onCreate() {
        super.onCreate();
        AssistantSocketClient.start(this);
    }
}
