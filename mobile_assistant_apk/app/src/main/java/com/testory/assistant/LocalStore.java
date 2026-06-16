package com.testory.assistant;

import android.content.ContentValues;
import android.content.Context;
import android.database.Cursor;
import android.database.sqlite.SQLiteDatabase;
import android.database.sqlite.SQLiteOpenHelper;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;

/** 本地用例/步骤/运行会话存储。 */
public final class LocalStore extends SQLiteOpenHelper {

    private static final String DB = "testory_assistant.db";
    private static final int VER = 1;

    private static volatile LocalStore instance;

    static LocalStore get(Context ctx) {
        if (instance == null) {
            synchronized (LocalStore.class) {
                if (instance == null) {
                    instance = new LocalStore(ctx.getApplicationContext());
                }
            }
        }
        return instance;
    }

    private LocalStore(Context ctx) {
        super(ctx, DB, null, VER);
    }

    @Override
    public void onCreate(SQLiteDatabase db) {
        db.execSQL(
                "CREATE TABLE cases ("
                        + "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                        + "remote_id INTEGER,"
                        + "name TEXT,"
                        + "project_id INTEGER,"
                        + "updated_at INTEGER"
                        + ")"
        );
        db.execSQL(
                "CREATE TABLE steps ("
                        + "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                        + "case_id INTEGER,"
                        + "step_order INTEGER,"
                        + "action TEXT,"
                        + "selector_type TEXT,"
                        + "selector_value TEXT,"
                        + "input_value TEXT,"
                        + "description TEXT,"
                        + "mobile_spec TEXT,"
                        + "FOREIGN KEY(case_id) REFERENCES cases(id) ON DELETE CASCADE"
                        + ")"
        );
        db.execSQL(
                "CREATE TABLE run_sessions ("
                        + "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                        + "case_id INTEGER,"
                        + "status TEXT,"
                        + "started_at INTEGER,"
                        + "finished_at INTEGER,"
                        + "result_json TEXT"
                        + ")"
        );
    }

    @Override
    public void onUpgrade(SQLiteDatabase db, int oldV, int newV) {
        /* future migrations */
    }

    long upsertCase(String name, Integer remoteId, int projectId) {
        SQLiteDatabase db = getWritableDatabase();
        ContentValues cv = new ContentValues();
        cv.put("name", name);
        cv.put("remote_id", remoteId);
        cv.put("project_id", projectId);
        cv.put("updated_at", System.currentTimeMillis());
        if (remoteId != null) {
            try (Cursor c = db.query("cases", new String[]{"id"}, "remote_id=?",
                    new String[]{String.valueOf(remoteId)}, null, null, null, "1")) {
                if (c.moveToFirst()) {
                    long id = c.getLong(0);
                    db.update("cases", cv, "id=?", new String[]{String.valueOf(id)});
                    return id;
                }
            }
        }
        return db.insert("cases", null, cv);
    }

    void replaceSteps(long caseId, JSONArray steps) throws Exception {
        SQLiteDatabase db = getWritableDatabase();
        db.delete("steps", "case_id=?", new String[]{String.valueOf(caseId)});
        for (int i = 0; i < steps.length(); i++) {
            JSONObject s = steps.getJSONObject(i);
            ContentValues cv = new ContentValues();
            cv.put("case_id", caseId);
            cv.put("step_order", s.optInt("step_order", i + 1));
            cv.put("action", s.optString("action", "tap"));
            cv.put("selector_type", s.optString("selector_type", s.optString("strategy", "")));
            cv.put("selector_value", s.optString("selector_value", ""));
            cv.put("input_value", s.optString("input_value", ""));
            cv.put("description", s.optString("description", ""));
            cv.put("mobile_spec", s.optJSONObject("mobile_spec") != null
                    ? s.getJSONObject("mobile_spec").toString() : s.optString("mobile_spec", ""));
            db.insert("steps", null, cv);
        }
    }

