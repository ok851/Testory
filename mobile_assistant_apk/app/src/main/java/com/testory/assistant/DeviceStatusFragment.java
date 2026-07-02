package com.testory.assistant;

import android.content.Intent;
import android.os.Build;
import android.os.Bundle;
import android.provider.Settings;
import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.EditText;
import android.widget.TextView;
import android.widget.Toast;

import androidx.annotation.NonNull;
import androidx.annotation.Nullable;
import androidx.fragment.app.Fragment;

import org.json.JSONObject;

public class DeviceStatusFragment extends Fragment {

    private View statusDot;
    private TextView statusText, modeText, versionTag;
    private TextView deviceModel, deviceOs, deviceResolution, deviceSerial, appVersion;
    private View maestroStatusDot;
    private TextView maestroStatusText, maestroVersionText;
    private EditText syncBaseUrl, syncPairCode;

    @Nullable
    @Override
    public View onCreateView(@NonNull LayoutInflater inflater, @Nullable ViewGroup container,
                             @Nullable Bundle savedInstanceState) {
        return inflater.inflate(R.layout.fragment_device_status, container, false);
    }

    @Override
    public void onViewCreated(@NonNull View view, @Nullable Bundle savedInstanceState) {
        super.onViewCreated(view, savedInstanceState);

        statusDot = view.findViewById(R.id.status_dot);
        statusText = view.findViewById(R.id.status_text);
        modeText = view.findViewById(R.id.mode_text);
        versionTag = view.findViewById(R.id.version_tag);
        deviceModel = view.findViewById(R.id.device_model);
        deviceOs = view.findViewById(R.id.device_os);
        deviceResolution = view.findViewById(R.id.device_resolution);
        deviceSerial = view.findViewById(R.id.device_serial);
        appVersion = view.findViewById(R.id.app_version);
        maestroStatusDot = view.findViewById(R.id.maestro_status_dot);
        maestroStatusText = view.findViewById(R.id.maestro_status_text);
        maestroVersionText = view.findViewById(R.id.maestro_version_text);
        syncBaseUrl = view.findViewById(R.id.sync_base_url);
        syncPairCode = view.findViewById(R.id.sync_pair_code);

        Button openA11yBtn = view.findViewById(R.id.open_a11y_btn);
        openA11yBtn.setOnClickListener(v -> {
            Intent intent = new Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS);
            startActivity(intent);
        });

        Button btnPair = view.findViewById(R.id.btn_pair);
        btnPair.setOnClickListener(v -> pairWithPC());

        Button btnSyncCases = view.findViewById(R.id.btn_sync_cases);
        btnSyncCases.setOnClickListener(v -> syncCases());

        Button btnSaveToPc = view.findViewById(R.id.btn_save_to_pc);
        btnSaveToPc.setOnClickListener(v -> saveToPC());

        // 版本号
        try {
            String vn = requireContext().getPackageManager()
                    .getPackageInfo(requireContext().getPackageName(), 0).versionName;
            versionTag.setText("v" + vn);
            appVersion.setText(vn);
        } catch (Exception e) {
            versionTag.setText("");
        }

        refreshStatus();
    }

    void refreshStatus() {
        AssistantAccessibilityService svc = AssistantSession.getService();
        boolean a11yOk = svc != null;
        if (a11yOk) {
            statusDot.setBackgroundResource(R.drawable.status_dot_green);
            statusText.setText("无障碍服务已连接 ✓");
        } else {
            statusDot.setBackgroundResource(R.drawable.status_dot_red);
            statusText.setText("无障碍服务未开启");
        }
        modeText.setText("模式: 本地 + Maestro");

        // 设备信息
        deviceModel.setText(Build.MODEL);
        deviceOs.setText("Android " + Build.VERSION.RELEASE + " (API " + Build.VERSION.SDK_INT + ")");
        android.util.DisplayMetrics dm = requireContext().getResources().getDisplayMetrics();
        deviceResolution.setText(dm.widthPixels + " × " + dm.heightPixels + " (" + dm.densityDpi + "dpi)");
        deviceSerial.setText(Build.SERIAL != null ? Build.SERIAL : "--");

        // Maestro 状态
        updateMaestroStatus();

        // PC 配对
        String savedUrl = SyncClient.getBaseUrl(requireContext());
        if (savedUrl != null && !savedUrl.isEmpty() && syncBaseUrl.getText().toString().isEmpty()) {
            syncBaseUrl.setText(savedUrl);
        }
    }

    private void updateMaestroStatus() {
        // 手机端检测 Maestro 环境 (通过检测 Java/adb 等)
        boolean maestroAvailable = checkMaestro();
        if (maestroAvailable) {
            maestroStatusDot.setBackgroundResource(R.drawable.status_dot_green);
            maestroStatusText.setText("Maestro 环境就绪");
            maestroVersionText.setText("引擎: Maestro CLI (与 PC 端协同)");
        } else {
            maestroStatusDot.setBackgroundResource(R.drawable.status_dot_red);
            maestroStatusText.setText("Maestro 环境未检测到 (需 PC 端安装)");
            maestroVersionText.setText("请在 PC 端运行 install_maestro_env 安装脚本");
        }
    }

    private boolean checkMaestro() {
        // 手机端不直接安装 Maestro CLI，通过 PC 端的 mobile_engine 同调
        // 这里检查基础条件: Java 环境 (理论上 Maestro 在 PC 运行)
        try {
            Process p = Runtime.getRuntime().exec(new String[]{"which", "java"});
            p.waitFor();
            return p.exitValue() == 0;
        } catch (Exception e) {
            return false;
        }
    }

    private void pairWithPC() {
        String url = syncBaseUrl.getText().toString().trim();
        String code = syncPairCode.getText().toString().trim();
        if (url.isEmpty() || code.isEmpty()) {
            Toast.makeText(requireContext(), "请输入 PC 地址和配对码", Toast.LENGTH_SHORT).show();
            return;
        }
        String deviceId = Build.SERIAL != null ? Build.SERIAL : "unknown";
        SyncClient.saveBaseUrl(requireContext(), url);
        SyncClient.pair(requireContext(), url, code, deviceId, new SyncClient.Callback() {
            @Override
            public void onSuccess(JSONObject data) {
                Toast.makeText(requireContext(), "配对成功", Toast.LENGTH_SHORT).show();
            }
            @Override
            public void onError(String message) {
                Toast.makeText(requireContext(), "配对失败: " + message, Toast.LENGTH_SHORT).show();
            }
        });
    }

    private void syncCases() {
        String token = SyncClient.getToken(requireContext());
        if (token.isEmpty()) {
            Toast.makeText(requireContext(), "请先配对 PC", Toast.LENGTH_SHORT).show();
            return;
        }
        SyncClient.fetchCases(requireContext(), new SyncClient.Callback() {
            @Override
            public void onSuccess(JSONObject data) {
                Toast.makeText(requireContext(), "同步成功", Toast.LENGTH_SHORT).show();
            }
            @Override
            public void onError(String message) {
                Toast.makeText(requireContext(), "同步失败: " + message, Toast.LENGTH_SHORT).show();
            }
        });
    }

    private void saveToPC() {
        Toast.makeText(requireContext(), "保存到 PC 功能开发中", Toast.LENGTH_SHORT).show();
    }
}
