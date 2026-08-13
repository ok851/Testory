# -*- coding: utf-8 -*-
"""Extend step_results table with detailed execution fields."""

FILE = r"D:\mkst_baixiang\Python_Code\NewUITestPlatform\NewUITestPlatform\database.py"
with open(FILE, "r", encoding="utf-8") as f:
    content = f.read()

# Add ALTER TABLE statements after step_results CREATE TABLE
marker = '''        # 创建全局变量表（支持全局/项目/用例三种作用域）'''
new_cols = '''        # ── step_results 扩展字段（企业级运行历史） ──
        _sr_alter_cols = [
            ("started_at", "TIMESTAMP"),
            ("selector_strategy", "TEXT"),
            ("selector_attempts", "INTEGER DEFAULT 1"),
            ("selector_resolve_ms", "REAL DEFAULT 0"),
            ("action_execute_ms", "REAL DEFAULT 0"),
            ("wait_ms", "REAL DEFAULT 0"),
            ("retry_count", "INTEGER DEFAULT 0"),
            ("page_url_before", "TEXT"),
            ("page_url_after", "TEXT"),
            ("page_title", "TEXT"),
            ("iframe_context", "TEXT"),
            ("extracted_value", "TEXT"),
            ("expected_value", "TEXT"),
            ("compare_result", "TEXT"),
            ("screenshot_before", "TEXT"),
            ("console_errors", "TEXT"),
        ]
        for _col_name, _col_type in _sr_alter_cols:
            try:
                cursor.execute(f"ALTER TABLE step_results ADD COLUMN {_col_name} {_col_type}")
            except sqlite3.OperationalError:
                pass

        # 创建全局变量表（支持全局/项目/用例三种作用域）'''

assert marker in content, "marker not found"
content = content.replace(marker, new_cols, 1)

with open(FILE, "w", encoding="utf-8") as f:
    f.write(content)
print("OK: database.py schema extended")
