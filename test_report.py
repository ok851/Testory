from database import Database
from typing import Dict, List, Any
from datetime import datetime, timedelta
import sqlite3


class TestReportGenerator:
    """测试报告生成器"""

    __test__ = False  # 避免 pytest 把本业务类当测试收集
    
    def __init__(self, db: Database = None):
        self.db = db if db else Database()

    def _build_report_filters(
        self,
        project_id: int = None,
        start_date: str = None,
        end_date: str = None,
        case_category: str = None,
        *,
        project_column: str = "tc.project_id",
        extra_conditions: List[str] = None,
        include_orphan_runs: bool = False,
    ):
        """构建报表 WHERE 子句与参数。case_category: all|web|api|android|mobile|desktop|cross_end

        include_orphan_runs=True 时：项目过滤用 COALESCE(rh.project_id, tc.project_id)，
        并支持无 case_id 的 cross_end / agent_teams。
        """
        where_conditions = list(extra_conditions or [])
        params: List[Any] = []

        if project_id:
            if include_orphan_runs and project_column == "tc.project_id":
                where_conditions.append("COALESCE(rh.project_id, tc.project_id) = ?")
            else:
                where_conditions.append(f"{project_column} = ?")
            params.append(project_id)

        if start_date:
            where_conditions.append("DATE(rh.created_at) >= ?")
            params.append(start_date)

        if end_date:
            where_conditions.append("DATE(rh.created_at) <= ?")
            params.append(end_date)

        cat = (case_category or "all").strip().lower()
        if cat in ("cross_end", "cross-end", "linkage"):
            where_conditions.append(
                "LOWER(COALESCE(rh.test_type, 'web')) IN ('cross_end', 'agent_teams')"
            )
        elif cat == "api":
            where_conditions.append("LOWER(COALESCE(tc.case_type, 'ui')) = 'api'")
        elif cat in ("android", "mobile"):
            where_conditions.append("LOWER(COALESCE(tc.case_type, 'ui')) = 'ui'")
            where_conditions.append("LOWER(COALESCE(tc.platform, 'web')) = 'android'")
        elif cat in ("web", "ui"):
            where_conditions.append("LOWER(COALESCE(tc.case_type, 'ui')) = 'ui'")
            where_conditions.append("LOWER(COALESCE(tc.platform, 'web')) = 'web'")
        elif cat == "desktop":
            where_conditions.append("LOWER(COALESCE(tc.case_type, 'ui')) = 'ui'")
            where_conditions.append("LOWER(COALESCE(tc.platform, 'web')) = 'desktop'")

        where_clause = " AND ".join(where_conditions) if where_conditions else "1=1"
        return where_clause, params

    @staticmethod
    def _history_from_sql(*, case_bound: bool = False) -> str:
        """case_bound=True 仅绑定用例；默认 LEFT JOIN 含无 case 的跨端/AgentTeams。"""
        if case_bound:
            return "FROM run_history rh INNER JOIN test_cases tc ON rh.case_id = tc.id"
        return "FROM run_history rh LEFT JOIN test_cases tc ON rh.case_id = tc.id"

    def get_ops_governance_summary(
        self,
        project_id: int = None,
        start_date: str = None,
        end_date: str = None,
        case_category: str = None,
        *,
        scan_limit: int = 2000,
        recent_limit: int = 10,
    ) -> Dict[str, Any]:
        """治理看板：含无 case_id 的跨端/AgentTeams 历史；JSON 字段在应用层聚合。"""
        from ai_modules.execute.history_ops_summary import aggregate_ops_governance

        conn = sqlite3.connect(self.db.db_path)
        cursor = conn.cursor()
        where_clause, params = self._build_report_filters(
            project_id,
            start_date,
            end_date,
            case_category,
            include_orphan_runs=True,
        )
        limit = max(1, min(int(scan_limit or 2000), 5000))
        cursor.execute(
            f"""
            SELECT
                rh.id,
                rh.status,
                rh.error,
                rh.extracted_text,
                rh.expected_text,
                COALESCE(rh.test_type, 'web') AS test_type,
                COALESCE(NULLIF(rh.flow_name, ''), tc.name, '') AS case_name,
                rh.created_at,
                rh.flow_name
            FROM run_history rh
            LEFT JOIN test_cases tc ON rh.case_id = tc.id
            WHERE {where_clause}
            ORDER BY rh.id DESC
            LIMIT ?
            """,
            list(params) + [limit],
        )
        rows = cursor.fetchall()
        conn.close()

        records = [
            {
                "id": r[0],
                "status": r[1],
                "error": r[2] or "",
                "extracted_text": r[3] or "",
                "expected_text": r[4] or "",
                "test_type": r[5] or "web",
                "case_name": r[6] or "",
                "created_at": r[7],
                "flow_name": r[8] or "",
            }
            for r in rows
        ]
        summary = aggregate_ops_governance(records, recent_limit=recent_limit)
        summary["scan_limit"] = limit
        summary["truncated"] = len(records) >= limit
        return summary
    
    def get_statistics_overview(
        self,
        project_id: int = None,
        start_date: str = None,
        end_date: str = None,
        case_category: str = None,
    ) -> Dict[str, Any]:
        """获取测试统计概览（含无 case_id 的跨端历史；失败计入失败，不美化）。"""
        conn = sqlite3.connect(self.db.db_path)
        cursor = conn.cursor()
        from_sql = self._history_from_sql()
        where_clause, params = self._build_report_filters(
            project_id,
            start_date,
            end_date,
            case_category,
            include_orphan_runs=True,
        )

        cursor.execute(
            f"SELECT COUNT(*) {from_sql} WHERE {where_clause}",
            params,
        )
        total_runs = cursor.fetchone()[0]

        cursor.execute(
            f"""
            SELECT COUNT(*) {from_sql}
            WHERE (rh.status = 'passed' OR rh.status = 'success') AND {where_clause}
            """,
            params,
        )
        passed_runs = cursor.fetchone()[0]

        cursor.execute(
            f"""
            SELECT COUNT(*) {from_sql}
            WHERE (rh.status = 'failed' OR rh.status = 'error' OR rh.status = 'fail') AND {where_clause}
            """,
            params,
        )
        failed_runs = cursor.fetchone()[0]

        cursor.execute(
            f"""
            SELECT COUNT(*) {from_sql}
            WHERE rh.status NOT IN ('passed', 'failed', 'success', 'error', 'fail') AND {where_clause}
            """,
            params,
        )
        other_runs = cursor.fetchone()[0]

        pass_rate = (passed_runs / total_runs * 100) if total_runs > 0 else 0

        cursor.execute(
            f"""
            SELECT AVG(rh.duration) {from_sql}
            WHERE rh.duration IS NOT NULL AND {where_clause}
            """,
            params,
        )
        avg_duration = cursor.fetchone()[0] or 0

        cursor.execute(
            f"""
            SELECT SUM(rh.duration) {from_sql}
            WHERE rh.duration IS NOT NULL AND {where_clause}
            """,
            params,
        )
        total_duration = cursor.fetchone()[0] or 0

        cursor.execute(
            f"""
            SELECT COUNT(DISTINCT rh.case_id) {from_sql}
            WHERE rh.case_id IS NOT NULL AND {where_clause}
            """,
            params,
        )
        total_cases = cursor.fetchone()[0]

        cursor.execute(
            f"""
            SELECT COUNT(*) {from_sql}
            WHERE {where_clause}
              AND rh.case_id IS NULL
              AND LOWER(COALESCE(rh.test_type, 'web')) IN ('cross_end', 'agent_teams')
            """,
            params,
        )
        orphan_cross_end_runs = cursor.fetchone()[0]

        conn.close()

        return {
            "total_runs": total_runs,
            "passed_runs": passed_runs,
            "failed_runs": failed_runs,
            "other_runs": other_runs,
            "pass_rate": round(pass_rate, 2),
            "avg_duration": round(avg_duration, 2),
            "total_duration": round(total_duration, 2),
            "total_cases": total_cases,
            "orphan_cross_end_runs": orphan_cross_end_runs,
            "includes_orphan_runs": True,
        }

    def get_status_distribution(
        self,
        project_id: int = None,
        start_date: str = None,
        end_date: str = None,
        case_category: str = None,
    ) -> List[Dict[str, Any]]:
        """获取状态分布数据（含跨端孤儿运行）。"""
        conn = sqlite3.connect(self.db.db_path)
        cursor = conn.cursor()
        from_sql = self._history_from_sql()
        where_clause, params = self._build_report_filters(
            project_id,
            start_date,
            end_date,
            case_category,
            include_orphan_runs=True,
        )

        cursor.execute(
            f"""
            SELECT rh.status, COUNT(*) as count
            {from_sql}
            WHERE {where_clause}
            GROUP BY rh.status
            ORDER BY count DESC
            """,
            params,
        )
        results = cursor.fetchall()
        conn.close()

        return [
            {"status": row[0] or "unknown", "count": row[1]}
            for row in results
        ]

    def get_duration_distribution(
        self,
        project_id: int = None,
        start_date: str = None,
        end_date: str = None,
        case_category: str = None,
    ) -> Dict[str, Any]:
        """获取耗时分布数据（含跨端孤儿运行）。"""
        conn = sqlite3.connect(self.db.db_path)
        cursor = conn.cursor()
        from_sql = self._history_from_sql()
        where_clause, params = self._build_report_filters(
            project_id,
            start_date,
            end_date,
            case_category,
            include_orphan_runs=True,
        )

        ranges = [
            {"label": "0-10s", "min": 0, "max": 10},
            {"label": "10-30s", "min": 10, "max": 30},
            {"label": "30-60s", "min": 30, "max": 60},
            {"label": "60-120s", "min": 60, "max": 120},
            {"label": "120-300s", "min": 120, "max": 300},
            {"label": ">300s", "min": 300, "max": float("inf")},
        ]

        distribution = []
        for range_info in ranges:
            if range_info["max"] == float("inf"):
                cursor.execute(
                    f"""
                    SELECT COUNT(*) {from_sql}
                    WHERE rh.duration >= ? AND {where_clause}
                    """,
                    [range_info["min"]] + params,
                )
            else:
                cursor.execute(
                    f"""
                    SELECT COUNT(*) {from_sql}
                    WHERE rh.duration >= ? AND rh.duration < ? AND {where_clause}
                    """,
                    [range_info["min"], range_info["max"]] + params,
                )
            count = cursor.fetchone()[0]
            distribution.append({"range": range_info["label"], "count": count})

        conn.close()
        return {
            "distribution": distribution,
            "labels": [item["range"] for item in distribution],
            "data": [item["count"] for item in distribution],
        }

    def get_trend_data(self, project_id: int = None, days: int = 30) -> Dict[str, Any]:
        """获取趋势数据（含跨端孤儿运行）。"""
        conn = sqlite3.connect(self.db.db_path)
        cursor = conn.cursor()
        from_sql = self._history_from_sql()

        where_conditions = ["DATE(rh.created_at) >= DATE('now', '-' || ? || ' days')"]
        params: List[Any] = [days]
        if project_id:
            where_conditions.append("COALESCE(rh.project_id, tc.project_id) = ?")
            params.append(project_id)
        where_clause = " AND ".join(where_conditions)

        cursor.execute(
            f"""
            SELECT
                DATE(rh.created_at) as date,
                COUNT(*) as total,
                SUM(CASE WHEN rh.status = 'passed' OR rh.status = 'success' THEN 1 ELSE 0 END) as passed,
                SUM(CASE WHEN rh.status = 'failed' OR rh.status = 'error' OR rh.status = 'fail' THEN 1 ELSE 0 END) as failed
            {from_sql}
            WHERE {where_clause}
            GROUP BY DATE(rh.created_at)
            ORDER BY date ASC
            """,
            params,
        )
        results = cursor.fetchall()
        conn.close()

        return {
            "dates": [row[0] for row in results],
            "total": [row[1] for row in results],
            "passed": [row[2] for row in results],
            "failed": [row[3] for row in results],
        }

    def get_case_statistics(
        self,
        project_id: int = None,
        start_date: str = None,
        end_date: str = None,
        case_category: str = None,
    ) -> List[Dict[str, Any]]:
        """获取用例统计（仅绑定用例；跨端无 case 见治理看板 / overview 孤儿计数）。"""
        conn = sqlite3.connect(self.db.db_path)
        cursor = conn.cursor()

        where_clause, params = self._build_report_filters(
            project_id,
            start_date,
            end_date,
            case_category,
            extra_conditions=[
                "(tc.project_id IS NULL OR EXISTS (SELECT 1 FROM projects pr WHERE pr.id = tc.project_id))"
            ],
        )
        from_sql = self._history_from_sql(case_bound=True)

        cursor.execute(
            f"""
            SELECT
                tc.id as case_id,
                tc.name as case_name,
                tc.case_type,
                tc.platform,
                COUNT(rh.id) as total_runs,
                SUM(CASE WHEN rh.status = 'passed' OR rh.status = 'success' THEN 1 ELSE 0 END) as passed_runs,
                SUM(CASE WHEN rh.status = 'failed' OR rh.status = 'error' OR rh.status = 'fail' THEN 1 ELSE 0 END) as failed_runs,
                AVG(rh.duration) as avg_duration,
                MAX(rh.created_at) as last_run_time
            {from_sql}
            WHERE {where_clause}
            GROUP BY tc.id, tc.name, tc.case_type, tc.platform
            ORDER BY total_runs DESC
            """,
            params,
        )
        results = cursor.fetchall()
        conn.close()

        def _category_label(case_type: str, platform: str) -> str:
            ct = (case_type or "ui").lower()
            pf = (platform or "web").lower()
            if ct == "api":
                return "接口"
            if pf == "android":
                return "移动端"
            if pf == "desktop":
                return "桌面"
            return "UI"

        return [
            {
                "case_id": row[0],
                "case_name": row[1],
                "case_type": row[2],
                "platform": row[3],
                "case_category": _category_label(row[2], row[3]),
                "total_runs": row[4],
                "passed_runs": row[5],
                "failed_runs": row[6],
                "pass_rate": round(row[5] / row[4] * 100, 2) if row[4] > 0 else 0,
                "avg_duration": round(row[7], 2) if row[7] else 0,
                "last_run_time": row[8],
            }
            for row in results
        ]

    def get_project_statistics(
        self,
        project_id: int = None,
        start_date: str = None,
        end_date: str = None,
        case_category: str = None,
    ) -> List[Dict[str, Any]]:
        """项目统计：运行经 COALESCE(rh.project_id, tc.project_id) 归属，含跨端孤儿。"""
        conn = sqlite3.connect(self.db.db_path)
        cursor = conn.cursor()

        where_clause, params = self._build_report_filters(
            project_id,
            start_date,
            end_date,
            case_category,
            project_column="p.id",
            include_orphan_runs=False,
        )

        cursor.execute(
            f"""
            SELECT
                p.id as project_id,
                p.name as project_name,
                (SELECT COUNT(*) FROM test_cases tc0 WHERE tc0.project_id = p.id) as total_cases,
                COUNT(rh.id) as total_runs,
                COALESCE(SUM(CASE WHEN rh.status = 'passed' OR rh.status = 'success' THEN 1 ELSE 0 END), 0) as passed_runs,
                COALESCE(SUM(CASE WHEN rh.status = 'failed' OR rh.status = 'error' OR rh.status = 'fail' THEN 1 ELSE 0 END), 0) as failed_runs,
                AVG(rh.duration) as avg_duration
            FROM projects p
            LEFT JOIN run_history rh
              ON COALESCE(
                   rh.project_id,
                   (SELECT tc1.project_id FROM test_cases tc1 WHERE tc1.id = rh.case_id)
                 ) = p.id
            LEFT JOIN test_cases tc ON tc.id = rh.case_id
            WHERE {where_clause}
            GROUP BY p.id, p.name
            ORDER BY total_runs DESC
            """,
            params,
        )
        results = cursor.fetchall()
        conn.close()

        return [
            {
                "project_id": row[0],
                "project_name": row[1],
                "total_cases": row[2],
                "total_runs": row[3],
                "passed_runs": row[4],
                "failed_runs": row[5],
                "pass_rate": round(row[4] / row[3] * 100, 2) if row[3] > 0 else 0,
                "avg_duration": round(row[6], 2) if row[6] else 0,
            }
            for row in results
        ]
