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
        
        conn.commit()
        conn.close()
    
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
    
    def get_case_steps(self, case_id: int, page: int = 1, page_size: int = 10) -> List[Dict[str, Any]]:
        """获取测试用例的步骤（支持分页）"""
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
