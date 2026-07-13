# -*- coding: utf-8 -*-
"""
元素仓库 — 集中管理元素标识，支持版本解耦与远程同步。

在现有 `element_repository` 表基础上增强:
- CRUD 操作
- 远程同步 (通过 API 拉取最新定位符)
- 自愈记录持久化
- 按项目/平台隔离
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional

from mobile_engine.engine_interface import LocatorInfo, LocatorStrategy

try:
    from uat_logger import uat_logger
except ImportError:
    import logging

    uat_logger = logging.getLogger(__name__)


class ElementRepository:
    """元素仓库 — 应用版本解耦的定位符管理"""

    def __init__(self, db=None):
        if db is None:
            from database import Database

            db = Database()
        self._db = db

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def add_element(
        self,
        project_id: int,
        alias: str,
        selector_type: str,
        selector_value: str,
        *,
        platform: str = "android",
        semantic_desc: str = "",
        visual_template_path: str = "",
        attributes: Optional[Dict[str, Any]] = None,
    ) -> int:
        """
        添加元素到仓库。

        Returns:
            元素 id
        """
        conn = self._db._sqlite_connect()
        cursor = conn.cursor()

        attrs_json = json.dumps(attributes or {}, ensure_ascii=False)
        locator_candidates = json.dumps([], ensure_ascii=False)

        cursor.execute(
            """INSERT INTO element_repository
               (project_id, alias, platform, selector_type, selector_value,
                attributes_json, semantic_desc, visual_template_path,
                locator_candidates)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (project_id, alias, platform, selector_type, selector_value,
             attrs_json, semantic_desc, visual_template_path, locator_candidates),
        )
        element_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return element_id

    def get_element(
        self,
        project_id: int,
        alias: str,
        platform: str = "android",
    ) -> Optional[Dict[str, Any]]:
        """获取元素"""
        conn = self._db._sqlite_connect()
        cursor = conn.cursor()
        cursor.execute(
            """SELECT * FROM element_repository
               WHERE project_id = ? AND alias = ? AND platform = ?
               ORDER BY updated_at DESC LIMIT 1""",
            (project_id, alias, platform),
        )
        row = cursor.fetchone()
        conn.close()
        if row:
            return self._row_to_dict(row)
        return None

    def get_elements_by_project(
        self,
        project_id: int,
        platform: str = "android",
    ) -> List[Dict[str, Any]]:
        """获取项目下所有元素"""
        conn = self._db._sqlite_connect()
        cursor = conn.cursor()
        cursor.execute(
            """SELECT * FROM element_repository
               WHERE project_id = ? AND platform = ?
               ORDER BY alias""",
            (project_id, platform),
        )
        rows = cursor.fetchall()
        conn.close()
        return [self._row_to_dict(r) for r in rows]

    def update_element(
        self,
        element_id: int,
        **kwargs,
    ) -> bool:
        """更新元素"""
        allowed = {
            "selector_type", "selector_value", "semantic_desc",
            "visual_template_path", "heuristic_selector",
        }
        updates = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
        if not updates:
            return False

        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values())

        conn = self._db._sqlite_connect()
        cursor = conn.cursor()
        cursor.execute(
            f"UPDATE element_repository SET {set_clause}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (*values, element_id),
        )
        conn.commit()
        affected = cursor.rowcount
        conn.close()
        return affected > 0

    def delete_element(self, element_id: int) -> bool:
        """删除元素"""
        conn = self._db._sqlite_connect()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM element_repository WHERE id = ?", (element_id,))
        conn.commit()
        affected = cursor.rowcount
        conn.close()
        return affected > 0

    def resolve_locator(
        self,
        project_id: int,
        alias: str,
        platform: str = "android",
    ) -> Optional[LocatorInfo]:
        """
        从仓库解析为 LocatorInfo。

        优先级: heuristic_selector > selector_value > locator_candidates
        """
        elem = self.get_element(project_id, alias, platform)
        if not elem:
            return None

        strat = elem.get("selector_type", LocatorStrategy.ACCESSIBILITY_ID)
        value = elem.get("selector_value", "")

        # 优先使用自愈后的定位符
        healed = elem.get("heuristic_selector") or ""
        if healed:
            value = healed
            strat = LocatorStrategy.TEXT  # 自愈定位符通常是 text

        # 解析候选定位符
        candidates_raw = elem.get("locator_candidates") or "[]"
        try:
            candidates = json.loads(candidates_raw) if isinstance(candidates_raw, str) else candidates_raw
        except json.JSONDecodeError:
            candidates = []

        fallback_values = [c.get("value", "") for c in candidates]

        return LocatorInfo(
            strategy=strat,
            value=value,
            semantic_desc=elem.get("semantic_desc", ""),
            visual_template_path=elem.get("visual_template_path", ""),
            fallback_values=fallback_values,
        )

    # ------------------------------------------------------------------
    # 自愈审计日志
    # ------------------------------------------------------------------

    def _ensure_heal_history_table(self) -> None:
        conn = self._db._sqlite_connect()
        cursor = conn.cursor()
        cursor.execute(
            """CREATE TABLE IF NOT EXISTS heal_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                element_alias TEXT NOT NULL,
                platform TEXT NOT NULL DEFAULT 'android',
                case_id INTEGER,
                step_index INTEGER,
                original_selector TEXT,
                original_strategy TEXT,
                healed_selector TEXT,
                healed_strategy TEXT,
                confidence REAL DEFAULT 0.0,
                verified INTEGER DEFAULT 0,
                status TEXT DEFAULT 'healed',
                error_message TEXT,
                healed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        conn.commit()
        conn.close()

    def record_heal_history(
        self,
        project_id: int,
        alias: str,
        original_strategy: str,
        original_value: str,
        healed_strategy: str,
        healed_value: str,
        confidence: float = 0.0,
        case_id: Optional[int] = None,
        step_index: Optional[int] = None,
        platform: str = "android",
    ) -> int:
        self._ensure_heal_history_table()
        conn = self._db._sqlite_connect()
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO heal_history
               (project_id, element_alias, platform, case_id, step_index,
                original_selector, original_strategy, healed_selector,
                healed_strategy, confidence)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (project_id, alias, platform, case_id, step_index,
             original_value, original_strategy, healed_value,
             healed_strategy, confidence),
        )
        history_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return history_id

    def mark_heal_verified(self, history_id: int, success: bool, error_message: str = "") -> None:
        self._ensure_heal_history_table()
        conn = self._db._sqlite_connect()
        cursor = conn.cursor()
        cursor.execute(
            """UPDATE heal_history
               SET verified = ?, status = ?, error_message = ?
               WHERE id = ?""",
            (1 if success else 0, "verified" if success else "failed", error_message, history_id),
        )
        conn.commit()
        conn.close()

    def get_heal_history(
        self,
        project_id: int,
        alias: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        self._ensure_heal_history_table()
        conn = self._db._sqlite_connect()
        cursor = conn.cursor()
        if alias:
            cursor.execute(
                """SELECT * FROM heal_history
                   WHERE project_id = ? AND element_alias = ?
                   ORDER BY healed_at DESC LIMIT ?""",
                (project_id, alias, limit),
            )
        else:
            cursor.execute(
                """SELECT * FROM heal_history
                   WHERE project_id = ?
                   ORDER BY healed_at DESC LIMIT ?""",
                (project_id, limit),
            )
        rows = cursor.fetchall()
        conn.close()
        columns = [
            "id", "project_id", "element_alias", "platform", "case_id",
            "step_index", "original_selector", "original_strategy",
            "healed_selector", "healed_strategy", "confidence",
            "verified", "status", "error_message", "healed_at",
        ]
        return [dict(zip(columns, r)) for r in rows]

    def get_heal_stats(self, project_id: int) -> Dict[str, Any]:
        self._ensure_heal_history_table()
        conn = self._db._sqlite_connect()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM heal_history WHERE project_id = ?",
            (project_id,),
        )
        total = cursor.fetchone()[0]
        cursor.execute(
            "SELECT COUNT(*) FROM heal_history WHERE project_id = ? AND status = 'verified'",
            (project_id,),
        )
        verified = cursor.fetchone()[0]
        cursor.execute(
            "SELECT COUNT(*) FROM heal_history WHERE project_id = ? AND status = 'failed'",
            (project_id,),
        )
        failed = cursor.fetchone()[0]
        cursor.execute(
            "SELECT AVG(confidence) FROM heal_history WHERE project_id = ?",
            (project_id,),
        )
        avg_row = cursor.fetchone()
        avg_confidence = round(avg_row[0], 4) if avg_row and avg_row[0] else 0.0
        conn.close()
        return {
            "total": total,
            "verified": verified,
            "failed": failed,
            "pending": total - verified - failed,
            "avg_confidence": avg_confidence,
            "success_rate": round(verified / total, 4) if total > 0 else 0.0,
        }

    def record_healed_locator(
        self,
        project_id: int,
        alias: str,
        healed_strategy: str,
        healed_value: str,
        confidence: float,
        platform: str = "android",
    ) -> bool:
        """记录自愈后的定位符"""
        elem = self.get_element(project_id, alias, platform)
        if not elem:
            return False

        candidates_raw = elem.get("locator_candidates") or "[]"
        try:
            candidates = json.loads(candidates_raw) if isinstance(candidates_raw, str) else candidates_raw
        except json.JSONDecodeError:
            candidates = []

        # 添加/更新候选
        entry = {
            "strategy": healed_strategy,
            "value": healed_value,
            "confidence": confidence,
            "last_used": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        # 去重更新
        existing = [c for c in candidates if c.get("value") == healed_value]
        if existing:
            existing[0].update(entry)
        else:
            candidates.insert(0, entry)
        # 最多保留 10 个候选
        candidates = candidates[:10]

        conn = self._db._sqlite_connect()
        cursor = conn.cursor()
        cursor.execute(
            """UPDATE element_repository
               SET heuristic_selector = ?,
                   locator_candidates = ?,
                   last_success_at = CURRENT_TIMESTAMP,
                   success_count = COALESCE(success_count, 0) + 1,
                   updated_at = CURRENT_TIMESTAMP
               WHERE project_id = ? AND alias = ? AND platform = ?""",
            (healed_value, json.dumps(candidates), project_id, alias, platform),
        )
        conn.commit()
        affected = cursor.rowcount
        conn.close()

        # 记录审计日志
        original_strategy = elem.get("selector_type", "")
        original_value = elem.get("selector_value", "")
        self.record_heal_history(
            project_id=project_id,
            alias=alias,
            original_strategy=original_strategy,
            original_value=original_value,
            healed_strategy=healed_strategy,
            healed_value=healed_value,
            confidence=confidence,
            platform=platform,
        )

        return affected > 0

    # ------------------------------------------------------------------
    # 远程同步
    # ------------------------------------------------------------------

    def sync_from_remote(
        self,
        project_id: int,
        remote_url: str,
        api_key: str = "",
    ) -> Dict[str, Any]:
        """
        从远程服务器同步元素仓库。

        Args:
            project_id: 项目 ID
            remote_url: 远程 API URL
            api_key: API 密钥

        Returns:
            {"added": int, "updated": int, "errors": [...]}
        """
        try:
            import requests

            headers = {"Content-Type": "application/json"}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"

            resp = requests.get(
                f"{remote_url.rstrip('/')}/api/elements/{project_id}",
                headers=headers,
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            elements = data.get("elements", [])
        except Exception as exc:
            return {"added": 0, "updated": 0, "errors": [str(exc)]}

        added = 0
        updated = 0
        errors = []

        for elem in elements:
            try:
                existing = self.get_element(
                    project_id,
                    elem.get("alias", ""),
                    elem.get("platform", "android"),
                )
                if existing:
                    self.update_element(
                        existing["id"],
                        selector_type=elem.get("selector_type"),
                        selector_value=elem.get("selector_value"),
                        semantic_desc=elem.get("semantic_desc"),
                        visual_template_path=elem.get("visual_template_path"),
                    )
                    updated += 1
                else:
                    self.add_element(
                        project_id,
                        elem.get("alias", ""),
                        elem.get("selector_type", "accessibility_id"),
                        elem.get("selector_value", ""),
                        platform=elem.get("platform", "android"),
                        semantic_desc=elem.get("semantic_desc", ""),
                        visual_template_path=elem.get("visual_template_path", ""),
                    )
                    added += 1
            except Exception as exc:
                errors.append(f"{elem.get('alias', '?')}: {exc}")

        return {"added": added, "updated": updated, "errors": errors}

    def export_to_json(
        self,
        project_id: int,
        platform: str = "android",
    ) -> str:
        """导出元素仓库为 JSON"""
        elements = self.get_elements_by_project(project_id, platform)
        export = {
            "project_id": project_id,
            "platform": platform,
            "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "elements": [
                {
                    "alias": e.get("alias"),
                    "selector_type": e.get("selector_type"),
                    "selector_value": e.get("selector_value"),
                    "semantic_desc": e.get("semantic_desc"),
                    "heuristic_selector": e.get("heuristic_selector"),
                }
                for e in elements
            ],
        }
        return json.dumps(export, ensure_ascii=False, indent=2)

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_dict(row) -> Dict[str, Any]:
        """将 SQLite 行转为 dict"""
        # SQLite 返回 tuple, 需要按列名映射
        columns = [
            "id", "project_id", "alias", "platform",
            "selector_type", "selector_value", "attributes_json",
            "created_at", "updated_at",
        ]
        d = dict(zip(columns, row))
        return d
