package com.testory.assistant;

import android.content.Context;
import android.content.SharedPreferences;
import android.os.Handler;
import android.os.Looper;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** PC 平台 Sync API 客户端（adb reverse / LAN）。 */
public final class SyncClient {

    private static final String PREFS = "sync_client";
    private static final ExecutorService EXEC = Executors.newSingleThreadExecutor();
    private static final Handler MAIN = new Handler(Looper.getMainLooper());

    public interface Callback {
        void onSuccess(JSONObject data);
        void onError(String message);
    }

    private SyncClient() {
    }

    static void saveBaseUrl(Context ctx, String url) {
        prefs(ctx).edit().putString("base_url", url == null ? "" : url.trim()).apply();
    }

    static String getBaseUrl(Context ctx) {
        String saved = prefs(ctx).getString("base_url", "");
        if (saved.isEmpty() || "http://127.0.0.1:5000".equals(saved)) {
            return "http://192.168.2.38:5000";
        }
        return saved;
    }

    static void saveToken(Context ctx, String token) {
        prefs(ctx).edit().putString("device_token", token == null ? "" : token.trim()).apply();
    }

    static String getToken(Context ctx) {
        return prefs(ctx).getString("device_token", "");
    }

    static void pair(Context ctx, String baseUrl, String code, String deviceId, Callback cb) {
        EXEC.execute(() -> {
            try {
                JSONObject body = new JSONObject();
                body.put("code", code);
                body.put("device_id", deviceId);
                JSONObject resp = post(baseUrl, "/api/mobile/sync/pair/confirm", body, null);
                if (resp.optBoolean("success")) {
                    saveBaseUrl(ctx, baseUrl);
                    saveToken(ctx, resp.optString("device_token", ""));
                    postMain(cb, resp, null);
                } else {
                    postMain(cb, null, resp.optString("error", "配对失败"));
                }
            } catch (Exception e) {
                postMain(cb, null, friendlyError(e));
            }
        });
    }

    static void fetchProjects(Context ctx, Callback cb) {
        EXEC.execute(() -> {
            try {
                JSONObject resp = get(ctx, "/api/mobile/sync/projects");
                postMain(cb, resp, resp.optBoolean("success") ? null : resp.optString("error", "拉取失败"));
            } catch (Exception e) {
                postMain(cb, null, friendlyError(e));
            }
        });
    }

    static void pushCase(Context ctx, int projectId, String name, JSONArray steps,
                         Integer remoteCaseId, boolean replace, Callback cb) {
        EXEC.execute(() -> {
            try {
                JSONObject body = new JSONObject();
                body.put("project_id", projectId);
                body.put("name", name);
                body.put("steps", steps);
                body.put("replace", replace);
                if (remoteCaseId != null && remoteCaseId > 0) {
                    body.put("remote_case_id", remoteCaseId);
                }
                JSONObject resp = post(ctx, "/api/mobile/sync/cases/push", body);
                postMain(cb, resp, resp.optBoolean("success") ? null : resp.optString("error", "保存失败"));
            } catch (Exception e) {
                postMain(cb, null, friendlyError(e));
            }
        });
    }

    static void fetchCases(Context ctx, Callback cb) {
        EXEC.execute(() -> {
            try {
                JSONObject resp = get(ctx, "/api/mobile/sync/cases");
                postMain(cb, resp, resp.optBoolean("success") ? null : resp.optString("error", "拉取失败"));
            } catch (Exception e) {
                postMain(cb, null, friendlyError(e));
            }
        });
    }

    static void fetchCaseBundle(Context ctx, int caseId, Callback cb) {
        EXEC.execute(() -> {
            try {
                JSONObject resp = get(ctx, "/api/mobile/sync/cases/" + caseId + "/bundle");
                postMain(cb, resp, resp.optBoolean("success") ? null : resp.optString("error", "拉取失败"));
            } catch (Exception e) {
                postMain(cb, null, friendlyError(e));
            }
        });
    }

    static void uploadSteps(Context ctx, int remoteCaseId, JSONArray steps, Callback cb) {
        EXEC.execute(() -> {
            try {
                JSONObject body = new JSONObject();
                body.put("steps", steps);
                JSONObject resp = post(ctx, "/api/mobile/sync/cases/" + remoteCaseId + "/steps", body);
                postMain(cb, resp, resp.optBoolean("success") ? null : resp.optString("error", "上传失败"));
            } catch (Exception e) {
                postMain(cb, null, friendlyError(e));
            }
        });
    }

