"""
Migration State & Checkpoint Store.

Security Guardrails:
- Zero sensitive payload storage (no raw email bodies, no passwords, no tokens).
- Tracks nonces, resource IDs, SHA-256 checksums, and sync status for idempotent resumability.
- Thread-safe and resilient to interruption.
"""

import sqlite3
import time
import json
from typing import Dict, List, Optional, Tuple, Any
from pathlib import Path


from contextlib import contextmanager


class CheckpointStore:
    """Manages persistent migration state in SQLite."""

    def __init__(self, db_path: str = "migration_checkpoint.db"):
        self.db_path = db_path
        self._init_db()

    @contextmanager
    def _get_conn(self):
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        """Initializes tables and indexes."""
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                cursor = conn.cursor()

                # User provisioning table
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS users (
                        zoho_zuid TEXT PRIMARY KEY,
                        email TEXT UNIQUE NOT NULL,
                        first_name TEXT,
                        last_name TEXT,
                        aliases_json TEXT,
                        status TEXT NOT NULL DEFAULT 'PENDING',
                        error_msg TEXT,
                        created_at REAL NOT NULL
                    )
                """)

                # Items table (Mail, Contacts, Calendar)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS items (
                        entity_type TEXT NOT NULL,
                        source_id TEXT NOT NULL,
                        user_email TEXT NOT NULL,
                        destination_id TEXT,
                        checksum TEXT,
                        status TEXT NOT NULL DEFAULT 'PENDING',
                        error_msg TEXT,
                        synced_at REAL NOT NULL,
                        PRIMARY KEY (entity_type, source_id, user_email)
                    )
                """)

                # Folder / Label mappings
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS folders (
                        user_email TEXT NOT NULL,
                        zoho_folder_id TEXT NOT NULL,
                        folder_name TEXT NOT NULL,
                        google_label_id TEXT,
                        status TEXT NOT NULL DEFAULT 'ACTIVE',
                        PRIMARY KEY (user_email, zoho_folder_id)
                    )
                """)

                # Indexes for high performance
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_items_status ON items (entity_type, status)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_items_user ON items (user_email, entity_type)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_status ON users (status)")
        finally:
            conn.close()

    # --- User Provisioning State ---

    def register_user(self, zuid: str, email: str, first_name: str, last_name: str, aliases: List[str]) -> None:
        """Registers discovered Zoho user into checkpoint DB."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR IGNORE INTO users (zoho_zuid, email, first_name, last_name, aliases_json, status, created_at)
                VALUES (?, ?, ?, ?, ?, 'PENDING', ?)
            """, (zuid, email.lower().strip(), first_name, last_name, json.dumps(aliases), time.time()))
            conn.commit()

    def update_user_status(self, email: str, status: str, error_msg: Optional[str] = None) -> None:
        """Updates user provisioning status."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE users
                SET status = ?, error_msg = ?
                WHERE email = ?
            """, (status, error_msg, email.lower().strip()))
            conn.commit()

    def get_all_users(self) -> List[Dict[str, Any]]:
        """Retrieves all registered users."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users ORDER BY email ASC")
            rows = cursor.fetchall()
            return [
                {
                    "zoho_zuid": r["zoho_zuid"],
                    "email": r["email"],
                    "first_name": r["first_name"],
                    "last_name": r["last_name"],
                    "aliases": json.loads(r["aliases_json"] or "[]"),
                    "status": r["status"],
                    "error_msg": r["error_msg"],
                }
                for r in rows
            ]

    # --- Folder / Label Mapping State ---

    def save_folder_mapping(self, user_email: str, zoho_folder_id: str, folder_name: str, google_label_id: str) -> None:
        """Saves folder mapping."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO folders (user_email, zoho_folder_id, folder_name, google_label_id, status)
                VALUES (?, ?, ?, ?, 'ACTIVE')
            """, (user_email.lower().strip(), zoho_folder_id, folder_name, google_label_id))
            conn.commit()

    def record_folder_mapping(self, user_email: str, zoho_folder_id: str, zoho_folder_name: str, google_label_id: str) -> None:
        """Alias for save_folder_mapping."""
        self.save_folder_mapping(user_email, zoho_folder_id, zoho_folder_name, google_label_id)

    def get_google_label_id(self, user_email: str, zoho_folder_id: str) -> Optional[str]:
        """Gets mapped Google label ID for a Zoho folder."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT google_label_id FROM folders WHERE user_email = ? AND zoho_folder_id = ?
            """, (user_email.lower().strip(), zoho_folder_id))
            row = cursor.fetchone()
            return row["google_label_id"] if row else None

    def get_label_mapping(self, user_email: str, zoho_folder_id: str) -> Optional[str]:
        """Alias for get_google_label_id."""
        return self.get_google_label_id(user_email, zoho_folder_id)

    # --- Item Sync State ---

    def is_item_synced(self, entity_type: str, source_id: str, user_email: str) -> bool:
        """Checks if an item (message, contact, event) is already synced."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT status FROM items
                WHERE entity_type = ? AND source_id = ? AND user_email = ? AND status = 'SYNCED'
            """, (entity_type.upper(), str(source_id), user_email.lower().strip()))
            return cursor.fetchone() is not None

    def record_item_sync(
        self,
        entity_type: str,
        source_id: str,
        user_email: str,
        destination_id: Optional[str] = None,
        status: str = "SYNCED",
        error_msg: Optional[str] = None,
        checksum: Optional[str] = None
    ) -> None:
        """Records sync result for an item."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO items (entity_type, source_id, user_email, destination_id, checksum, status, error_msg, synced_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                entity_type.upper(),
                str(source_id),
                user_email.lower().strip(),
                destination_id,
                checksum,
                status,
                error_msg,
                time.time()
            ))
            conn.commit()

    def get_summary_stats(self) -> Dict[str, Any]:
        """Returns aggregate migration statistics."""
        with self._get_conn() as conn:
            cursor = conn.cursor()

            # User stats
            cursor.execute("SELECT status, COUNT(*) as count FROM users GROUP BY status")
            user_stats = {r["status"]: r["count"] for r in cursor.fetchall()}

            # Item stats by entity_type and status
            cursor.execute("""
                SELECT entity_type, status, COUNT(*) as count
                FROM items
                GROUP BY entity_type, status
            """)
            item_stats = {}
            for r in cursor.fetchall():
                entity = r["entity_type"]
                status = r["status"]
                item_stats.setdefault(entity, {})[status] = r["count"]

            return {
                "users": user_stats,
                "items": item_stats,
            }
