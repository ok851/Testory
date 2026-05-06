from database import Database
from typing import Dict, List, Any
from datetime import datetime, timedelta
import sqlite3


class TestReportGenerator:
    """测试报告生成器"""
    
    def __init__(self, db: Database = None):
        self.db = db if db else Database()
    
    def get_statistics_overview(self, project_id: int = None, start_date: str = None, end_date: str = None) -> Dict[str, Any]:
        """获取测试统计概览
        
        Args:
            project_id: 项目ID，可选
            start_date: 开始日期，格式：YYYY-MM-DD
            end_date: 结束日期，格式：YYYY-MM-DD
        
        Returns:
            包含统计数据的字典
        """
        conn = sqlite3.connect(self.db.db_path)
        cursor = conn.cursor()
        
        # 构建查询条件
        where_conditions = []
        params = []
        
        if project_id:
            where_conditions.append("tc.project_id = ?")
            params.append(project_id)
        
        if start_date:
            where_conditions.append("DATE(rh.created_at) >= ?")
            params.append(start_date)
        
        if end_date:
            where_conditions.append("DATE(rh.created_at) <= ?")
            params.append(end_date)
        
        where_clause = " AND ".join(where_conditions) if where_conditions else "1=1"
        
        # 总执行次数
        cursor.execute(f"""
            SELECT COUNT(*) 
            FROM run_history rh 
            INNER JOIN test_cases tc ON rh.case_id = tc.id 
            WHERE {where_clause}
        """, params)
        total_runs = cursor.fetchone()[0]
        
        # 通过次数（包括success状态）
        cursor.execute(f"""
            SELECT COUNT(*) 
            FROM run_history rh 
            INNER JOIN test_cases tc ON rh.case_id = tc.id 
            WHERE (rh.status = 'passed' OR rh.status = 'success') AND {where_clause}
        """, params)
        passed_runs = cursor.fetchone()[0]
        
        # 失败次数（包括error状态）
        cursor.execute(f"""
            SELECT COUNT(*) 
            FROM run_history rh 
            INNER JOIN test_cases tc ON rh.case_id = tc.id 
            WHERE (rh.status = 'failed' OR rh.status = 'error' OR rh.status = 'fail') AND {where_clause}
        """, params)
        failed_runs = cursor.fetchone()[0]
        
        # 其他状态次数
        cursor.execute(f"""
            SELECT COUNT(*) 
            FROM run_history rh 
            INNER JOIN test_cases tc ON rh.case_id = tc.id 
            WHERE rh.status NOT IN ('passed', 'failed', 'success', 'error', 'fail') AND {where_clause}
        """, params)
        other_runs = cursor.fetchone()[0]
        
        # 计算通过率
        pass_rate = (passed_runs / total_runs * 100) if total_runs > 0 else 0
        
        # 平均执行时间
        cursor.execute(f"""
            SELECT AVG(rh.duration) 
            FROM run_history rh 
            INNER JOIN test_cases tc ON rh.case_id = tc.id 
            WHERE rh.duration IS NOT NULL AND {where_clause}
        """, params)
        avg_duration = cursor.fetchone()[0] or 0
        
        # 总执行时间
        cursor.execute(f"""
            SELECT SUM(rh.duration) 
            FROM run_history rh 
            INNER JOIN test_cases tc ON rh.case_id = tc.id 
            WHERE rh.duration IS NOT NULL AND {where_clause}
        """, params)
        total_duration = cursor.fetchone()[0] or 0
        
        # 获取用例数量
        cursor.execute(f"""
            SELECT COUNT(DISTINCT rh.case_id) 
            FROM run_history rh 
            INNER JOIN test_cases tc ON rh.case_id = tc.id 
            WHERE {where_clause}
        """, params)
        total_cases = cursor.fetchone()[0]
        
        conn.close()
        
        return {
            'total_runs': total_runs,
            'passed_runs': passed_runs,
            'failed_runs': failed_runs,
            'other_runs': other_runs,
            'pass_rate': round(pass_rate, 2),
            'avg_duration': round(avg_duration, 2),
            'total_duration': round(total_duration, 2),
            'total_cases': total_cases
        }
    
    def get_status_distribution(self, project_id: int = None, start_date: str = None, end_date: str = None) -> List[Dict[str, Any]]:
        """获取状态分布数据
        
        Args:
            project_id: 项目ID，可选
            start_date: 开始日期，格式：YYYY-MM-DD
            end_date: 结束日期，格式：YYYY-MM-DD
        
        Returns:
            状态分布数据列表
        """
        conn = sqlite3.connect(self.db.db_path)
        cursor = conn.cursor()
        
        # 构建查询条件
        where_conditions = []
        params = []
        
        if project_id:
            where_conditions.append("tc.project_id = ?")
            params.append(project_id)
        
        if start_date:
            where_conditions.append("DATE(rh.created_at) >= ?")
            params.append(start_date)
        
        if end_date:
            where_conditions.append("DATE(rh.created_at) <= ?")
            params.append(end_date)
        
        where_clause = " AND ".join(where_conditions) if where_conditions else "1=1"
        
        # 按状态分组统计
        cursor.execute(f"""
            SELECT rh.status, COUNT(*) as count 
            FROM run_history rh 
            INNER JOIN test_cases tc ON rh.case_id = tc.id 
            WHERE {where_clause}
            GROUP BY rh.status
            ORDER BY count DESC
        """, params)
        
        results = cursor.fetchall()
        
        conn.close()
        
        return [
            {
                'status': row[0] or 'unknown',
                'count': row[1]
            }
            for row in results
        ]
    
    def get_duration_distribution(self, project_id: int = None, start_date: str = None, end_date: str = None) -> Dict[str, Any]:
        """获取耗时分布数据
        
        Args:
            project_id: 项目ID，可选
            start_date: 开始日期，格式：YYYY-MM-DD
            end_date: 结束日期，格式：YYYY-MM-DD
        
        Returns:
            耗时分布数据
        """
        conn = sqlite3.connect(self.db.db_path)
        cursor = conn.cursor()
        
        # 构建查询条件
        where_conditions = []
        params = []
        
        if project_id:
            where_conditions.append("tc.project_id = ?")
            params.append(project_id)
        
        if start_date:
            where_conditions.append("DATE(rh.created_at) >= ?")
            params.append(start_date)
        
        if end_date:
            where_conditions.append("DATE(rh.created_at) <= ?")
            params.append(end_date)
        
        where_clause = " AND ".join(where_conditions) if where_conditions else "1=1"
        
        # 定义耗时区间
        ranges = [
            {'label': '0-10s', 'min': 0, 'max': 10},
            {'label': '10-30s', 'min': 10, 'max': 30},
            {'label': '30-60s', 'min': 30, 'max': 60},
            {'label': '60-120s', 'min': 60, 'max': 120},
            {'label': '120-300s', 'min': 120, 'max': 300},
            {'label': '>300s', 'min': 300, 'max': float('inf')}
        ]
        
        distribution = []
        
        for range_info in ranges:
            if range_info['max'] == float('inf'):
                cursor.execute(f"""
                    SELECT COUNT(*) 
                    FROM run_history rh 
                    INNER JOIN test_cases tc ON rh.case_id = tc.id 
                    WHERE rh.duration >= ? AND {where_clause}
                """, [range_info['min']] + params)
            else:
                cursor.execute(f"""
                    SELECT COUNT(*) 
                    FROM run_history rh 
                    INNER JOIN test_cases tc ON rh.case_id = tc.id 
                    WHERE rh.duration >= ? AND rh.duration < ? AND {where_clause}
                """, [range_info['min'], range_info['max']] + params)
            
            count = cursor.fetchone()[0]
            distribution.append({
                'range': range_info['label'],
                'count': count
            })
        
        conn.close()
        
        return {
            'distribution': distribution,
            'labels': [item['range'] for item in distribution],
            'data': [item['count'] for item in distribution]
        }
    
    def get_trend_data(self, project_id: int = None, days: int = 30) -> Dict[str, Any]:
        """获取趋势数据
        
        Args:
            project_id: 项目ID，可选
            days: 统计天数，默认30天
        
        Returns:
            趋势数据
        """
        conn = sqlite3.connect(self.db.db_path)
        cursor = conn.cursor()
        
        # 构建查询条件
        where_conditions = ["DATE(rh.created_at) >= DATE('now', '-' || ? || ' days')"]
        params = [days]
        
        if project_id:
            where_conditions.append("tc.project_id = ?")
            params.append(project_id)
        
        where_clause = " AND ".join(where_conditions)
        
        # 按日期统计
        cursor.execute(f"""
            SELECT 
                DATE(rh.created_at) as date,
                COUNT(*) as total,
                SUM(CASE WHEN rh.status = 'passed' OR rh.status = 'success' THEN 1 ELSE 0 END) as passed,
                SUM(CASE WHEN rh.status = 'failed' OR rh.status = 'error' OR rh.status = 'fail' THEN 1 ELSE 0 END) as failed
            FROM run_history rh 
            INNER JOIN test_cases tc ON rh.case_id = tc.id 
            WHERE {where_clause}
            GROUP BY DATE(rh.created_at)
            ORDER BY date ASC
        """, params)
        
        results = cursor.fetchall()
        
        conn.close()
        
        return {
            'dates': [row[0] for row in results],
            'total': [row[1] for row in results],
            'passed': [row[2] for row in results],
            'failed': [row[3] for row in results]
        }
    
    def get_case_statistics(self, project_id: int = None, start_date: str = None, end_date: str = None) -> List[Dict[str, Any]]:
        """获取用例统计数据
        
        Args:
            project_id: 项目ID，可选
            start_date: 开始日期，格式：YYYY-MM-DD
            end_date: 结束日期，格式：YYYY-MM-DD
        
        Returns:
            用例统计数据列表
        """
        conn = sqlite3.connect(self.db.db_path)
        cursor = conn.cursor()
        
        # 构建查询条件（排除 project_id 指向已删项目的脏数据）
        where_conditions = [
            "(tc.project_id IS NULL OR EXISTS (SELECT 1 FROM projects pr WHERE pr.id = tc.project_id))"
        ]
        params = []
        
        if project_id:
            where_conditions.append("tc.project_id = ?")
            params.append(project_id)
        
        if start_date:
            where_conditions.append("DATE(rh.created_at) >= ?")
            params.append(start_date)
        
        if end_date:
            where_conditions.append("DATE(rh.created_at) <= ?")
            params.append(end_date)
        
        where_clause = " AND ".join(where_conditions) if where_conditions else "1=1"
        
        # 从运行记录聚合：已删除用例的运行历史会一并删除，不会出现在报表；孤立 run_history 无对应 tc 也会被 INNER JOIN 排除
        cursor.execute(f"""
            SELECT 
                tc.id as case_id,
                tc.name as case_name,
                COUNT(rh.id) as total_runs,
                SUM(CASE WHEN rh.status = 'passed' OR rh.status = 'success' THEN 1 ELSE 0 END) as passed_runs,
                SUM(CASE WHEN rh.status = 'failed' OR rh.status = 'error' OR rh.status = 'fail' THEN 1 ELSE 0 END) as failed_runs,
                AVG(rh.duration) as avg_duration,
                MAX(rh.created_at) as last_run_time
            FROM run_history rh
            INNER JOIN test_cases tc ON tc.id = rh.case_id
            WHERE {where_clause}
            GROUP BY tc.id, tc.name
            ORDER BY total_runs DESC
        """, params)
        
        results = cursor.fetchall()
        
        conn.close()
        
        return [
            {
                'case_id': row[0],
                'case_name': row[1],
                'total_runs': row[2],
                'passed_runs': row[3],
                'failed_runs': row[4],
                'pass_rate': round(row[3] / row[2] * 100, 2) if row[2] > 0 else 0,
                'avg_duration': round(row[5], 2) if row[5] else 0,
                'last_run_time': row[6]
            }
            for row in results
        ]
    
    def get_project_statistics(self, project_id: int = None, start_date: str = None, end_date: str = None) -> List[Dict[str, Any]]:
        """获取项目统计数据
        
        Args:
            project_id: 项目ID，可选
            start_date: 开始日期，格式：YYYY-MM-DD
            end_date: 结束日期，格式：YYYY-MM-DD
        
        Returns:
            项目统计数据列表
        """
        conn = sqlite3.connect(self.db.db_path)
        cursor = conn.cursor()
        
        # 构建查询条件
        where_conditions = []
        params = []
        
        if project_id:
            where_conditions.append("p.id = ?")
            params.append(project_id)
        
        if start_date:
            where_conditions.append("DATE(rh.created_at) >= ?")
            params.append(start_date)
        
        if end_date:
            where_conditions.append("DATE(rh.created_at) <= ?")
            params.append(end_date)
        
        where_clause = " AND ".join(where_conditions) if where_conditions else "1=1"
        
        # 按项目统计
        cursor.execute(f"""
            SELECT 
                p.id as project_id,
                p.name as project_name,
                COUNT(DISTINCT tc.id) as total_cases,
                COUNT(rh.id) as total_runs,
                SUM(CASE WHEN rh.status = 'passed' OR rh.status = 'success' THEN 1 ELSE 0 END) as passed_runs,
                SUM(CASE WHEN rh.status = 'failed' OR rh.status = 'error' OR rh.status = 'fail' THEN 1 ELSE 0 END) as failed_runs,
                AVG(rh.duration) as avg_duration
            FROM projects p
            LEFT JOIN test_cases tc ON p.id = tc.project_id
            LEFT JOIN run_history rh ON tc.id = rh.case_id
            WHERE {where_clause}
            GROUP BY p.id, p.name
            ORDER BY total_runs DESC
        """, params)
        
        results = cursor.fetchall()
        
        conn.close()
        
        return [
            {
                'project_id': row[0],
                'project_name': row[1],
                'total_cases': row[2],
                'total_runs': row[3],
                'passed_runs': row[4],
                'failed_runs': row[5],
                'pass_rate': round(row[4] / row[3] * 100, 2) if row[3] > 0 else 0,
                'avg_duration': round(row[6], 2) if row[6] else 0
            }
            for row in results
        ]