    static void pollPendingRun(Context ctx, Callback cb) {
        EXEC.execute(() -> {
            try {
                JSONObject resp = get(ctx, "/api/mobile/sync/run/pending");
                postMain(cb, resp, null);
            } catch (Exception e) {
                postMain(cb, null, friendlyError(e));
            }
        });
    }

    static void postRunEvents(Context ctx, String jobId, JSONObject payload, Callback cb) {
        EXEC.execute(() -> {
            try {
                JSONObject resp = post(ctx, "/api/mobile/sync/run/" + jobId + "/events", payload);
                postMain(cb, resp, resp.optBoolean("success") ? null : resp.optString("error", "上报失败"));
            } catch (Exception e) {
                postMain(cb, null, friendlyError(e));
            }
        });
    }


    private static JSONObject get(Context ctx, String path) throws Exception {
        return request(ctx, "GET", path, null);
    }

    private static JSONObject post(Context ctx, String path, JSONObject body) throws Exception {
        return request(ctx, "POST", path, body);
    }

    private static JSONObject post(String baseUrl, String path, JSONObject body, String token) throws Exception {
        URL url = new URL(baseUrl.replaceAll("/+$", "") + path);
        HttpURLConnection conn = (HttpURLConnection) url.openConnection();
        conn.setConnectTimeout(15000);
        conn.setReadTimeout(120000);
        conn.setRequestMethod("POST");
        conn.setDoOutput(true);
        conn.setRequestProperty("Content-Type", "application/json; charset=utf-8");
        if (token != null && !token.isEmpty()) {
            conn.setRequestProperty("X-Mobile-Device-Token", token);
        }
        byte[] bytes = body.toString().getBytes(StandardCharsets.UTF_8);
        try (OutputStream os = conn.getOutputStream()) {
            os.write(bytes);
        }
        return readJson(conn);
    }

    private static JSONObject request(Context ctx, String method, String path, JSONObject body) throws Exception {
        URL url = new URL(getBaseUrl(ctx).replaceAll("/+$", "") + path);
        HttpURLConnection conn = (HttpURLConnection) url.openConnection();
        conn.setConnectTimeout(15000);
        conn.setReadTimeout(120000);
        conn.setRequestMethod(method);
        String token = getToken(ctx);
        if (!token.isEmpty()) {
            conn.setRequestProperty("X-Mobile-Device-Token", token);
        }
        if ("POST".equals(method) && body != null) {
            conn.setDoOutput(true);
            conn.setRequestProperty("Content-Type", "application/json; charset=utf-8");
            byte[] bytes = body.toString().getBytes(StandardCharsets.UTF_8);
            try (OutputStream os = conn.getOutputStream()) {
                os.write(bytes);
            }
        }
        return readJson(conn);
    }

    private static JSONObject readJson(HttpURLConnection conn) throws Exception {
        int code = conn.getResponseCode();
        BufferedReader br = new BufferedReader(new InputStreamReader(
                code >= 400 ? conn.getErrorStream() : conn.getInputStream(), StandardCharsets.UTF_8));
        StringBuilder sb = new StringBuilder();
        String line;
        while ((line = br.readLine()) != null) sb.append(line);
        br.close();
        String text = sb.toString();
        if (text.isEmpty()) {
            JSONObject o = new JSONObject();
            o.put("success", code >= 200 && code < 300);
            o.put("http_code", code);
            return o;
        }
        JSONObject o = new JSONObject(text);
        if (!o.has("success") && code >= 200 && code < 300) {
            o.put("success", true);
        }
        return o;
    }

    private static SharedPreferences prefs(Context ctx) {
        return ctx.getApplicationContext().getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    private static void postMain(Callback cb, JSONObject data, String err) {
        if (cb == null) return;
        MAIN.post(() -> {
            if (err != null) cb.onError(err);
            else cb.onSuccess(data);
        });
    }

    private static String friendlyError(Exception e) {
        String msg = e.getMessage() == null ? "网络错误" : e.getMessage();
        if (msg.contains("Cleartext HTTP traffic")) {
            return "无法使用 HTTP 连接 PC，请升级 Testory Assistant 或检查网络安全配置";
        }
        return msg;
    }
}
