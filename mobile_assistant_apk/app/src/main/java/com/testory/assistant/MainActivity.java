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
    private TextView versionView;
    private TextView modeView;
    private TextView hintView;
    private TextView recordStatusView;
    private TextView stepsListView;
    private Spinner caseSpinner;
    private Spinner projectSpinner;
    private EditText syncBaseUrl;
    private EditText syncPairCode;

    private long activeCaseId = -1L;
    private boolean recording = false;
    private boolean spinnerInitializing = false;
    private final List<JSONObject> caseOptions = new ArrayList<>();
    private final List<JSONObject> projectOptions = new ArrayList<>();

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
        versionView = findViewById(R.id.versionText);
        modeView = findViewById(R.id.modeText);
        hintView = findViewById(R.id.hintText);
        recordStatusView = findViewById(R.id.recordStatusText);
        stepsListView = findViewById(R.id.stepsListText);
        caseSpinner = findViewById(R.id.caseSpinner);
        projectSpinner = findViewById(R.id.projectSpinner);
        syncBaseUrl = findViewById(R.id.syncBaseUrl);
        syncPairCode = findViewById(R.id.syncPairCode);

        hintView.setText(getString(R.string.main_hint_jsonrpc));
        if (versionView != null) {
            versionView.setText("v" + BuildConfig.VERSION_NAME + " (" + BuildConfig.VERSION_CODE + ")");
        }
        syncBaseUrl.setText(SyncClient.getBaseUrl(this));

        findViewById(R.id.openAccessibilityBtn).setOnClickListener(v -> openAccessibilitySettings());
        findViewById(R.id.btnStartRecord).setOnClickListener(v -> startLocalRecording());
        findViewById(R.id.btnStopRecord).setOnClickListener(v -> stopLocalRecording());
        findViewById(R.id.btnRunCase).setOnClickListener(v -> runSelectedCase());
        findViewById(R.id.btnStopRun).setOnClickListener(v -> stopRunningCase());
        findViewById(R.id.btnPair).setOnClickListener(v -> pairWithPc());
        findViewById(R.id.btnSyncCases).setOnClickListener(v -> syncCasesFromPc());
        findViewById(R.id.btnSaveToPc).setOnClickListener(v -> saveCaseToPc());
        findViewById(R.id.btnVisionProbe).setOnClickListener(v -> runVisionProbe());

        ensureDraftCase();
        setupSpinnerListeners();
        reloadProjectSpinner();
        reloadCaseSpinner();
        refreshStepsList();
        AssistantSession.setStepsUpdatedListener(() -> runOnUiThread(this::refreshStepsList));
    }

    private void setupSpinnerListeners() {
        projectSpinner.setOnItemSelectedListener(new android.widget.AdapterView.OnItemSelectedListener() {
            @Override
            public void onItemSelected(android.widget.AdapterView<?> parent, View view, int position, long id) {
                if (spinnerInitializing) return;
                reloadCaseSpinner();
                refreshStepsList();
            }

            @Override
            public void onNothingSelected(android.widget.AdapterView<?> parent) {
            }
        });
        caseSpinner.setOnItemSelectedListener(new android.widget.AdapterView.OnItemSelectedListener() {
            @Override
            public void onItemSelected(android.widget.AdapterView<?> parent, View view, int position, long id) {
                if (spinnerInitializing) return;
                activeCaseId = selectedCaseId();
                refreshStepsList();
            }

            @Override
            public void onNothingSelected(android.widget.AdapterView<?> parent) {
            }
        });
    }

    @Override
    protected void onResume() {
        super.onResume();
        syncUiToRecordedCase();
        handler.post(refreshRunnable);
        handler.postDelayed(runPollRunnable, 2000);
    }

    @Override
    protected void onPause() {
        if (!recording) {
            handler.removeCallbacks(refreshRunnable);
        }
        handler.removeCallbacks(runPollRunnable);
        super.onPause();
    }

    @Override
    protected void onDestroy() {
        AssistantSession.setStepsUpdatedListener(null);
        super.onDestroy();
    }

    private void ensureDraftCase() {
        try {
            LocalStore store = LocalStore.get(this);
            List<JSONObject> cases = store.listCases();
            if (cases.isEmpty()) {
                activeCaseId = store.upsertCase(
                        getString(R.string.local_draft_case), null, 0, getString(R.string.local_recordings));
            } else {
                activeCaseId = cases.get(0).getLong("id");
            }
            AssistantSession.setLocalCaseId(activeCaseId);
        } catch (Exception e) {
            activeCaseId = -1L;
        }
    }

    private void reloadProjectSpinner() {
        try {
            spinnerInitializing = true;
            projectOptions.clear();
            JSONObject all = new JSONObject();
            all.put("project_id", 0);
            all.put("project_name", getString(R.string.all_projects));
            projectOptions.add(all);
            LocalStore store = LocalStore.get(this);
            if (store.hasLocalCases()) {
                JSONObject local = new JSONObject();
                local.put("project_id", LocalStore.LOCAL_PROJECT_ID);
                local.put("project_name", getString(R.string.local_recordings));
                projectOptions.add(local);
            }
            for (JSONObject p : store.listProjects()) {
                if (p.optInt("project_id", 0) <= 0) continue;
                projectOptions.add(p);
            }
            List<String> labels = new ArrayList<>();
            for (JSONObject p : projectOptions) {
                int pid = p.optInt("project_id", 0);
                String pname = p.optString("project_name", "");
                if (pid <= 0 && pid != LocalStore.LOCAL_PROJECT_ID) {
                    labels.add(pname);
                } else if (pid == LocalStore.LOCAL_PROJECT_ID) {
                    labels.add(getString(R.string.local_recordings));
                } else if (pname.isEmpty()) {
                    labels.add(getString(R.string.project_fallback, pid));
                } else {
                    labels.add(pname);
                }
            }
            ArrayAdapter<String> adapter = new ArrayAdapter<>(this,
                    android.R.layout.simple_spinner_item, labels);
            adapter.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item);
            projectSpinner.setAdapter(adapter);
        } catch (Exception ignored) {
        } finally {
            spinnerInitializing = false;
        }
    }

    private int selectedProjectId() {
        int idx = projectSpinner.getSelectedItemPosition();
        if (idx >= 0 && idx < projectOptions.size()) {
            return projectOptions.get(idx).optInt("project_id", 0);
        }
        return 0;
    }

    private void reloadCaseSpinner() {
        reloadCaseSpinner(-1L);
    }

    private void reloadCaseSpinner(long preferCaseId) {
        try {
            spinnerInitializing = true;
            long prevCaseId = preferCaseId > 0 ? preferCaseId : selectedCaseId();
            if (prevCaseId <= 0 && activeCaseId > 0) {
                prevCaseId = activeCaseId;
            }
            caseOptions.clear();
            caseOptions.addAll(LocalStore.get(this).listCasesByProject(selectedProjectId()));
            List<String> labels = new ArrayList<>();
            for (JSONObject c : caseOptions) {
                labels.add(c.optString("name", "case") + " (#" + c.optLong("id") + ")");
            }
            ArrayAdapter<String> adapter = new ArrayAdapter<>(this,
                    android.R.layout.simple_spinner_item, labels);
            adapter.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item);
            caseSpinner.setAdapter(adapter);
            int selectIdx = 0;
            for (int i = 0; i < caseOptions.size(); i++) {
                if (caseOptions.get(i).optLong("id") == prevCaseId) {
                    selectIdx = i;
                    break;
                }
            }
            if (!caseOptions.isEmpty()) {
                caseSpinner.setSelection(selectIdx);
                activeCaseId = caseOptions.get(selectIdx).optLong("id");
            } else {
                activeCaseId = -1L;
            }
        } catch (Exception ignored) {
        } finally {
            spinnerInitializing = false;
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
        boolean serviceLive = AssistantSession.isAccessibilityReady();
        boolean a11ySetting = AccessibilityProbe.isAccessibilityOn(this);
        boolean server = PluginHttpServer.isRunning();
        String mode = AssistantSession.getArmedMode();

        if (serviceLive && server) {
            statusView.setText(R.string.status_ready);
            statusView.setTextColor(0xFF059669);
        } else if (serviceLive || a11ySetting) {
            statusView.setText(server ? R.string.status_ready : R.string.status_waiting_platform);
            statusView.setTextColor(0xFFB45309);
        } else if (AccessibilityProbe.isEnabledInSettings(this)
                || AccessibilityProbe.isEnabledViaManager(this)) {
            statusView.setText(R.string.status_a11y_pending);
            statusView.setTextColor(0xFFB45309);
        } else {
            statusView.setText(R.string.status_need_accessibility);
            statusView.setTextColor(0xFFB91C1C);
        }

        if (AssistantSession.MODE_IDLE.equals(mode)) {
            modeView.setText(R.string.mode_idle);
            if (recording) {
                recording = false;
                recordStatusView.setText(R.string.recording_stopped);
                syncUiToRecordedCase();
                refreshStepsList();
            }
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
        doStartLocalRecording();
    }

    private void doStartLocalRecording() {
        activeCaseId = selectedCaseId();
        if (activeCaseId < 0) {
            ensureDraftCase();
            activeCaseId = AssistantSession.getLocalCaseId();
        }
        if (activeCaseId < 0) activeCaseId = selectedCaseId();
        AssistantSession.setLocalCaseId(activeCaseId);
        reloadProjectSpinner();
        focusCaseById(activeCaseId);
        RecordingController.startRecording(this, activeCaseId);
        recording = true;
        recordStatusView.setText(R.string.recording_active);
        toast(getString(R.string.recording_active));
    }

    private void stopLocalRecording() {
        RecordingController.stopRecording(this);
        recording = false;
        recordStatusView.setText(R.string.recording_stopped);
        syncUiToRecordedCase();
        refreshStepsList();
    }

    private void syncUiToRecordedCase() {
        long cid = AssistantSession.getLocalCaseId();
        if (cid > 0) {
            focusCaseById(cid);
        }
    }

    private void focusCaseById(long caseId) {
        if (caseId < 0) return;
        bg.execute(() -> {
            try {
                JSONObject row = LocalStore.get(this).getCase(caseId);
                if (row == null) return;
                runOnUiThread(() -> {
                    try {
                        spinnerInitializing = true;
                        reloadProjectSpinner();
                        int projectIdx = 0;
                        boolean localOnly = row.optBoolean("local_only")
                                || row.isNull("remote_id");
                        int targetProject = localOnly
                                ? LocalStore.LOCAL_PROJECT_ID
                                : row.optInt("project_id", 0);
                        if (!localOnly && targetProject <= 0) {
                            targetProject = 0;
                        }
                        for (int i = 0; i < projectOptions.size(); i++) {
                            if (projectOptions.get(i).optInt("project_id") == targetProject) {
                                projectIdx = i;
                                break;
                            }
                        }
                        projectSpinner.setSelection(projectIdx);
                        activeCaseId = caseId;
                        reloadCaseSpinner(caseId);
                        refreshStepsList();
                    } catch (Exception ignored) {
                    } finally {
                        spinnerInitializing = false;
                    }
                });
            } catch (Exception ignored) {
            }
        });
    }

    private void pollRecordedSteps() {
        long cid = resolveStepsCaseId();
        if (cid < 0) return;
        activeCaseId = cid;
        JSONArray batch = PluginHttpServer.drainPendingSteps(20);
        if (batch.length() == 0) return;
        try {
            LocalStore store = LocalStore.get(this);
            for (int i = 0; i < batch.length(); i++) {
                JSONObject raw = batch.getJSONObject(i);
                JSONObject step = RecordStepConverter.toDbStep(raw, 0);
                if (RecordEventFilter.isAssistantStep(step)) continue;
                store.appendNormalizedStep(cid, step);
            }
            refreshStepsList();
        } catch (Exception ignored) {
        }
    }

    private long resolveStepsCaseId() {
        long sessionCase = AssistantSession.getLocalCaseId();
        if (sessionCase > 0) return sessionCase;
        long selected = selectedCaseId();
        if (selected > 0) return selected;
        return activeCaseId;
    }

    private void refreshStepsList() {
        long cid = resolveStepsCaseId();
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
        if (RunSession.isRunning()) {
            toast("用例正在运行");
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
                runOnUiThread(() -> toast(getString(R.string.run_started)));
                RunSession.start(this, steps, result -> bg.execute(() -> {
                    try {
                        LocalStore.get(this).finishRunSession(sessionId,
                                result.optBoolean("success") ? "success"
                                        : (result.optString("status", "").equals("cancelled")
                                        ? "cancelled" : "error"),
                                result.optJSONArray("results"));
                        runOnUiThread(() -> toast(result.optBoolean("success")
                                ? getString(R.string.run_success)
                                : getString(R.string.run_failed) + ": "
                                + result.optString("error")));
                        refreshStepsList();
                    } catch (Exception e) {
                        runOnUiThread(() -> toast(e.getMessage()));
                    }
                }));
            } catch (Exception e) {
                runOnUiThread(() -> toast(e.getMessage()));
            }
        });
    }

    private void stopRunningCase() {
        if (!RunSession.isRunning()) {
            toast("当前无运行任务");
            return;
        }
        RunSession.cancel();
        toast(getString(R.string.run_cancelled));
    }

    private void executeRemoteRunJob(JSONObject job) {
        if (RunSession.isRunning()) return;
        bg.execute(() -> {
            try {
                String jobId = job.optString("job_id", "");
                JSONArray stepsArr = job.optJSONArray("steps");
                if (stepsArr == null || stepsArr.length() == 0) return;
                List<JSONObject> steps = new ArrayList<>();
                for (int i = 0; i < stepsArr.length(); i++) {
                    steps.add(stepsArr.getJSONObject(i));
                }
                runOnUiThread(() -> RunSession.start(this, steps, result -> {
                    JSONObject payload = new JSONObject();
                    try {
                        payload.put("status", result.optBoolean("success") ? "success" : "error");
                        payload.put("results", result.optJSONArray("results"));
                        payload.put("error", result.optString("error", ""));
                    } catch (Exception ignored) {
                    }
                    SyncClient.postRunEvents(MainActivity.this, jobId, payload, new SyncClient.Callback() {
                        @Override
                        public void onSuccess(JSONObject data) {
                            runOnUiThread(() -> toast(getString(R.string.run_success)));
                        }

                        @Override
                        public void onError(String message) {
                            runOnUiThread(() -> toast(message));
                        }
                    });
                }));
            } catch (Exception e) {
                runOnUiThread(() -> toast(e.getMessage()));
            }
        });
    }

    private void saveCaseToPc() {
        if (SyncClient.getToken(this).isEmpty()) {
            toast(getString(R.string.save_to_pc_need_pair));
            return;
        }
        long cid = selectedCaseId();
        if (cid < 0) {
            toast("无用例");
            return;
        }
        bg.execute(() -> {
            try {
                LocalStore store = LocalStore.get(this);
                JSONObject caseRow = store.getCase(cid);
                List<JSONObject> steps = store.getSteps(cid);
                if (steps.isEmpty()) {
                    runOnUiThread(() -> toast(getString(R.string.save_to_pc_no_steps)));
                    return;
                }
                JSONArray arr = new JSONArray();
                for (JSONObject s : steps) {
                    arr.put(s);
                }
                String caseName = caseRow != null ? caseRow.optString("name", "移动端用例") : "移动端用例";
                int projectId = caseRow != null ? caseRow.optInt("project_id", 0) : 0;
                Integer remoteId = null;
                if (caseRow != null && !caseRow.isNull("remote_id")) {
                    remoteId = caseRow.optInt("remote_id");
                }
                if (projectId <= 0 || projectId == LocalStore.LOCAL_PROJECT_ID) {
                    int spinnerPid = selectedProjectId();
                    if (spinnerPid > 0 && spinnerPid != LocalStore.LOCAL_PROJECT_ID) {
                        projectId = spinnerPid;
                    }
                }
                final int resolvedProjectId = projectId;
                final Integer resolvedRemoteId = remoteId;
                if (resolvedProjectId > 0 && resolvedProjectId != LocalStore.LOCAL_PROJECT_ID) {
                    pushCaseToPcServer(cid, caseName, arr, resolvedProjectId, resolvedRemoteId);
                    return;
                }
                runOnUiThread(() -> SyncClient.fetchProjects(this, new SyncClient.Callback() {
                    @Override
                    public void onSuccess(JSONObject data) {
                        bg.execute(() -> {
                            try {
                                JSONArray projects = data.optJSONArray("projects");
                                if (projects == null || projects.length() == 0) {
                                    runOnUiThread(() -> toast(getString(R.string.save_to_pc_need_project)));
                                    return;
                                }
                                JSONObject first = projects.getJSONObject(0);
                                int pid = first.optInt("id", 0);
                                if (pid <= 0) {
                                    runOnUiThread(() -> toast(getString(R.string.save_to_pc_need_project)));
                                    return;
                                }
                                pushCaseToPcServer(cid, caseName, arr, pid, resolvedRemoteId);
                            } catch (Exception e) {
                                runOnUiThread(() -> toast(e.getMessage()));
                            }
                        });
                    }

                    @Override
                    public void onError(String message) {
                        toast(message != null ? message : getString(R.string.save_to_pc_need_project));
                    }
                }));
            } catch (Exception e) {
                runOnUiThread(() -> toast(e.getMessage()));
            }
        });
    }

    private void pushCaseToPcServer(
            long localCaseId, String caseName, JSONArray steps, int projectId, Integer remoteCaseId) {
        boolean replace = remoteCaseId != null && remoteCaseId > 0;
        SyncClient.pushCase(this, projectId, caseName, steps, remoteCaseId, replace,
                new SyncClient.Callback() {
                    @Override
                    public void onSuccess(JSONObject data) {
                        bg.execute(() -> {
                            try {
                                int pcCaseId = data.optInt("case_id", 0);
                                String pname = data.optString("project_name", "");
                                if (pname.isEmpty()) {
                                    pname = getString(R.string.project_fallback, projectId);
                                }
                                LocalStore store = LocalStore.get(MainActivity.this);
                                store.linkToRemote(localCaseId, pcCaseId, projectId, pname);
                                runOnUiThread(() -> {
                                    toast(getString(R.string.save_to_pc_ok, pcCaseId));
                                    reloadProjectSpinner();
                                    reloadCaseSpinner(localCaseId);
                                });
                            } catch (Exception e) {
                                runOnUiThread(() -> toast(e.getMessage()));
                            }
                        });
                    }

                    @Override
                    public void onError(String message) {
                        runOnUiThread(() -> toast(message));
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
                String hint = message;
                if (message != null && message.contains("Cleartext HTTP")) {
                    hint = "请先在 PC 端点击「安装插件」升级到 v1.1.3+，再配对";
                }
                toast(hint);
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
                                c.optInt("project_id", 0),
                                c.optString("project_name", ""));
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
                    reloadProjectSpinner();
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

    private void openAccessibilitySettings() {
        try {
            android.content.Intent intent =
                    new android.content.Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS);
            intent.addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK);
            startActivity(intent);
        } catch (Exception ignored) {
        }
    }

    private void toast(String msg) {
        Toast.makeText(this, msg, Toast.LENGTH_SHORT).show();
    }
}
