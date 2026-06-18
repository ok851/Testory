package com.testory.assistant;

import android.content.Context;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.List;

/** 本地用例回放会话（可取消）。 */
final class RunSession {

    interface Callback {
        void onFinished(JSONObject result);
    }

    private static volatile boolean running;
    private static volatile boolean cancelled;

    private RunSession() {
    }

    static boolean isRunning() {
        return running;
    }

    static void cancel() {
        cancelled = true;
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
        cancelled = false;
        RunOverlay.show(ctx, RunSession::cancel);
        RunOverlay.setStatus("系统级运行 0/" + steps.size());

        new Thread(() -> {
            JSONObject result = null;
            try {
                Thread.sleep(350);
                if (cancelled) {
                    result = cancelledResult();
                } else {
                    result = ReplayEngine.runSteps(steps, (index, stepResult) -> {
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
                cancelled = false;
                RunOverlay.hide();
                if (callback != null && result != null) callback.onFinished(result);
            }
        }, "testory-run-session").start();
    }

    static boolean isCancelled() {
        return cancelled;
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
