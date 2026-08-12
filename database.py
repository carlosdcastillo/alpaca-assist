import json
import logging
import os
import sqlite3
from datetime import datetime
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

CONVERSATIONS_DB: str = "conversations.db"

logger = logging.getLogger(__name__)


class ConversationDatabase:
    def __init__(self, db_path: str = CONVERSATIONS_DB):
        self.db_path = db_path
        self.init_database()

    def init_database(self) -> None:
        """Initialize the database with the conversations table."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS conversations (
                    id INTEGER PRIMARY KEY,
                    title TEXT NOT NULL,
                    chat_data TEXT NOT NULL,
                    created_date TEXT NOT NULL,
                    closed_date TEXT NOT NULL,
                    summary_generated INTEGER DEFAULT 0,
                    original_id INTEGER DEFAULT NULL
                )
                """,
            )
            # Added after the table already shipped — migrate existing DBs
            # rather than relying on CREATE TABLE, which only runs once.
            # A plain column (rather than parsing chat_data's JSON blob on
            # every history list load, as find_conversation_by_tab_id
            # already warns against for hot paths) so History can show
            # which conversations are Pack tabs without a full scan.
            existing_columns = {
                row[1] for row in cursor.execute("PRAGMA table_info(conversations)")
            }
            if "tab_type" not in existing_columns:
                cursor.execute("ALTER TABLE conversations ADD COLUMN tab_type TEXT")
                # One-time backfill for rows stored before this column
                # existed — a full-table JSON parse is fine here (runs
                # once per DB, not on every history load).
                for row_id, chat_data_json in cursor.execute(
                    "SELECT id, chat_data FROM conversations",
                ).fetchall():
                    try:
                        tab_type = json.loads(chat_data_json).get("tab_type")
                    except json.JSONDecodeError:
                        continue
                    if tab_type:
                        cursor.execute(
                            "UPDATE conversations SET tab_type = ? WHERE id = ?",
                            (tab_type, row_id),
                        )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS sequences (
                    name TEXT PRIMARY KEY,
                    next_val INTEGER NOT NULL DEFAULT 1
                )
                """,
            )
            # Seed so the sequence never collides with existing rows.
            cursor.execute(
                """
                INSERT OR IGNORE INTO sequences (name, next_val)
                VALUES ('conversation',
                        (SELECT COALESCE(MAX(id), 0) + 1 FROM conversations))
                """,
            )
            conn.commit()

    def allocate_conversation_id(self) -> int:
        """Atomically reserve the next conversation ID from the sequences table."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE sequences SET next_val = next_val + 1 WHERE name = 'conversation'",
            )
            cursor.execute(
                "SELECT next_val - 1 FROM sequences WHERE name = 'conversation'",
            )
            row = cursor.fetchone()
            conn.commit()
            if row is None:
                raise RuntimeError("Failed to allocate conversation ID")
            return int(row[0])

    def get_conversations(self) -> list[tuple[int, str, str, str, bool, str | None]]:
        """Get all conversations ordered by closed_date descending."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "\n                SELECT id, title, created_date, closed_date, summary_generated, tab_type\n                FROM conversations\n                ORDER BY closed_date DESC\n            ",
            )
            return cursor.fetchall()

    def get_conversation(self, conversation_id: int) -> dict[str, Any] | None:
        """Get a specific conversation by ID.

        Returns the chat_data dict with additional keys:
        - 'conversation_id': the stable database conversation ID
        - 'title': the conversation title from the database
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT title, chat_data FROM conversations WHERE id = ?",
                (conversation_id,),
            )
            result = cursor.fetchone()
            if result:
                title, chat_data_json = result
                chat_data: dict[str, Any] = json.loads(chat_data_json)
                chat_data["conversation_id"] = conversation_id
                chat_data["title"] = title
                return chat_data
            return None

    def delete_conversation(self, conversation_id: int) -> bool:
        """Delete a conversation from the database."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
            deleted = cursor.rowcount > 0
            conn.commit()
            return deleted

    def search_conversations(
        self,
        search_term: str,
    ) -> list[tuple[int, str, str, str, bool, str | None]]:
        """Search conversations by title."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "\n                SELECT id, title, created_date, closed_date, summary_generated, tab_type\n                FROM conversations\n                WHERE title LIKE ?\n                ORDER BY closed_date DESC\n            ",
                (f"%{search_term}%",),
            )
            return cursor.fetchall()

    def conversation_exists(self, conversation_id: int) -> bool:
        """Check if a conversation exists in the database."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT 1 FROM conversations WHERE id = ? LIMIT 1",
                (conversation_id,),
            )
            return cursor.fetchone() is not None

    def find_conversation_by_tab_id(self, tab_id: str) -> int | None:
        """Find a conversation ID by the tab_id stored in its chat_data.

        NOTE: This performs a full table scan with JSON parsing for each row.
        This is acceptable for the raven:// link use case (rare operation, small DB),
        but should not be used in a hot path. If the DB grows large, consider adding
        a dedicated tab_id column or a SQLite JSON index.

        Args:
            tab_id: The tab ID to search for

        Returns:
            The conversation ID if found, None otherwise
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            # Full table scan - acceptable for rare raven:// link clicks
            cursor.execute(
                "SELECT id, chat_data FROM conversations",
            )
            for row in cursor.fetchall():
                conv_id, chat_data_json = row
                try:
                    chat_data: dict[str, Any] = json.loads(chat_data_json)
                    # tab_id is stored at top level of serialized chat_data
                    if chat_data.get("tab_id") == tab_id:
                        return int(conv_id)
                except json.JSONDecodeError:
                    continue
            return None

    def store_conversation(
        self,
        conversation_id: int,
        title: str,
        chat_data: dict[str, Any],
    ) -> int:
        """Upsert a conversation using its pre-allocated permanent ID.

        The created_date is preserved if the row already exists so that
        reviving and re-closing a conversation does not reset its age.
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            fallback_date = chat_data.get("created_date", datetime.now().isoformat())
            json_data = json.dumps(chat_data, ensure_ascii=False)
            logger.debug(
                f"Storing conversation {conversation_id} with {len(json_data)} bytes",
            )
            cursor.execute(
                """
                INSERT OR REPLACE INTO conversations
                    (id, title, chat_data, created_date, closed_date, summary_generated, tab_type)
                VALUES (
                    ?,
                    ?,
                    ?,
                    COALESCE((SELECT created_date FROM conversations WHERE id = ?), ?),
                    ?,
                    ?,
                    ?
                )
                """,
                (
                    conversation_id,
                    title,
                    json_data,
                    conversation_id,
                    fallback_date,
                    datetime.now().isoformat(),
                    int(chat_data.get("summary_generated", False)),
                    chat_data.get("tab_type"),
                ),
            )
            conn.commit()
            logger.debug(f"Stored conversation {conversation_id}")
            return conversation_id
