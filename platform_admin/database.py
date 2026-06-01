# -*- coding: utf-8 -*-
"""创始人控制面 SQLite 存储。"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


def _db_path() -> str:
    raw = (os.environ.get("PLATFORM_ADMIN_DB") or "").strip()
    if raw:
        return raw
    base = Path(__file__).resolve().parent.parent / "data"
    base.mkdir(parents=True, exist_ok=True)
    return str(base / "platform_admin.db")


class PlatformAdminDB:
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or _db_path()
        self._init_db()

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        conn = self._connect()
        cur = conn.cursor()
        cur.executescript(
            """
            CREATE TABLE IF NOT EXISTS admin_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS licenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                license_id TEXT NOT NULL UNIQUE,
                license_key TEXT NOT NULL,
                license_type TEXT NOT NULL,
                issued_to TEXT NOT NULL,
                binding_type TEXT,
                binding_id TEXT,
                expires_at TEXT,
                revoked INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS license_activations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                license_id TEXT NOT NULL,
                binding_type TEXT,
                binding_id TEXT NOT NULL,
                activated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS releases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                version TEXT NOT NULL,
                channel TEXT NOT NULL DEFAULT 'stable',
                download_url TEXT NOT NULL,
                sha256 TEXT,
                download_count INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS download_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                release_id INTEGER,
                ip TEXT,
                user_agent TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (release_id) REFERENCES releases (id)
            );
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_no TEXT NOT NULL UNIQUE,
                customer_name TEXT,
                plan_type TEXT NOT NULL,
                amount REAL NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'pending',
                license_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS instances (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                instance_id TEXT NOT NULL UNIQUE,
                company_name TEXT,
                deployment_type TEXT DEFAULT 'self_hosted',
                last_seen_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS product_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                external_user_id INTEGER NOT NULL,
                username TEXT NOT NULL,
                email TEXT,
                team_server_url TEXT,
                license_type TEXT DEFAULT 'free',
                last_login_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(external_user_id, team_server_url)
            );
            """
        )
        self._migrate_platform_admin(cur)
        conn.commit()
        conn.close()

    def _migrate_platform_admin(self, cur) -> None:
        for sql in (
            "ALTER TABLE releases ADD COLUMN local_filename TEXT",
            "ALTER TABLE releases ADD COLUMN original_filename TEXT",
            "ALTER TABLE releases ADD COLUMN file_size_bytes INTEGER DEFAULT 0",
            "ALTER TABLE orders ADD COLUMN user_id INTEGER",
            "ALTER TABLE orders ADD COLUMN username TEXT",
            "ALTER TABLE orders ADD COLUMN email TEXT",
            "ALTER TABLE orders ADD COLUMN period TEXT DEFAULT 'monthly'",
        ):
            try:
                cur.execute(sql)
            except sqlite3.OperationalError:
                pass

    def ensure_admin(self, username: str, password_hash: str) -> None:
        conn = self._connect()
        cur = conn.cursor()
        cur.execute("SELECT id FROM admin_users WHERE username = ?", (username,))
        row = cur.fetchone()
        if row:
            cur.execute(
                "UPDATE admin_users SET password_hash = ? WHERE username = ?",
                (password_hash, username),
            )
        else:
            cur.execute(
                "INSERT INTO admin_users (username, password_hash) VALUES (?, ?)",
                (username, password_hash),
            )
        conn.commit()
        conn.close()

    def get_admin_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        conn = self._connect()
        cur = conn.cursor()
        cur.execute("SELECT * FROM admin_users WHERE username = ?", (username,))
        row = cur.fetchone()
        conn.close()
        return dict(row) if row else None

    def insert_license(
        self,
        license_id: str,
        license_key: str,
        license_type: str,
        issued_to: str,
        binding_type: str = "",
        binding_id: str = "",
        expires_at: str = "",
    ) -> int:
        conn = self._connect()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO licenses (license_id, license_key, license_type, issued_to, binding_type, binding_id, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (license_id, license_key, license_type, issued_to, binding_type, binding_id, expires_at),
        )
        lid = int(cur.lastrowid)
        conn.commit()
        conn.close()
        return lid

    def list_licenses(self, limit: int = 200) -> List[Dict[str, Any]]:
        conn = self._connect()
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM licenses ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows

    def revoke_license(self, license_id: str) -> bool:
        conn = self._connect()
        cur = conn.cursor()
        cur.execute("UPDATE licenses SET revoked = 1 WHERE license_id = ?", (license_id,))
        ok = cur.rowcount > 0
        conn.commit()
        conn.close()
        return ok

    def is_license_revoked(self, license_id: str) -> bool:
        conn = self._connect()
        cur = conn.cursor()
        cur.execute("SELECT revoked FROM licenses WHERE license_id = ?", (license_id,))
        row = cur.fetchone()
        conn.close()
        return bool(row and row[0])

    def record_activation(self, license_id: str, binding_type: str, binding_id: str) -> None:
        conn = self._connect()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO license_activations (license_id, binding_type, binding_id)
            VALUES (?, ?, ?)
            """,
            (license_id, binding_type, binding_id),
        )
        conn.commit()
        conn.close()

    def list_revoked_license_ids(self) -> List[str]:
        conn = self._connect()
        cur = conn.cursor()
        cur.execute("SELECT license_id FROM licenses WHERE revoked = 1")
        ids = [r[0] for r in cur.fetchall()]
        conn.close()
        return ids

    def insert_release(
        self,
        version: str,
        download_url: str = "",
        channel: str = "stable",
        sha256: str = "",
        local_filename: str = "",
        original_filename: str = "",
        file_size_bytes: int = 0,
    ) -> int:
        conn = self._connect()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO releases (version, channel, download_url, sha256, local_filename, original_filename, file_size_bytes)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (version, channel, download_url, sha256, local_filename, original_filename, int(file_size_bytes or 0)),
        )
        rid = int(cur.lastrowid)
        conn.commit()
        conn.close()
        return rid

    def update_release_file(self, release_id: int, local_filename: str, original_filename: str, file_size_bytes: int) -> None:
        conn = self._connect()
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE releases
            SET local_filename = ?, original_filename = ?, file_size_bytes = ?
            WHERE id = ?
            """,
            (local_filename, original_filename, int(file_size_bytes or 0), release_id),
        )
        conn.commit()
        conn.close()

    def get_release(self, release_id: int) -> Optional[Dict[str, Any]]:
        conn = self._connect()
        cur = conn.cursor()
        cur.execute("SELECT * FROM releases WHERE id = ?", (release_id,))
        row = cur.fetchone()
        conn.close()
        return dict(row) if row else None

    def get_latest_release(self) -> Optional[Dict[str, Any]]:
        conn = self._connect()
        cur = conn.cursor()
        cur.execute("SELECT * FROM releases ORDER BY id DESC LIMIT 1")
        row = cur.fetchone()
        conn.close()
        return dict(row) if row else None

    def list_releases(self) -> List[Dict[str, Any]]:
        conn = self._connect()
        cur = conn.cursor()
        cur.execute("SELECT * FROM releases ORDER BY id DESC")
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows

    def record_download(self, release_id: int, ip: str = "", user_agent: str = "") -> None:
        conn = self._connect()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO download_events (release_id, ip, user_agent) VALUES (?, ?, ?)",
            (release_id, ip, user_agent),
        )
        cur.execute(
            "UPDATE releases SET download_count = download_count + 1 WHERE id = ?",
            (release_id,),
        )
        conn.commit()
        conn.close()

    def download_stats(self) -> Dict[str, Any]:
        conn = self._connect()
        cur = conn.cursor()
        cur.execute("SELECT COALESCE(SUM(download_count), 0) FROM releases")
        total = int(cur.fetchone()[0])
        cur.execute("SELECT COUNT(*) FROM download_events")
        events = int(cur.fetchone()[0])
        conn.close()
        return {"total_downloads": total, "download_events": events}

    def insert_order(
        self,
        order_no: str,
        customer_name: str,
        plan_type: str,
        amount: float,
        status: str = "pending",
        license_id: str = "",
        user_id: Optional[int] = None,
        username: str = "",
        email: str = "",
        period: str = "monthly",
    ) -> int:
        conn = self._connect()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO orders (order_no, customer_name, plan_type, amount, status, license_id, user_id, username, email, period)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (order_no, customer_name, plan_type, amount, status, license_id, user_id, username, email, period),
        )
        oid = int(cur.lastrowid)
        conn.commit()
        conn.close()
        return oid

    def update_order_status(self, order_no: str, status: str, license_id: str = "") -> bool:
        conn = self._connect()
        cur = conn.cursor()
        if license_id:
            cur.execute(
                "UPDATE orders SET status = ?, license_id = ? WHERE order_no = ?",
                (status, license_id, order_no),
            )
        else:
            cur.execute("UPDATE orders SET status = ? WHERE order_no = ?", (status, order_no))
        ok = cur.rowcount > 0
        conn.commit()
        conn.close()
        return ok

    def get_order(self, order_no: str) -> Optional[Dict[str, Any]]:
        conn = self._connect()
        cur = conn.cursor()
        cur.execute("SELECT * FROM orders WHERE order_no = ?", (order_no,))
        row = cur.fetchone()
        conn.close()
        return dict(row) if row else None

    def upsert_product_user(
        self,
        external_user_id: int,
        username: str,
        email: str = "",
        team_server_url: str = "",
        license_type: str = "free",
    ) -> int:
        conn = self._connect()
        cur = conn.cursor()
        now = datetime.now().isoformat()
        cur.execute(
            """
            SELECT id FROM product_users
            WHERE external_user_id = ? AND IFNULL(team_server_url, '') = ?
            """,
            (external_user_id, team_server_url or ""),
        )
        row = cur.fetchone()
        if row:
            uid = int(row[0])
            cur.execute(
                """
                UPDATE product_users
                SET username = ?, email = ?, license_type = ?, last_login_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (username, email, license_type, now, now, uid),
            )
        else:
            cur.execute(
                """
                INSERT INTO product_users (external_user_id, username, email, team_server_url, license_type, last_login_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (external_user_id, username, email, team_server_url, license_type, now, now),
            )
            uid = int(cur.lastrowid)
        conn.commit()
        conn.close()
        return uid

    def list_product_users(self, limit: int = 500) -> List[Dict[str, Any]]:
        conn = self._connect()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT * FROM product_users ORDER BY COALESCE(last_login_at, updated_at, created_at) DESC LIMIT ?
            """,
            (limit,),
        )
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows

    def count_product_users(self) -> int:
        conn = self._connect()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM product_users")
        n = int(cur.fetchone()[0])
        conn.close()
        return n

    def list_orders(self, limit: int = 200) -> List[Dict[str, Any]]:
        conn = self._connect()
        cur = conn.cursor()
        cur.execute("SELECT * FROM orders ORDER BY id DESC LIMIT ?", (limit,))
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows

    def dashboard_stats(self) -> Dict[str, Any]:
        conn = self._connect()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM licenses WHERE revoked = 0")
        active_licenses = int(cur.fetchone()[0])
        cur.execute("SELECT COUNT(*) FROM license_activations")
        activations = int(cur.fetchone()[0])
        cur.execute("SELECT COUNT(*) FROM orders")
        orders = int(cur.fetchone()[0])
        cur.execute("SELECT COALESCE(SUM(amount), 0) FROM orders WHERE status = 'paid'")
        revenue = float(cur.fetchone()[0])
        conn.close()
        dl = self.download_stats()
        return {
            "active_licenses": active_licenses,
            "activations": activations,
            "orders": orders,
            "revenue": revenue,
            "product_users": self.count_product_users(),
            **dl,
        }
