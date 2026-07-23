import sqlite3
import os
import json
import logging
import threading
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple

from time_utils import utc_now_sqlite_str as _utc_now_sql, to_beijing_iso as _bj_iso

_db_log = logging.getLogger(__name__)

# update_user 未传字段与「显式置空」区分（email=None 表示清空邮箱）
_UNSET = object()


def _normalize_user_email(email: Any) -> Optional[str]:
    if email is None:
        return None
    if isinstance(email, str):
        s = email.strip().lower()
        return s or None
    return email

# API 返回前统一转为北京时间 ISO（与 time_utils 约定：库内 naive 为 UTC）
_API_TS_KEYS = frozenset({
    "created_at", "updated_at", "last_login", "started_at", "completed_at",
    "last_run", "next_run", "resolved_at", "closed_at", "paid_at", "expires_at",
    "bind_time", "login_time",
})


def _api_ts_dict(d: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if d is None:
        return None
    out = dict(d)
    for k in _API_TS_KEYS:
        if k in out:
            out[k] = _bj_iso(out[k])
    return out

# projects 表列（勿用 SELECT * + 固定下标：迁移后 tenant_id 与 created_at 顺序因建表/ALTER 而异）
_PROJECTS_SELECT = "id, name, description, tenant_id, created_at"

# test_cases 当前列（与 CREATE + ALTER 一致）
_TEST_CASES_SELECT = (
    "id, project_id, name, url, description, created_at, precondition, expected_result, "
    "case_type, COALESCE(case_role, 'business') AS case_role, "
    "COALESCE(platform, 'web') AS platform"
)


def _normalize_platform(raw: Any) -> str:
    p = (raw or "web").strip().lower() if isinstance(raw, str) else "web"
    if p in ("android", "mobile", "app"):
        return "android"
    if p == "desktop":
        return "desktop"
    return "web"

# test_steps 当前列（勿用 SELECT * + 固定下标，与 _row_to_step_dict 一致）
_TEST_STEPS_SELECT = (
    "id, case_id, action, selector_type, selector_value, input_value, description, "
    "step_order, created_at, page_name, swipe_x, swipe_y, url, enter_iframe, iframe_selector, "
    "compare_type, locator_candidates, click_repeat_count, api_spec, automation_layer, desktop_spec, "
    "captcha_max_attempts, mobile_spec"
)


def _normalize_case_type(raw: Any) -> str:
    t = (raw or "ui").strip().lower() if isinstance(raw, str) else "ui"
    return "api" if t == "api" else "ui"


def _project_row_to_dict(row: Tuple) -> Dict[str, Any]:
    return {
        "id": row[0],
        "name": row[1],
        "description": row[2] or "",
        "tenant_id": row[3],
        "created_at": _bj_iso(row[4]),
    }


def _test_case_row_to_dict(
    row: Tuple,
    step_count: Optional[int] = None,
    unit_id: Optional[int] = None,
    unit_name: str = "",
) -> Dict[str, Any]:
    u = row[3] or ""
    out: Dict[str, Any] = {
        "id": row[0],
        "project_id": row[1],
        "name": row[2],
        "url": u,
        "target_url": u,
        "description": row[4] or "",
        "created_at": _bj_iso(row[5]),
        "precondition": (row[6] or "") if len(row) > 6 else "",
        "expected_result": (row[7] or "") if len(row) > 7 else "",
        "case_type": _normalize_case_type(row[8]) if len(row) > 8 else "ui",
        "case_role": (row[9] or "business").strip() if len(row) > 9 and row[9] else "business",
        "platform": _normalize_platform(row[10]) if len(row) > 10 else "web",
    }
    if step_count is not None:
        out["step_count"] = step_count
    if unit_id is not None:
        out["unit_id"] = unit_id
    if unit_name:
        out["unit_name"] = unit_name
    return out


_UPDATE_UNSET = object()


class Database:
    """每个进程只对库执行一次 init_db（建表/迁移/索引）；避免每个 API 请求重复全量迁移。"""

    _schema_lock = threading.Lock()
    _schema_initialized: bool = False

    def __init__(self, db_path: str = "test_cases.db"):
        # 部署（Docker/服务器）时可能通过环境变量指定持久化数据库路径
        # 例如 docker-compose.yml 里的 DATABASE_PATH=/app/data/test_cases.db
        env_db_path = os.environ.get("DATABASE_PATH")
        if env_db_path and (db_path == "test_cases.db"):
            db_path = env_db_path
        self.db_path = db_path
        with Database._schema_lock:
            if not Database._schema_initialized:
                self.init_db()
                Database._schema_initialized = True

    def ping(self, timeout: float = 2.0) -> bool:
        """快速检测库是否可读（用于就绪探针）。"""
        try:
            conn = self._sqlite_connect(timeout=timeout)
            try:
                conn.execute("SELECT 1")
            finally:
                conn.close()
            return True
        except sqlite3.Error:
            return False

    def _sqlite_connect(self, timeout: Optional[float] = None) -> sqlite3.Connection:
        """统一连接参数：WAL 提升读并发；busy_timeout 缓解锁竞争（数据库在网络盘时仍可能较慢）。"""
        if timeout is not None:
            conn = sqlite3.connect(self.db_path, timeout=timeout)
        else:
            conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("PRAGMA busy_timeout=5000")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("PRAGMA foreign_keys=ON")
        except sqlite3.OperationalError:
            pass
        return conn
    
    def init_db(self):
        """初始化数据库表"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()

        # 创建用户表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                email TEXT UNIQUE,
                role TEXT NOT NULL DEFAULT 'tester',
                is_active INTEGER NOT NULL DEFAULT 1,
                tenant_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_login TIMESTAMP,
                FOREIGN KEY (tenant_id) REFERENCES tenants (id)
            )
        ''')

        # 创建租户表（多租户隔离）
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS tenants (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                display_name TEXT,
                plan_type TEXT NOT NULL DEFAULT 'free',
                max_users INTEGER DEFAULT 5,
                max_projects INTEGER DEFAULT 10,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP
            )
        ''')

        # 创建项目表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT,
                tenant_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (tenant_id) REFERENCES tenants (id)
            )
        ''')
        
        # 创建测试用例表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS test_cases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER,
                name TEXT NOT NULL,
                url TEXT,
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (project_id) REFERENCES projects (id)
            )
        ''')
        
        # 创建测试步骤表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS test_steps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                case_id INTEGER,
                action TEXT NOT NULL,
                selector_type TEXT,
                selector_value TEXT,
                input_value TEXT,
                description TEXT,
                step_order INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (case_id) REFERENCES test_cases (id)
            )
        ''')
        
        # 创建测试脚本表（保留用于兼容性）
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS test_scripts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                case_id INTEGER,
                name TEXT NOT NULL,
                steps TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (case_id) REFERENCES test_cases (id)
            )
        ''')
        
        # 添加新字段到test_cases表（如果不存在）
        try:
            cursor.execute("ALTER TABLE test_cases ADD COLUMN precondition TEXT")
        except sqlite3.OperationalError:
            pass
        
        try:
            cursor.execute("ALTER TABLE test_cases ADD COLUMN expected_result TEXT")
        except sqlite3.OperationalError:
            pass

        try:
            cursor.execute("ALTER TABLE test_cases ADD COLUMN case_type TEXT DEFAULT 'ui'")
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute(
                "UPDATE test_cases SET case_type = 'ui' WHERE case_type IS NULL OR TRIM(case_type) = ''"
            )
        except sqlite3.OperationalError:
            pass

        try:
            cursor.execute("ALTER TABLE test_cases ADD COLUMN case_role TEXT DEFAULT 'business'")
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute(
                "UPDATE test_cases SET case_role = 'business' WHERE case_role IS NULL OR TRIM(case_role) = ''"
            )
        except sqlite3.OperationalError:
            pass

        try:
            cursor.execute("ALTER TABLE test_cases ADD COLUMN platform TEXT DEFAULT 'web'")
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute(
                "UPDATE test_cases SET platform = 'web' WHERE platform IS NULL OR TRIM(platform) = ''"
            )
        except sqlite3.OperationalError:
            pass

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS test_units (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                description TEXT,
                sort_order INTEGER DEFAULT 0,
                parent_unit_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (project_id) REFERENCES projects (id),
                FOREIGN KEY (parent_unit_id) REFERENCES test_units (id)
            )
        """)
        try:
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_test_units_project ON test_units(project_id)"
            )
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute("ALTER TABLE test_cases ADD COLUMN unit_id INTEGER")
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_test_cases_unit_id ON test_cases(unit_id)"
            )
        except sqlite3.OperationalError:
            pass

        try:
            cursor.execute("ALTER TABLE run_history ADD COLUMN device_log TEXT")
        except sqlite3.OperationalError:
            pass

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS element_repository (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                alias TEXT NOT NULL,
                platform TEXT NOT NULL DEFAULT 'android',
                selector_type TEXT NOT NULL,
                selector_value TEXT NOT NULL,
                attributes_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (project_id) REFERENCES projects (id),
                UNIQUE(project_id, alias, platform)
            )
        """)
        
        # 添加新字段到test_steps表（如果不存在）
        try:
            cursor.execute("ALTER TABLE test_steps ADD COLUMN page_name TEXT")
        except sqlite3.OperationalError:
            pass
        
        try:
            cursor.execute("ALTER TABLE test_steps ADD COLUMN swipe_x TEXT")
        except sqlite3.OperationalError:
            pass
        
        try:
            cursor.execute("ALTER TABLE test_steps ADD COLUMN swipe_y TEXT")
        except sqlite3.OperationalError:
            pass
        
        try:
            cursor.execute("ALTER TABLE test_steps ADD COLUMN url TEXT")
        except sqlite3.OperationalError:
            pass
        
        # 添加iframe相关字段
        try:
            cursor.execute("ALTER TABLE test_steps ADD COLUMN enter_iframe BOOLEAN DEFAULT FALSE")
        except sqlite3.OperationalError:
            pass
        
        try:
            cursor.execute("ALTER TABLE test_steps ADD COLUMN iframe_selector TEXT")
        except sqlite3.OperationalError:
            pass

        # compare_type：用于断言/匹配策略（例如 equals/contains 等）
        # 旧库可能没有该列，部署后添加步骤会直接写入 compare_type，因此必须迁移补齐。
        try:
            cursor.execute("ALTER TABLE test_steps ADD COLUMN compare_type TEXT DEFAULT 'equals'")
        except sqlite3.OperationalError:
            pass

        # 录制器多定位器备选（JSON 数组：[{type,value,score}, ...]）
        try:
            cursor.execute("ALTER TABLE test_steps ADD COLUMN locator_candidates TEXT")
        except sqlite3.OperationalError:
            pass

        # 点击步骤连续执行次数（仅 action=click 时有效，默认 1）
        try:
            cursor.execute("ALTER TABLE test_steps ADD COLUMN click_repeat_count INTEGER DEFAULT 1")
        except sqlite3.OperationalError:
            pass

        # 接口测试步骤：JSON 规格（method/url/headers/body/断言等）
        try:
            cursor.execute("ALTER TABLE test_steps ADD COLUMN api_spec TEXT")
        except sqlite3.OperationalError:
            pass

        # 自动化层：web | desktop（混排用例按步骤区分）
        try:
            cursor.execute(
                "ALTER TABLE test_steps ADD COLUMN automation_layer TEXT DEFAULT 'web'"
            )
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute(
                "UPDATE test_steps SET automation_layer = 'web' "
                "WHERE automation_layer IS NULL OR TRIM(automation_layer) = ''"
            )
        except sqlite3.OperationalError:
            pass

        # 桌面步骤扩展：窗口/进程/backend 等 JSON
        try:
            cursor.execute("ALTER TABLE test_steps ADD COLUMN desktop_spec TEXT")
        except sqlite3.OperationalError:
            pass

        # verify 验证码步骤：最大自动验证次数（空则使用 .env CAPTCHA_SOLVE_RETRY）
        try:
            cursor.execute(
                "ALTER TABLE test_steps ADD COLUMN captcha_max_attempts INTEGER"
            )
        except sqlite3.OperationalError:
            pass

        try:
            cursor.execute("ALTER TABLE test_steps ADD COLUMN mobile_spec TEXT")
        except sqlite3.OperationalError:
            pass

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS test_machines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                agent_url TEXT NOT NULL,
                agent_secret TEXT,
                os_version TEXT,
                status TEXT DEFAULT 'unknown',
                last_seen_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # 创建运行历史记录表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS run_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                case_id INTEGER,
                status TEXT NOT NULL,
                duration REAL,
                error TEXT,
                extracted_text TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (case_id) REFERENCES test_cases (id)
            )
        ''')
        
        # 添加 expected_text 列（若表已存在可能无此列）
        try:
            cursor.execute("ALTER TABLE run_history ADD COLUMN expected_text TEXT")
        except sqlite3.OperationalError:
            pass

        # 添加截图列到 run_history（记录失败截图路径）
        try:
            cursor.execute("ALTER TABLE run_history ADD COLUMN screenshots TEXT")
        except sqlite3.OperationalError:
            pass

        # 数据驱动等场景曾写入 status='fail'，与报表/筛选使用的 'failed' 不一致，统一为 failed
        try:
            cursor.execute("UPDATE run_history SET status = 'failed' WHERE status = 'fail'")
        except sqlite3.OperationalError:
            pass

        # 创建步骤执行结果表（记录每个步骤的执行状态）
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS step_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_history_id INTEGER NOT NULL,
                step_id INTEGER,
                step_order INTEGER DEFAULT 0,
                action TEXT,
                selector_value TEXT,
                input_value TEXT,
                description TEXT,
                status TEXT NOT NULL,
                error TEXT,
                screenshot TEXT,
                duration REAL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (run_history_id) REFERENCES run_history (id)
            )
        ''')

        # 创建全局变量表（支持全局/项目/用例三种作用域）
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS variables (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                value TEXT,
                scope TEXT NOT NULL DEFAULT 'global',
                project_id INTEGER,
                case_id INTEGER,
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (project_id) REFERENCES projects (id),
                FOREIGN KEY (case_id) REFERENCES test_cases (id)
            )
        ''')

        # 创建定时调度表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS schedules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                project_id INTEGER,
                case_ids TEXT NOT NULL,
                cron_expr TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                retry_count INTEGER NOT NULL DEFAULT 3,
                retry_interval INTEGER NOT NULL DEFAULT 5,
                last_run TIMESTAMP,
                next_run TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (project_id) REFERENCES projects (id)
            )
        ''')

        # 为旧版 schedules 表添加缺失的列（数据库迁移）
        try:
            cursor.execute("ALTER TABLE schedules ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 3")
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute("ALTER TABLE schedules ADD COLUMN retry_interval INTEGER NOT NULL DEFAULT 5")
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute("ALTER TABLE schedules ADD COLUMN execution_count INTEGER NOT NULL DEFAULT 0")
        except sqlite3.OperationalError:
            pass

        # 定时任务 execution_count 语义：-1=无限次，0=不自动执行，>0=剩余次数
        # 一次性迁移：旧版「0 表示无限」→ -1（避免与「0=不跑」混淆）
        try:
            cursor.execute(
                "CREATE TABLE IF NOT EXISTS _schema_patches (patch_id TEXT PRIMARY KEY)"
            )
            cursor.execute(
                "SELECT 1 FROM _schema_patches WHERE patch_id = ?",
                ("sched_exec_minus_one_unlimited",),
            )
            if not cursor.fetchone():
                cursor.execute(
                    "UPDATE schedules SET execution_count = -1 WHERE execution_count = 0"
                )
                cursor.execute(
                    "INSERT INTO _schema_patches (patch_id) VALUES (?)",
                    ("sched_exec_minus_one_unlimited",),
                )
        except sqlite3.OperationalError:
            pass

        # 用户邮箱：空字符串与 NULL 混用会导致 UNIQUE 冲突，统一为 NULL
        try:
            cursor.execute(
                "CREATE TABLE IF NOT EXISTS _schema_patches (patch_id TEXT PRIMARY KEY)"
            )
            cursor.execute(
                "SELECT 1 FROM _schema_patches WHERE patch_id = ?",
                ("users_email_empty_to_null",),
            )
            if not cursor.fetchone():
                cursor.execute("UPDATE users SET email = NULL WHERE email = ''")
                cursor.execute(
                    "INSERT INTO _schema_patches (patch_id) VALUES (?)",
                    ("users_email_empty_to_null",),
                )
        except sqlite3.OperationalError:
            pass

        # 创建调度执行历史表（用于记录每次执行和重跑）
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS schedule_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                schedule_id INTEGER NOT NULL,
                case_ids TEXT NOT NULL,
                status TEXT NOT NULL,
                retry_count INTEGER DEFAULT 0,
                max_retries INTEGER DEFAULT 3,
                error_message TEXT,
                started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP,
                FOREIGN KEY (schedule_id) REFERENCES schedules (id)
            )
        ''')

        # 创建通知配置表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS notification_configs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                type TEXT NOT NULL,
                config TEXT NOT NULL,
                events TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # 创建 API 访问令牌表（用于 Webhook/CI 触发）
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS api_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                token TEXT NOT NULL UNIQUE,
                project_id INTEGER,
                is_active INTEGER NOT NULL DEFAULT 1,
                expires_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (project_id) REFERENCES projects (id)
            )
        ''')

        # 创建数据驱动测试数据集表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS test_data_sets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                case_id INTEGER,
                project_id INTEGER,
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (case_id) REFERENCES test_cases (id),
                FOREIGN KEY (project_id) REFERENCES projects (id)
            )
        ''')

        # 创建数据驱动测试数据行表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS test_data_rows (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dataset_id INTEGER NOT NULL,
                row_index INTEGER DEFAULT 0,
                data TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (dataset_id) REFERENCES test_data_sets (id)
            )
        ''')

        # 创建项目成员表（实现项目级权限控制）
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS project_members (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                role TEXT NOT NULL DEFAULT 'editor',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (project_id) REFERENCES projects (id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
                UNIQUE(project_id, user_id)
            )
        ''')

        # 创建审计日志表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                username TEXT,
                action TEXT NOT NULL,
                target_type TEXT NOT NULL,
                target_id INTEGER,
                details TEXT,
                ip_address TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        ''')

        # 创建用户操作统计表（用于免费版限制）
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_usage_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                stat_date DATE NOT NULL,
                execution_count INTEGER DEFAULT 0,
                created_cases INTEGER DEFAULT 0,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id),
                UNIQUE(user_id, stat_date)
            )
        ''')

        # AI 向量记忆：历史修复案例 / 用户习惯（Ollama embeddings + 余弦检索）
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS ai_context_memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id INTEGER,
                user_id INTEGER NOT NULL,
                kind TEXT NOT NULL,
                source_text TEXT NOT NULL,
                meta_json TEXT,
                embedding BLOB NOT NULL,
                embedding_dim INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        ''')

        # 添加数据库索引以优化查询性能
        self._create_indexes(cursor)
        
        # 多租户数据库迁移（为现有表添加 tenant_id 字段）
        self._migrate_multi_tenant(cursor)
        
        # 创建SSO相关表
        self._create_sso_tables(cursor)
        
        # 创建支付相关表
        self._create_payment_tables(cursor)
        
        # 创建缺陷管理相关表
        self._create_defect_tables(cursor)

        # PC 端转型：工作空间、执行队列、客户端节点
        self._create_deployment_tables(cursor)
        self._ensure_default_workspace(cursor)

        # 清理「用例已删除但运行历史仍在」的遗留数据，避免测试报表统计偏高
        try:
            n_orphan = self._cleanup_orphan_run_history(cursor)
            if n_orphan:
                _db_log.info("已清理孤立运行历史记录 %s 条（关联用例已不存在）", n_orphan)
        except Exception as e:
            _db_log.warning("清理孤立运行历史失败: %s", e)
        
        conn.commit()
        conn.close()
    
    def _create_indexes(self, cursor):
        """创建数据库索引以优化查询性能"""
        indexes = [
            "CREATE INDEX IF NOT EXISTS idx_run_history_case_id ON run_history(case_id)",
            "CREATE INDEX IF NOT EXISTS idx_run_history_created_at ON run_history(created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_run_history_status ON run_history(status)",
            "CREATE INDEX IF NOT EXISTS idx_test_cases_project_id ON test_cases(project_id)",
            "CREATE INDEX IF NOT EXISTS idx_test_cases_name ON test_cases(name)",
            "CREATE INDEX IF NOT EXISTS idx_projects_name ON projects(name)",
            "CREATE INDEX IF NOT EXISTS idx_step_results_run_id ON step_results(run_history_id)",
            "CREATE INDEX IF NOT EXISTS idx_variables_scope ON variables(scope)",
            "CREATE INDEX IF NOT EXISTS idx_variables_project ON variables(project_id)",
            "CREATE INDEX IF NOT EXISTS idx_schedules_active ON schedules(is_active)",
            "CREATE INDEX IF NOT EXISTS idx_api_tokens_token ON api_tokens(token)",
            "CREATE INDEX IF NOT EXISTS idx_test_data_sets_case ON test_data_sets(case_id)",
            "CREATE INDEX IF NOT EXISTS idx_test_data_rows_dataset ON test_data_rows(dataset_id)",
            "CREATE INDEX IF NOT EXISTS idx_project_members_project ON project_members(project_id)",
            "CREATE INDEX IF NOT EXISTS idx_project_members_user ON project_members(user_id)",
            "CREATE INDEX IF NOT EXISTS idx_audit_logs_user ON audit_logs(user_id)",
            "CREATE INDEX IF NOT EXISTS idx_audit_logs_created ON audit_logs(created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_user_usage_stats_user_date ON user_usage_stats(user_id, stat_date)",
            # 多租户索引
            "CREATE INDEX IF NOT EXISTS idx_users_tenant ON users(tenant_id)",
            "CREATE INDEX IF NOT EXISTS idx_projects_tenant ON projects(tenant_id)",
            # 步骤列表：按用例查询 / 排序极常见，缺少索引时大数据量会明显变慢
            "CREATE INDEX IF NOT EXISTS idx_test_steps_case_id ON test_steps(case_id)",
            "CREATE INDEX IF NOT EXISTS idx_test_steps_case_order ON test_steps(case_id, step_order)",
            "CREATE INDEX IF NOT EXISTS idx_ai_context_memory_tenant ON ai_context_memory(tenant_id)",
            "CREATE INDEX IF NOT EXISTS idx_ai_context_memory_user ON ai_context_memory(user_id)",
            "CREATE INDEX IF NOT EXISTS idx_execution_jobs_status ON execution_jobs(status)",
            "CREATE INDEX IF NOT EXISTS idx_execution_jobs_case ON execution_jobs(case_id)",
            "CREATE INDEX IF NOT EXISTS idx_client_nodes_machine ON client_nodes(machine_id)",
            "CREATE INDEX IF NOT EXISTS idx_workspaces_name ON workspaces(name)",
        ]
        
        for index_sql in indexes:
            try:
                cursor.execute(index_sql)
            except sqlite3.Error as e:
                # 记录错误但继续执行，避免因为索引创建失败影响主要功能
                _db_log.warning("创建索引失败: %s, 错误: %s", index_sql, e)

    def _migrate_multi_tenant(self, cursor):
        """多租户数据库迁移 - 为现有表添加 tenant_id 字段"""
        # 为 users 表添加 tenant_id
        try:
            cursor.execute("ALTER TABLE users ADD COLUMN tenant_id INTEGER")
        except sqlite3.OperationalError:
            pass
        
        # 为 projects 表添加 tenant_id
        try:
            cursor.execute("ALTER TABLE projects ADD COLUMN tenant_id INTEGER")
        except sqlite3.OperationalError:
            pass
        
        # 为 users 表添加 display_name 和 phone 字段
        try:
            cursor.execute("ALTER TABLE users ADD COLUMN display_name TEXT")
        except sqlite3.OperationalError:
            pass
        
        try:
            cursor.execute("ALTER TABLE users ADD COLUMN phone TEXT")
        except sqlite3.OperationalError:
            pass
        
        try:
            cursor.execute("ALTER TABLE users ADD COLUMN avatar_url TEXT")
        except sqlite3.OperationalError:
            pass

        try:
            cursor.execute("ALTER TABLE users ADD COLUMN recovery_key_hash TEXT")
        except sqlite3.OperationalError:
            pass

    def _create_sso_tables(self, cursor):
        """创建SSO相关表"""
        # SSO配置表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS sso_configs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id INTEGER,
                provider_type TEXT NOT NULL,
                name TEXT NOT NULL,
                client_id TEXT,
                client_secret TEXT,
                auth_url TEXT,
                token_url TEXT,
                userinfo_url TEXT,
                callback_url TEXT,
                ldap_host TEXT,
                ldap_port INTEGER DEFAULT 389,
                ldap_base_dn TEXT,
                ldap_bind_dn TEXT,
                ldap_bind_password TEXT,
                ldap_user_filter TEXT DEFAULT '(uid={username})',
                wecom_corp_id TEXT,
                wecom_agent_id TEXT,
                wecom_secret TEXT,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (tenant_id) REFERENCES tenants (id)
            )
        ''')
        
        # SSO登录记录表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS sso_login_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                provider_type TEXT NOT NULL,
                external_id TEXT,
                login_ip TEXT,
                user_agent TEXT,
                login_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        ''')
        
        # 用户绑定SSO账号表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_sso_bindings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                provider_type TEXT NOT NULL,
                external_id TEXT NOT NULL,
                external_username TEXT,
                external_email TEXT,
                bind_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id),
                UNIQUE(user_id, provider_type)
            )
        ''')

    def _create_payment_tables(self, cursor):
        """创建支付相关表"""
        # 订单表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_no TEXT NOT NULL UNIQUE,
                user_id INTEGER NOT NULL,
                tenant_id INTEGER,
                plan_type TEXT NOT NULL,
                amount REAL NOT NULL,
                currency TEXT DEFAULT 'CNY',
                status TEXT NOT NULL DEFAULT 'pending',
                payment_method TEXT,
                payment_channel TEXT,
                transaction_id TEXT,
                paid_at TIMESTAMP,
                expires_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id),
                FOREIGN KEY (tenant_id) REFERENCES tenants (id)
            )
        ''')
        
        # 支付记录表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS payment_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER NOT NULL,
                payment_method TEXT NOT NULL,
                transaction_id TEXT,
                amount REAL NOT NULL,
                status TEXT NOT NULL,
                raw_response TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (order_id) REFERENCES orders (id)
            )
        ''')
        
        # 订阅记录表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                tenant_id INTEGER,
                plan_type TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP,
                auto_renew INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id),
                FOREIGN KEY (tenant_id) REFERENCES tenants (id)
            )
        ''')

    def _create_defect_tables(self, cursor):
        """创建缺陷管理相关表"""
        # 缺陷主表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS defects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER,
                title TEXT NOT NULL,
                description TEXT,
                severity TEXT NOT NULL DEFAULT 'medium',
                priority TEXT NOT NULL DEFAULT 'medium',
                status TEXT NOT NULL DEFAULT 'open',
                assignee_id INTEGER,
                reporter_id INTEGER NOT NULL,
                case_id INTEGER,
                run_history_id INTEGER,
                step_result_id INTEGER,
                error_message TEXT,
                screenshots TEXT,
                environment TEXT,
                browser_info TEXT,
                reproduce_steps TEXT,
                expected_result TEXT,
                actual_result TEXT,
                resolution TEXT,
                resolved_at TIMESTAMP,
                closed_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (project_id) REFERENCES projects (id),
                FOREIGN KEY (assignee_id) REFERENCES users (id),
                FOREIGN KEY (reporter_id) REFERENCES users (id),
                FOREIGN KEY (case_id) REFERENCES test_cases (id),
                FOREIGN KEY (run_history_id) REFERENCES run_history (id),
                FOREIGN KEY (step_result_id) REFERENCES step_results (id)
            )
        ''')
        
        # 缺陷评论表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS defect_comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                defect_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (defect_id) REFERENCES defects (id),
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        ''')
        
        # 缺陷状态变更历史表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS defect_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                defect_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                field_name TEXT NOT NULL,
                old_value TEXT,
                new_value TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (defect_id) REFERENCES defects (id),
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        ''')
        
        # 缺陷索引
        try:
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_defects_project ON defects(project_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_defects_status ON defects(status)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_defects_assignee ON defects(assignee_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_defects_case ON defects(case_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_defect_comments_defect ON defect_comments(defect_id)")
        except sqlite3.Error:
            pass

        # =====================================================================
        # Maestro 移动引擎相关表 (v2.0)
        # =====================================================================

        # Maestro 测试流定义表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS maestro_flows (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                case_id INTEGER,
                project_id INTEGER,
                name TEXT NOT NULL,
                description TEXT,
                yaml_content TEXT,
                app_package TEXT,
                app_activity TEXT,
                platform TEXT DEFAULT 'android',
                version INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (case_id) REFERENCES test_cases (id),
                FOREIGN KEY (project_id) REFERENCES projects (id)
            )
        ''')

        # Maestro 本地版本管理表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS maestro_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                version TEXT NOT NULL UNIQUE,
                jar_path TEXT NOT NULL,
                sha256 TEXT,
                is_active INTEGER DEFAULT 1,
                downloaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # 增强 element_repository 表（自愈字段）
        for col_spec in [
            ("locator_candidates", "TEXT"),
            ("semantic_desc", "TEXT"),
            ("visual_template_path", "TEXT"),
            ("heuristic_selector", "TEXT"),
            ("last_success_at", "TIMESTAMP"),
            ("success_count", "INTEGER DEFAULT 0"),
            ("fail_count", "INTEGER DEFAULT 0"),
        ]:
            col_name, col_type = col_spec
            try:
                cursor.execute(
                    f"ALTER TABLE element_repository ADD COLUMN {col_name} {col_type}"
                )
            except sqlite3.OperationalError:
                pass

        # 移动设备配置表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS mobile_devices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                udid TEXT NOT NULL UNIQUE,
                platform TEXT DEFAULT 'android',
                device_name TEXT,
                model TEXT,
                os_version TEXT,
                screen_width INTEGER,
                screen_height INTEGER,
                is_emulator INTEGER DEFAULT 0,
                connection_type TEXT DEFAULT 'usb',
                last_connected TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                config_json TEXT
            )
        ''')

        # 用户邮箱 SMTP 配置（注册时填写，找回密码时复用）
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_smtp_configs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL UNIQUE,
                email       TEXT NOT NULL,
                host        TEXT NOT NULL,
                port        INTEGER NOT NULL DEFAULT 587,
                username    TEXT NOT NULL,
                password    TEXT NOT NULL,
                use_tls     INTEGER NOT NULL DEFAULT 1,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
            )
        ''')

        # test_cases 新增 mobile_engine 字段
        try:
            cursor.execute(
                "ALTER TABLE test_cases ADD COLUMN mobile_engine TEXT DEFAULT 'maestro'"
            )
        except sqlite3.OperationalError:
            pass

        # test_cases 新增 generated_by_ai 字段（标记 AI 生成的用例）
        try:
            cursor.execute(
                "ALTER TABLE test_cases ADD COLUMN generated_by_ai INTEGER DEFAULT 0"
            )
        except sqlite3.OperationalError:
            pass

        # run_history 新增引擎信息字段
        try:
            cursor.execute("ALTER TABLE run_history ADD COLUMN engine_type TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute("ALTER TABLE run_history ADD COLUMN device_udid TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute("ALTER TABLE run_history ADD COLUMN flow_name TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute("ALTER TABLE run_history ADD COLUMN self_healed_count INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute("ALTER TABLE run_history ADD COLUMN test_type TEXT DEFAULT 'web'")
        except sqlite3.OperationalError:
            pass

        # step_results 新增自愈信息字段
        try:
            cursor.execute("ALTER TABLE step_results ADD COLUMN healed_locator TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute("ALTER TABLE step_results ADD COLUMN locator_strategy TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute("ALTER TABLE step_results ADD COLUMN visual_confidence REAL")
        except sqlite3.OperationalError:
            pass

        # test_steps 新增 Maestro 扩展字段
        for col_spec in [
            ("maestro_label", "TEXT"),
            ("maestro_optional", "BOOLEAN DEFAULT FALSE"),
            ("maestro_retry", "INTEGER DEFAULT 0"),
            ("wait_timeout_ms", "INTEGER DEFAULT 10000"),
            ("swipe_start", "TEXT"),
            ("swipe_end", "TEXT"),
        ]:
            col_name, col_type = col_spec
            try:
                cursor.execute(
                    f"ALTER TABLE test_steps ADD COLUMN {col_name} {col_type}"
                )
            except sqlite3.OperationalError:
                pass
    
    def create_test_case(self, name: str, description: str = "", url: str = "") -> int:
        """创建测试用例"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        
        cursor.execute(
            "INSERT INTO test_cases (name, description, url) VALUES (?, ?, ?)",
            (name, description, url)
        )
        case_id = cursor.lastrowid
        
        conn.commit()
        conn.close()
        
        return case_id
    
    def get_test_case(self, case_id: int) -> Dict[str, Any]:
        """获取测试用例"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        
        cursor.execute(
            f"SELECT {_TEST_CASES_SELECT} FROM test_cases WHERE id = ?",
            (case_id,),
        )
        row = cursor.fetchone()
        
        if row:
            return _test_case_row_to_dict(row)
        
        conn.close()
        return None
    
    def get_all_test_cases(self) -> List[Dict[str, Any]]:
        """获取所有测试用例"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        
        cursor.execute(
            f"SELECT {_TEST_CASES_SELECT} FROM test_cases ORDER BY created_at DESC"
        )
        rows = cursor.fetchall()
        
        cases = [_test_case_row_to_dict(row) for row in rows]
        
        conn.close()
        return cases
    
    def update_test_case(self, case_id: int, name: str = None, description: str = None, url: str = None) -> bool:
        """更新测试用例"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        
        # 构建更新语句和参数
        updates = []
        params = []
        
        if name is not None:
            updates.append("name = ?")
            params.append(name)
        
        if description is not None:
            updates.append("description = ?")
            params.append(description)
        
        if url is not None:
            updates.append("url = ?")
            params.append(url)
        
        if not updates:
            conn.close()
            return False
        
        query = f"UPDATE test_cases SET {', '.join(updates)} WHERE id = ?"
        params.append(case_id)
        
        cursor.execute(query, params)
        success = cursor.rowcount > 0
        
        conn.commit()
        conn.close()
        
        return success
    
    def _delete_run_histories_bundle(self, cursor, rh_ids: List[int]) -> None:
        """删除给定 run_history 主键及其 step_results、关联缺陷（不按 case_id 删缺陷）。"""
        if not rh_ids:
            return
        ph = ",".join("?" * len(rh_ids))
        cursor.execute(
            f"SELECT id FROM step_results WHERE run_history_id IN ({ph})",
            rh_ids,
        )
        sr_ids = [row[0] for row in cursor.fetchall()]

        defect_ids = set()
        cursor.execute(f"SELECT id FROM defects WHERE run_history_id IN ({ph})", rh_ids)
        defect_ids.update(row[0] for row in cursor.fetchall())
        if sr_ids:
            sph = ",".join("?" * len(sr_ids))
            cursor.execute(
                f"SELECT id FROM defects WHERE step_result_id IN ({sph})",
                sr_ids,
            )
            defect_ids.update(row[0] for row in cursor.fetchall())

        if defect_ids:
            ids_list = list(defect_ids)
            dph = ",".join("?" * len(ids_list))
            cursor.execute(f"DELETE FROM defect_comments WHERE defect_id IN ({dph})", ids_list)
            cursor.execute(f"DELETE FROM defect_history WHERE defect_id IN ({dph})", ids_list)
            cursor.execute(f"DELETE FROM defects WHERE id IN ({dph})", ids_list)

        cursor.execute(f"DELETE FROM step_results WHERE run_history_id IN ({ph})", rh_ids)
        cursor.execute(f"DELETE FROM run_history WHERE id IN ({ph})", rh_ids)

    def _cleanup_orphan_run_history(self, cursor) -> int:
        """删除 case_id 为空或对应 test_cases 行已不存在的运行历史。返回删除的 run_history 条数。"""
        cursor.execute(
            """
            SELECT rh.id FROM run_history rh
            WHERE rh.case_id IS NULL
               OR NOT EXISTS (SELECT 1 FROM test_cases tc WHERE tc.id = rh.case_id)
            """
        )
        orphan_ids = [row[0] for row in cursor.fetchall()]
        self._delete_run_histories_bundle(cursor, orphan_ids)
        return len(orphan_ids)

    def prune_orphan_run_history(self) -> int:
        """删除孤立运行历史（独立事务）。删除项目/用例后调用可使报表立即与库一致。"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        try:
            n = self._cleanup_orphan_run_history(cursor)
            conn.commit()
            return n
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _delete_case_cascade(self, cursor, case_id: int) -> None:
        """在同一 cursor/事务内删除用例及依赖行（SQLite 外键顺序：缺陷 → 步骤结果 → 运行历史 → … → 用例）。"""
        cursor.execute("SELECT id FROM run_history WHERE case_id = ?", (case_id,))
        rh_ids = [row[0] for row in cursor.fetchall()]
        self._delete_run_histories_bundle(cursor, rh_ids)

        defect_ids = set()
        cursor.execute("SELECT id FROM defects WHERE case_id = ?", (case_id,))
        defect_ids.update(row[0] for row in cursor.fetchall())

        if defect_ids:
            ids_list = list(defect_ids)
            dph = ",".join("?" * len(ids_list))
            cursor.execute(f"DELETE FROM defect_comments WHERE defect_id IN ({dph})", ids_list)
            cursor.execute(f"DELETE FROM defect_history WHERE defect_id IN ({dph})", ids_list)
            cursor.execute(f"DELETE FROM defects WHERE id IN ({dph})", ids_list)

        cursor.execute("DELETE FROM test_scripts WHERE case_id = ?", (case_id,))
        cursor.execute("DELETE FROM variables WHERE case_id = ?", (case_id,))

        cursor.execute("SELECT id FROM test_data_sets WHERE case_id = ?", (case_id,))
        ds_ids = [row[0] for row in cursor.fetchall()]
        if ds_ids:
            dph = ",".join("?" * len(ds_ids))
            cursor.execute(f"DELETE FROM test_data_rows WHERE dataset_id IN ({dph})", ds_ids)
            cursor.execute(f"DELETE FROM test_data_sets WHERE id IN ({dph})", ds_ids)

        cursor.execute("DELETE FROM test_steps WHERE case_id = ?", (case_id,))
        cursor.execute("DELETE FROM test_cases WHERE id = ?", (case_id,))

    def delete_test_case(self, case_id: int) -> bool:
        """删除测试用例（级联删除步骤、运行历史、缺陷等依赖数据）。"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT 1 FROM test_cases WHERE id = ?", (case_id,))
            if not cursor.fetchone():
                return False
            self._delete_case_cascade(cursor, case_id)
            conn.commit()
            return True
        except Exception as e:
            _db_log.warning("删除测试用例失败: %s", e)
            conn.rollback()
            return False
        finally:
            conn.close()
    
    # ==================== 项目管理方法 ====================
    
    def create_project(self, name: str, description: str = "") -> int:
        """创建项目"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        
        cursor.execute(
            "INSERT INTO projects (name, description) VALUES (?, ?)",
            (name, description)
        )
        project_id = cursor.lastrowid
        
        conn.commit()
        conn.close()
        
        return project_id
    
    def get_project(self, project_id: int) -> Dict[str, Any]:
        """获取项目"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        
        cursor.execute(
            f"SELECT {_PROJECTS_SELECT} FROM projects WHERE id = ?",
            (project_id,),
        )
        row = cursor.fetchone()
        
        if row:
            d = _project_row_to_dict(row)
            conn.close()
            return d
        
        conn.close()
        return None
    
    def get_all_projects(self) -> List[Dict[str, Any]]:
        """获取所有项目"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        
        cursor.execute(
            f"SELECT {_PROJECTS_SELECT} FROM projects ORDER BY created_at DESC"
        )
        rows = cursor.fetchall()
        
        projects = [_project_row_to_dict(row) for row in rows]
        
        conn.close()
        return projects
    
    def update_project(self, project_id: int, name: str = None, description: str = None) -> bool:
        """更新项目"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        
        updates = []
        params = []
        
        if name is not None:
            updates.append("name = ?")
            params.append(name)
        
        if description is not None:
            updates.append("description = ?")
            params.append(description)
        
        if not updates:
            conn.close()
            return False
        
        query = f"UPDATE projects SET {', '.join(updates)} WHERE id = ?"
        params.append(project_id)
        
        cursor.execute(query, params)
        success = cursor.rowcount > 0
        
        conn.commit()
        conn.close()
        
        return success
    
    def delete_project(self, project_id: int) -> bool:
        """删除项目及调度、令牌、用例（级联）、数据集、缺陷、变量等依赖数据。"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,))
            if not cursor.fetchone():
                return False

            cursor.execute("SELECT id FROM schedules WHERE project_id = ?", (project_id,))
            sched_ids = [row[0] for row in cursor.fetchall()]
            if sched_ids:
                sph = ",".join("?" * len(sched_ids))
                cursor.execute(
                    f"DELETE FROM schedule_history WHERE schedule_id IN ({sph})",
                    sched_ids,
                )
            cursor.execute("DELETE FROM schedules WHERE project_id = ?", (project_id,))

            cursor.execute("DELETE FROM api_tokens WHERE project_id = ?", (project_id,))

            cursor.execute("SELECT id FROM test_cases WHERE project_id = ?", (project_id,))
            case_ids = [row[0] for row in cursor.fetchall()]
            for cid in case_ids:
                self._delete_case_cascade(cursor, cid)

            cursor.execute("SELECT id FROM test_data_sets WHERE project_id = ?", (project_id,))
            ds_ids = [row[0] for row in cursor.fetchall()]
            if ds_ids:
                dph = ",".join("?" * len(ds_ids))
                cursor.execute(
                    f"DELETE FROM test_data_rows WHERE dataset_id IN ({dph})", ds_ids
                )
                cursor.execute("DELETE FROM test_data_sets WHERE project_id = ?", (project_id,))

            cursor.execute("SELECT id FROM defects WHERE project_id = ?", (project_id,))
            defect_ids = [row[0] for row in cursor.fetchall()]
            if defect_ids:
                dph = ",".join("?" * len(defect_ids))
                cursor.execute(
                    f"DELETE FROM defect_comments WHERE defect_id IN ({dph})",
                    defect_ids,
                )
                cursor.execute(
                    f"DELETE FROM defect_history WHERE defect_id IN ({dph})",
                    defect_ids,
                )
                cursor.execute("DELETE FROM defects WHERE project_id = ?", (project_id,))

            cursor.execute("DELETE FROM variables WHERE project_id = ?", (project_id,))
            cursor.execute("DELETE FROM test_units WHERE project_id = ?", (project_id,))
            cursor.execute("DELETE FROM projects WHERE id = ?", (project_id,))
            success = cursor.rowcount > 0
            conn.commit()
            return success
        except Exception as e:
            _db_log.warning("删除项目失败: %s", e)
            conn.rollback()
            return False
        finally:
            conn.close()
    
    def get_project_case_count(self, project_id: int, case_type: Optional[str] = None) -> int:
        """项目下用例数量。case_type 为 'ui' / 'api' 时只计该类型；None 表示全部。"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        if case_type:
            ct = _normalize_case_type(case_type)
            cursor.execute(
                "SELECT COUNT(*) FROM test_cases WHERE project_id = ? AND COALESCE(case_type, 'ui') = ?",
                (project_id, ct),
            )
        else:
            cursor.execute(
                "SELECT COUNT(*) FROM test_cases WHERE project_id = ?",
                (project_id,),
            )
        n = int(cursor.fetchone()[0])
        conn.close()
        return n

    def get_ai_stats(self) -> Dict[str, Any]:
        """返回 AI 中心首页需要的真实统计数据。"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()

        # 1. AI 生成用例数
        cursor.execute(
            "SELECT COUNT(*) FROM test_cases WHERE COALESCE(generated_by_ai, 0) = 1"
        )
        ai_generated_cases = int(cursor.fetchone()[0])

        # 2. 效率提升：基于 run_history 中 self_healed_count > 0 的执行比例
        cursor.execute("SELECT COUNT(*) FROM run_history")
        total_runs = int(cursor.fetchone()[0] or 0)
        if total_runs > 0:
            cursor.execute(
                "SELECT COUNT(*) FROM run_history WHERE COALESCE(self_healed_count, 0) > 0"
            )
            healed_runs = int(cursor.fetchone()[0] or 0)
            efficiency_boost = round(healed_runs * 100.0 / total_runs)
        else:
            efficiency_boost = 0

        # 3. 覆盖率提升：有步骤的用例 / 总用例 的比例
        cursor.execute("SELECT COUNT(*) FROM test_cases")
        total_cases = int(cursor.fetchone()[0] or 0)
        if total_cases > 0:
            cursor.execute(
                "SELECT COUNT(DISTINCT case_id) FROM test_steps"
            )
            cases_with_steps = int(cursor.fetchone()[0] or 0)
            coverage_boost = round(cases_with_steps * 100.0 / total_cases)
        else:
            coverage_boost = 0

        conn.close()
        return {
            "ai_generated_cases": ai_generated_cases,
            "efficiency_boost": efficiency_boost,
            "coverage_boost": coverage_boost,
        }

    # ==================== 测试单元（项目 → 单元 → 用例 → 步骤） ====================

    def create_test_unit(
        self,
        project_id: int,
        name: str,
        description: str = "",
        sort_order: int = 0,
        parent_unit_id: Optional[int] = None,
    ) -> int:
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO test_units (project_id, name, description, sort_order, parent_unit_id)
            VALUES (?, ?, ?, ?, ?)
            """,
            (project_id, (name or "").strip(), description or "", int(sort_order or 0), parent_unit_id),
        )
        uid = cursor.lastrowid
        conn.commit()
        conn.close()
        return int(uid)

    def get_test_units(self, project_id: int) -> List[Dict[str, Any]]:
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT u.id, u.project_id, u.name, u.description, u.sort_order, u.parent_unit_id, u.created_at,
                   (SELECT COUNT(*) FROM test_cases tc WHERE tc.unit_id = u.id) AS case_count
            FROM test_units u
            WHERE u.project_id = ?
            ORDER BY u.sort_order ASC, u.id ASC
            """,
            (project_id,),
        )
        rows = cursor.fetchall()
        conn.close()
        return [
            {
                "id": r[0],
                "project_id": r[1],
                "name": r[2],
                "description": r[3] or "",
                "sort_order": int(r[4] or 0),
                "parent_unit_id": r[5],
                "created_at": _bj_iso(r[6]),
                "case_count": int(r[7] or 0),
            }
            for r in rows
        ]

    def get_test_unit(self, unit_id: int) -> Optional[Dict[str, Any]]:
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, project_id, name, description, sort_order, parent_unit_id, created_at "
            "FROM test_units WHERE id = ?",
            (unit_id,),
        )
        row = cursor.fetchone()
        conn.close()
        if not row:
            return None
        return {
            "id": row[0],
            "project_id": row[1],
            "name": row[2],
            "description": row[3] or "",
            "sort_order": int(row[4] or 0),
            "parent_unit_id": row[5],
            "created_at": _bj_iso(row[6]),
        }

    def update_test_unit(
        self,
        unit_id: int,
        *,
        name: Optional[str] = None,
        description: Optional[str] = None,
        sort_order: Optional[int] = None,
    ) -> bool:
        updates: List[str] = []
        params: List[Any] = []
        if name is not None:
            updates.append("name = ?")
            params.append(name.strip())
        if description is not None:
            updates.append("description = ?")
            params.append(description)
        if sort_order is not None:
            updates.append("sort_order = ?")
            params.append(int(sort_order))
        if not updates:
            return False
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        params.append(unit_id)
        cursor.execute(f"UPDATE test_units SET {', '.join(updates)} WHERE id = ?", params)
        ok = cursor.rowcount > 0
        conn.commit()
        conn.close()
        return ok

    def delete_test_unit(self, unit_id: int) -> bool:
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        cursor.execute("UPDATE test_cases SET unit_id = NULL WHERE unit_id = ?", (unit_id,))
        cursor.execute("DELETE FROM test_units WHERE id = ?", (unit_id,))
        ok = cursor.rowcount > 0
        conn.commit()
        conn.close()
        return ok

    def ensure_default_test_unit(self, project_id: int) -> int:
        units = self.get_test_units(project_id)
        if units:
            return int(units[0]["id"])
        return self.create_test_unit(project_id, "默认单元", description="系统自动创建")

    def get_project_cases(
        self,
        project_id: int,
        case_type: str = "ui",
        unit_id: Optional[Any] = None,
    ) -> List[Dict[str, Any]]:
        """获取项目下的测试用例。unit_id: None=全部, 'ungrouped'=未分组, int=指定单元。"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        ct_filter = _normalize_case_type(case_type)

        unit_clause = ""
        unit_params: List[Any] = []
        if unit_id is not None and str(unit_id).strip().lower() in ("ungrouped", "none", "0"):
            unit_clause = " AND tc.unit_id IS NULL"
        elif unit_id is not None and str(unit_id).strip() != "":
            try:
                uid = int(unit_id)
                unit_clause = " AND tc.unit_id = ?"
                unit_params.append(uid)
            except (TypeError, ValueError):
                pass

        cursor.execute(
            f"""
            SELECT tc.id, tc.project_id, tc.name, tc.url, tc.description, tc.created_at,
                   tc.precondition, tc.expected_result, COALESCE(tc.case_type, 'ui') AS case_type,
                   COALESCE(tc.case_role, 'business') AS case_role,
                   tc.unit_id, COALESCE(tu.name, '') AS unit_name,
                   COUNT(ts.id) AS step_count
            FROM test_cases tc
            LEFT JOIN test_steps ts ON tc.id = ts.case_id
            LEFT JOIN test_units tu ON tc.unit_id = tu.id
            WHERE tc.project_id = ? AND COALESCE(tc.case_type, 'ui') = ?{unit_clause}
            GROUP BY tc.id, tc.project_id, tc.name, tc.url, tc.description, tc.created_at,
                     tc.precondition, tc.expected_result, tc.case_type, tc.case_role, tc.unit_id, tu.name
            ORDER BY tc.created_at DESC
            """,
            (project_id, ct_filter, *unit_params),
        )
        rows = cursor.fetchall()

        cases = []
        for row in rows:
            base = row[:10]
            uid_val = row[10]
            unit_name = row[11] or ""
            sc = int(row[12] or 0)
            item = _test_case_row_to_dict(base, step_count=sc, unit_id=uid_val, unit_name=unit_name)
            cases.append(item)

        conn.close()
        return cases

    def get_project_cases_paginated(
        self,
        project_id: int,
        case_type: str = "ui",
        unit_id: Optional[Any] = None,
        page: int = 1,
        page_size: int = 10,
    ) -> tuple:
        """分页获取项目用例，返回 (cases, total)。"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        ct_filter = _normalize_case_type(case_type)

        unit_clause = ""
        unit_params: List[Any] = []
        if unit_id is not None and str(unit_id).strip().lower() in ("ungrouped", "none", "0"):
            unit_clause = " AND tc.unit_id IS NULL"
        elif unit_id is not None and str(unit_id).strip() != "":
            try:
                uid = int(unit_id)
                unit_clause = " AND tc.unit_id = ?"
                unit_params.append(uid)
            except (TypeError, ValueError):
                pass

        offset = (max(1, int(page)) - 1) * max(1, int(page_size))
        limit = max(1, int(page_size))
        cursor.execute(
            f"""
            SELECT tc.id, tc.project_id, tc.name, tc.url, tc.description, tc.created_at,
                   tc.precondition, tc.expected_result, COALESCE(tc.case_type, 'ui') AS case_type,
                   COALESCE(tc.case_role, 'business') AS case_role,
                   tc.unit_id, COALESCE(tu.name, '') AS unit_name,
                   COUNT(ts.id) AS step_count,
                   COUNT(*) OVER() AS __total
            FROM test_cases tc
            LEFT JOIN test_steps ts ON tc.id = ts.case_id
            LEFT JOIN test_units tu ON tc.unit_id = tu.id
            WHERE tc.project_id = ? AND COALESCE(tc.case_type, 'ui') = ?{unit_clause}
            GROUP BY tc.id, tc.project_id, tc.name, tc.url, tc.description, tc.created_at,
                     tc.precondition, tc.expected_result, tc.case_type, tc.case_role, tc.unit_id, tu.name
            ORDER BY tc.created_at DESC
            LIMIT ? OFFSET ?
            """,
            (project_id, ct_filter, *unit_params, limit, offset),
        )
        rows = cursor.fetchall()
        if not rows:
            cursor.execute(
                f"""
                SELECT COUNT(*) FROM test_cases tc
                WHERE tc.project_id = ? AND COALESCE(tc.case_type, 'ui') = ?{unit_clause}
                """,
                (project_id, ct_filter, *unit_params),
            )
            total = int(cursor.fetchone()[0] or 0)
            conn.close()
            return [], total

        total = int(rows[0][-1] or 0)
        cases = []
        for row in rows:
            base = row[:10]
            uid_val = row[10]
            unit_name = row[11] or ""
            sc = int(row[12] or 0)
            item = _test_case_row_to_dict(base, step_count=sc, unit_id=uid_val, unit_name=unit_name)
            cases.append(item)

        conn.close()
        return cases, total
    
    # ==================== 测试用例管理方法（新版本） ====================
    
    def create_test_case_v2(
        self,
        project_id: int,
        name: str,
        url: str = "",
        description: str = "",
        precondition: str = "",
        expected_result: str = "",
        case_type: str = "ui",
        case_role: str = "business",
        platform: str = "web",
        unit_id: Optional[int] = None,
        generated_by_ai: bool = False,
    ) -> int:
        """创建测试用例（新版本，关联到项目）"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        ct = _normalize_case_type(case_type)
        role = (case_role or "business").strip().lower()
        if role not in ("login_feature", "business", "auth_fixture"):
            role = "business"
        plat = _normalize_platform(platform)
        uid = int(unit_id) if unit_id else None
        cursor.execute(
            "INSERT INTO test_cases (project_id, name, url, description, precondition, expected_result, case_type, case_role, platform, unit_id, generated_by_ai) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (project_id, name, url, description, precondition, expected_result, ct, role, plat, uid, 1 if generated_by_ai else 0),
        )
        case_id = cursor.lastrowid

        conn.commit()
        conn.close()

        return case_id
    
    def get_test_case_v2(self, case_id: int) -> Dict[str, Any]:
        """获取测试用例（新版本）"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        
        cursor.execute(
            "SELECT id, project_id, name, url, description, created_at, precondition, expected_result, "
            "COALESCE(case_type, 'ui'), COALESCE(case_role, 'business'), COALESCE(platform, 'web'), unit_id "
            "FROM test_cases WHERE id = ?",
            (case_id,),
        )
        row = cursor.fetchone()

        if row:
            out = {
                'id': row[0],
                'project_id': row[1],
                'name': row[2],
                'url': row[3],
                'description': row[4],
                'created_at': _bj_iso(row[5]),
                'precondition': row[6] if len(row) > 6 else '',
                'expected_result': row[7] if len(row) > 7 else '',
                'case_type': _normalize_case_type(row[8]) if len(row) > 8 else 'ui',
                'case_role': (row[9] or 'business').strip() if len(row) > 9 else 'business',
                'platform': _normalize_platform(row[10]) if len(row) > 10 else 'web',
                'unit_id': row[11] if len(row) > 11 else None,
            }
            conn.close()
            return out

        conn.close()
        return None
    
    def update_test_case_v2(
        self,
        case_id: int,
        name: str = None,
        url: str = None,
        description: str = None,
        precondition: str = None,
        expected_result: str = None,
        platform: str = None,
        unit_id: Any = _UPDATE_UNSET,
    ) -> bool:
        """更新测试用例（新版本）"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        
        updates = []
        params = []
        
        if name is not None:
            updates.append("name = ?")
            params.append(name)
        
        if url is not None:
            updates.append("url = ?")
            params.append(url)
        
        if description is not None:
            updates.append("description = ?")
            params.append(description)
        
        if precondition is not None:
            updates.append("precondition = ?")
            params.append(precondition)
        
        if expected_result is not None:
            updates.append("expected_result = ?")
            params.append(expected_result)

        if platform is not None:
            updates.append("platform = ?")
            params.append(_normalize_platform(platform))

        if unit_id is not _UPDATE_UNSET:
            updates.append("unit_id = ?")
            params.append(int(unit_id) if unit_id else None)
        
        if not updates:
            conn.close()
            return False
        
        query = f"UPDATE test_cases SET {', '.join(updates)} WHERE id = ?"
        params.append(case_id)
        
        cursor.execute(query, params)
        success = cursor.rowcount > 0
        
        conn.commit()
        conn.close()
        
        return success

    def list_element_repository(
        self,
        project_id: int,
        platform: str = "",
    ) -> List[Dict[str, Any]]:
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        plat = _normalize_platform(platform) if platform else ""
        if plat:
            cursor.execute(
                "SELECT id, project_id, alias, platform, selector_type, selector_value, attributes_json, created_at, updated_at "
                "FROM element_repository WHERE project_id = ? AND platform = ? ORDER BY alias",
                (project_id, plat),
            )
        else:
            cursor.execute(
                "SELECT id, project_id, alias, platform, selector_type, selector_value, attributes_json, created_at, updated_at "
                "FROM element_repository WHERE project_id = ? ORDER BY platform, alias",
                (project_id,),
            )
        rows = cursor.fetchall()
        conn.close()
        out = []
        for row in rows:
            attrs = {}
            try:
                if row[6]:
                    attrs = json.loads(row[6])
            except (json.JSONDecodeError, TypeError):
                attrs = {}
            out.append({
                "id": row[0],
                "project_id": row[1],
                "alias": row[2],
                "platform": row[3],
                "selector_type": row[4],
                "selector_value": row[5],
                "attributes": attrs,
                "created_at": _bj_iso(row[7]),
                "updated_at": _bj_iso(row[8]),
            })
        return out

    def create_element_repository_entry(
        self,
        project_id: int,
        alias: str,
        platform: str,
        selector_type: str,
        selector_value: str,
        attributes: Optional[Dict[str, Any]] = None,
    ) -> int:
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        plat = _normalize_platform(platform)
        attrs_json = json.dumps(attributes or {}, ensure_ascii=False)
        cursor.execute(
            "INSERT INTO element_repository (project_id, alias, platform, selector_type, selector_value, attributes_json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (project_id, (alias or "").strip(), plat, (selector_type or "").strip(), selector_value or "", attrs_json),
        )
        eid = cursor.lastrowid
        conn.commit()
        conn.close()
        return int(eid)

    def update_element_repository_entry(
        self,
        element_id: int,
        *,
        alias: str = None,
        selector_type: str = None,
        selector_value: str = None,
        attributes: Optional[Dict[str, Any]] = None,
    ) -> bool:
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        updates = ["updated_at = CURRENT_TIMESTAMP"]
        params: List[Any] = []
        if alias is not None:
            updates.append("alias = ?")
            params.append(alias.strip())
        if selector_type is not None:
            updates.append("selector_type = ?")
            params.append(selector_type.strip())
        if selector_value is not None:
            updates.append("selector_value = ?")
            params.append(selector_value)
        if attributes is not None:
            updates.append("attributes_json = ?")
            params.append(json.dumps(attributes, ensure_ascii=False))
        if len(updates) <= 1:
            conn.close()
            return False
        params.append(element_id)
        cursor.execute(
            f"UPDATE element_repository SET {', '.join(updates)} WHERE id = ?",
            params,
        )
        ok = cursor.rowcount > 0
        conn.commit()
        conn.close()
        return ok

    def get_element_by_alias(
        self,
        project_id: int,
        alias: str,
        platform: str = "android",
    ) -> Optional[Dict[str, Any]]:
        items = self.list_element_repository(project_id, platform=platform)
        key = (alias or "").strip()
        for item in items:
            if item.get("alias") == key:
                return item
        return None
    
    def delete_test_case_v2(self, case_id: int) -> bool:
        """删除测试用例及其步骤、运行历史、缺陷等依赖（新版本，与 delete_test_case 级联一致）。"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT 1 FROM test_cases WHERE id = ?", (case_id,))
            if not cursor.fetchone():
                return False
            self._delete_case_cascade(cursor, case_id)
            conn.commit()
            return True
        except Exception as e:
            _db_log.warning("删除测试用例失败: %s", e)
            conn.rollback()
            return False
        finally:
            conn.close()
    
    # ==================== 测试步骤管理方法 ====================
    
    def create_test_step(self, case_id: int, action: str, selector_type: str = "", 
                         selector_value: str = "", input_value: str = "", 
                         description: str = "", step_order: int = None, page_name: str = "",
                         swipe_x: str = "", swipe_y: str = "", url: str = "",
                         enter_iframe: bool = False, iframe_selector: str = "", compare_type: str = "equals",
                         locator_candidates: str = "", click_repeat_count: int = 1,
                         api_spec: str = "", automation_layer: str = "web",
                         desktop_spec: str = "", mobile_spec: str = "",
                         captcha_max_attempts: Optional[int] = None) -> int:
        """创建测试步骤"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()
            
        # 如果没有指定 step_order，自动计算最大顺序值
        if step_order is None:
            cursor.execute("SELECT MAX(step_order) FROM test_steps WHERE case_id = ?", (case_id,))
            max_order = cursor.fetchone()[0]
            step_order = (max_order or 0) + 1
        try:
            crc = int(click_repeat_count)
        except (TypeError, ValueError):
            crc = 1
        if crc < 1:
            crc = 1
        if crc > 99:
            crc = 99
        layer = (automation_layer or "web").strip().lower()
        if layer not in ("web", "desktop", "android"):
            layer = "web"
        if desktop_spec is not None and not isinstance(desktop_spec, str):
            try:
                desktop_spec = json.dumps(desktop_spec, ensure_ascii=False)
            except Exception:
                desktop_spec = ""
        desktop_spec = desktop_spec or ""
        if mobile_spec is not None and not isinstance(mobile_spec, str):
            try:
                mobile_spec = json.dumps(mobile_spec, ensure_ascii=False)
            except Exception:
                mobile_spec = ""
        mobile_spec = mobile_spec or ""
        cma = None
        if captcha_max_attempts is not None:
            try:
                cma = int(captcha_max_attempts)
            except (TypeError, ValueError):
                cma = None
            if cma is not None and (cma < 1 or cma > 20):
                cma = max(1, min(cma, 20))
            
        cursor.execute(
            """INSERT INTO test_steps 
               (case_id, action, selector_type, selector_value, input_value, description, step_order, page_name, swipe_x, swipe_y, url, enter_iframe, iframe_selector, compare_type, locator_candidates, click_repeat_count, api_spec, automation_layer, desktop_spec, captcha_max_attempts, mobile_spec) 
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (case_id, action, selector_type, selector_value, input_value, description, step_order, page_name, swipe_x, swipe_y, url, enter_iframe, iframe_selector, compare_type, locator_candidates or '', crc, api_spec or '', layer, desktop_spec, cma, mobile_spec)
        )
        step_id = cursor.lastrowid
            
        conn.commit()
        conn.close()
            
        return step_id
        
    def batch_insert_steps(self, case_id: int, steps: List[Dict[str, Any]]) -> bool:
        """批量插入测试步骤（用于录制功能）"""
        if not steps:
            return True
                
        conn = self._sqlite_connect()
        cursor = conn.cursor()
            
        try:
            # 获取当前最大 step_order
            cursor.execute("SELECT MAX(step_order) FROM test_steps WHERE case_id = ?", (case_id,))
            max_order = cursor.fetchone()[0] or 0
                
            # 批量插入步骤
            for i, step in enumerate(steps):
                # 兼容两种录制格式：
                # 新格式：action/selector_type/selector_value/input_value/step_order
                # 旧格式：operation_type/operation_locator/operation_value/sort_order
                action = step.get('action')
                selector_type = step.get('selector_type')
                selector_value = step.get('selector_value')
                input_value = step.get('input_value')
                desc = step.get('description', '')
                sort_order = step.get('step_order', step.get('sort_order', max_order + i + 1))
                locator_candidates = step.get('locator_candidates')
                if locator_candidates is not None and not isinstance(locator_candidates, str):
                    try:
                        locator_candidates = json.dumps(locator_candidates, ensure_ascii=False)
                    except Exception:
                        locator_candidates = ''
                if locator_candidates is None:
                    locator_candidates = ''

                if not action:
                    operation_type = step.get('operation_type', 'click')
                    action_map = {
                        'click': 'click',
                        'input': 'input',
                        'select': 'select',
                        'hover': 'hover',
                        'keypress': 'keypress',
                        'navigate': 'navigate',
                        'scroll': 'scroll',
                        'double_click': 'double_click',
                        'right_click': 'right_click',
                    }
                    action = action_map.get(operation_type, 'click')
                    selector_value = step.get('operation_locator', '')
                    input_value = step.get('operation_value', '')

                if selector_value is None:
                    selector_value = ''
                if input_value is None:
                    input_value = ''
                if not selector_type:
                    selector_type = 'xpath' if str(selector_value).startswith('//') or str(selector_value).startswith('/') else 'css'
                    
                layer = (step.get("automation_layer") or "web").strip().lower()
                if layer not in ("web", "desktop", "android"):
                    layer = "web"
                ds = step.get("desktop_spec") or ""
                ms = step.get("mobile_spec") or ""
                if ds is not None and not isinstance(ds, str):
                    try:
                        ds = json.dumps(ds, ensure_ascii=False)
                    except Exception:
                        ds = ""
                if ms is not None and not isinstance(ms, str):
                    try:
                        ms = json.dumps(ms, ensure_ascii=False)
                    except Exception:
                        ms = ""
                cursor.execute(
                    """INSERT INTO test_steps 
                       (case_id, action, selector_type, selector_value, input_value, description, step_order, locator_candidates, automation_layer, desktop_spec, mobile_spec) 
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (case_id, action, selector_type, selector_value, input_value, desc, sort_order, locator_candidates, layer, ds or "", ms or "")
                )
                
            conn.commit()
            return True
        except Exception as e:
            _db_log.warning("批量插入步骤失败：%s", e)
            conn.rollback()
            return False
        finally:
            conn.close()
    
    def get_test_step(self, step_id: int) -> Dict[str, Any]:
        """获取测试步骤"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        
        cursor.execute(
            f"SELECT {_TEST_STEPS_SELECT} FROM test_steps WHERE id = ?",
            (step_id,),
        )
        row = cursor.fetchone()
        conn.close()
        if row:
            return self._row_to_step_dict(row)
        return None
    
    def _row_to_step_dict(self, row: tuple) -> Dict[str, Any]:
        crc = row[17] if len(row) > 17 else 1
        try:
            crc = int(crc) if crc is not None else 1
        except (TypeError, ValueError):
            crc = 1
        if crc < 1:
            crc = 1
        if crc > 99:
            crc = 99
        return {
            'id': row[0],
            'case_id': row[1],
            'action': row[2],
            'selector_type': row[3],
            'selector_value': row[4],
            'input_value': row[5],
            'description': row[6],
            'step_order': row[7],
            'created_at': _bj_iso(row[8]),
            'page_name': row[9] if len(row) > 9 else '',
            'swipe_x': row[10] if len(row) > 10 else '',
            'swipe_y': row[11] if len(row) > 11 else '',
            'url': row[12] if len(row) > 12 else '',
            'enter_iframe': row[13] if len(row) > 13 else False,
            'iframe_selector': row[14] if len(row) > 14 else '',
            'compare_type': row[15] if len(row) > 15 else 'equals',
            'locator_candidates': row[16] if len(row) > 16 else '',
            'click_repeat_count': crc,
            'api_spec': row[18] if len(row) > 18 else '',
            'automation_layer': (row[19] if len(row) > 19 and row[19] else 'web') or 'web',
            'desktop_spec': row[20] if len(row) > 20 else '',
            'captcha_max_attempts': row[21] if len(row) > 21 else None,
            'mobile_spec': row[22] if len(row) > 22 else '',
        }

    def get_case_steps(self, case_id: int, page: int = 1, page_size: int = 9999) -> List[Dict[str, Any]]:
        """获取测试用例的步骤（支持分页）- 修改默认page_size为9999以获取所有步骤"""
        steps, _total = self.get_case_steps_paginated(case_id, page, page_size)
        return steps

    def get_case_steps_paginated(self, case_id: int, page: int = 1, page_size: int = 10) -> tuple:
        """分页查询步骤，同一连接内用窗口函数返回 total，避免两次往返数据库。"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        offset = (max(1, page) - 1) * max(1, page_size)
        limit = max(1, page_size)
        cursor.execute(
            f"""
            SELECT {_TEST_STEPS_SELECT},
                   COUNT(*) OVER() AS __total
            FROM test_steps
            WHERE case_id = ?
            ORDER BY step_order ASC
            LIMIT ? OFFSET ?
            """,
            (case_id, limit, offset),
        )
        rows = cursor.fetchall()
        if not rows:
            cursor.execute("SELECT COUNT(*) FROM test_steps WHERE case_id = ?", (case_id,))
            total = int(cursor.fetchone()[0])
            conn.close()
            return [], total
        total = int(rows[0][-1])
        steps = [self._row_to_step_dict(row[:-1]) for row in rows]
        conn.close()
        return steps, total
    
    def get_case_steps_count(self, case_id: int) -> int:
        """获取测试用例步骤的总数"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM test_steps WHERE case_id = ?", (case_id,))
        count = cursor.fetchone()[0]
        conn.close()
        return count
    
    def update_test_step(self, step_id: int, action: str = None, selector_type: str = None,
                        selector_value: str = None, input_value: str = None,
                        description: str = None, step_order: int = None,
                        enter_iframe: bool = None, iframe_selector: str = None, compare_type: str = None,
                        locator_candidates: str = None, click_repeat_count: int = None,
                        api_spec: str = None, url: str = None,
                        automation_layer: str = None, desktop_spec: str = None,
                        mobile_spec: str = None,
                        captcha_max_attempts: int = None) -> bool:
        """更新测试步骤"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        
        updates = []
        params = []
        
        if action is not None:
            updates.append("action = ?")
            params.append(action)
        
        if selector_type is not None:
            updates.append("selector_type = ?")
            params.append(selector_type)
        
        if selector_value is not None:
            updates.append("selector_value = ?")
            params.append(selector_value)
        
        if input_value is not None:
            updates.append("input_value = ?")
            params.append(input_value)
        
        if description is not None:
            updates.append("description = ?")
            params.append(description)
        
        if step_order is not None:
            updates.append("step_order = ?")
            params.append(step_order)
        
        if enter_iframe is not None:
            updates.append("enter_iframe = ?")
            params.append(enter_iframe)
        
        if iframe_selector is not None:
            updates.append("iframe_selector = ?")
            params.append(iframe_selector)
        
        if compare_type is not None:
            updates.append("compare_type = ?")
            params.append(compare_type)

        if locator_candidates is not None:
            updates.append("locator_candidates = ?")
            params.append(locator_candidates)

        if click_repeat_count is not None:
            try:
                crc = int(click_repeat_count)
            except (TypeError, ValueError):
                crc = 1
            if crc < 1:
                crc = 1
            if crc > 99:
                crc = 99
            updates.append("click_repeat_count = ?")
            params.append(crc)

        if api_spec is not None:
            updates.append("api_spec = ?")
            params.append(api_spec)

        if url is not None:
            updates.append("url = ?")
            params.append(url)

        if automation_layer is not None:
            layer = (automation_layer or "web").strip().lower()
            if layer not in ("web", "desktop", "android"):
                layer = "web"
            updates.append("automation_layer = ?")
            params.append(layer)

        if desktop_spec is not None:
            if not isinstance(desktop_spec, str):
                try:
                    desktop_spec = json.dumps(desktop_spec, ensure_ascii=False)
                except Exception:
                    desktop_spec = ""
            updates.append("desktop_spec = ?")
            params.append(desktop_spec)

        if mobile_spec is not None:
            if not isinstance(mobile_spec, str):
                try:
                    mobile_spec = json.dumps(mobile_spec, ensure_ascii=False)
                except Exception:
                    mobile_spec = ""
            updates.append("mobile_spec = ?")
            params.append(mobile_spec)

        if captcha_max_attempts is not None:
            try:
                cma = int(captcha_max_attempts)
            except (TypeError, ValueError):
                cma = None
            if cma is not None:
                cma = max(1, min(cma, 20))
            updates.append("captcha_max_attempts = ?")
            params.append(cma)
        
        if not updates:
            conn.close()
            return False
        
        query = f"UPDATE test_steps SET {', '.join(updates)} WHERE id = ?"
        params.append(step_id)
        
        cursor.execute(query, params)
        success = cursor.rowcount > 0
        
        conn.commit()
        conn.close()
        
        return success

    def case_has_desktop_steps(self, case_id: int) -> bool:
        """用例是否包含桌面自动化步骤（用于混排运行环境检测）。"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT 1 FROM test_steps
            WHERE case_id = ? AND LOWER(TRIM(COALESCE(automation_layer, 'web'))) = 'desktop'
            LIMIT 1
            """,
            (case_id,),
        )
        row = cursor.fetchone()
        conn.close()
        return row is not None
    
    def delete_test_step(self, step_id: int) -> bool:
        """删除测试步骤"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        
        cursor.execute("DELETE FROM test_steps WHERE id = ?", (step_id,))
        
        success = cursor.rowcount > 0
        
        conn.commit()
        conn.close()
        
        return success
    
    # ==================== 运行历史记录管理方法 ====================
    
    def create_run_history(self, case_id: int, status: str, duration: float, error: str = "", extracted_text: str = "", expected_text: str = "", test_type: str = "web") -> int:
        """创建运行历史记录"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        
        # 与 SQLite CURRENT_TIMESTAMP 一致：写入 UTC，展示由 API 层转北京时间
        local_time = _utc_now_sql()
        
        cursor.execute(
            "INSERT INTO run_history (case_id, status, duration, error, extracted_text, expected_text, test_type, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (case_id, status, duration, error, extracted_text, expected_text, test_type, local_time)
        )
        history_id = cursor.lastrowid
        
        conn.commit()
        conn.close()
        
        return history_id
    
    def get_all_run_history(self, page: int = 1, page_size: int = 20, case_id: int = None, search_text: str = None, project_id: int = None, status_filter: str = None) -> List[Dict[str, Any]]:
        """获取所有运行历史记录（支持分页、按测试用例ID过滤、按项目ID过滤、搜索、执行状态过滤：passed/failed）"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        
        offset = (page - 1) * page_size
        st_rh = ""
        if status_filter == 'passed':
            st_rh = " AND (rh.status IN ('passed', 'success'))"
        elif status_filter == 'failed':
            st_rh = " AND (rh.status IN ('failed', 'error', 'fail'))"
        
        if case_id:
            if search_text:
                cursor.execute(f"""
                    SELECT rh.*, tc.name as case_name 
                    FROM run_history rh 
                    LEFT JOIN test_cases tc ON rh.case_id = tc.id 
                    WHERE rh.case_id = ? AND tc.name LIKE ?{st_rh}
                    ORDER BY rh.created_at DESC
                    LIMIT ? OFFSET ?
                """, (case_id, f'%{search_text}%', page_size, offset))
            else:
                cursor.execute(f"""
                    SELECT rh.*, tc.name as case_name 
                    FROM run_history rh 
                    LEFT JOIN test_cases tc ON rh.case_id = tc.id 
                    WHERE rh.case_id = ?{st_rh}
                    ORDER BY rh.created_at DESC
                    LIMIT ? OFFSET ?
                """, (case_id, page_size, offset))
        else:
            if project_id:
                if search_text:
                    cursor.execute(f"""
                        SELECT rh.*, tc.name as case_name 
                        FROM run_history rh 
                        LEFT JOIN test_cases tc ON rh.case_id = tc.id 
                        WHERE tc.project_id = ? AND tc.name LIKE ?{st_rh}
                        ORDER BY rh.created_at DESC
                        LIMIT ? OFFSET ?
                    """, (project_id, f'%{search_text}%', page_size, offset))
                else:
                    cursor.execute(f"""
                        SELECT rh.*, tc.name as case_name 
                        FROM run_history rh 
                        LEFT JOIN test_cases tc ON rh.case_id = tc.id 
                        WHERE tc.project_id = ?{st_rh}
                        ORDER BY rh.created_at DESC
                        LIMIT ? OFFSET ?
                    """, (project_id, page_size, offset))
            else:
                if search_text:
                    cursor.execute(f"""
                        SELECT rh.*, tc.name as case_name 
                        FROM run_history rh 
                        LEFT JOIN test_cases tc ON rh.case_id = tc.id 
                        WHERE tc.name LIKE ?{st_rh}
                        ORDER BY rh.created_at DESC
                        LIMIT ? OFFSET ?
                    """, (f'%{search_text}%', page_size, offset))
                else:
                    cursor.execute(f"""
                        SELECT rh.*, tc.name as case_name 
                        FROM run_history rh 
                        LEFT JOIN test_cases tc ON rh.case_id = tc.id 
                        WHERE 1=1{st_rh}
                        ORDER BY rh.created_at DESC
                        LIMIT ? OFFSET ?
                    """, (page_size, offset))
        rows = cursor.fetchall()
        
        history = []
        for row in rows:
            # run_history 表字段: id, case_id, status, duration, error, extracted_text, created_at, expected_text, screenshots
            # 最后一个是 case_name (来自 JOIN)
            history.append({
                'id': row[0],
                'case_id': row[1],
                'status': row[2],
                'duration': row[3],
                'error': row[4],
                'extracted_text': row[5],
                'created_at': _bj_iso(row[6]),
                'expected_text': row[7] if len(row) > 7 else '',
                'screenshots': row[8] if len(row) > 8 else None,
                'case_name': row[9] if len(row) > 9 else '未知用例'
            })
        
        conn.close()
        return history

    def get_run_history_count(self, case_id: int = None, search_text: str = None, project_id: int = None, status_filter: str = None) -> int:
        """获取运行历史记录总数（支持按测试用例ID过滤、按项目ID过滤、搜索和执行状态过滤）"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        
        st_rh = ""
        st_plain = ""
        if status_filter == 'passed':
            st_rh = " AND (rh.status IN ('passed', 'success'))"
            st_plain = " AND status IN ('passed', 'success')"
        elif status_filter == 'failed':
            st_rh = " AND (rh.status IN ('failed', 'error', 'fail'))"
            st_plain = " AND status IN ('failed', 'error', 'fail')"
        
        if case_id:
            if search_text:
                cursor.execute(f"""
                    SELECT COUNT(*) 
                    FROM run_history rh 
                    LEFT JOIN test_cases tc ON rh.case_id = tc.id 
                    WHERE rh.case_id = ? AND tc.name LIKE ?{st_rh}
                """, (case_id, f'%{search_text}%'))
            else:
                cursor.execute(f"SELECT COUNT(*) FROM run_history WHERE case_id = ?{st_plain}", (case_id,))
        else:
            if project_id:
                if search_text:
                    cursor.execute(f"""
                        SELECT COUNT(*) 
                        FROM run_history rh 
                        LEFT JOIN test_cases tc ON rh.case_id = tc.id 
                        WHERE tc.project_id = ? AND tc.name LIKE ?{st_rh}
                    """, (project_id, f'%{search_text}%'))
                else:
                    cursor.execute(f"""
                        SELECT COUNT(*) 
                        FROM run_history rh 
                        LEFT JOIN test_cases tc ON rh.case_id = tc.id 
                        WHERE tc.project_id = ?{st_rh}
                    """, (project_id,))
            else:
                if search_text:
                    cursor.execute(f"""
                        SELECT COUNT(*) 
                        FROM run_history rh 
                        LEFT JOIN test_cases tc ON rh.case_id = tc.id 
                        WHERE tc.name LIKE ?{st_rh}
                    """, (f'%{search_text}%',))
                else:
                    if st_plain:
                        cursor.execute(f"SELECT COUNT(*) FROM run_history WHERE 1=1{st_plain}")
                    else:
                        cursor.execute("SELECT COUNT(*) FROM run_history")
        count = cursor.fetchone()[0]
        
        conn.close()
        return count
    
    def get_case_run_history(self, case_id: int) -> List[Dict[str, Any]]:
        """获取指定测试用例的运行历史记录"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT * FROM run_history 
            WHERE case_id = ? 
            ORDER BY created_at DESC
        """, (case_id,))
        rows = cursor.fetchall()
        
        history = []
        for row in rows:
            history.append({
                'id': row[0],
                'case_id': row[1],
                'status': row[2],
                'duration': row[3],
                'error': row[4],
                'extracted_text': row[5],
                'created_at': _bj_iso(row[6]),
                'expected_text': row[7] if len(row) > 7 else ''
            })
        
        conn.close()
        return history
    
    def delete_run_history(self, history_id: int) -> bool:
        """删除运行历史记录（含步骤结果与关联缺陷）"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT 1 FROM run_history WHERE id = ?", (history_id,))
            if not cursor.fetchone():
                return False
            self._delete_run_histories_bundle(cursor, [history_id])
            conn.commit()
            return True
        finally:
            conn.close()
    
    def delete_case_run_history(self, case_id: int) -> bool:
        """删除指定测试用例的所有运行历史记录"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT id FROM run_history WHERE case_id = ?", (case_id,))
            rh_ids = [row[0] for row in cursor.fetchall()]
            self._delete_run_histories_bundle(cursor, rh_ids)
            conn.commit()
            return len(rh_ids) > 0
        finally:
            conn.close()
    
    def delete_all_run_history(self) -> bool:
        """删除所有运行历史记录"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT id FROM run_history")
            rh_ids = [row[0] for row in cursor.fetchall()]
            self._delete_run_histories_bundle(cursor, rh_ids)
            conn.commit()
            return len(rh_ids) > 0
        finally:
            conn.close()
    
    def get_run_history_detail(self, record_id: int) -> Dict[str, Any]:
        """获取运行历史记录详情"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT rh.*, tc.name as case_name 
            FROM run_history rh 
            LEFT JOIN test_cases tc ON rh.case_id = tc.id 
            WHERE rh.id = ?
        """, (record_id,))
        row = cursor.fetchone()
        
        if row:
            # run_history 表字段: id, case_id, status, duration, error, extracted_text, created_at, expected_text, screenshots
            # 最后一个是 case_name (来自 JOIN)
            result = {
                'id': row[0],
                'case_id': row[1],
                'status': row[2],
                'duration': row[3],
                'error': row[4],
                'extracted_text': row[5],
                'created_at': _bj_iso(row[6]),
                'expected_text': row[7] if len(row) > 7 else '',
                'screenshots': row[8] if len(row) > 8 else None,
                'case_name': row[9] if len(row) > 9 else '未知用例'
            }
            conn.close()
            return result
        
        conn.close()
        return None
    
    def delete_case_steps(self, case_id: int) -> bool:
        """删除测试用例的所有步骤"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        
        cursor.execute("DELETE FROM test_steps WHERE case_id = ?", (case_id,))
        
        success = cursor.rowcount > 0
        
        conn.commit()
        conn.close()
        
        return success
    
    def update_step_order(self, case_id: int, steps: List[Dict[str, Any]]) -> bool:
        """更新测试步骤的顺序"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        
        try:
            # 开始事务
            conn.execute("BEGIN TRANSACTION")
            
            # 更新每个步骤的顺序
            for step in steps:
                step_id = step.get('id')
                step_order = step.get('order')
                
                if step_id and step_order:
                    cursor.execute(
                        "UPDATE test_steps SET step_order = ? WHERE id = ? AND case_id = ?",
                        (step_order, step_id, case_id)
                    )
            
            # 提交事务
            conn.commit()
            return True
        except Exception as e:
            _db_log.warning("更新步骤顺序失败: %s", e)
            conn.rollback()
            return False
        finally:
            conn.close()

    def count_steps_with_action(self, case_id: int, action: str) -> int:
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM test_steps WHERE case_id = ? AND action = ?",
            (case_id, action),
        )
        n = int(cursor.fetchone()[0])
        conn.close()
        return n

    def migrate_api_steps_from_ui_case(
        self,
        ui_case_id: int,
        target_api_case_id: Optional[int] = None,
        target_api_case_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """将 Web 用例中的 api_request 步骤复制到接口用例并从 Web 用例删除，剩余步骤重排 step_order。"""
        api_steps = [
            s
            for s in self.get_case_steps(ui_case_id)
            if (s.get("action") or "") == "api_request"
        ]
        if not api_steps:
            return {"success": False, "error": "该 Web 用例没有可迁移的接口步骤（api_request）"}

        conn = self._sqlite_connect()
        cursor = conn.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            cursor.execute(
                "SELECT id, project_id, name, url, description, precondition, expected_result, COALESCE(case_type,'ui') FROM test_cases WHERE id = ?",
                (ui_case_id,),
            )
            urow = cursor.fetchone()
            if not urow:
                conn.rollback()
                return {"success": False, "error": "源用例不存在"}
            if _normalize_case_type(urow[7]) != "ui":
                conn.rollback()
                return {"success": False, "error": "只能从 Web 用例迁移接口步骤"}
            project_id = int(urow[1])

            if target_api_case_id:
                tid = int(target_api_case_id)
                cursor.execute(
                    "SELECT id, project_id, COALESCE(case_type,'ui') FROM test_cases WHERE id = ?",
                    (tid,),
                )
                trow = cursor.fetchone()
                if not trow:
                    conn.rollback()
                    return {"success": False, "error": "目标接口用例不存在"}
                if int(trow[1]) != project_id:
                    conn.rollback()
                    return {"success": False, "error": "目标用例必须与源用例属于同一项目"}
                if _normalize_case_type(trow[2]) != "api":
                    conn.rollback()
                    return {"success": False, "error": "目标用例必须是接口用例（case_type=api）"}
                api_case_id = tid
            else:
                name = (target_api_case_name or "").strip() or f"{urow[2] or '用例'} (接口)"
                cursor.execute(
                    "INSERT INTO test_cases (project_id, name, url, description, precondition, expected_result, case_type) VALUES (?, ?, ?, ?, ?, ?, 'api')",
                    (project_id, name, urow[3] or "", urow[4] or "", urow[5] or "", urow[6] or ""),
                )
                api_case_id = int(cursor.lastrowid)

            cursor.execute(
                "SELECT COALESCE(MAX(step_order), 0) FROM test_steps WHERE case_id = ?",
                (api_case_id,),
            )
            next_ord = int(cursor.fetchone()[0] or 0)

            migrated_ids: List[int] = []
            for st in sorted(api_steps, key=lambda x: int(x.get("step_order") or 0)):
                next_ord += 1
                migrated_ids.append(int(st["id"]))
                cursor.execute(
                    """INSERT INTO test_steps
                    (case_id, action, selector_type, selector_value, input_value, description, step_order,
                     page_name, swipe_x, swipe_y, url, enter_iframe, iframe_selector, compare_type,
                     locator_candidates, click_repeat_count, api_spec)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        api_case_id,
                        st.get("action") or "api_request",
                        st.get("selector_type") or "",
                        st.get("selector_value") or "",
                        st.get("input_value") or "",
                        st.get("description") or "",
                        next_ord,
                        st.get("page_name") or "",
                        st.get("swipe_x") or "",
                        st.get("swipe_y") or "",
                        st.get("url") or "",
                        1 if st.get("enter_iframe") else 0,
                        st.get("iframe_selector") or "",
                        st.get("compare_type") or "equals",
                        st.get("locator_candidates") or "",
                        int(st.get("click_repeat_count") or 1),
                        st.get("api_spec") or "",
                    ),
                )

            if migrated_ids:
                ph = ",".join("?" * len(migrated_ids))
                cursor.execute(
                    f"DELETE FROM test_steps WHERE case_id = ? AND id IN ({ph})",
                    [ui_case_id] + migrated_ids,
                )

            cursor.execute(
                "SELECT id FROM test_steps WHERE case_id = ? ORDER BY step_order ASC, id ASC",
                (ui_case_id,),
            )
            rem = [int(r[0]) for r in cursor.fetchall()]
            for i, sid in enumerate(rem, start=1):
                cursor.execute(
                    "UPDATE test_steps SET step_order = ? WHERE id = ? AND case_id = ?",
                    (i, sid, ui_case_id),
                )

            conn.commit()
            return {
                "success": True,
                "target_api_case_id": api_case_id,
                "migrated_count": len(migrated_ids),
            }
        except Exception as e:
            _db_log.warning("migrate_api_steps_from_ui_case: %s", e)
            conn.rollback()
            return {"success": False, "error": str(e)}
        finally:
            conn.close()

    def duplicate_test_case_with_steps(
        self, source_case_id: int, new_name: str, case_type: Optional[str] = None
    ) -> Optional[int]:
        """复制用例及其全部步骤。case_type 默认沿用源用例（规范化后）。"""
        src = self.get_test_case_v2(source_case_id)
        if not src:
            return None
        ct = _normalize_case_type(case_type) if case_type else _normalize_case_type(src.get("case_type"))
        pid = int(src["project_id"])
        new_id = self.create_test_case_v2(
            pid,
            new_name.strip() or f"{src.get('name') or '用例'} 副本",
            src.get("url") or "",
            src.get("description") or "",
            src.get("precondition") or "",
            src.get("expected_result") or "",
            case_type=ct,
        )
        steps = self.get_case_steps(source_case_id)
        if not steps:
            return new_id
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        try:
            for st in sorted(steps, key=lambda x: int(x.get("step_order") or 0)):
                layer = (st.get("automation_layer") or "web").strip().lower()
                if layer not in ("web", "desktop"):
                    layer = "web"
                cursor.execute(
                    """INSERT INTO test_steps
                    (case_id, action, selector_type, selector_value, input_value, description, step_order,
                     page_name, swipe_x, swipe_y, url, enter_iframe, iframe_selector, compare_type,
                     locator_candidates, click_repeat_count, api_spec, automation_layer, desktop_spec)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        new_id,
                        st.get("action") or "",
                        st.get("selector_type") or "",
                        st.get("selector_value") or "",
                        st.get("input_value") or "",
                        st.get("description") or "",
                        int(st.get("step_order") or 0),
                        st.get("page_name") or "",
                        st.get("swipe_x") or "",
                        st.get("swipe_y") or "",
                        st.get("url") or "",
                        1 if st.get("enter_iframe") else 0,
                        st.get("iframe_selector") or "",
                        st.get("compare_type") or "equals",
                        st.get("locator_candidates") or "",
                        int(st.get("click_repeat_count") or 1),
                        st.get("api_spec") or "",
                        layer,
                        st.get("desktop_spec") or "",
                    ),
                )
            conn.commit()
        except Exception as e:
            _db_log.warning("duplicate_test_case_with_steps: %s", e)
            conn.rollback()
            try:
                self.delete_test_case_v2(new_id)
            except Exception:
                pass
            return None
        finally:
            conn.close()
        return new_id

    # ==================== 用户管理方法 ====================

    def create_user(
        self,
        username: str,
        password_hash: str,
        email: str = None,
        role: str = 'tester',
        recovery_key_hash: str = None,
    ) -> int:
        """创建用户"""
        email = _normalize_user_email(email)
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        try:
            cursor.execute(
                "INSERT INTO users (username, password_hash, email, role, recovery_key_hash) VALUES (?, ?, ?, ?, ?)",
                (username, password_hash, email, role, recovery_key_hash),
            )
            user_id = cursor.lastrowid
            conn.commit()
            return user_id
        except sqlite3.IntegrityError:
            return None
        finally:
            conn.close()

    def get_user_by_username(self, username: str) -> Dict[str, Any]:
        """根据用户名获取用户"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, username, password_hash, email, role, is_active, created_at, last_login, recovery_key_hash FROM users WHERE username = ?",
            (username,),
        )
        row = cursor.fetchone()
        conn.close()
        if row:
            return {
                'id': row[0],
                'username': row[1],
                'password_hash': row[2],
                'email': row[3],
                'role': row[4],
                'is_active': row[5],
                'created_at': _bj_iso(row[6]),
                'last_login': _bj_iso(row[7]),
                'recovery_key_hash': row[8] if len(row) > 8 else None,
            }
        return None

    def get_user_by_id(self, user_id: int) -> Dict[str, Any]:
        """根据ID获取用户"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, username, password_hash, email, role, is_active, created_at, last_login, recovery_key_hash FROM users WHERE id = ?",
            (user_id,),
        )
        row = cursor.fetchone()
        conn.close()
        if row:
            return {
                'id': row[0],
                'username': row[1],
                'password_hash': row[2],
                'email': row[3],
                'role': row[4],
                'is_active': row[5],
                'created_at': _bj_iso(row[6]),
                'last_login': _bj_iso(row[7]),
                'recovery_key_hash': row[8] if len(row) > 8 else None,
            }
        return None

    def get_user_tenant_id(self, user_id: int) -> Optional[int]:
        """兼容旧名：返回工作空间 ID（原 tenant_id）。"""
        return self.get_user_workspace_id(user_id)

    def insert_ai_context_memory(
        self,
        user_id: int,
        kind: str,
        source_text: str,
        embedding: bytes,
        embedding_dim: int,
        tenant_id: Optional[int] = None,
        meta_json: Optional[str] = None,
    ) -> int:
        """写入一条向量记忆，返回 id。"""
        conn = self._sqlite_connect()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO ai_context_memory
                (tenant_id, user_id, kind, source_text, meta_json, embedding, embedding_dim)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (tenant_id, user_id, kind, source_text, meta_json or "", embedding, embedding_dim),
            )
            conn.commit()
            return int(cursor.lastrowid)
        finally:
            conn.close()

    def fetch_ai_context_memory_rows(
        self,
        user_id: int,
        tenant_id: Optional[int],
    ) -> List[Dict[str, Any]]:
        """按租户或用户范围拉取全部记忆行（用于余弦检索；数据量由应用层限制）。"""
        conn = self._sqlite_connect()
        try:
            cursor = conn.cursor()
            if tenant_id is not None:
                cursor.execute(
                    """
                    SELECT id, kind, source_text, meta_json, embedding, embedding_dim
                    FROM ai_context_memory
                    WHERE tenant_id = ?
                    ORDER BY id DESC
                    LIMIT 5000
                    """,
                    (tenant_id,),
                )
            else:
                cursor.execute(
                    """
                    SELECT id, kind, source_text, meta_json, embedding, embedding_dim
                    FROM ai_context_memory
                    WHERE user_id = ? AND tenant_id IS NULL
                    ORDER BY id DESC
                    LIMIT 5000
                    """,
                    (user_id,),
                )
            rows = cursor.fetchall()
            out: List[Dict[str, Any]] = []
            for r in rows:
                out.append(
                    {
                        "id": r[0],
                        "kind": r[1],
                        "source_text": r[2],
                        "meta_json": r[3] or "",
                        "embedding": r[4],
                        "embedding_dim": int(r[5]),
                    }
                )
            return out
        finally:
            conn.close()

    def get_all_users(self) -> List[Dict[str, Any]]:
        """获取所有用户"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        cursor.execute("SELECT id, username, email, role, is_active, created_at, last_login FROM users ORDER BY created_at DESC")
        rows = cursor.fetchall()
        conn.close()
        return [{'id': r[0], 'username': r[1], 'email': r[2] or None, 'role': r[3],
                 'is_active': r[4], 'created_at': _bj_iso(r[5]), 'last_login': _bj_iso(r[6])} for r in rows]

    def update_user_last_login(self, user_id: int):
        """更新用户最后登录时间"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET last_login = ? WHERE id = ?",
                       (_utc_now_sql(), user_id))
        conn.commit()
        conn.close()

    def update_user(
        self,
        user_id: int,
        *,
        username: Any = _UNSET,
        email: Any = _UNSET,
        role: Any = _UNSET,
        is_active: Any = _UNSET,
        password_hash: Any = _UNSET,
        recovery_key_hash: Any = _UNSET,
    ) -> bool:
        """更新用户信息（不以 rowcount 判定成功；email=None 可清空邮箱）。"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT 1 FROM users WHERE id = ?", (user_id,))
            if not cursor.fetchone():
                return False
            updates, params = [], []
            if username is not _UNSET:
                updates.append("username = ?")
                params.append(username)
            if email is not _UNSET:
                updates.append("email = ?")
                params.append(_normalize_user_email(email))
            if role is not _UNSET:
                updates.append("role = ?")
                params.append(role)
            if is_active is not _UNSET:
                updates.append("is_active = ?")
                params.append(is_active)
            if password_hash is not _UNSET:
                updates.append("password_hash = ?")
                params.append(password_hash)
            if recovery_key_hash is not _UNSET:
                updates.append("recovery_key_hash = ?")
                params.append(recovery_key_hash)
            if not updates:
                return False
            params.append(user_id)
            cursor.execute(f"UPDATE users SET {', '.join(updates)} WHERE id = ?", params)
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            conn.rollback()
            return False
        finally:
            conn.close()

    def delete_user(self, user_id: int) -> bool:
        """删除用户（清理引用该用户的外键行；project_members 等对 users 为 ON DELETE CASCADE）。"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT id FROM users WHERE id = ?", (user_id,))
            if not cursor.fetchone():
                return False
            cursor.execute("SELECT id FROM users WHERE id != ? ORDER BY id LIMIT 1", (user_id,))
            row = cursor.fetchone()
            if not row:
                return False
            replacement_id = int(row[0])

            cursor.execute("DELETE FROM defect_comments WHERE user_id = ?", (user_id,))
            cursor.execute("DELETE FROM defect_history WHERE user_id = ?", (user_id,))
            cursor.execute("UPDATE defects SET assignee_id = NULL WHERE assignee_id = ?", (user_id,))
            cursor.execute(
                "UPDATE defects SET reporter_id = ? WHERE reporter_id = ?",
                (replacement_id, user_id),
            )

            cursor.execute("SELECT id FROM orders WHERE user_id = ?", (user_id,))
            order_ids = [int(r[0]) for r in cursor.fetchall()]
            if order_ids:
                ph = ",".join("?" * len(order_ids))
                cursor.execute(f"DELETE FROM payment_records WHERE order_id IN ({ph})", order_ids)
                cursor.execute(f"DELETE FROM orders WHERE id IN ({ph})", order_ids)
            cursor.execute("DELETE FROM subscriptions WHERE user_id = ?", (user_id,))

            cursor.execute("UPDATE audit_logs SET user_id = NULL WHERE user_id = ?", (user_id,))
            cursor.execute("DELETE FROM user_usage_stats WHERE user_id = ?", (user_id,))
            cursor.execute("DELETE FROM ai_context_memory WHERE user_id = ?", (user_id,))
            cursor.execute("DELETE FROM sso_login_records WHERE user_id = ?", (user_id,))
            cursor.execute("DELETE FROM user_sso_bindings WHERE user_id = ?", (user_id,))
            cursor.execute("DELETE FROM project_members WHERE user_id = ?", (user_id,))
            try:
                cursor.execute("DELETE FROM order_licenses WHERE user_id = ?", (user_id,))
            except sqlite3.OperationalError:
                pass

            cursor.execute("DELETE FROM users WHERE id = ?", (user_id,))
            conn.commit()
            return True
        except sqlite3.IntegrityError as e:
            conn.rollback()
            _db_log.warning("delete_user: integrity error for user_id=%s: %s", user_id, e)
            return False
        except Exception as e:
            conn.rollback()
            _db_log.warning("delete_user: failed for user_id=%s: %s", user_id, e)
            return False
        finally:
            conn.close()

    def count_users(self) -> int:
        """获取用户总数（用于判断是否需要初始化管理员）"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM users")
        count = cursor.fetchone()[0]
        conn.close()
        return count

    def get_user_by_email(self, email: str):
        """按邮箱查询用户"""
        email = _normalize_user_email(email)
        if not email:
            return None
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT * FROM users WHERE email = ?", (email,))
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def save_user_smtp_config(self, user_id: int, email: str, host: str,
                               port: int, username: str, password: str,
                               use_tls: int = 1):
        """插入或更新用户 SMTP 配置"""
        email = _normalize_user_email(email)
        if not email:
            return
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        try:
            cursor.execute("""
                INSERT INTO user_smtp_configs (user_id, email, host, port, username, password, use_tls)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    email = excluded.email,
                    host = excluded.host,
                    port = excluded.port,
                    username = excluded.username,
                    password = excluded.password,
                    use_tls = excluded.use_tls,
                    updated_at = CURRENT_TIMESTAMP
            """, (user_id, email, host, port, username, password, use_tls))
            conn.commit()
        finally:
            conn.close()

    def get_user_smtp_config_by_email(self, email: str):
        """按邮箱查找 SMTP 配置（找回密码用）"""
        email = _normalize_user_email(email)
        if not email:
            return None
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT * FROM user_smtp_configs WHERE email = ?", (email,))
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def get_user_smtp_config_by_user_id(self, user_id: int):
        """按用户 ID 查找 SMTP 配置"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT * FROM user_smtp_configs WHERE user_id = ?", (user_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    # ==================== 步骤执行结果方法 ====================

    def create_step_result(self, run_history_id: int, step_id: int, step_order: int,
                           action: str, selector_value: str, input_value: str,
                           description: str, status: str, error: str = "",
                           screenshot: str = "", duration: float = 0) -> int:
        """记录单步骤执行结果"""
        import datetime
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        local_time = _utc_now_sql()
        cursor.execute(
            """INSERT INTO step_results
               (run_history_id, step_id, step_order, action, selector_value, input_value,
                description, status, error, screenshot, duration, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (run_history_id, step_id, step_order, action, selector_value, input_value,
             description, status, error, screenshot, duration, local_time)
        )
        result_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return result_id

    def get_step_results(self, run_history_id: int) -> List[Dict[str, Any]]:
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
                 'screenshot': r[10], 'duration': r[11], 'created_at': _bj_iso(r[12])} for r in rows]

    # ==================== 变量管理方法 ====================

    def create_variable(self, name: str, value: str, scope: str = 'global',
                        project_id: int = None, case_id: int = None, description: str = '') -> int:
        """创建变量"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO variables (name, value, scope, project_id, case_id, description) VALUES (?, ?, ?, ?, ?, ?)",
            (name, value, scope, project_id, case_id, description)
        )
        var_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return var_id

    def get_variables(self, scope: str = None, project_id: int = None, case_id: int = None) -> List[Dict[str, Any]]:
        """获取变量列表"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        if scope == 'global':
            cursor.execute("SELECT * FROM variables WHERE scope = 'global' ORDER BY name")
        elif scope == 'project' and project_id:
            cursor.execute("SELECT * FROM variables WHERE scope IN ('global','project') AND (project_id = ? OR project_id IS NULL) ORDER BY scope, name", (project_id,))
        elif scope == 'case' and case_id:
            cursor.execute("SELECT * FROM variables WHERE scope IN ('global','project','case') AND (case_id = ? OR case_id IS NULL) ORDER BY scope, name", (case_id,))
        else:
            cursor.execute("SELECT * FROM variables ORDER BY scope, name")
        rows = cursor.fetchall()
        conn.close()
        return [{'id': r[0], 'name': r[1], 'value': r[2], 'scope': r[3],
                 'project_id': r[4], 'case_id': r[5], 'description': r[6], 'created_at': _bj_iso(r[7])} for r in rows]

    def update_variable(self, var_id: int, name: str = None, value: str = None, description: str = None) -> bool:
        """更新变量"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        updates, params = [], []
        if name is not None:
            updates.append("name = ?"); params.append(name)
        if value is not None:
            updates.append("value = ?"); params.append(value)
        if description is not None:
            updates.append("description = ?"); params.append(description)
        if not updates:
            conn.close()
            return False
        params.append(var_id)
        cursor.execute(f"UPDATE variables SET {', '.join(updates)} WHERE id = ?", params)
        success = cursor.rowcount > 0
        conn.commit()
        conn.close()
        return success

    def delete_variable(self, var_id: int) -> bool:
        """删除变量"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM variables WHERE id = ?", (var_id,))
        success = cursor.rowcount > 0
        conn.commit()
        conn.close()
        return success

    def resolve_variables(
        self,
        text: str,
        project_id: int = None,
        case_id: int = None,
        runtime_overlay: Optional[Dict[str, Any]] = None,
    ) -> str:
        """将文本中的 {{变量名}} 替换为实际值。runtime_overlay 为本次执行中的临时变量（优先级高于库内同名变量）。"""
        if not text or '{{' not in text:
            return text
        import re
        variables = self.get_variables(scope='case' if case_id else ('project' if project_id else 'global'),
                                       project_id=project_id, case_id=case_id)
        var_map = {v['name']: v['value'] for v in variables}
        if runtime_overlay:
            for rk, rv in runtime_overlay.items():
                if rk is None:
                    continue
                var_map[str(rk)] = '' if rv is None else str(rv)
        def replace_var(match):
            var_name = match.group(1).strip()
            return var_map.get(var_name, match.group(0))
        return re.sub(r'\{\{(.+?)\}\}', replace_var, text)

    def upsert_case_scoped_variable(
        self, name: str, value: str, project_id: Optional[int], case_id: int
    ) -> None:
        """用例作用域变量：同名则更新，否则插入。"""
        if not name or not case_id:
            return
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        try:
            cursor.execute(
                "SELECT id FROM variables WHERE scope = 'case' AND case_id = ? AND name = ?",
                (case_id, name),
            )
            row = cursor.fetchone()
            if row:
                cursor.execute("UPDATE variables SET value = ? WHERE id = ?", (value, row[0]))
            else:
                cursor.execute(
                    """INSERT INTO variables (name, value, scope, project_id, case_id, description)
                       VALUES (?, ?, 'case', ?, ?, '')""",
                    (name, value, project_id, case_id),
                )
            conn.commit()
        finally:
            conn.close()

    # ==================== 定时调度方法 ====================

    def create_schedule(self, name: str, case_ids: list, cron_expr: str, project_id: int = None,
                        retry_count: int = 3, retry_interval: int = 5, is_active: int = 1,
                        execution_count: int = -1) -> int:
        """创建定时调度"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO schedules (name, project_id, case_ids, cron_expr, is_active, retry_count, retry_interval, execution_count) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (name, project_id, json.dumps(case_ids), cron_expr, is_active, retry_count, retry_interval, execution_count)
        )
        schedule_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return schedule_id

    def consume_schedule_execution(self, schedule_id: int) -> Dict[str, Any]:
        """
        原子消耗一次定时任务执行次数。
        规则：
        - execution_count < 0（通常为 -1）: 无限次，不扣减
        - execution_count = 0: 不允许执行（定时与「立即执行」均不应触发）
        - execution_count > 0: 每次触发扣减1，扣减到0时自动禁用任务（is_active=0）
        返回:
            {
              'allowed': bool,
              'reason': str | None,
              'remaining': int | None,
              'unlimited': bool,
              'exhausted': bool
            }
        """
        conn = self._sqlite_connect(timeout=10)
        conn.isolation_level = None  # 手动事务控制
        cursor = conn.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            cursor.execute("SELECT is_active, execution_count FROM schedules WHERE id = ?", (schedule_id,))
            row = cursor.fetchone()
            if not row:
                cursor.execute("ROLLBACK")
                return {'allowed': False, 'reason': 'not_found', 'remaining': None, 'unlimited': False, 'exhausted': False}

            is_active, execution_count = row[0], row[1] if row[1] is not None else 0
            if not is_active:
                cursor.execute("ROLLBACK")
                return {
                    'allowed': False,
                    'reason': 'inactive',
                    'remaining': execution_count,
                    'unlimited': execution_count is not None and int(execution_count) < 0,
                    'exhausted': False,
                }

            if int(execution_count) == 0:
                cursor.execute("ROLLBACK")
                return {
                    'allowed': False,
                    'reason': 'zero_executions',
                    'remaining': 0,
                    'unlimited': False,
                    'exhausted': True,
                }

            if int(execution_count) < 0:
                cursor.execute("COMMIT")
                return {'allowed': True, 'reason': None, 'remaining': -1, 'unlimited': True, 'exhausted': False}

            # 有限次数：原子扣减
            new_count = max(0, int(execution_count) - 1)
            new_active = 1 if new_count > 0 else 0
            cursor.execute(
                "UPDATE schedules SET execution_count = ?, is_active = ? WHERE id = ?",
                (new_count, new_active, schedule_id)
            )
            cursor.execute("COMMIT")
            return {
                'allowed': True,
                'reason': None,
                'remaining': new_count,
                'unlimited': False,
                'exhausted': new_count == 0
            }
        except Exception:
            try:
                cursor.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            conn.close()

    def get_all_schedules(self) -> List[Dict[str, Any]]:
        """获取所有调度任务"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM schedules ORDER BY created_at DESC")
        rows = cursor.fetchall()
        conn.close()
        return [{'id': r[0], 'name': r[1], 'project_id': r[2],
                 'case_ids': json.loads(r[3]) if r[3] else [],
                 'cron_expr': r[4], 'is_active': r[5],
                 'retry_count': r[6], 'retry_interval': r[7],
                 'last_run': _bj_iso(r[8]), 'next_run': _bj_iso(r[9]), 'created_at': _bj_iso(r[10]),
                 'execution_count': r[11] if len(r) > 11 else 0} for r in rows]

    def get_active_schedules(self) -> List[Dict[str, Any]]:
        """获取所有激活的调度任务"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM schedules WHERE is_active = 1")
        rows = cursor.fetchall()
        conn.close()
        return [{'id': r[0], 'name': r[1], 'project_id': r[2],
                 'case_ids': json.loads(r[3]) if r[3] else [],
                 'cron_expr': r[4], 'is_active': r[5],
                 'retry_count': r[6], 'retry_interval': r[7],
                 'last_run': _bj_iso(r[8]), 'next_run': _bj_iso(r[9]), 'created_at': _bj_iso(r[10]),
                 'execution_count': r[11] if len(r) > 11 else 0} for r in rows]

    def update_schedule(self, schedule_id: int, name: str = None, cron_expr: str = None,
                        is_active: int = None, case_ids: list = None, last_run: str = None,
                        project_id: int = None, execution_count: int = None) -> bool:
        """更新调度任务"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        updates, params = [], []
        if name is not None:
            updates.append("name = ?"); params.append(name)
        if cron_expr is not None:
            updates.append("cron_expr = ?"); params.append(cron_expr)
        if is_active is not None:
            updates.append("is_active = ?"); params.append(is_active)
        if case_ids is not None:
            updates.append("case_ids = ?"); params.append(json.dumps(case_ids))
        if last_run is not None:
            updates.append("last_run = ?"); params.append(last_run)
        if project_id is not None:
            updates.append("project_id = ?"); params.append(project_id)
        if execution_count is not None:
            updates.append("execution_count = ?"); params.append(execution_count)
        if not updates:
            conn.close()
            return False
        params.append(schedule_id)
        cursor.execute(f"UPDATE schedules SET {', '.join(updates)} WHERE id = ?", params)
        success = cursor.rowcount > 0
        conn.commit()
        conn.close()
        return success

    def delete_schedule(self, schedule_id: int) -> bool:
        """删除调度任务"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM schedules WHERE id = ?", (schedule_id,))
        success = cursor.rowcount > 0
        conn.commit()
        conn.close()
        return success

    def create_schedule_history(self, schedule_id: int, case_ids: list, status: str,
                                retry_count: int = 0, max_retries: int = 3, error_message: str = None) -> int:
        """创建调度执行历史记录"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO schedule_history (schedule_id, case_ids, status, retry_count, max_retries, error_message) VALUES (?, ?, ?, ?, ?, ?)",
            (schedule_id, json.dumps(case_ids), status, retry_count, max_retries, error_message)
        )
        history_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return history_id

    def update_schedule_history(self, history_id: int, status: str = None, error_message: str = None):
        """更新调度执行历史记录"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        updates, params = [], []
        if status is not None:
            updates.append("status = ?")
            params.append(status)
        if error_message is not None:
            updates.append("error_message = ?")
            params.append(error_message)
        if not updates:
            conn.close()
            return
        params.append(history_id)
        cursor.execute(f"UPDATE schedule_history SET {', '.join(updates)}, completed_at = CURRENT_TIMESTAMP WHERE id = ?", params)
        conn.commit()
        conn.close()

    def get_schedule_history(self, schedule_id: int = None, limit: int = 50) -> List[Dict[str, Any]]:
        """获取调度执行历史"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        if schedule_id:
            cursor.execute("SELECT * FROM schedule_history WHERE schedule_id = ? ORDER BY started_at DESC LIMIT ?",
                          (schedule_id, limit))
        else:
            cursor.execute("SELECT * FROM schedule_history ORDER BY started_at DESC LIMIT ?", (limit,))
        rows = cursor.fetchall()
        conn.close()
        return [{'id': r[0], 'schedule_id': r[1],
                 'case_ids': json.loads(r[2]) if r[2] else [],
                 'status': r[3], 'retry_count': r[4], 'max_retries': r[5],
                 'error_message': r[6], 'started_at': _bj_iso(r[7]), 'completed_at': _bj_iso(r[8])} for r in rows]

    # ==================== 通知配置管理方法 ====================

    def create_notification_config(self, name: str, type: str, config: dict, events: list, is_active: int = 1) -> int:
        """创建通知配置"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO notification_configs (name, type, config, events, is_active) VALUES (?, ?, ?, ?, ?)",
            (name, type, json.dumps(config), json.dumps(events), is_active)
        )
        config_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return config_id

    def get_all_notification_configs(self) -> List[Dict[str, Any]]:
        """获取所有通知配置"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM notification_configs ORDER BY created_at DESC")
        rows = cursor.fetchall()
        conn.close()
        return [{'id': r[0], 'name': r[1], 'type': r[2],
                 'config': json.loads(r[3]) if r[3] else {},
                 'events': json.loads(r[4]) if r[4] else [],
                 'is_active': r[5], 'created_at': _bj_iso(r[6])} for r in rows]

    def get_active_notification_configs(self, event_type: str = None) -> List[Dict[str, Any]]:
        """获取激活的通知配置"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        if event_type:
            cursor.execute("SELECT * FROM notification_configs WHERE is_active = 1")
            rows = cursor.fetchall()
            conn.close()
            configs = [{'id': r[0], 'name': r[1], 'type': r[2],
                       'config': json.loads(r[3]) if r[3] else {},
                       'events': json.loads(r[4]) if r[4] else [],
                       'is_active': r[5], 'created_at': _bj_iso(r[6])} for r in rows]
            # 过滤包含指定事件的配置
            return [c for c in configs if event_type in c['events']]
        else:
            cursor.execute("SELECT * FROM notification_configs WHERE is_active = 1")
            rows = cursor.fetchall()
            conn.close()
            return [{'id': r[0], 'name': r[1], 'type': r[2],
                     'config': json.loads(r[3]) if r[3] else {},
                     'events': json.loads(r[4]) if r[4] else [],
                     'is_active': r[5], 'created_at': _bj_iso(r[6])} for r in rows]

    def update_notification_config(self, config_id: int, name: str = None, type: str = None,
                                   config: dict = None, events: list = None, is_active: int = None) -> bool:
        """更新通知配置"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        updates, params = [], []
        if name is not None:
            updates.append("name = ?")
            params.append(name)
        if type is not None:
            updates.append("type = ?")
            params.append(type)
        if config is not None:
            updates.append("config = ?")
            params.append(json.dumps(config))
        if events is not None:
            updates.append("events = ?")
            params.append(json.dumps(events))
        if is_active is not None:
            updates.append("is_active = ?")
            params.append(is_active)
        if not updates:
            conn.close()
            return False
        params.append(config_id)
        cursor.execute(f"UPDATE notification_configs SET {', '.join(updates)} WHERE id = ?", params)
        success = cursor.rowcount > 0
        conn.commit()
        conn.close()
        return success

    def delete_notification_config(self, config_id: int) -> bool:
        """删除通知配置"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM notification_configs WHERE id = ?", (config_id,))
        success = cursor.rowcount > 0
        conn.commit()
        conn.close()
        return success

    # ==================== API 令牌管理方法 ====================

    def create_api_token(self, name: str, token: str, project_id: int = None, expires_at: str = None) -> int:
        """创建 API 令牌"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO api_tokens (name, token, project_id, expires_at) VALUES (?, ?, ?, ?)",
            (name, token, project_id, expires_at)
        )
        token_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return token_id

    def get_token_by_value(self, token: str) -> Dict[str, Any]:
        """根据 token 值获取令牌信息"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM api_tokens WHERE token = ? AND is_active = 1", (token,))
        row = cursor.fetchone()
        conn.close()
        if row:
            return {'id': row[0], 'name': row[1], 'token': row[2], 'project_id': row[3],
                    'is_active': row[4], 'expires_at': _bj_iso(row[5]), 'created_at': _bj_iso(row[6])}
        return None

    def get_all_tokens(self) -> List[Dict[str, Any]]:
        """获取所有令牌（不含 token 明文）"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, project_id, is_active, expires_at, created_at FROM api_tokens ORDER BY created_at DESC")
        rows = cursor.fetchall()
        conn.close()
        return [{'id': r[0], 'name': r[1], 'project_id': r[2], 'is_active': r[3],
                 'expires_at': _bj_iso(r[4]), 'created_at': _bj_iso(r[5])} for r in rows]

    def revoke_token(self, token_id: int) -> bool:
        """撤销令牌"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        cursor.execute("UPDATE api_tokens SET is_active = 0 WHERE id = ?", (token_id,))
        success = cursor.rowcount > 0
        conn.commit()
        conn.close()
        return success

    # ==================== 数据驱动测试方法 ====================

    def create_dataset(self, name: str, case_id: int = None, project_id: int = None, description: str = '') -> int:
        """创建数据集"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO test_data_sets (name, case_id, project_id, description) VALUES (?, ?, ?, ?)",
            (name, case_id, project_id, description)
        )
        dataset_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return dataset_id

    def get_all_datasets(self, case_id: int = None, project_id: int = None) -> List[Dict[str, Any]]:
        """获取数据集列表"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        if case_id:
            cursor.execute(
                "SELECT ds.*, COUNT(dr.id) as row_count FROM test_data_sets ds LEFT JOIN test_data_rows dr ON ds.id = dr.dataset_id WHERE ds.case_id = ? GROUP BY ds.id ORDER BY ds.created_at DESC",
                (case_id,)
            )
        elif project_id:
            cursor.execute(
                "SELECT ds.*, COUNT(dr.id) as row_count FROM test_data_sets ds LEFT JOIN test_data_rows dr ON ds.id = dr.dataset_id WHERE ds.project_id = ? GROUP BY ds.id ORDER BY ds.created_at DESC",
                (project_id,)
            )
        else:
            cursor.execute(
                "SELECT ds.*, COUNT(dr.id) as row_count FROM test_data_sets ds LEFT JOIN test_data_rows dr ON ds.id = dr.dataset_id GROUP BY ds.id ORDER BY ds.created_at DESC"
            )
        rows = cursor.fetchall()
        conn.close()
        return [{'id': r[0], 'name': r[1], 'case_id': r[2], 'project_id': r[3],
                 'description': r[4], 'created_at': _bj_iso(r[5]), 'row_count': r[6]} for r in rows]

    def get_dataset(self, dataset_id: int) -> Dict[str, Any]:
        """获取单个数据集"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM test_data_sets WHERE id = ?", (dataset_id,))
        row = cursor.fetchone()
        conn.close()
        if row:
            return {'id': row[0], 'name': row[1], 'case_id': row[2], 'project_id': row[3],
                    'description': row[4], 'created_at': _bj_iso(row[5])}
        return None

    def delete_dataset(self, dataset_id: int) -> bool:
        """删除数据集及其所有数据行"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM test_data_rows WHERE dataset_id = ?", (dataset_id,))
        cursor.execute("DELETE FROM test_data_sets WHERE id = ?", (dataset_id,))
        success = cursor.rowcount > 0
        conn.commit()
        conn.close()
        return success

    def add_data_rows(self, dataset_id: int, rows: List[Dict[str, Any]]) -> int:
        """批量添加数据行（rows 为字典列表）"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        # 先清除已有数据行
        cursor.execute("DELETE FROM test_data_rows WHERE dataset_id = ?", (dataset_id,))
        for idx, row_data in enumerate(rows):
            cursor.execute(
                "INSERT INTO test_data_rows (dataset_id, row_index, data) VALUES (?, ?, ?)",
                (dataset_id, idx, json.dumps(row_data, ensure_ascii=False))
            )
        count = len(rows)
        conn.commit()
        conn.close()
        return count

    def get_data_rows(self, dataset_id: int) -> List[Dict[str, Any]]:
        """获取数据集的所有数据行"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, row_index, data FROM test_data_rows WHERE dataset_id = ? ORDER BY row_index ASC",
            (dataset_id,)
        )
        rows = cursor.fetchall()
        conn.close()
        result = []
        for r in rows:
            try:
                data = json.loads(r[2])
            except Exception:
                data = {}
            result.append({'id': r[0], 'row_index': r[1], 'data': data})
        return result

    # ==================== 项目成员权限管理方法 ====================

    def add_project_member(self, project_id: int, user_id: int, role: str = 'editor') -> bool:
        """添加项目成员"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        try:
            cursor.execute(
                "INSERT INTO project_members (project_id, user_id, role) VALUES (?, ?, ?)",
                (project_id, user_id, role)
            )
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False
        finally:
            conn.close()

    def remove_project_member(self, project_id: int, user_id: int) -> bool:
        """移除项目成员"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM project_members WHERE project_id = ? AND user_id = ?",
            (project_id, user_id)
        )
        success = cursor.rowcount > 0
        conn.commit()
        conn.close()
        return success

    def update_project_member_role(self, project_id: int, user_id: int, role: str) -> bool:
        """更新项目成员角色"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE project_members SET role = ? WHERE project_id = ? AND user_id = ?",
            (role, project_id, user_id)
        )
        success = cursor.rowcount > 0
        conn.commit()
        conn.close()
        return success

    def get_project_members(self, project_id: int) -> List[Dict[str, Any]]:
        """获取项目所有成员"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT pm.id, pm.user_id, pm.role, pm.created_at, u.username, u.email
            FROM project_members pm
            JOIN users u ON pm.user_id = u.id
            WHERE pm.project_id = ?
        """, (project_id,))
        rows = cursor.fetchall()
        conn.close()
        return [{
            'id': r[0], 'user_id': r[1], 'role': r[2], 'created_at': _bj_iso(r[3]),
            'username': r[4], 'email': r[5]
        } for r in rows]

    def check_project_access(self, user_id: int, project_id: int, min_role: str = 'viewer') -> bool:
        """检查用户是否有项目访问权限
        min_role: viewer/editor/owner
        权限级别: owner > editor > viewer
        """
        conn = self._sqlite_connect()
        cursor = conn.cursor()

        # 管理员拥有所有权限
        cursor.execute("SELECT role FROM users WHERE id = ?", (user_id,))
        user_row = cursor.fetchone()
        if user_row and user_row[0] == 'admin':
            conn.close()
            return True

        # 检查项目成员权限
        cursor.execute(
            "SELECT role FROM project_members WHERE project_id = ? AND user_id = ?",
            (project_id, user_id)
        )
        row = cursor.fetchone()
        conn.close()

        if not row:
            return False

        user_role = row[0]
        role_levels = {'viewer': 1, 'editor': 2, 'owner': 3}
        required_level = role_levels.get(min_role, 1)
        user_level = role_levels.get(user_role, 1)

        return user_level >= required_level

    def get_user_projects(self, user_id: int) -> List[Dict[str, Any]]:
        """获取用户有权限访问的所有项目"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()

        # 获取用户是成员的项目 + 管理员可查看所有项目
        cursor.execute("""
            SELECT DISTINCT p.id, p.name, p.description, p.tenant_id, p.created_at
            FROM projects p
            LEFT JOIN project_members pm ON p.id = pm.project_id
            LEFT JOIN users u ON u.id = ?
            WHERE pm.user_id = ? OR u.role = 'admin'
            ORDER BY p.created_at DESC
        """, (user_id, user_id))

        rows = cursor.fetchall()
        conn.close()

        return [_project_row_to_dict(row) for row in rows]

    def is_project_owner(self, user_id: int, project_id: int) -> bool:
        """检查用户是否是项目所有者"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT 1 FROM project_members WHERE project_id = ? AND user_id = ? AND role = 'owner'",
            (project_id, user_id)
        )
        result = cursor.fetchone() is not None
        conn.close()
        return result

    # ==================== 审计日志方法 ====================

    def add_audit_log(self, user_id: int, username: str, action: str, target_type: str,
                      target_id: int = None, details: str = None, ip_address: str = None) -> int:
        """添加审计日志"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO audit_logs (user_id, username, action, target_type, target_id, details, ip_address, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, username, action, target_type, target_id, details, ip_address, _utc_now_sql()),
        )
        log_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return log_id

    def get_audit_logs(self, user_id: int = None, target_type: str = None,
                       username: str = None, page: int = 1, page_size: int = 50) -> List[Dict[str, Any]]:
        """获取审计日志"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()

        offset = (page - 1) * page_size
        params = []
        where_clause = ""

        if user_id:
            where_clause = "WHERE user_id = ?"
            params.append(user_id)
        if target_type:
            where_clause = (where_clause + " AND " if where_clause else "WHERE ") + "target_type = ?"
            params.append(target_type)
        if username and str(username).strip():
            u = str(username).strip()
            u_esc = u.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            like_pat = "%" + u_esc + "%"
            where_clause = (where_clause + " AND " if where_clause else "WHERE ") + "username LIKE ? ESCAPE '\\'"
            params.append(like_pat)

        cursor.execute(f"""
            SELECT * FROM audit_logs {where_clause}
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
        """, params + [page_size, offset])

        rows = cursor.fetchall()
        conn.close()

        return [{
            'id': r[0], 'user_id': r[1], 'username': r[2], 'action': r[3],
            'target_type': r[4], 'target_id': r[5], 'details': r[6],
            'ip_address': r[7], 'created_at': _bj_iso(r[8])
        } for r in rows]

    def get_audit_logs_count(self, user_id: int = None, target_type: str = None, username: str = None) -> int:
        """获取审计日志总数"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()

        params = []
        where_clause = ""

        if user_id:
            where_clause = "WHERE user_id = ?"
            params.append(user_id)
        if target_type:
            where_clause = (where_clause + " AND " if where_clause else "WHERE ") + "target_type = ?"
            params.append(target_type)
        if username and str(username).strip():
            u = str(username).strip()
            u_esc = u.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            like_pat = "%" + u_esc + "%"
            where_clause = (where_clause + " AND " if where_clause else "WHERE ") + "username LIKE ? ESCAPE '\\'"
            params.append(like_pat)

        cursor.execute(f"SELECT COUNT(*) FROM audit_logs {where_clause}", params)
        count = cursor.fetchone()[0]
        conn.close()
        return count

    # ==================== 用户使用统计方法（免费版限制）====================

    def get_or_create_usage_stats(self, user_id: int, stat_date: str = None) -> Dict[str, Any]:
        """获取或创建用户使用统计"""
        import datetime
        if stat_date is None:
            stat_date = datetime.date.today().isoformat()

        conn = self._sqlite_connect()
        cursor = conn.cursor()

        cursor.execute(
            "SELECT * FROM user_usage_stats WHERE user_id = ? AND stat_date = ?",
            (user_id, stat_date)
        )
        row = cursor.fetchone()

        if not row:
            cursor.execute(
                "INSERT INTO user_usage_stats (user_id, stat_date) VALUES (?, ?)",
                (user_id, stat_date)
            )
            conn.commit()
            row = (cursor.lastrowid, user_id, stat_date, 0, 0, datetime.datetime.now())

        conn.close()
        return {
            'id': row[0], 'user_id': row[1], 'stat_date': row[2],
            'execution_count': row[3], 'created_cases': row[4]
        }

    def increment_execution_count(self, user_id: int) -> int:
        """增加执行次数，返回当前次数"""
        import datetime
        stat_date = datetime.date.today().isoformat()

        conn = self._sqlite_connect()
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO user_usage_stats (user_id, stat_date, execution_count)
            VALUES (?, ?, 1)
            ON CONFLICT(user_id, stat_date) DO UPDATE SET
            execution_count = execution_count + 1,
            last_updated = CURRENT_TIMESTAMP
        """, (user_id, stat_date))

        cursor.execute(
            "SELECT execution_count FROM user_usage_stats WHERE user_id = ? AND stat_date = ?",
            (user_id, stat_date)
        )
        count = cursor.fetchone()[0]
        conn.commit()
        conn.close()
        return count

    def increment_created_cases(self, user_id: int) -> int:
        """增加创建用例计数，返回当前数量"""
        import datetime
        stat_date = datetime.date.today().isoformat()

        conn = self._sqlite_connect()
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO user_usage_stats (user_id, stat_date, created_cases)
            VALUES (?, ?, 1)
            ON CONFLICT(user_id, stat_date) DO UPDATE SET
            created_cases = created_cases + 1,
            last_updated = CURRENT_TIMESTAMP
        """, (user_id, stat_date))

        cursor.execute(
            "SELECT created_cases FROM user_usage_stats WHERE user_id = ? AND stat_date = ?",
            (user_id, stat_date)
        )
        count = cursor.fetchone()[0]
        conn.commit()
        conn.close()
        return count

    def get_usage_stats(self, user_id: int, days: int = 30) -> List[Dict[str, Any]]:
        """获取用户使用统计（最近N天）"""
        import datetime
        conn = self._sqlite_connect()
        cursor = conn.cursor()

        start_date = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()

        cursor.execute("""
            SELECT stat_date, execution_count, created_cases
            FROM user_usage_stats
            WHERE user_id = ? AND stat_date >= ?
            ORDER BY stat_date DESC
        """, (user_id, start_date))

        rows = cursor.fetchall()
        conn.close()

        return [{
            'stat_date': r[0],
            'execution_count': r[1],
            'created_cases': r[2]
        } for r in rows]

    def get_user_usage_stats(self, user_id: int) -> Dict[str, Any]:
        """获取用户今日使用统计（用于License页面）"""
        return self.get_or_create_usage_stats(user_id)

    # ==================== 缺陷管理方法 ====================

    def create_defect(self, project_id: int, title: str, reporter_id: int,
                      description: str = "", severity: str = "medium",
                      priority: str = "medium", assignee_id: int = None,
                      case_id: int = None, run_history_id: int = None,
                      step_result_id: int = None, error_message: str = "",
                      screenshots: str = "", environment: str = "",
                      browser_info: str = "", reproduce_steps: str = "",
                      expected_result: str = "", actual_result: str = "",
                      status: str = "open") -> int:
        """创建缺陷"""
        # 验证状态值有效性
        valid_statuses = ['open', 'in_progress', 'resolved', 'closed', 'reopened']
        if status not in valid_statuses:
            status = 'open'
        
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO defects (
                project_id, title, description, severity, priority, status,
                assignee_id, reporter_id, case_id, run_history_id, step_result_id,
                error_message, screenshots, environment, browser_info,
                reproduce_steps, expected_result, actual_result
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (project_id, title, description, severity, priority, status, assignee_id,
              reporter_id, case_id, run_history_id, step_result_id, error_message,
              screenshots, environment, browser_info, reproduce_steps,
              expected_result, actual_result))
        
        defect_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return defect_id

    def batch_create_defects_from_cases(self, case_ids: list, project_id: int, reporter_id: int,
                                            title_template: str = '', description: str = '',
                                            severity: str = 'medium', priority: str = 'medium',
                                            assignee_id: int = None, environment: str = '',
                                            expected_result: str = '', actual_result: str = '') -> list:
        """从多个测试用例批量创建缺陷，自动提取用例步骤信息作为复现步骤"""
        conn = self._sqlite_connect()
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        created_defect_ids = []
        
        try:
            for case_id in case_ids:
                # 获取测试用例信息（使用 row_factory 直接按列名访问）
                cursor.execute("SELECT * FROM test_cases WHERE id = ?", (case_id,))
                case_row = cursor.fetchone()
                if not case_row:
                    continue
                
                case_keys = case_row.keys()
                case_dict = dict(case_row)
                case_name = case_dict.get('name', '') or ''
                case_url = case_dict.get('url', '') or case_dict.get('target_url', '') or ''
                case_expected = case_dict.get('expected_result', '') or ''
                case_desc_val = case_dict.get('description', '') or ''
                
                # 获取该用例的所有步骤
                cursor.execute(
                    "SELECT action, selector_type, selector_value, input_value, description, step_order "
                    "FROM test_steps WHERE case_id = ? ORDER BY step_order ASC",
                    (case_id,)
                )
                step_rows = cursor.fetchall()
                
                # 构建复现步骤文本
                reproduce_steps_text = f"用例名称: {case_name}\n"
                if case_url:
                    reproduce_steps_text += f"测试URL: {case_url}\n"
                reproduce_steps_text += "\n复现步骤:\n"
                for i, step_row in enumerate(step_rows, 1):
                    action = step_row[0] or ''
                    step_desc = step_row[4] or ''
                    input_val = step_row[3] or ''
                    action_label = {
                        'click': '点击', 'input': '输入', 'navigate': '导航到', 'assert': '断言',
                        'wait': '等待', 'scroll': '滚动', 'select': '选择', 'hover': '悬停'
                    }.get(action, action)
                    step_line = f"{i}. {action_label}"
                    if step_desc:
                        step_line += f" - {step_desc}"
                    if input_val and action == 'input':
                        step_line += f" (输入值: {input_val})"
                    reproduce_steps_text += step_line + "\n"
                
                # 构建缺陷标题
                if title_template:
                    defect_title = title_template.replace('{case_name}', case_name)
                else:
                    defect_title = f"[缺陷] {case_name}"
                
                # 构建缺陷描述
                defect_desc = description
                if not defect_desc and case_desc_val:
                    defect_desc = f"用例描述: {case_desc_val}"
                
                # 期望结果：优先用传入的，其次用用例自带的
                final_expected = expected_result or case_expected or ''
                
                cursor.execute('''
                    INSERT INTO defects (
                        project_id, title, description, severity, priority, status,
                        assignee_id, reporter_id, case_id,
                        environment, reproduce_steps, expected_result, actual_result
                    ) VALUES (?, ?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    project_id, defect_title, defect_desc, severity, priority,
                    assignee_id, reporter_id, case_id,
                    environment, reproduce_steps_text, final_expected, actual_result
                ))
                created_defect_ids.append(cursor.lastrowid)
            
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        
        return created_defect_ids

    def get_defect(self, defect_id: int) -> Dict[str, Any]:
        """获取缺陷详情"""
        conn = self._sqlite_connect()
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT d.*, 
                   u1.username as reporter_name,
                   u2.username as assignee_name,
                   tc.name as case_name,
                   p.name as project_name
            FROM defects d
            LEFT JOIN users u1 ON d.reporter_id = u1.id
            LEFT JOIN users u2 ON d.assignee_id = u2.id
            LEFT JOIN test_cases tc ON d.case_id = tc.id
            LEFT JOIN projects p ON d.project_id = p.id
            WHERE d.id = ?
        ''', (defect_id,))
        
        row = cursor.fetchone()
        conn.close()
        
        if row:
            return _api_ts_dict(dict(row))
        return None

    def get_defects(self, project_id: int = None, status: str = None,
                    assignee_id: int = None, severity: str = None,
                    case_id: int = None, page: int = 1, page_size: int = 20) -> Dict[str, Any]:
        """获取缺陷列表（支持筛选和分页）"""
        conn = self._sqlite_connect()
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        where_conditions = []
        params = []
        
        if project_id:
            where_conditions.append("d.project_id = ?")
            params.append(project_id)
        if status:
            where_conditions.append("d.status = ?")
            params.append(status)
        if assignee_id:
            where_conditions.append("d.assignee_id = ?")
            params.append(assignee_id)
        if severity:
            where_conditions.append("d.severity = ?")
            params.append(severity)
        if case_id:
            where_conditions.append("d.case_id = ?")
            params.append(case_id)
        
        where_clause = " AND ".join(where_conditions) if where_conditions else "1=1"
        
        # 获取总数
        cursor.execute(f"SELECT COUNT(*) FROM defects d WHERE {where_clause}", params)
        total = cursor.fetchone()[0]
        
        # 获取分页数据
        offset = (page - 1) * page_size
        cursor.execute(f'''
            SELECT d.*, 
                   u1.username as reporter_name,
                   u2.username as assignee_name,
                   tc.name as case_name,
                   p.name as project_name
            FROM defects d
            LEFT JOIN users u1 ON d.reporter_id = u1.id
            LEFT JOIN users u2 ON d.assignee_id = u2.id
            LEFT JOIN test_cases tc ON d.case_id = tc.id
            LEFT JOIN projects p ON d.project_id = p.id
            WHERE {where_clause}
            ORDER BY d.created_at DESC
            LIMIT ? OFFSET ?
        ''', params + [page_size, offset])
        
        rows = cursor.fetchall()
        conn.close()
        
        return {
            'defects': [_api_ts_dict(dict(row)) for row in rows],
            'total': total,
            'page': page,
            'page_size': page_size,
            'total_pages': (total + page_size - 1) // page_size
        }


    def update_defect(self, defect_id: int, user_id: int, **kwargs) -> bool:
        """更新缺陷（并记录历史）"""
        allowed_fields = ['title', 'description', 'severity', 'priority', 'status',
                          'assignee_id', 'resolution', 'environment', 'browser_info',
                          'reproduce_steps', 'expected_result', 'actual_result']
        
        conn = self._sqlite_connect()
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # 获取当前缺陷状态
        cursor.execute("SELECT * FROM defects WHERE id = ?", (defect_id,))
        old_defect = cursor.fetchone()
        if not old_defect:
            conn.close()
            return False
        
        old_defect = dict(old_defect)
        updates = []
        params = []

        for field, value in kwargs.items():
            if field in allowed_fields:
                old_value = old_defect.get(field)
                # 处理 None 和空字符串的情况
                if old_value != value:
                    updates.append(f"{field} = ?")
                    params.append(value)

                    # 记录历史
                    cursor.execute('''
                        INSERT INTO defect_history (defect_id, user_id, field_name, old_value, new_value)
                        VALUES (?, ?, ?, ?, ?)
                    ''', (defect_id, user_id, field, str(old_value if old_value is not None else ''), str(value if value is not None else '')))

        if updates:
            updates.append("updated_at = ?")
            params.append(_utc_now_sql())

            # 处理特殊状态时间戳
            if 'status' in kwargs:
                if kwargs['status'] == 'resolved':
                    updates.append("resolved_at = ?")
                    params.append(_utc_now_sql())
                elif kwargs['status'] == 'closed':
                    updates.append("closed_at = ?")
                    params.append(_utc_now_sql())

            params.append(defect_id)
            cursor.execute(f"UPDATE defects SET {', '.join(updates)} WHERE id = ?", params)

        conn.commit()
        conn.close()
        return True

    def update_defect_status(self, defect_id: int, user_id: int, new_status: str) -> bool:
        """更新缺陷状态（状态流转）"""
        valid_statuses = ['open', 'in_progress', 'resolved', 'closed', 'reopened']
        if new_status not in valid_statuses:
            return False
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        cursor.execute("SELECT status FROM defects WHERE id = ?", (defect_id,))
        row = cursor.fetchone()
        conn.close()
        if not row:
            return False
        if row[0] == new_status:
            return False
        return self.update_defect(defect_id, user_id, status=new_status)

    def add_defect_comment(self, defect_id: int, user_id: int, content: str) -> int:
        """添加缺陷评论"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO defect_comments (defect_id, user_id, content)
            VALUES (?, ?, ?)
        ''', (defect_id, user_id, content))
        
        comment_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return comment_id

    def get_defect_comments(self, defect_id: int) -> List[Dict[str, Any]]:
        """获取缺陷评论列表"""
        conn = self._sqlite_connect()
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT dc.*, u.username
            FROM defect_comments dc
            LEFT JOIN users u ON dc.user_id = u.id
            WHERE dc.defect_id = ?
            ORDER BY dc.created_at ASC
        ''', (defect_id,))
        
        rows = cursor.fetchall()
        conn.close()
        return [_api_ts_dict(dict(row)) for row in rows]

    def get_defect_history(self, defect_id: int) -> List[Dict[str, Any]]:
        """获取缺陷状态变更历史"""
        conn = self._sqlite_connect()
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT dh.*, u.username
            FROM defect_history dh
            LEFT JOIN users u ON dh.user_id = u.id
            WHERE dh.defect_id = ?
            ORDER BY dh.created_at DESC
        ''', (defect_id,))
        
        rows = cursor.fetchall()
        conn.close()
        return [_api_ts_dict(dict(row)) for row in rows]

    def get_defect_statistics(self, project_id: int = None) -> Dict[str, Any]:
        """获取缺陷统计数据"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        
        where_clause = "WHERE project_id = ?" if project_id else ""
        params = [project_id] if project_id else []
        
        # 按状态统计
        cursor.execute(f'''
            SELECT status, COUNT(*) as count
            FROM defects {where_clause}
            GROUP BY status
        ''', params)
        status_stats = {row[0]: row[1] for row in cursor.fetchall()}
        
        # 按严重级统计
        cursor.execute(f'''
            SELECT severity, COUNT(*) as count
            FROM defects {where_clause}
            GROUP BY severity
        ''', params)
        severity_stats = {row[0]: row[1] for row in cursor.fetchall()}
        
        # 总数
        cursor.execute(f"SELECT COUNT(*) FROM defects {where_clause}", params)
        total = cursor.fetchone()[0]
        
        conn.close()
        
        return {
            'total': total,
            'by_status': status_stats,
            'by_severity': severity_stats,
            'open_count': status_stats.get('open', 0) + status_stats.get('in_progress', 0) + status_stats.get('reopened', 0),
            'resolved_count': status_stats.get('resolved', 0) + status_stats.get('closed', 0)
        }

    def delete_defect(self, defect_id: int) -> bool:
        """删除缺陷（同时删除评论和历史）"""
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        
        cursor.execute("DELETE FROM defect_comments WHERE defect_id = ?", (defect_id,))
        cursor.execute("DELETE FROM defect_history WHERE defect_id = ?", (defect_id,))
        cursor.execute("DELETE FROM defects WHERE id = ?", (defect_id,))
        
        conn.commit()
        conn.close()
        return True

    def create_defect_from_failure(self, run_history_id: int, reporter_id: int,
                                    title: str = None, assignee_id: int = None) -> int:
        """从失败的执行历史一键创建缺陷"""
        # 获取执行历史详情
        history = self.get_run_history_detail(run_history_id)
        if not history:
            return None
        
        # 获取用例信息
        case = self.get_test_case_v2(history['case_id'])
        if not case:
            return None
        
        # 获取失败的步骤结果
        step_results = self.get_step_results(run_history_id)
        failed_steps = [s for s in step_results if s['status'] in ('failed', 'error')]
        
        # 构建复现步骤
        reproduce_steps = f"""用例名称: {case.get('name', '')}
用例URL: {case.get('url', '')}

执行步骤:
"""
        for i, step in enumerate(step_results, 1):
            status_icon = 'X' if step['status'] in ('failed', 'error') else 'O'
            reproduce_steps += f"{i}. [{status_icon}] {step.get('action', '')} - {step.get('description', '')}\n"
        
        # 错误信息
        error_message = history.get('error', '')
        if failed_steps:
            error_message = failed_steps[0].get('error', error_message)
        
        # 自动生成标题
        if not title:
            title = f"[用例失败] {case.get('name', '未知用例')}"
        
        # 创建缺陷
        defect_id = self.create_defect(
            project_id=case.get('project_id'),
            title=title,
            reporter_id=reporter_id,
            description=f"用例执行失败，自动创建缺陷。\n\n错误信息: {error_message}",
            severity='high' if 'error' in history.get('status', '') else 'medium',
            priority='high',
            assignee_id=assignee_id,
            case_id=history['case_id'],
            run_history_id=run_history_id,
            step_result_id=failed_steps[0]['id'] if failed_steps else None,
            error_message=error_message,
            screenshots=history.get('screenshots', ''),
            reproduce_steps=reproduce_steps,
            expected_result=case.get('expected_result', ''),
            actual_result=f"执行状态: {history.get('status', '')}"
        )
        
        return defect_id

    def create_test_machine(
        self,
        name: str,
        agent_url: str,
        os_version: str = "",
        agent_secret: str = "",
    ) -> int:
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO test_machines (name, agent_url, os_version, agent_secret, status)
            VALUES (?, ?, ?, ?, 'offline')
            """,
            (name, agent_url, os_version or "", agent_secret or ""),
        )
        mid = cursor.lastrowid
        conn.commit()
        conn.close()
        return int(mid)

    def list_test_machines(self) -> List[Dict[str, Any]]:
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, name, agent_url, os_version, status, last_seen_at, created_at
            FROM test_machines ORDER BY id DESC
            """
        )
        rows = cursor.fetchall()
        conn.close()
        out = []
        for row in rows:
            out.append({
                "id": row[0],
                "name": row[1],
                "agent_url": row[2],
                "os_version": row[3] or "",
                "status": row[4] or "unknown",
                "last_seen_at": _bj_iso(row[5]) if row[5] else None,
                "created_at": _bj_iso(row[6]),
            })
        return out

    def get_test_machine(self, machine_id: int) -> Optional[Dict[str, Any]]:
        conn = self._sqlite_connect()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, name, agent_url, agent_secret, os_version, status FROM test_machines WHERE id = ?",
            (machine_id,),
        )
        row = cursor.fetchone()
        conn.close()
        if not row:
            return None
        return {
            "id": row[0],
            "name": row[1],
            "agent_url": row[2],
            "agent_secret": row[3] or "",
            "os_version": row[4] or "",
            "status": row[5] or "unknown",
        }

    # ==================== 工作空间（原 tenants 语义） ====================

    def _create_deployment_tables(self, cursor) -> None:
        """工作空间、远程执行任务、桌面客户端节点、实例设置。"""
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS workspaces (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                display_name TEXT,
                instance_id TEXT,
                plan_type TEXT NOT NULL DEFAULT 'free',
                max_users INTEGER DEFAULT 5,
                max_projects INTEGER DEFAULT 10,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS instance_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS client_nodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                machine_id TEXT NOT NULL UNIQUE,
                machine_name TEXT,
                user_id INTEGER,
                status TEXT NOT NULL DEFAULT 'offline',
                last_seen_at TIMESTAMP,
                capabilities_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS execution_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                case_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                assigned_machine_id TEXT,
                run_history_id INTEGER,
                error TEXT,
                result_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                started_at TIMESTAMP,
                finished_at TIMESTAMP,
                FOREIGN KEY (case_id) REFERENCES test_cases (id),
                FOREIGN KEY (user_id) REFERENCES users (id),
                FOREIGN KEY (run_history_id) REFERENCES run_history (id)
            )
        ''')

    def _ensure_default_workspace(self, cursor) -> None:
        """单实例团队服务器默认工作空间 id=1。"""
        cursor.execute("SELECT COUNT(*) FROM workspaces")
        if cursor.fetchone()[0] == 0:
            cursor.execute(
                """
                INSERT INTO workspaces (id, name, display_name, plan_type, is_active)
                VALUES (1, 'default', '默认工作空间', 'free', 1)
                """
            )

    def get_default_workspace_id(self) -> int:
        conn = self._sqlite_connect()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM workspaces WHERE is_active = 1 ORDER BY id LIMIT 1")
            row = cursor.fetchone()
            return int(row[0]) if row else 1
        finally:
            conn.close()

    def get_workspace(self, workspace_id: int) -> Optional[Dict[str, Any]]:
        conn = self._sqlite_connect()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, name, display_name, instance_id, plan_type, max_users, max_projects, is_active, created_at
                FROM workspaces WHERE id = ?
                """,
                (workspace_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            return {
                "id": row[0],
                "name": row[1],
                "display_name": row[2],
                "instance_id": row[3],
                "plan_type": row[4],
                "max_users": row[5],
                "max_projects": row[6],
                "is_active": bool(row[7]),
                "created_at": _bj_iso(row[8]),
            }
        finally:
            conn.close()

    def get_user_workspace_id(self, user_id: int) -> Optional[int]:
        """workspace_id 优先；兼容旧 tenant_id 列。"""
        conn = self._sqlite_connect()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT tenant_id FROM users WHERE id = ?", (user_id,))
            row = cursor.fetchone()
            if row and row[0] is not None:
                return int(row[0])
            return self.get_default_workspace_id()
        finally:
            conn.close()

    def set_instance_setting(self, key: str, value: str) -> None:
        conn = self._sqlite_connect()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO instance_settings (key, value, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (key, value, _utc_now_sql()),
            )
            conn.commit()
        finally:
            conn.close()

    def get_instance_setting(self, key: str, default: str = "") -> str:
        conn = self._sqlite_connect()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM instance_settings WHERE key = ?", (key,))
            row = cursor.fetchone()
            return row[0] if row else default
        finally:
            conn.close()

    # ==================== 客户端执行节点 ====================

    def upsert_client_node(
        self,
        machine_id: str,
        machine_name: str = "",
        user_id: Optional[int] = None,
        status: str = "online",
        capabilities: Optional[Dict[str, Any]] = None,
    ) -> int:
        import json as _json

        conn = self._sqlite_connect()
        try:
            cursor = conn.cursor()
            caps = _json.dumps(capabilities or {}, ensure_ascii=False)
            now = _utc_now_sql()
            cursor.execute("SELECT id FROM client_nodes WHERE machine_id = ?", (machine_id,))
            row = cursor.fetchone()
            if row:
                cursor.execute(
                    """
                    UPDATE client_nodes
                    SET machine_name = ?, user_id = ?, status = ?, last_seen_at = ?, capabilities_json = ?
                    WHERE machine_id = ?
                    """,
                    (machine_name, user_id, status, now, caps, machine_id),
                )
                node_id = int(row[0])
            else:
                cursor.execute(
                    """
                    INSERT INTO client_nodes (machine_id, machine_name, user_id, status, last_seen_at, capabilities_json)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (machine_id, machine_name, user_id, status, now, caps),
                )
                node_id = int(cursor.lastrowid)
            conn.commit()
            return node_id
        finally:
            conn.close()

    def list_client_nodes(self, online_only: bool = False) -> List[Dict[str, Any]]:
        conn = self._sqlite_connect()
        try:
            cursor = conn.cursor()
            sql = """
                SELECT id, machine_id, machine_name, user_id, status, last_seen_at, capabilities_json, created_at
                FROM client_nodes
            """
            if online_only:
                sql += " WHERE status = 'online'"
            sql += " ORDER BY last_seen_at DESC"
            cursor.execute(sql)
            rows = cursor.fetchall()
            out = []
            for row in rows:
                out.append({
                    "id": row[0],
                    "machine_id": row[1],
                    "machine_name": row[2],
                    "user_id": row[3],
                    "status": row[4],
                    "last_seen_at": _bj_iso(row[5]) if row[5] else None,
                    "capabilities": row[6] or "{}",
                    "created_at": _bj_iso(row[7]),
                })
            return out
        finally:
            conn.close()

    # ==================== 远程执行任务 ====================

    def create_execution_job(self, case_id: int, user_id: int) -> int:
        conn = self._sqlite_connect()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO execution_jobs (case_id, user_id, status, created_at)
                VALUES (?, ?, 'pending', ?)
                """,
                (case_id, user_id, _utc_now_sql()),
            )
            job_id = int(cursor.lastrowid)
            conn.commit()
            return job_id
        finally:
            conn.close()

    def get_execution_job(self, job_id: int) -> Optional[Dict[str, Any]]:
        conn = self._sqlite_connect()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, case_id, user_id, status, assigned_machine_id, run_history_id,
                       error, result_json, created_at, started_at, finished_at
                FROM execution_jobs WHERE id = ?
                """,
                (job_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            return {
                "id": row[0],
                "case_id": row[1],
                "user_id": row[2],
                "status": row[3],
                "assigned_machine_id": row[4],
                "run_history_id": row[5],
                "error": row[6] or "",
                "result_json": row[7] or "",
                "created_at": _bj_iso(row[8]),
                "started_at": _bj_iso(row[9]) if row[9] else None,
                "finished_at": _bj_iso(row[10]) if row[10] else None,
            }
        finally:
            conn.close()

    def claim_execution_job(self, machine_id: str, machine_name: str = "") -> Optional[Dict[str, Any]]:
        conn = self._sqlite_connect()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id FROM execution_jobs
                WHERE status = 'pending'
                ORDER BY id ASC LIMIT 1
                """
            )
            row = cursor.fetchone()
            if not row:
                return None
            job_id = int(row[0])
            now = _utc_now_sql()
            cursor.execute(
                """
                UPDATE execution_jobs
                SET status = 'running', assigned_machine_id = ?, started_at = ?
                WHERE id = ? AND status = 'pending'
                """,
                (machine_id, now, job_id),
            )
            if cursor.rowcount != 1:
                conn.commit()
                return None
            now = _utc_now_sql()
            cursor.execute("SELECT id FROM client_nodes WHERE machine_id = ?", (machine_id,))
            node_row = cursor.fetchone()
            if node_row:
                cursor.execute(
                    """
                    UPDATE client_nodes
                    SET machine_name = ?, status = 'online', last_seen_at = ?
                    WHERE machine_id = ?
                    """,
                    (machine_name, now, machine_id),
                )
            else:
                cursor.execute(
                    """
                    INSERT INTO client_nodes (machine_id, machine_name, status, last_seen_at, capabilities_json)
                    VALUES (?, ?, 'online', ?, '{}')
                    """,
                    (machine_id, machine_name, now),
                )
            conn.commit()
            return self.get_execution_job(job_id)
        finally:
            conn.close()

    def complete_execution_job(
        self,
        job_id: int,
        status: str,
        run_history_id: Optional[int] = None,
        error: str = "",
        result_json: str = "",
    ) -> bool:
        conn = self._sqlite_connect()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE execution_jobs
                SET status = ?, run_history_id = ?, error = ?, result_json = ?, finished_at = ?
                WHERE id = ?
                """,
                (status, run_history_id, error, result_json, _utc_now_sql(), job_id),
            )
            ok = cursor.rowcount > 0
            conn.commit()
            return ok
        finally:
            conn.close()

    def list_execution_jobs(self, limit: int = 50, status: Optional[str] = None) -> List[Dict[str, Any]]:
        conn = self._sqlite_connect()
        try:
            cursor = conn.cursor()
            if status:
                cursor.execute(
                    """
                    SELECT id, case_id, user_id, status, assigned_machine_id, run_history_id,
                           error, created_at, started_at, finished_at
                    FROM execution_jobs WHERE status = ?
                    ORDER BY id DESC LIMIT ?
                    """,
                    (status, limit),
                )
            else:
                cursor.execute(
                    """
                    SELECT id, case_id, user_id, status, assigned_machine_id, run_history_id,
                           error, created_at, started_at, finished_at
                    FROM execution_jobs ORDER BY id DESC LIMIT ?
                    """,
                    (limit,),
                )
            rows = cursor.fetchall()
            out = []
            for row in rows:
                out.append({
                    "id": row[0],
                    "case_id": row[1],
                    "user_id": row[2],
                    "status": row[3],
                    "assigned_machine_id": row[4],
                    "run_history_id": row[5],
                    "error": row[6] or "",
                    "created_at": _bj_iso(row[7]),
                    "started_at": _bj_iso(row[8]) if row[8] else None,
                    "finished_at": _bj_iso(row[9]) if row[9] else None,
                })
            return out
        finally:
            conn.close()