    List<JSONObject> listCases() throws Exception {
        List<JSONObject> out = new ArrayList<>();
        SQLiteDatabase db = getReadableDatabase();
        try (Cursor c = db.query("cases", null, null, null, null, null, "updated_at DESC")) {
            while (c.moveToNext()) {
                JSONObject o = new JSONObject();
                o.put("id", c.getLong(c.getColumnIndexOrThrow("id")));
                o.put("remote_id", c.isNull(c.getColumnIndexOrThrow("remote_id"))
                        ? JSONObject.NULL : c.getInt(c.getColumnIndexOrThrow("remote_id")));
                o.put("name", c.getString(c.getColumnIndexOrThrow("name")));
                o.put("project_id", c.getInt(c.getColumnIndexOrThrow("project_id")));
                out.add(o);
            }
        }
        return out;
    }

    List<JSONObject> getSteps(long caseId) throws Exception {
        List<JSONObject> out = new ArrayList<>();
        SQLiteDatabase db = getReadableDatabase();
        try (Cursor c = db.query("steps", null, "case_id=?",
                new String[]{String.valueOf(caseId)}, null, null, "step_order ASC")) {
            while (c.moveToNext()) {
                JSONObject o = new JSONObject();
                o.put("id", c.getLong(c.getColumnIndexOrThrow("id")));
                o.put("step_order", c.getInt(c.getColumnIndexOrThrow("step_order")));
                o.put("action", c.getString(c.getColumnIndexOrThrow("action")));
                o.put("selector_type", c.getString(c.getColumnIndexOrThrow("selector_type")));
                o.put("selector_value", c.getString(c.getColumnIndexOrThrow("selector_value")));
                o.put("input_value", c.getString(c.getColumnIndexOrThrow("input_value")));
                o.put("description", c.getString(c.getColumnIndexOrThrow("description")));
                String ms = c.getString(c.getColumnIndexOrThrow("mobile_spec"));
                if (ms != null && !ms.isEmpty()) {
                    try {
                        o.put("mobile_spec", new JSONObject(ms));
                    } catch (Exception ignored) {
                        o.put("mobile_spec", ms);
                    }
                }
                o.put("automation_layer", "android");
                out.add(o);
            }
        }
        return out;
    }

    void appendRecordedStep(long caseId, JSONObject raw) throws Exception {
        List<JSONObject> existing = getSteps(caseId);
        int order = existing.size() + 1;
        JSONObject step = RecordStepConverter.toDbStep(raw, order);
        SQLiteDatabase db = getWritableDatabase();
        ContentValues cv = new ContentValues();
        cv.put("case_id", caseId);
        cv.put("step_order", order);
        cv.put("action", step.optString("action"));
        cv.put("selector_type", step.optString("selector_type"));
        cv.put("selector_value", step.optString("selector_value"));
        cv.put("input_value", step.optString("input_value"));
        cv.put("description", step.optString("description"));
        cv.put("mobile_spec", step.optJSONObject("mobile_spec") != null
                ? step.getJSONObject("mobile_spec").toString() : "");
        db.insert("steps", null, cv);
    }

    long startRunSession(long caseId) {
        SQLiteDatabase db = getWritableDatabase();
        ContentValues cv = new ContentValues();
        cv.put("case_id", caseId);
        cv.put("status", "running");
        cv.put("started_at", System.currentTimeMillis());
        return db.insert("run_sessions", null, cv);
    }

    void finishRunSession(long sessionId, String status, JSONArray results) throws Exception {
        SQLiteDatabase db = getWritableDatabase();
        ContentValues cv = new ContentValues();
        cv.put("status", status);
        cv.put("finished_at", System.currentTimeMillis());
        cv.put("result_json", results != null ? results.toString() : "[]");
        db.update("run_sessions", cv, "id=?", new String[]{String.valueOf(sessionId)});
    }
}
