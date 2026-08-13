# -*- coding: utf-8 -*-
"""Add create_step_result_v2 and update get_step_results."""

FILE = r"D:\mkst_baixiang\Python_Code\NewUITestPlatform\NewUITestPlatform\database.py"
with open(FILE, "r", encoding="utf-8") as f:
    content = f.read()

# 1. Add create_step_result_v2 after create_step_result
old_get = '''    def get_step_results(self, run_history_id: int) -> List[Dict[str, Any]]:
        """获取某次运行的所有步骤结果"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM step_results WHERE run_history_id = ? ORDER BY step_order ASC",
            (run_history_id,)
        )
        rows = cursor.fetchall()
        conn.close()
        return [{'id': r[0], 'run_history_id': r[1], 'step_id': r[2], 'step_order': r[3],
                 'action': r[4], 'selector_value': r[5], 'input_value': r[6],
                 'description': r[7], 'status': r[8], 'error': r[9],
                 'screenshot': r[10], 'duration': r[11], 'created_at': _bj_iso(r[12])} for r in rows]'''

new_get = '''    def create_step_result_v2(
        self,
        run_history_id: int,
        *,
        step_id: int = 0,
        step_order: int = 0,
        action: str = "",
        selector_value: str = "",
        input_value: str = "",
        description: str = "",
        status: str = "success",
        error: str = "",
        screenshot: str = "",
        duration: float = 0.0,
        started_at: str = "",
        selector_strategy: str = "",
        selector_attempts: int = 1,
        selector_resolve_ms: float = 0.0,
        action_execute_ms: float = 0.0,
        wait_ms: float = 0.0,
        retry_count: int = 0,
        page_url_before: str = "",
        page_url_after: str = "",
        page_title: str = "",
        iframe_context: str = "",
        extracted_value: str = "",
        expected_value: str = "",
        compare_result: str = "",
        screenshot_before: str = "",
        console_errors: str = "",
    ) -> int:
        """记录单步骤执行结果（企业级详细版本）。"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        local_time = _utc_now_sql()
        cursor.execute(
            """INSERT INTO step_results
               (run_history_id, step_id, step_order, action, selector_value, input_value,
                description, status, error, screenshot, duration, created_at,
                started_at, selector_strategy, selector_attempts, selector_resolve_ms,
                action_execute_ms, wait_ms, retry_count,
                page_url_before, page_url_after, page_title, iframe_context,
                extracted_value, expected_value, compare_result,
                screenshot_before, console_errors)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                run_history_id, step_id, step_order, action, selector_value, input_value,
                description, status, error, screenshot, duration, local_time,
                started_at, selector_strategy, selector_attempts, selector_resolve_ms,
                action_execute_ms, wait_ms, retry_count,
                page_url_before, page_url_after, page_title, iframe_context,
                extracted_value, expected_value, compare_result,
                screenshot_before, console_errors,
            ),
        )
        result_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return result_id

    def get_step_results(self, run_history_id: int) -> List[Dict[str, Any]]:
        """获取某次运行的所有步骤结果（含扩展字段）。"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM step_results WHERE run_history_id = ? ORDER BY step_order ASC",
            (run_history_id,)
        )
        rows = cursor.fetchall()
        # 获取列名（兼容新增列）
        col_names = [desc[0] for desc in cursor.description] if cursor.description else []
        conn.close()
        results = []
        for r in rows:
            row_dict = {}
            for idx, col in enumerate(col_names):
                val = r[idx] if idx < len(r) else None
                if col == "created_at" or col == "started_at":
                    val = _bj_iso(val)
                row_dict[col] = val
            results.append(row_dict)
        return results'''

assert old_get in content, "old_get not found"
content = content.replace(old_get, new_get, 1)

with open(FILE, "w", encoding="utf-8") as f:
    f.write(content)
print("OK: database.py v2 methods added")
