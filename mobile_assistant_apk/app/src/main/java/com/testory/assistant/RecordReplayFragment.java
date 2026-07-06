package com.testory.assistant;

import android.app.AlertDialog;
import android.content.Context;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;
import android.widget.ArrayAdapter;
import android.widget.Button;
import android.widget.EditText;
import android.widget.Spinner;
import android.widget.TextView;
import android.widget.Toast;

import androidx.annotation.NonNull;
import androidx.annotation.Nullable;
import androidx.fragment.app.Fragment;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public class RecordReplayFragment extends Fragment {

    private TextView recordStatusTag, recordInfoText;
    private TextView runStatusText, stepsListText, stepCountTag;
    private Spinner caseSpinner;
    private View stepsPreviewCard;
    private Button btnStartRecord, btnStopRecord, btnRunCase, btnStopRun;

    private final Handler handler = new Handler(Looper.getMainLooper());
    private final ExecutorService bg = Executors.newSingleThreadExecutor();
    private boolean recording = false;
    private long activeCaseId = -1L;

    private final Runnable refreshRunnable = new Runnable() {
        @Override
        public void run() {
            // 同步全局录制状态：悬浮窗或其他途径停止后，自动刷新本地 UI
            if (recording && !AssistantSession.MODE_RECORD.equals(AssistantSession.getArmedMode())) {
                recording = false;
                recordStatusTag.setText("已停止");
                btnStartRecord.setEnabled(true);
                btnStopRecord.setEnabled(false);
                recordInfoText.setText("录制已停止，点击「运行回放」执行步骤");
                loadCases();
            }
            if (recording) pollSteps();
            handler.postDelayed(this, recording ? 500 : 900);
        }
    };

    @Nullable
    @Override
    public View onCreateView(@NonNull LayoutInflater inflater, @Nullable ViewGroup container,
                             @Nullable Bundle savedInstanceState) {
        return inflater.inflate(R.layout.fragment_record_replay, container, false);
    }

    @Override
    public void onViewCreated(@NonNull View view, @Nullable Bundle savedInstanceState) {
        super.onViewCreated(view, savedInstanceState);

        recordStatusTag = view.findViewById(R.id.record_status_tag);
        recordInfoText = view.findViewById(R.id.record_info_text);
        runStatusText = view.findViewById(R.id.run_status_text);
        stepsListText = view.findViewById(R.id.steps_list_text);
        stepCountTag = view.findViewById(R.id.step_count_tag);
        caseSpinner = view.findViewById(R.id.case_spinner);
        stepsPreviewCard = view.findViewById(R.id.steps_preview_card);
        btnStartRecord = view.findViewById(R.id.btn_start_record);
        btnStopRecord = view.findViewById(R.id.btn_stop_record);
        btnRunCase = view.findViewById(R.id.btn_run_case);
        btnStopRun = view.findViewById(R.id.btn_stop_run);

        btnStartRecord.setOnClickListener(v -> startRecording());
        btnStopRecord.setOnClickListener(v -> stopRecording());
        btnRunCase.setOnClickListener(v -> runReplay());
        btnStopRun.setOnClickListener(v -> RunSession.cancel());

        view.findViewById(R.id.btn_refresh_cases).setOnClickListener(v -> loadCases());
        view.findViewById(R.id.btn_new_case_inline).setOnClickListener(v -> showNewCaseDialog());

        loadCases();
        handler.post(refreshRunnable);
    }

    void loadCases() {
        Context ctx = getContext();
        if (ctx == null) return;
        bg.execute(() -> {
            try {
                LocalStore store = LocalStore.get(ctx);
                List<JSONObject> cases = store.listCases();
                List<String> labels = new ArrayList<>();
                final List<Long> ids = new ArrayList<>();

                for (JSONObject c : cases) {
                    String name = c.optString("name", "未命名");
                    int steps = store.getStepCount(c.optLong("id"));
                    labels.add(name + " (" + steps + "步)");
                    ids.add(c.optLong("id"));
                }

                handler.post(() -> {
                    ArrayAdapter<String> adapter = new ArrayAdapter<>(
                            requireContext(), android.R.layout.simple_spinner_item, labels);
                    adapter.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item);
                    caseSpinner.setAdapter(adapter);

                    if (!ids.isEmpty()) {
                        caseSpinner.setTag(ids);
                    }
                });
            } catch (Exception e) {
                String msg = e.getMessage();
                if (msg == null) msg = e.getClass().getSimpleName();
                String finalMsg = msg;
                handler.post(() -> Toast.makeText(ctx, "加载用例失败: " + finalMsg, Toast.LENGTH_SHORT).show());
            }
        });
    }

    private long getSelectedCaseId() {
        @SuppressWarnings("unchecked")
        List<Long> ids = (List<Long>) caseSpinner.getTag();
        if (ids == null || ids.isEmpty()) return -1L;
        int pos = caseSpinner.getSelectedItemPosition();
        if (pos < 0 || pos >= ids.size()) return -1L;
        return ids.get(pos);
    }

    private void startRecording() {
        long caseId = getSelectedCaseId();
        if (caseId <= 0) {
            Toast.makeText(requireContext(), "请创建用例或选择一个用例", Toast.LENGTH_SHORT).show();
            return;
        }
        activeCaseId = caseId;
        recording = true;
        recordStatusTag.setText("录制中...");
        btnStartRecord.setEnabled(false);
        btnStopRecord.setEnabled(true);
        recordInfoText.setText("录制中，操作手机即可录制步骤");
        RecordingController.startRecording(requireContext(), caseId, false);
    }

    private void stopRecording() {
        recording = false;
        recordStatusTag.setText("已停止");
        btnStartRecord.setEnabled(true);
        btnStopRecord.setEnabled(false);
        recordInfoText.setText("录制已停止，点击「运行回放」执行步骤");
        RecordingController.stopRecording(requireContext());
        loadCases();
    }

    private void runReplay() {
        long caseId = getSelectedCaseId();
        if (caseId <= 0) {
            Toast.makeText(requireContext(), "请选择一个用例", Toast.LENGTH_SHORT).show();
            return;
        }
        Context ctx = getContext();
        if (ctx == null) return;

        bg.execute(() -> {
            try {
                LocalStore store = LocalStore.get(ctx);
                List<JSONObject> steps = store.getSteps(caseId);
                if (steps.isEmpty()) {
                    handler.post(() -> Toast.makeText(ctx, "该用例无步骤", Toast.LENGTH_SHORT).show());
                    return;
                }
                handler.post(() -> {
                    runStatusText.setText("回放中...");
                    btnRunCase.setEnabled(false);
                    btnStopRun.setEnabled(true);
                });
                // 使用 RunSession 启动回放，确保先退出桌面
                RunSession.start(ctx, steps, result -> {
                    handler.post(() -> {
                        boolean ok = result.optBoolean("success", false);
                        runStatusText.setText(ok ? "回放完成 ✓" : "回放失败: " + result.optString("error", ""));
                        btnRunCase.setEnabled(true);
                        btnStopRun.setEnabled(false);
                        Toast.makeText(ctx, ok ? "回放完成" : "回放失败", Toast.LENGTH_SHORT).show();
                    });
                });
            } catch (Exception e) {
                handler.post(() -> {
                    runStatusText.setText("回放异常: " + e.getMessage());
                    btnRunCase.setEnabled(true);
                    btnStopRun.setEnabled(false);
                    Toast.makeText(ctx, "回放失败: " + e.getMessage(), Toast.LENGTH_SHORT).show();
                });
            }
        });
    }

    private void showNewCaseDialog() {
        Context ctx = getContext();
        if (ctx == null) return;
        AlertDialog.Builder builder = new AlertDialog.Builder(ctx);
        builder.setTitle("新建用例");
        final EditText input = new EditText(ctx);
        input.setHint("输入用例名称");
        input.setPadding(48, 32, 48, 32);
        builder.setView(input);
        builder.setPositiveButton("创建", (dialog, which) -> {
            String name = input.getText().toString().trim();
            if (name.isEmpty()) {
                Toast.makeText(ctx, "名称不能为空", Toast.LENGTH_SHORT).show();
                return;
            }
            bg.execute(() -> {
                LocalStore store = LocalStore.get(ctx);
                store.createCase(name, LocalStore.LOCAL_PROJECT_ID);
                handler.post(() -> {
                    Toast.makeText(ctx, "用例已创建", Toast.LENGTH_SHORT).show();
                    loadCases();
                });
            });
        });
        builder.setNegativeButton("取消", null);
        builder.show();
    }

    private void pollSteps() {
        if (activeCaseId <= 0) return;
        Context ctx = getContext();
        if (ctx == null) return;
        bg.execute(() -> {
            try {
                LocalStore store = LocalStore.get(ctx);
                List<JSONObject> steps = store.getSteps(activeCaseId);
                StringBuilder sb = new StringBuilder();
                for (int i = 0; i < steps.size(); i++) {
                    JSONObject s = steps.get(i);
                    String action = s.optString("action", "?");
                    String desc = s.optString("description", "");
                    String line = (sb.length() > 0 ? "\n" : "") + (i + 1) + ". ";
                    if (!desc.isEmpty()) {
                        line += desc;
                    } else {
                        line += action;
                    }
                    if (line.length() > 60) line = line.substring(0, 57) + "...";
                    sb.append(line);
                }
                String text = sb.length() > 0 ? sb.toString() : "等待操作...";
                handler.post(() -> {
                    stepsListText.setText(text);
                    stepCountTag.setText(steps.size() + " 步");
                    if (!steps.isEmpty()) stepsPreviewCard.setVisibility(View.VISIBLE);
                });
            } catch (Exception ignored) {}
        });
    }

    @Override
    public void onDestroyView() {
        super.onDestroyView();
        handler.removeCallbacks(refreshRunnable);
    }
}
