package com.testory.assistant;

import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;
import android.view.View;
import android.widget.ArrayAdapter;
import android.widget.Button;
import android.widget.EditText;
import android.widget.Spinner;
import android.widget.TextView;
import android.widget.Toast;

import androidx.appcompat.app.AppCompatActivity;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * 本地录制/运行 + PC 同步 + Vision Probe。
 */
public class MainActivity extends AppCompatActivity {

    private final Handler handler = new Handler(Looper.getMainLooper());
    private final ExecutorService bg = Executors.newSingleThreadExecutor();

    private TextView statusView;
    private TextView modeView;
    private TextView hintView;
    private TextView recordStatusView;
    private TextView stepsListView;
    private Spinner caseSpinner;
    private EditText syncBaseUrl;
    private EditText syncPairCode;

    private long activeCaseId = -1L;
    private boolean recording = false;
    private final List<JSONObject> caseOptions = new ArrayList<>();

    private final Runnable refreshRunnable = new Runnable() {
        @Override
        public void run() {
            refreshStatus();
            if (recording) pollRecordedSteps();
            handler.postDelayed(this, 900);
        }
    };

    private final Runnable runPollRunnable = new Runnable() {
        @Override
        public void run() {
            if (SyncClient.getToken(MainActivity.this).isEmpty()) return;
            SyncClient.pollPendingRun(MainActivity.this, new SyncClient.Callback() {
                @Override
                public void onSuccess(JSONObject data) {
                    if (data == null || !data.optBoolean("has_job")) return;
                    executeRemoteRunJob(data);
                }

                @Override
                public void onError(String message) {
                    /* ignore poll errors */
                }
            });
            handler.postDelayed(runPollRunnable, 4000);
        }
    };

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);

        statusView = findViewById(R.id.statusText);
        modeView = findViewById(R.id.modeText);
        hintView = findViewById(R.id.hintText);
        recordStatusView = findViewById(R.id.recordStatusText);
        stepsListView = findViewById(R.id.stepsListText);
        caseSpinner = findViewById(R.id.caseSpinner);
        syncBaseUrl = findViewById(R.id.syncBaseUrl);
        syncPairCode = findViewById(R.id.syncPairCode);

        hintView.setText(getString(R.string.main_hint_jsonrpc));
        syncBaseUrl.setText(SyncClient.getBaseUrl(this));

        findViewById(R.id.openAccessibilityBtn).setOnClickListener(v -> openAccessibilitySettings());
        findViewById(R.id.btnStartRecord).setOnClickListener(v -> startLocalRecording());
        findViewById(R.id.btnStopRecord).setOnClickListener(v -> stopLocalRecording());
        findViewById(R.id.btnRunCase).setOnClickListener(v -> runSelectedCase());
        findViewById(R.id.btnPair).setOnClickListener(v -> pairWithPc());
        findViewById(R.id.btnSyncCases).setOnClickListener(v -> syncCasesFromPc());
        findViewById(R.id.btnVisionProbe).setOnClickListener(v -> runVisionProbe());

        ensureDraftCase();
        reloadCaseSpinner();
        refreshStepsList();
    }

    @Override
    protected void onResume() {
        super.onResume();
        handler.post(refreshRunnable);
        handler.postDelayed(runPollRunnable, 2000);
    }

    @Override
    protected void onPause() {
        handler.removeCallbacks(refreshRunnable);
        handler.removeCallbacks(runPollRunnable);
        super.onPause();
    }

    private void ensureDraftCase() {
        try {
            LocalStore store = LocalStore.get(this);
            List<JSONObject> cases = store.listCases();
            if (cases.isEmpty()) {
                activeCaseId = store.upsertCase(getString(R.string.local_draft_case), null, 0);
            } else {
                activeCaseId = cases.get(0).getLong("id");
            }
        } catch (Exception e) {
            activeCaseId = -1L;
        }
    }

    private void reloadCaseSpinner() {
        try {
            caseOptions.clear();
            caseOptions.addAll(LocalStore.get(this).listCases());
            List<String> labels = new ArrayList<>();
            for (JSONObject c : caseOptions) {
                labels.add(c.optString("name", "case") + " (#" + c.optLong("id") + ")");
            }
            ArrayAdapter<String> adapter = new ArrayAdapter<>(this,
                    android.R.layout.simple_spinner_item, labels);
            adapter.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item);
            caseSpinner.setAdapter(adapter);
        } catch (Exception ignored) {
        }
    }

    private long selectedCaseId() {
        int idx = caseSpinner.getSelectedItemPosition();
        if (idx >= 0 && idx < caseOptions.size()) {
            try {
                return caseOptions.get(idx).getLong("id");
            } catch (Exception ignored) {
            }
        }
        return activeCaseId;
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

    private void startLocalRecording() {
        if (!AssistantSession.isAccessibilityReady()) {
            toast("请先开启无障碍");
            return;
        }
        activeCaseId = selectedCaseId();
        if (activeCaseId < 0) ensureDraftCase();
        AssistantSession.setArmedMode(AssistantSession.MODE_RECORD);
        PluginForegroundService.startRecording(this);
        recording = true;
        recordStatusView.setText(R.string.recording_active);
        toast(getString(R.string.recording_active));
    }

    private void stopLocalRecording() {
        AssistantSession.setArmedMode(AssistantSession.MODE_IDLE);
        PluginForegroundService.stopRecording(this);
        recording = false;
        recordStatusView.setText(R.string.recording_stopped);
        refreshStepsList();
    }

    private void pollRecordedSteps() {
        if (activeCaseId < 0) return;
        JSONArray batch = PluginHttpServer.drainPendingSteps(20);
        if (batch.length() == 0) return;
        try {
            LocalStore store = LocalStore.get(this);
            for (int i = 0; i < batch.length(); i++) {
                store.appendRecordedStep(activeCaseId, batch.getJSONObject(i));
            }
            refreshStepsList();
        } catch (Exception ignored) {
        }
    }

    private void refreshStepsList() {
        long cid = selectedCaseId();
        if (cid < 0) {
            stepsListView.setText(R.string.no_steps);
            return;
        }
        bg.execute(() -> {
            try {
                List<JSONObject> steps = LocalStore.get(this).getSteps(cid);
                StringBuilder sb = new StringBuilder();
                for (JSONObject s : steps) {
                    sb.append(s.optInt("step_order")).append(". ")
                            .append(s.optString("action")).append(" — ")
                            .append(s.optString("description", "")).append("\n");
                }
                String text = sb.length() > 0 ? sb.toString() : getString(R.string.no_steps);
                runOnUiThread(() -> stepsListView.setText(text));
            } catch (Exception e) {
                runOnUiThread(() -> stepsListView.setText(e.getMessage()));
            }
        });
    }

    private void runSelectedCase() {
        if (!AssistantSession.isAccessibilityReady()) {
            toast("请先开启无障碍");
            return;
        }
        long cid = selectedCaseId();
        if (cid < 0) {
            toast("无用例");
            return;
        }
        bg.execute(() -> {
            try {
                List<JSONObject> steps = LocalStore.get(this).getSteps(cid);
                if (steps.isEmpty()) {
                    runOnUiThread(() -> toast("该用例无步骤"));
                    return;
                }
                long sessionId = LocalStore.get(this).startRunSession(cid);
                JSONObject result = ReplayEngine.runSteps(steps, null);
                LocalStore.get(this).finishRunSession(sessionId,
                        result.optBoolean("success") ? "success" : "error",
                        result.optJSONArray("results"));
                runOnUiThread(() -> toast(result.optBoolean("success")
                        ? getString(R.string.run_success)
                        : getString(R.string.run_failed) + ": " + result.optString("error")));
            } catch (Exception e) {
                runOnUiThread(() -> toast(e.getMessage()));
            }
        });
    }

    private void executeRemoteRunJob(JSONObject job) {
        bg.execute(() -> {
            try {
                String jobId = job.optString("job_id", "");
                JSONArray stepsArr = job.optJSONArray("steps");
                if (stepsArr == null || stepsArr.length() == 0) return;
                List<JSONObject> steps = new ArrayList<>();
                for (int i = 0; i < stepsArr.length(); i++) {
                    steps.add(stepsArr.getJSONObject(i));
                }
                JSONObject result = ReplayEngine.runSteps(steps, null);
                JSONObject payload = new JSONObject();
                payload.put("status", result.optBoolean("success") ? "success" : "error");
                payload.put("results", result.optJSONArray("results"));
                payload.put("error", result.optString("error", ""));
                SyncClient.postRunEvents(this, jobId, payload, new SyncClient.Callback() {
                    @Override
                    public void onSuccess(JSONObject data) {
                        runOnUiThread(() -> toast(getString(R.string.run_success)));
                    }

                    @Override
                    public void onError(String message) {
                        runOnUiThread(() -> toast(message));
                    }
                });
            } catch (Exception e) {
                runOnUiThread(() -> toast(e.getMessage()));
            }
        });
    }

    private void pairWithPc() {
        String base = syncBaseUrl.getText().toString().trim();
        String code = syncPairCode.getText().toString().trim();
        if (base.isEmpty() || code.length() != 6) {
            toast("请填写 PC 地址与 6 位配对码");
            return;
        }
        String deviceId = Settings.Secure.getString(getContentResolver(), Settings.Secure.ANDROID_ID);
        SyncClient.pair(this, base, code, deviceId == null ? "device" : deviceId, new SyncClient.Callback() {
            @Override
            public void onSuccess(JSONObject data) {
                toast(getString(R.string.pair_ok));
            }

            @Override
            public void onError(String message) {
                toast(message);
            }
        });
    }

    private void syncCasesFromPc() {
        if (SyncClient.getToken(this).isEmpty()) {
            toast("请先配对 PC");
            return;
        }
        bg.execute(() -> {
            try {
                java.net.URL url = new java.net.URL(
                        SyncClient.getBaseUrl(this).replaceAll("/+$", "") + "/api/mobile/sync/cases");
                java.net.HttpURLConnection conn = (java.net.HttpURLConnection) url.openConnection();
                conn.setRequestProperty("X-Mobile-Device-Token", SyncClient.getToken(this));
                conn.setConnectTimeout(15000);
                conn.setReadTimeout(60000);
                java.io.BufferedReader br = new java.io.BufferedReader(
                        new java.io.InputStreamReader(conn.getInputStream(), java.nio.charset.StandardCharsets.UTF_8));
                StringBuilder sb = new StringBuilder();
                String line;
                while ((line = br.readLine()) != null) sb.append(line);
                br.close();
                JSONObject data = new JSONObject(sb.toString());
                JSONArray cases = data.optJSONArray("cases");
                LocalStore store = LocalStore.get(this);
                if (cases != null) {
                    for (int i = 0; i < cases.length(); i++) {
                        JSONObject c = cases.getJSONObject(i);
                        int remoteId = c.optInt("id", 0);
                        long localId = store.upsertCase(
                                c.optString("name", "case"),
                                remoteId > 0 ? remoteId : null,
                                c.optInt("project_id", 0));
                        if (remoteId > 0) {
                            java.net.URL burl = new java.net.URL(
                                    SyncClient.getBaseUrl(this).replaceAll("/+$", "")
                                            + "/api/mobile/sync/cases/" + remoteId + "/bundle");
                            java.net.HttpURLConnection bc = (java.net.HttpURLConnection) burl.openConnection();
                            bc.setRequestProperty("X-Mobile-Device-Token", SyncClient.getToken(this));
                            bc.setConnectTimeout(15000);
                            bc.setReadTimeout(60000);
                            java.io.BufferedReader bbr = new java.io.BufferedReader(
                                    new java.io.InputStreamReader(bc.getInputStream(),
                                            java.nio.charset.StandardCharsets.UTF_8));
                            StringBuilder bsb = new StringBuilder();
                            while ((line = bbr.readLine()) != null) bsb.append(line);
                            bbr.close();
                            JSONObject bundle = new JSONObject(bsb.toString());
                            store.replaceSteps(localId, bundle.optJSONArray("steps"));
                        }
                    }
                }
                runOnUiThread(() -> {
                    reloadCaseSpinner();
                    refreshStepsList();
                    toast(getString(R.string.sync_ok));
                });
            } catch (Exception e) {
                runOnUiThread(() -> toast(e.getMessage()));
            }
        });
    }

    private void runVisionProbe() {
        if (SyncClient.getToken(this).isEmpty()) {
            toast("请先配对 PC");
            return;
        }
        bg.execute(() -> {
            try {
                AssistantAccessibilityService svc = AssistantSession.getService();
                if (svc == null) {
                    runOnUiThread(() -> toast("无障碍未就绪"));
                    return;
                }
                byte[] png = svc.captureScreenshotPng();
                if (png == null) {
                    runOnUiThread(() -> toast("截图不可用（需 Android 11+）"));
                    return;
                }
                JSONObject treeResp = new JSONObject();
                treeResp.put("tree", new JSONObject());
                SyncClient.probeVision(this, "分析当前屏幕并生成 3-5 步 Android 测试步骤",
                        android.util.Base64.encodeToString(png, android.util.Base64.NO_WRAP),
                        treeResp, new SyncClient.Callback() {
                            @Override
                            public void onSuccess(JSONObject data) {
                                try {
                                    JSONObject plan = data.optJSONObject("plan");
                                    if (plan == null) {
                                        toast("未生成 plan");
                                        return;
                                    }
                                    JSONArray steps = plan.optJSONArray("steps");
                                    long cid = selectedCaseId();
                                    if (cid < 0) ensureDraftCase();
                                    cid = selectedCaseId();
                                    LocalStore.get(MainActivity.this).replaceSteps(cid, steps);
                                    refreshStepsList();
                                    toast("已生成 " + (steps != null ? steps.length() : 0) + " 步");
                                } catch (Exception e) {
                                    toast(e.getMessage());
                                }
                            }

                            @Override
                            public void onError(String message) {
                                toast(message);
                            }
                        });
            } catch (Exception e) {
                runOnUiThread(() -> toast(e.getMessage()));
            }
        });
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
            android.content.Intent intent = new android.content.Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS);
            intent.addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK);
            startActivity(intent);
        } catch (Exception ignored) {
        }
    }

    private void toast(String msg) {
        Toast.makeText(this, msg, Toast.LENGTH_SHORT).show();
    }
}
