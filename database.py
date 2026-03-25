import sqlite3
import json
from datetime import datetime
from typing import List, Dict, Any

class Database:
    def __init__(self, db_path: str = "test_cases.db"):
        self.db_path = db_path
        self.init_db()
    
    def init_db(self):
        """初始化数据库表"""
        conn = sqlite3.connect(self.db_path)
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
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_login TIMESTAMP
            )
        ''')

        # 创建项目表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
                last_run TIMESTAMP,
                next_run TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (project_id) REFERENCES projects (id)
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

        # 添加数据库索引以优化查询性能
        self._create_indexes(cursor)
        
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
        ]
        
        for index_sql in indexes:
            try:
                cursor.execute(index_sql)
            except sqlite3.Error as e:
                # 记录错误但继续执行，避免因为索引创建失败影响主要功能
                print(f"创建索引失败: {index_sql}, 错误: {e}")
    
    def create_test_case(self, name: str, description: str = "", url: str = "") -> int:
        """创建测试用例"""
        conn = sqlite3.connect(self.db_path)
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
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM test_cases WHERE id = ?", (case_id,))
        row = cursor.fetchone()
        
        if row:
            return {
                'id': row[0],
                'name': row[1],
                'description': row[2],
                'target_url': row[3],
                'created_at': row[4],
                'project_id': row[5] if len(row) > 5 else None,
                'url': row[6] if len(row) > 6 else '',
                'precondition': row[7] if len(row) > 7 else '',
                'expected_result': row[8] if len(row) > 8 else ''
            }
        
        conn.close()
        return None
    
    def get_all_test_cases(self) -> List[Dict[str, Any]]:
        """获取所有测试用例"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM test_cases ORDER BY created_at DESC")
        rows = cursor.fetchall()
        
        cases = []
        for row in rows:
            case = {
                'id': row[0],
                'name': row[1],
                'description': row[2],
                'target_url': row[3],
                'created_at': row[4],
                'project_id': row[5] if len(row) > 5 else None,
                'url': row[6] if len(row) > 6 else '',
                'precondition': row[7] if len(row) > 7 else '',
                'expected_result': row[8] if len(row) > 8 else ''
            }
            cases.append(case)
        
        conn.close()
        return cases
    
    def update_test_case(self, case_id: int, name: str = None, description: str = None, url: str = None) -> bool:
        """更新测试用例"""
        conn = sqlite3.connect(self.db_path)
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
    
    def delete_test_case(self, case_id: int) -> bool:
        """删除测试用例"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # 删除测试用例
        cursor.execute("DELETE FROM test_cases WHERE id = ?", (case_id,))
        
        success = cursor.rowcount > 0
        
        conn.commit()
        conn.close()
        
        return success
    
    # ==================== 项目管理方法 ====================
    
    def create_project(self, name: str, description: str = "") -> int:
        """创建项目"""
        conn = sqlite3.connect(self.db_path)
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
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM projects WHERE id = ?", (project_id,))
        row = cursor.fetchone()
        
        if row:
            return {
                'id': row[0],
                'name': row[1],
                'description': row[2],
                'created_at': row[3]
            }
        
        conn.close()
        return None
    
    def get_all_projects(self) -> List[Dict[str, Any]]:
        """获取所有项目"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM projects ORDER BY created_at DESC")
        rows = cursor.fetchall()
        
        projects = []
        for row in rows:
            projects.append({
                'id': row[0],
                'name': row[1],
                'description': row[2],
                'created_at': row[3]
            })
        
        conn.close()
        return projects
    
    def update_project(self, project_id: int, name: str = None, description: str = None) -> bool:
        """更新项目"""
        conn = sqlite3.connect(self.db_path)
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
        """删除项目及其相关测试用例和步骤"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # 获取该项目下的所有测试用例
        cursor.execute("SELECT id FROM test_cases WHERE project_id = ?", (project_id,))
        case_ids = [row[0] for row in cursor.fetchall()]
        
        # 删除所有测试用例的步骤
        for case_id in case_ids:
            cursor.execute("DELETE FROM test_steps WHERE case_id = ?", (case_id,))
        
        # 删除所有测试用例
        cursor.execute("DELETE FROM test_cases WHERE project_id = ?", (project_id,))
        
        # 删除项目
        cursor.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        
        success = cursor.rowcount > 0
        
        conn.commit()
        conn.close()
        
        return success
    
    def get_project_cases(self, project_id: int) -> List[Dict[str, Any]]:
        """获取项目下的所有测试用例"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT tc.*, COUNT(ts.id) as step_count
            FROM test_cases tc
            LEFT JOIN test_steps ts ON tc.id = ts.case_id
            WHERE tc.project_id = ?
            GROUP BY tc.id
            ORDER BY tc.created_at DESC
        """, (project_id,))
        rows = cursor.fetchall()
        
        cases = []
        for row in rows:
            cases.append({
                'id': row[0],
                'name': row[1],
                'description': row[2],
                'target_url': row[3],
                'created_at': row[4],
                'project_id': row[5] if len(row) > 5 else None,
                'url': row[6] if len(row) > 6 else '',
                'precondition': row[7] if len(row) > 7 else '',
                'expected_result': row[8] if len(row) > 8 else '',
                'step_count': row[9] if len(row) > 9 else 0
            })
        
        conn.close()
        return cases
    
    # ==================== 测试用例管理方法（新版本） ====================
    
    def create_test_case_v2(self, project_id: int, name: str, url: str = "", description: str = "", precondition: str = "", expected_result: str = "") -> int:
        """创建测试用例（新版本，关联到项目）"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute(
            "INSERT INTO test_cases (project_id, name, url, description, precondition, expected_result) VALUES (?, ?, ?, ?, ?, ?)",
            (project_id, name, url, description, precondition, expected_result)
        )
        case_id = cursor.lastrowid
        
        conn.commit()
        conn.close()
        
        return case_id
    
    def get_test_case_v2(self, case_id: int) -> Dict[str, Any]:
        """获取测试用例（新版本）"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT id, project_id, name, url, description, created_at, precondition, expected_result FROM test_cases WHERE id = ?", (case_id,))
        row = cursor.fetchone()
        
        if row:
            return {
                'id': row[0],
                'project_id': row[1],
                'name': row[2],
                'url': row[3],
                'description': row[4],
                'created_at': row[5],
                'precondition': row[6] if len(row) > 6 else '',
                'expected_result': row[7] if len(row) > 7 else ''
            }
        
        conn.close()
        return None
    
    def update_test_case_v2(self, case_id: int, name: str = None, url: str = None, description: str = None, precondition: str = None, expected_result: str = None) -> bool:
        """更新测试用例（新版本）"""
        conn = sqlite3.connect(self.db_path)
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
    
    def delete_test_case_v2(self, case_id: int) -> bool:
        """删除测试用例及其相关步骤（新版本）"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            # 删除该用例的所有步骤
            cursor.execute("DELETE FROM test_steps WHERE case_id = ?", (case_id,))
            
            # 删除测试用例
            cursor.execute("DELETE FROM test_cases WHERE id = ?", (case_id,))
            
            # 提交事务
            conn.commit()
            
            # 验证测试用例是否真的被删除
            cursor.execute("SELECT id FROM test_cases WHERE id = ?", (case_id,))
            case_exists = cursor.fetchone() is not None
            
            return not case_exists
        except Exception as e:
            print(f"删除测试用例失败: {e}")
            conn.rollback()
            return False
        finally:
            conn.close()
    
    # ==================== 测试步骤管理方法 ====================
    
    def create_test_step(self, case_id: int, action: str, selector_type: str = "", 
                         selector_value: str = "", input_value: str = "", 
                         description: str = "", step_order: int = None, page_name: str = "",
                         swipe_x: str = "", swipe_y: str = "", url: str = "",
                         enter_iframe: bool = False, iframe_selector: str = "", compare_type: str = "equals") -> int:
        """创建测试步骤"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # 如果没有指定step_order，自动计算最大顺序值
        if step_order is None:
            cursor.execute("SELECT MAX(step_order) FROM test_steps WHERE case_id = ?", (case_id,))
            max_order = cursor.fetchone()[0]
            step_order = (max_order or 0) + 1
        
        cursor.execute(
            """INSERT INTO test_steps 
               (case_id, action, selector_type, selector_value, input_value, description, step_order, page_name, swipe_x, swipe_y, url, enter_iframe, iframe_selector, compare_type) 
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (case_id, action, selector_type, selector_value, input_value, description, step_order, page_name, swipe_x, swipe_y, url, enter_iframe, iframe_selector, compare_type)
        )
        step_id = cursor.lastrowid
        
        conn.commit()
        conn.close()
        
        return step_id
    
    def get_test_step(self, step_id: int) -> Dict[str, Any]:
        """获取测试步骤"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM test_steps WHERE id = ?", (step_id,))
        row = cursor.fetchone()
        
        if row:
            return {
                'id': row[0],
                'case_id': row[1],
                'action': row[2],
                'selector_type': row[3],
                'selector_value': row[4],
                'input_value': row[5],
                'description': row[6],
                'step_order': row[7],
                'created_at': row[8],
                'page_name': row[9] if len(row) > 9 else '',
                'swipe_x': row[10] if len(row) > 10 else '',
                'swipe_y': row[11] if len(row) > 11 else '',
                'url': row[12] if len(row) > 12 else '',
                'enter_iframe': row[13] if len(row) > 13 else False,
                'iframe_selector': row[14] if len(row) > 14 else '',
                'compare_type': row[15] if len(row) > 15 else 'equals'
            }
        
        conn.close()
        return None
    
    def get_case_steps(self, case_id: int, page: int = 1, page_size: int = 9999) -> List[Dict[str, Any]]:
        """获取测试用例的步骤（支持分页）- 修改默认page_size为9999以获取所有步骤"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        offset = (page - 1) * page_size
        cursor.execute("SELECT * FROM test_steps WHERE case_id = ? ORDER BY step_order ASC LIMIT ? OFFSET ?", (case_id, page_size, offset))
        rows = cursor.fetchall()
        
        steps = []
        for row in rows:
            steps.append({
                'id': row[0],
                'case_id': row[1],
                'action': row[2],
                'selector_type': row[3],
                'selector_value': row[4],
                'input_value': row[5],
                'description': row[6],
                'step_order': row[7],
                'created_at': row[8],
                'page_name': row[9] if len(row) > 9 else '',
                'swipe_x': row[10] if len(row) > 10 else '',
                'swipe_y': row[11] if len(row) > 11 else '',
                'url': row[12] if len(row) > 12 else '',
                'enter_iframe': row[13] if len(row) > 13 else False,
                'iframe_selector': row[14] if len(row) > 14 else '',
                'compare_type': row[15] if len(row) > 15 else 'equals'
            })
        
        conn.close()
        return steps
    
    def get_case_steps_count(self, case_id: int) -> int:
        """获取测试用例步骤的总数"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT COUNT(*) FROM test_steps WHERE case_id = ?", (case_id,))
        count = cursor.fetchone()[0]
        
        conn.close()
        return count
    
    def update_test_step(self, step_id: int, action: str = None, selector_type: str = None,
                        selector_value: str = None, input_value: str = None,
                        description: str = None, step_order: int = None,
                        enter_iframe: bool = None, iframe_selector: str = None, compare_type: str = None) -> bool:
        """更新测试步骤"""
        conn = sqlite3.connect(self.db_path)
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
    
    def delete_test_step(self, step_id: int) -> bool:
        """删除测试步骤"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("DELETE FROM test_steps WHERE id = ?", (step_id,))
        
        success = cursor.rowcount > 0
        
        conn.commit()
        conn.close()
        
        return success
    
    # ==================== 运行历史记录管理方法 ====================
    
    def create_run_history(self, case_id: int, status: str, duration: float, error: str = "", extracted_text: str = "", expected_text: str = "") -> int:
        """创建运行历史记录"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # 获取本地时间，而不是使用 UTC 时间
        import datetime
        local_time = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        cursor.execute(
            "INSERT INTO run_history (case_id, status, duration, error, extracted_text, expected_text, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (case_id, status, duration, error, extracted_text, expected_text, local_time)
        )
        history_id = cursor.lastrowid
        
        conn.commit()
        conn.close()
        
        return history_id
    
    def get_all_run_history(self, page: int = 1, page_size: int = 20, case_id: int = None, search_text: str = None, project_id: int = None) -> List[Dict[str, Any]]:
        """获取所有运行历史记录（支持分页、按测试用例ID过滤、按项目ID过滤和搜索）"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        offset = (page - 1) * page_size
        
        if case_id:
            if search_text:
                cursor.execute("""
                    SELECT rh.*, tc.name as case_name 
                    FROM run_history rh 
                    LEFT JOIN test_cases tc ON rh.case_id = tc.id 
                    WHERE rh.case_id = ? AND tc.name LIKE ?
                    ORDER BY rh.created_at DESC
                    LIMIT ? OFFSET ?
                """, (case_id, f'%{search_text}%', page_size, offset))
            else:
                cursor.execute("""
                    SELECT rh.*, tc.name as case_name 
                    FROM run_history rh 
                    LEFT JOIN test_cases tc ON rh.case_id = tc.id 
                    WHERE rh.case_id = ?
                    ORDER BY rh.created_at DESC
                    LIMIT ? OFFSET ?
                """, (case_id, page_size, offset))
        else:
            if project_id:
                if search_text:
                    cursor.execute("""
                        SELECT rh.*, tc.name as case_name 
                        FROM run_history rh 
                        LEFT JOIN test_cases tc ON rh.case_id = tc.id 
                        WHERE tc.project_id = ? AND tc.name LIKE ?
                        ORDER BY rh.created_at DESC
                        LIMIT ? OFFSET ?
                    """, (project_id, f'%{search_text}%', page_size, offset))
                else:
                    cursor.execute("""
                        SELECT rh.*, tc.name as case_name 
                        FROM run_history rh 
                        LEFT JOIN test_cases tc ON rh.case_id = tc.id 
                        WHERE tc.project_id = ?
                        ORDER BY rh.created_at DESC
                        LIMIT ? OFFSET ?
                    """, (project_id, page_size, offset))
            else:
                if search_text:
                    cursor.execute("""
                        SELECT rh.*, tc.name as case_name 
                        FROM run_history rh 
                        LEFT JOIN test_cases tc ON rh.case_id = tc.id 
                        WHERE tc.name LIKE ?
                        ORDER BY rh.created_at DESC
                        LIMIT ? OFFSET ?
                    """, (f'%{search_text}%', page_size, offset))
                else:
                    cursor.execute("""
                        SELECT rh.*, tc.name as case_name 
                        FROM run_history rh 
                        LEFT JOIN test_cases tc ON rh.case_id = tc.id 
                        ORDER BY rh.created_at DESC
                        LIMIT ? OFFSET ?
                    """, (page_size, offset))
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
                'created_at': row[6],
                'expected_text': row[7] if len(row) > 7 else '',
                'case_name': row[8] if len(row) > 8 else ''
            })
        
        conn.close()
        return history

    def get_run_history_count(self, case_id: int = None, search_text: str = None, project_id: int = None) -> int:
        """获取运行历史记录总数（支持按测试用例ID过滤、按项目ID过滤和搜索）"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        if case_id:
            if search_text:
                cursor.execute("""
                    SELECT COUNT(*) 
                    FROM run_history rh 
                    LEFT JOIN test_cases tc ON rh.case_id = tc.id 
                    WHERE rh.case_id = ? AND tc.name LIKE ?
                """, (case_id, f'%{search_text}%'))
            else:
                cursor.execute("SELECT COUNT(*) FROM run_history WHERE case_id = ?", (case_id,))
        else:
            if project_id:
                if search_text:
                    cursor.execute("""
                        SELECT COUNT(*) 
                        FROM run_history rh 
                        LEFT JOIN test_cases tc ON rh.case_id = tc.id 
                        WHERE tc.project_id = ? AND tc.name LIKE ?
                    """, (project_id, f'%{search_text}%'))
                else:
                    cursor.execute("""
                        SELECT COUNT(*) 
                        FROM run_history rh 
                        LEFT JOIN test_cases tc ON rh.case_id = tc.id 
                        WHERE tc.project_id = ?
                    """, (project_id,))
            else:
                if search_text:
                    cursor.execute("""
                        SELECT COUNT(*) 
                        FROM run_history rh 
                        LEFT JOIN test_cases tc ON rh.case_id = tc.id 
                        WHERE tc.name LIKE ?
                    """, (f'%{search_text}%',))
                else:
                    cursor.execute("SELECT COUNT(*) FROM run_history")
        count = cursor.fetchone()[0]
        
        conn.close()
        return count
    
    def get_case_run_history(self, case_id: int) -> List[Dict[str, Any]]:
        """获取指定测试用例的运行历史记录"""
        conn = sqlite3.connect(self.db_path)
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
                'created_at': row[6],
                'expected_text': row[7] if len(row) > 7 else ''
            })
        
        conn.close()
        return history
    
    def delete_run_history(self, history_id: int) -> bool:
        """删除运行历史记录"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("DELETE FROM run_history WHERE id = ?", (history_id,))
        
        success = cursor.rowcount > 0
        
        conn.commit()
        conn.close()
        
        return success
    
    def delete_case_run_history(self, case_id: int) -> bool:
        """删除指定测试用例的所有运行历史记录"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("DELETE FROM run_history WHERE case_id = ?", (case_id,))
        
        success = cursor.rowcount > 0
        
        conn.commit()
        conn.close()
        
        return success
    
    def delete_all_run_history(self) -> bool:
        """删除所有运行历史记录"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("DELETE FROM run_history")
        
        success = cursor.rowcount > 0
        
        conn.commit()
        conn.close()
        
        return success
    
    def get_run_history_detail(self, record_id: int) -> Dict[str, Any]:
        """获取运行历史记录详情"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT rh.*, tc.name as case_name 
            FROM run_history rh 
            LEFT JOIN test_cases tc ON rh.case_id = tc.id 
            WHERE rh.id = ?
        """, (record_id,))
        row = cursor.fetchone()
        
        if row:
            result = {
                'id': row[0],
                'case_id': row[1],
                'status': row[2],
                'duration': row[3],
                'error': row[4],
                'extracted_text': row[5],
                'created_at': row[6],
                'expected_text': row[7] if len(row) > 7 else '',
                'case_name': row[8] if len(row) > 8 else ''
            }
            conn.close()
            return result
        
        conn.close()
        return None
    
    def delete_case_steps(self, case_id: int) -> bool:
        """删除测试用例的所有步骤"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("DELETE FROM test_steps WHERE case_id = ?", (case_id,))
        
        success = cursor.rowcount > 0
        
        conn.commit()
        conn.close()
        
        return success
    
    def update_step_order(self, case_id: int, steps: List[Dict[str, Any]]) -> bool:
        """更新测试步骤的顺序"""
        conn = sqlite3.connect(self.db_path)
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
            print(f"更新步骤顺序失败: {e}")
            conn.rollback()
            return False
        finally:
            conn.close()

    # ==================== 用户管理方法 ====================

    def create_user(self, username: str, password_hash: str, email: str = None, role: str = 'tester') -> int:
        """创建用户"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        try:
            cursor.execute(
                "INSERT INTO users (username, password_hash, email, role) VALUES (?, ?, ?, ?)",
                (username, password_hash, email, role)
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
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT id, username, password_hash, email, role, is_active, created_at, last_login FROM users WHERE username = ?", (username,))
        row = cursor.fetchone()
        conn.close()
        if row:
            return {'id': row[0], 'username': row[1], 'password_hash': row[2],
                    'email': row[3], 'role': row[4], 'is_active': row[5],
                    'created_at': row[6], 'last_login': row[7]}
        return None

    def get_user_by_id(self, user_id: int) -> Dict[str, Any]:
        """根据ID获取用户"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT id, username, password_hash, email, role, is_active, created_at, last_login FROM users WHERE id = ?", (user_id,))
        row = cursor.fetchone()
        conn.close()
        if row:
            return {'id': row[0], 'username': row[1], 'password_hash': row[2],
                    'email': row[3], 'role': row[4], 'is_active': row[5],
                    'created_at': row[6], 'last_login': row[7]}
        return None

    def get_all_users(self) -> List[Dict[str, Any]]:
        """获取所有用户"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT id, username, email, role, is_active, created_at, last_login FROM users ORDER BY created_at DESC")
        rows = cursor.fetchall()
        conn.close()
        return [{'id': r[0], 'username': r[1], 'email': r[2], 'role': r[3],
                 'is_active': r[4], 'created_at': r[5], 'last_login': r[6]} for r in rows]

    def update_user_last_login(self, user_id: int):
        """更新用户最后登录时间"""
        import datetime
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET last_login = ? WHERE id = ?",
                       (datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), user_id))
        conn.commit()
        conn.close()

    def update_user(self, user_id: int, email: str = None, role: str = None, is_active: int = None, password_hash: str = None) -> bool:
        """更新用户信息"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        updates, params = [], []
        if email is not None:
            updates.append("email = ?"); params.append(email)
        if role is not None:
            updates.append("role = ?"); params.append(role)
        if is_active is not None:
            updates.append("is_active = ?"); params.append(is_active)
        if password_hash is not None:
            updates.append("password_hash = ?"); params.append(password_hash)
        if not updates:
            conn.close()
            return False
        params.append(user_id)
        cursor.execute(f"UPDATE users SET {', '.join(updates)} WHERE id = ?", params)
        success = cursor.rowcount > 0
        conn.commit()
        conn.close()
        return success

    def delete_user(self, user_id: int) -> bool:
        """删除用户"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM users WHERE id = ?", (user_id,))
        success = cursor.rowcount > 0
        conn.commit()
        conn.close()
        return success

    def count_users(self) -> int:
        """获取用户总数（用于判断是否需要初始化管理员）"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM users")
        count = cursor.fetchone()[0]
        conn.close()
        return count

    # ==================== 步骤执行结果方法 ====================

    def create_step_result(self, run_history_id: int, step_id: int, step_order: int,
                           action: str, selector_value: str, input_value: str,
                           description: str, status: str, error: str = "",
                           screenshot: str = "", duration: float = 0) -> int:
        """记录单步骤执行结果"""
        import datetime
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        local_time = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
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
        conn = sqlite3.connect(self.db_path)
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
                 'screenshot': r[10], 'duration': r[11], 'created_at': r[12]} for r in rows]

    # ==================== 变量管理方法 ====================

    def create_variable(self, name: str, value: str, scope: str = 'global',
                        project_id: int = None, case_id: int = None, description: str = '') -> int:
        """创建变量"""
        conn = sqlite3.connect(self.db_path)
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
        conn = sqlite3.connect(self.db_path)
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
                 'project_id': r[4], 'case_id': r[5], 'description': r[6], 'created_at': r[7]} for r in rows]

    def update_variable(self, var_id: int, name: str = None, value: str = None, description: str = None) -> bool:
        """更新变量"""
        conn = sqlite3.connect(self.db_path)
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
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM variables WHERE id = ?", (var_id,))
        success = cursor.rowcount > 0
        conn.commit()
        conn.close()
        return success

    def resolve_variables(self, text: str, project_id: int = None, case_id: int = None) -> str:
        """将文本中的 {{变量名}} 替换为实际值"""
        if not text or '{{' not in text:
            return text
        import re
        variables = self.get_variables(scope='case' if case_id else ('project' if project_id else 'global'),
                                       project_id=project_id, case_id=case_id)
        var_map = {v['name']: v['value'] for v in variables}
        def replace_var(match):
            var_name = match.group(1).strip()
            return var_map.get(var_name, match.group(0))
        return re.sub(r'\{\{(.+?)\}\}', replace_var, text)

    # ==================== 定时调度方法 ====================

    def create_schedule(self, name: str, case_ids: list, cron_expr: str, project_id: int = None) -> int:
        """创建定时调度"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO schedules (name, project_id, case_ids, cron_expr) VALUES (?, ?, ?, ?)",
            (name, project_id, json.dumps(case_ids), cron_expr)
        )
        schedule_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return schedule_id

    def get_all_schedules(self) -> List[Dict[str, Any]]:
        """获取所有调度任务"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM schedules ORDER BY created_at DESC")
        rows = cursor.fetchall()
        conn.close()
        return [{'id': r[0], 'name': r[1], 'project_id': r[2],
                 'case_ids': json.loads(r[3]) if r[3] else [],
                 'cron_expr': r[4], 'is_active': r[5],
                 'last_run': r[6], 'next_run': r[7], 'created_at': r[8]} for r in rows]

    def get_active_schedules(self) -> List[Dict[str, Any]]:
        """获取所有激活的调度任务"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM schedules WHERE is_active = 1")
        rows = cursor.fetchall()
        conn.close()
        return [{'id': r[0], 'name': r[1], 'project_id': r[2],
                 'case_ids': json.loads(r[3]) if r[3] else [],
                 'cron_expr': r[4], 'is_active': r[5],
                 'last_run': r[6], 'next_run': r[7], 'created_at': r[8]} for r in rows]

    def update_schedule(self, schedule_id: int, name: str = None, cron_expr: str = None,
                        is_active: int = None, case_ids: list = None, last_run: str = None) -> bool:
        """更新调度任务"""
        conn = sqlite3.connect(self.db_path)
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
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM schedules WHERE id = ?", (schedule_id,))
        success = cursor.rowcount > 0
        conn.commit()
        conn.close()
        return success

    # ==================== API 令牌管理方法 ====================

    def create_api_token(self, name: str, token: str, project_id: int = None, expires_at: str = None) -> int:
        """创建 API 令牌"""
        conn = sqlite3.connect(self.db_path)
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
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM api_tokens WHERE token = ? AND is_active = 1", (token,))
        row = cursor.fetchone()
        conn.close()
        if row:
            return {'id': row[0], 'name': row[1], 'token': row[2], 'project_id': row[3],
                    'is_active': row[4], 'expires_at': row[5], 'created_at': row[6]}
        return None

    def get_all_tokens(self) -> List[Dict[str, Any]]:
        """获取所有令牌（不含 token 明文）"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, project_id, is_active, expires_at, created_at FROM api_tokens ORDER BY created_at DESC")
        rows = cursor.fetchall()
        conn.close()
        return [{'id': r[0], 'name': r[1], 'project_id': r[2], 'is_active': r[3],
                 'expires_at': r[4], 'created_at': r[5]} for r in rows]

    def revoke_token(self, token_id: int) -> bool:
        """撤销令牌"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("UPDATE api_tokens SET is_active = 0 WHERE id = ?", (token_id,))
        success = cursor.rowcount > 0
        conn.commit()
        conn.close()
        return success
