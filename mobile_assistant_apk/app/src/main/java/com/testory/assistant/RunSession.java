package com.testory.assistant;

import android.content.Context;
import android.widget.Toast;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.List;

/** 本地用例回放会话（可取消）。 */
final class RunSession {

    interface Callback {
        void onFinished(JSONObject result);
    }

    private static volatile boolean running;
    private static volatile CancellationToken currentToken;

    static final class CancellationToken {
        private volatile boolean cancelled;
        void cancel() { cancelled = true; }
        boolean isCancelled() { return cancelled; }
    }

    private RunSession() {
    }

    static boolean isRunning() {
        return running;
    }

    static void cancel() {
        CancellationToken token = currentToken;
        if (token != null) token.cancel();
    }

    static void start(Context ctx, List<JSONObject> steps, Callback callback) {
        if (running) {
            if (callback != null) {
                try {
                    JSONObject out = new JSONObject();
                    out.put("success", false);
                    out.put("error", "已有运行任务");
                    callback.onFinished(out);
                } catch (Exception ignored) {
                }
            }
            return;
        }
        running = true;
        CancellationToken token = new CancellationToken();
        currentToken = token;
        RunOverlay.show(ctx, RunSession::cancel);
        RunOverlay.setStatus("正在返回桌面，准备执行…");

        new Thread(() -> {
            JSONObject result = null;
            try {
                boolean desktop = SessionForegroundGuard.retreatToDesktopBlocking(ctx, SessionForegroundGuard.DEFAULT_TIMEOUT_MS);
                if (!desktop) {
                    Toast.makeText(ctx.getApplicationContext(),
                            "未能自动返回桌面，请手动按 Home 键", Toast.LENGTH_LONG).show();
                }
                if (token.isCancelled()) {
                    result = cancelledResult();
                } else {
                    RunOverlay.setStatus("系统级运行 0/" + steps.size());
                    result = ReplayEngine.runSteps(steps, token, (index, stepResult) -> {
                        RunOverlay.setStatus("运行 " + index + "/" + steps.size());
                    });
                }
            } catch (Exception e) {
                result = new JSONObject();
                try {
                    result.put("success", false);
                    result.put("error", e.getMessage());
                    result.put("results", new JSONArray());
                } catch (Exception ignored) {
                }
            } finally {
                running = false;
                currentToken = null;
                RunOverlay.hide();
                if (callback != null && result != null) callback.onFinished(result);
            }
        }, "testory-run-session").start();
    }

    static JSONObject cancelledResult() throws Exception {
        JSONObject out = new JSONObject();
        out.put("success", false);
        out.put("error", "已取消运行");
        out.put("status", "cancelled");
        out.put("results", new JSONArray());
        return out;
    }
}
