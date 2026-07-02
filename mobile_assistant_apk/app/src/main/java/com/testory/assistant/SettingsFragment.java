package com.testory.assistant;

import android.content.Context;
import android.content.Intent;
import android.os.Build;
import android.os.Bundle;
import android.provider.Settings;
import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;
import android.widget.EditText;
import android.widget.TextView;
import android.widget.Toast;

import androidx.annotation.NonNull;
import androidx.annotation.Nullable;
import androidx.fragment.app.Fragment;

import org.json.JSONObject;

/**
 * 设置模块 — 无障碍状态、PC 配对。
 */
public class SettingsFragment extends Fragment {

    private View statusDot;
    private TextView statusText;
    private EditText syncBaseUrl, syncPairCode;

    @Nullable
    @Override
    public View onCreateView(@NonNull LayoutInflater inflater, @Nullable ViewGroup container,
                             @Nullable Bundle savedInstanceState) {
        return inflater.inflate(R.layout.fragment_settings, container, false);
    }

    @Override
    public void onViewCreated(@NonNull View view, @Nullable Bundle savedInstanceState) {
        super.onViewCreated(view, savedInstanceState);

        statusDot = view.findViewById(R.id.status_dot);
        statusText = view.findViewById(R.id.status_text);
        syncBaseUrl = view.findViewById(R.id.sync_base_url);
        syncPairCode = view.findViewById(R.id.sync_pair_code);

        view.findViewById(R.id.open_a11y_btn).setOnClickListener(v -> {
            try {
                startActivity(new Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS));
            } catch (Exception ignored) {}
        });

        view.findViewById(R.id.btn_pair).setOnClickListener(v -> pairWithPC());
        view.findViewById(R.id.btn_sync_cases).setOnClickListener(v -> syncCases());
        view.findViewById(R.id.btn_export_cases).setOnClickListener(v -> exportToPC());

        refreshStatus();
    }

    void refreshStatus() {
        try {
            Context ctx = requireContext();
            AssistantAccessibilityService svc = AssistantSession.getService();
            if (svc != null) {
                statusDot.setBackgroundResource(R.drawable.status_dot_green);
                statusText.setText("无障碍服务已连接");
            } else {
                statusDot.setBackgroundResource(R.drawable.status_dot_red);
                statusText.setText("无障碍服务未开启");
            }

            String savedUrl = SyncClient.getBaseUrl(ctx);
            if (savedUrl != null && !savedUrl.isEmpty() && syncBaseUrl.getText().toString().isEmpty()) {
                syncBaseUrl.setText(savedUrl);
            }
        } catch (Exception e) {
            // 防御性：避免异常导致 Fragment 崩溃
        }
    }

    private void pairWithPC() {
        Context ctx = requireContext();
        String url = syncBaseUrl.getText().toString().trim();
        String code = syncPairCode.getText().toString().trim();
        if (url.isEmpty() || code.isEmpty()) {
            Toast.makeText(ctx, "请输入 PC 地址和配对码", Toast.LENGTH_SHORT).show();
            return;
        }
        String deviceId = Build.SERIAL != null ? Build.SERIAL : "unknown";
        SyncClient.saveBaseUrl(ctx, url);
        SyncClient.pair(ctx, url, code, deviceId, new SyncClient.Callback() {
            @Override
            public void onSuccess(JSONObject data) {
                Toast.makeText(ctx, "配对成功", Toast.LENGTH_SHORT).show();
            }
            @Override
            public void onError(String message) {
                Toast.makeText(ctx, "配对失败: " + message, Toast.LENGTH_SHORT).show();
            }
        });
    }

    private void syncCases() {
        Context ctx = requireContext();
        String token = SyncClient.getToken(ctx);
        if (token.isEmpty()) {
            Toast.makeText(ctx, "请先配对 PC", Toast.LENGTH_SHORT).show();
            return;
        }
        SyncClient.fetchCases(ctx, new SyncClient.Callback() {
            @Override
            public void onSuccess(JSONObject data) {
                Toast.makeText(ctx, "同步成功", Toast.LENGTH_SHORT).show();
            }
            @Override
            public void onError(String message) {
                Toast.makeText(ctx, "同步失败: " + message, Toast.LENGTH_SHORT).show();
            }
        });
    }

    private void exportToPC() {
        Toast.makeText(requireContext(), "导出功能开发中", Toast.LENGTH_SHORT).show();
    }
}
