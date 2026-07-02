package com.testory.assistant;

import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;

import androidx.annotation.NonNull;
import androidx.appcompat.app.AppCompatActivity;
import androidx.fragment.app.Fragment;

import com.google.android.material.bottomnavigation.BottomNavigationView;

import org.json.JSONObject;

/**
 * 主 Activity — 底部导航栏架构 (v3.0)。
 *
 * 4 个模块:
 *  - 录制回放 (RecordReplayFragment)
 *  - 用例管理 (CaseManagementFragment)
 *  - AI 测试  (AITestFragment)
 *  - 设置     (SettingsFragment)
 */
public class MainActivity extends AppCompatActivity {

    private static java.lang.ref.WeakReference<MainActivity> visibleInstance;

    /** 录制/回放前将助手退到后台 */
    static void moveTaskToBackIfVisible() {
        MainActivity act = visibleInstance != null ? visibleInstance.get() : null;
        if (act != null && !act.isFinishing()) {
            act.runOnUiThread(() -> {
                try { act.moveTaskToBack(true); } catch (Exception ignored) {}
            });
        }
    }

    private BottomNavigationView bottomNav;
    private final Handler handler = new Handler(Looper.getMainLooper());
    private int currentTab = R.id.nav_record_replay;

    private RecordReplayFragment recordReplayFragment;
    private CaseManagementFragment caseFragment;
    private AITestFragment aiFragment;
    private SettingsFragment settingsFragment;

    // 周期性 PC 轮询
    private final Runnable runPollRunnable = new Runnable() {
        @Override
        public void run() {
            if (SyncClient.getToken(MainActivity.this).isEmpty()) {
                handler.postDelayed(this, 10000);
                return;
            }
            SyncClient.pollPendingRun(MainActivity.this, new SyncClient.Callback() {
                @Override
                public void onSuccess(JSONObject data) {
                    if (data == null || !data.optBoolean("has_job")) return;
                    executeRemoteRunJob(data);
                }
                @Override
                public void onError(String message) {}
            });
            handler.postDelayed(this, 4000);
        }
    };

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);

        visibleInstance = new java.lang.ref.WeakReference<>(this);

        bottomNav = findViewById(R.id.bottom_nav);

        if (savedInstanceState == null) {
            showFragment(R.id.nav_record_replay);
        }

        bottomNav.setOnItemSelectedListener(item -> {
            int id = item.getItemId();
            if (id != currentTab) {
                showFragment(id);
            }
            return true;
        });

        // 启动 PC 轮询
        handler.postDelayed(runPollRunnable, 2000);
    }

    private void showFragment(int tabId) {
        Fragment frag;
        if (tabId == R.id.nav_record_replay) {
            if (recordReplayFragment == null) recordReplayFragment = new RecordReplayFragment();
            frag = recordReplayFragment;
        } else if (tabId == R.id.nav_case_management) {
            if (caseFragment == null) caseFragment = new CaseManagementFragment();
            frag = caseFragment;
        } else if (tabId == R.id.nav_ai_test) {
            if (aiFragment == null) aiFragment = new AITestFragment();
            frag = aiFragment;
        } else if (tabId == R.id.nav_settings) {
            if (settingsFragment == null) settingsFragment = new SettingsFragment();
            frag = settingsFragment;
        } else {
            return;
        }

        getSupportFragmentManager()
                .beginTransaction()
                .replace(R.id.fragment_container, frag)
                .commit();

        currentTab = tabId;

        // 刷新数据
        if (tabId == R.id.nav_settings && settingsFragment != null) {
            settingsFragment.refreshStatus();
        }
        if (tabId == R.id.nav_case_management && caseFragment != null) {
            caseFragment.loadCases();
        }
        if (tabId == R.id.nav_record_replay && recordReplayFragment != null) {
            recordReplayFragment.loadCases();
        }
    }

    private void executeRemoteRunJob(JSONObject data) {
        long caseId = data.optLong("case_id", -1);
        if (caseId <= 0) return;

        runOnUiThread(() -> {
            bottomNav.setSelectedItemId(R.id.nav_record_replay);
            if (recordReplayFragment != null) {
                recordReplayFragment.loadCases();
            }
        });
    }

    @Override
    protected void onDestroy() {
        super.onDestroy();
        handler.removeCallbacks(runPollRunnable);
        if (visibleInstance != null) {
            visibleInstance.clear();
            visibleInstance = null;
        }
    }
}
