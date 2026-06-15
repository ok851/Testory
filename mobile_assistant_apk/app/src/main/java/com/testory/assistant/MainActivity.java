package com.testory.assistant;

import android.content.Intent;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;
import android.widget.Button;
import android.widget.TextView;

import androidx.appcompat.app.AppCompatActivity;

/**
 * 安装后引导用户开启无障碍服务；插件 HTTP 服务在无障碍连接后自动启动。
 */
public class MainActivity extends AppCompatActivity {

    private final Handler handler = new Handler(Looper.getMainLooper());
    private TextView statusView;
    private TextView modeView;
    private TextView hintView;

    private final Runnable refreshRunnable = new Runnable() {
        @Override
        public void run() {
            refreshStatus();
            handler.postDelayed(this, 1200);
        }
    };

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);

        statusView = findViewById(R.id.statusText);
        modeView = findViewById(R.id.modeText);
        hintView = findViewById(R.id.hintText);

        Button openAccessibility = findViewById(R.id.openAccessibilityBtn);
        openAccessibility.setOnClickListener(v -> openAccessibilitySettings());

        hintView.setText(getString(R.string.main_hint_jsonrpc));
    }

    @Override
    protected void onResume() {
        super.onResume();
        handler.post(refreshRunnable);
    }

    @Override
    protected void onPause() {
        handler.removeCallbacks(refreshRunnable);
        super.onPause();
    }

    private void refreshStatus() {
        boolean a11y = isAccessibilityServiceEnabled();
        boolean server = PluginHttpServer.isRunning();
        String mode = AssistantSession.getArmedMode();

        if (a11y && server) {
            statusView.setText(R.string.status_ready);
            statusView.setTextColor(0xFF059669);
        } else if (a11y) {
            statusView.setText(R.string.status_waiting_platform);
            statusView.setTextColor(0xFFB45309);
        } else {
            statusView.setText(R.string.status_need_accessibility);
            statusView.setTextColor(0xFFB91C1C);
        }

        if (AssistantSession.MODE_IDLE.equals(mode)) {
            modeView.setText(R.string.mode_idle);
        } else if (AssistantSession.MODE_CAPTURE.equals(mode)) {
            modeView.setText(R.string.mode_capture);
        } else {
            modeView.setText(R.string.mode_record);
        }
    }

    private boolean isAccessibilityServiceEnabled() {
        try {
            String enabled = Settings.Secure.getString(
                    getContentResolver(),
                    Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES
            );
            if (enabled == null) return false;
            String target = getPackageName() + "/" + AssistantAccessibilityService.class.getName();
            return enabled.contains(target);
        } catch (Exception ignored) {
            return false;
        }
    }

    private void openAccessibilitySettings() {
        try {
            Intent intent = new Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS);
            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            startActivity(intent);
        } catch (Exception ignored) {
        }
    }
}
