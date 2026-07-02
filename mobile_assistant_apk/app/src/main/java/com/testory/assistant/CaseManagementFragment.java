package com.testory.assistant;

import android.app.AlertDialog;
import android.content.Context;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
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
import androidx.recyclerview.widget.LinearLayoutManager;
import androidx.recyclerview.widget.RecyclerView;

import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public class CaseManagementFragment extends Fragment {

    private RecyclerView caseList;
    private TextView emptyView;

    private final Handler handler = new Handler(Looper.getMainLooper());
    private final ExecutorService bg = Executors.newSingleThreadExecutor();
    private final List<JSONObject> cases = new ArrayList<>();
    private CaseAdapter adapter;

    @Nullable
    @Override
    public View onCreateView(@NonNull LayoutInflater inflater, @Nullable ViewGroup container,
                             @Nullable Bundle savedInstanceState) {
        return inflater.inflate(R.layout.fragment_case_management, container, false);
    }

    @Override
    public void onViewCreated(@NonNull View view, @Nullable Bundle savedInstanceState) {
        super.onViewCreated(view, savedInstanceState);

        caseList = view.findViewById(R.id.case_list);
        emptyView = view.findViewById(R.id.empty_view);
        caseList.setLayoutManager(new LinearLayoutManager(requireContext()));
        adapter = new CaseAdapter();
        caseList.setAdapter(adapter);

        view.findViewById(R.id.btn_new_case).setOnClickListener(v -> showNewCaseDialog());
    }

    @Override
    public void onResume() {
        super.onResume();
        loadCases();
    }

    void loadCases() {
        Context ctx = getContext();
        if (ctx == null) return;
        bg.execute(() -> {
            try {
                LocalStore store = LocalStore.get(ctx);
                List<JSONObject> loaded = store.listCases();
                cases.clear();
                cases.addAll(loaded);
                handler.post(() -> {
                    adapter.notifyDataSetChanged();
                    emptyView.setVisibility(cases.isEmpty() ? View.VISIBLE : View.GONE);
                    caseList.setVisibility(cases.isEmpty() ? View.GONE : View.VISIBLE);
                });
            } catch (Exception e) {
                handler.post(() -> {
                    String msg = e.getMessage();
                    if (msg == null) msg = e.getClass().getSimpleName();
                    Toast.makeText(ctx, "加载失败: " + msg, Toast.LENGTH_SHORT).show();
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
                    Toast.makeText(ctx, "创建成功", Toast.LENGTH_SHORT).show();
                    loadCases();
                });
            });
        });
        builder.setNegativeButton("取消", null);
        builder.show();
    }

    private void showRenameDialog(long caseId, String currentName) {
        Context ctx = getContext();
        if (ctx == null) return;
        AlertDialog.Builder builder = new AlertDialog.Builder(ctx);
        builder.setTitle("重命名用例");
        final EditText input = new EditText(ctx);
        input.setText(currentName);
        input.setPadding(48, 32, 48, 32);
        builder.setView(input);
        builder.setPositiveButton("确定", (dialog, which) -> {
            String name = input.getText().toString().trim();
            if (name.isEmpty()) return;
            bg.execute(() -> {
                LocalStore.get(ctx).renameCase(caseId, name);
                handler.post(() -> {
                    Toast.makeText(ctx, "已重命名", Toast.LENGTH_SHORT).show();
                    loadCases();
                });
            });
        });
        builder.setNegativeButton("取消", null);
        builder.show();
    }

    private void showClearStepsConfirm(long caseId, String name) {
        Context ctx = getContext();
        if (ctx == null) return;
        new AlertDialog.Builder(ctx)
                .setTitle("清空步骤")
                .setMessage("确定清空「" + name + "」的所有步骤吗？")
                .setPositiveButton("清空", (d, w) -> {
                    bg.execute(() -> {
                        LocalStore.get(ctx).clearSteps(caseId);
                        handler.post(() -> {
                            Toast.makeText(ctx, "步骤已清空", Toast.LENGTH_SHORT).show();
                            loadCases();
                        });
                    });
                })
                .setNegativeButton("取消", null)
                .show();
    }

    private void showDeleteConfirm(long caseId, String name) {
        Context ctx = getContext();
        if (ctx == null) return;
        new AlertDialog.Builder(ctx)
                .setTitle("删除用例")
                .setMessage("确定删除「" + name + "」吗？\n步骤数据将一并删除，不可恢复。")
                .setPositiveButton("删除", (d, w) -> {
                    bg.execute(() -> {
                        LocalStore.get(ctx).deleteCase(caseId);
                        handler.post(() -> {
                            Toast.makeText(ctx, "已删除", Toast.LENGTH_SHORT).show();
                            loadCases();
                        });
                    });
                })
                .setNegativeButton("取消", null)
                .show();
    }

    // ---- RecyclerView Adapter ----
    class CaseAdapter extends RecyclerView.Adapter<CaseAdapter.ViewHolder> {
        @NonNull
        @Override
        public ViewHolder onCreateViewHolder(@NonNull ViewGroup parent, int viewType) {
            View v = LayoutInflater.from(parent.getContext())
                    .inflate(R.layout.item_case_card, parent, false);
            return new ViewHolder(v);
        }

        @Override
        public void onBindViewHolder(@NonNull ViewHolder holder, int position) {
            JSONObject c = cases.get(position);
            String name = c.optString("name", "未命名");
            long caseId = c.optLong("id");

            holder.title.setText(name);

            Context ctx = getContext();
            if (ctx != null) {
                bg.execute(() -> {
                    int count = LocalStore.get(ctx).getStepCount(caseId);
                    handler.post(() -> holder.subtitle.setText(count + " 个步骤  |  ID: " + caseId));
                });
            }

            holder.btnRename.setOnClickListener(v -> showRenameDialog(caseId, name));
            holder.btnClear.setOnClickListener(v -> showClearStepsConfirm(caseId, name));
            holder.btnDelete.setOnClickListener(v -> showDeleteConfirm(caseId, name));
        }

        @Override
        public int getItemCount() {
            return cases.size();
        }

        class ViewHolder extends RecyclerView.ViewHolder {
            TextView title, subtitle;
            Button btnRename, btnClear, btnDelete;

            ViewHolder(View v) {
                super(v);
                title = v.findViewById(R.id.case_title);
                subtitle = v.findViewById(R.id.case_subtitle);
                btnRename = v.findViewById(R.id.btn_rename);
                btnClear = v.findViewById(R.id.btn_clear);
                btnDelete = v.findViewById(R.id.btn_delete);
            }
        }
    }
}
