import json
import os
import sqlite3
from datetime import datetime
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple


class ConversationDatabase:
    def __init__(self, db_path: str = "conversations.db"):
        self.db_path = db_path
        self.init_database()

    def init_database(self) -> None:
        """Initialize the database with the conversations table."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "\n                CREATE TABLE IF NOT EXISTS conversations (\n                    id INTEGER PRIMARY KEY AUTOINCREMENT,\n                    title TEXT NOT NULL,\n                    chat_data TEXT NOT NULL,\n                    created_date TEXT NOT NULL,\n                    closed_date TEXT NOT NULL,\n                    summary_generated INTEGER DEFAULT 0,\n                    original_id INTEGER DEFAULT NULL\n                )\n            ",
            )
            conn.commit()

    def get_conversations(self) -> list[tuple[int, str, str, str, bool]]:
        """Get all conversations ordered by closed_date descending."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "\n                SELECT id, title, created_date, closed_date, summary_generated\n                FROM conversations\n                ORDER BY closed_date DESC\n            ",
            )
            return cursor.fetchall()

    def get_conversation(self, conversation_id: int) -> dict[str, Any] | None:
        """Get a specific conversation by ID."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "\n                SELECT chat_data FROM conversations WHERE id = ?\n            ",
                (conversation_id,),
            )
            result = cursor.fetchone()
            if result:
                chat_data = json.loads(result[0])
                chat_data["original_conversation_id"] = conversation_id
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
    ) -> list[tuple[int, str, str, str, bool]]:
        """Search conversations by title."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "\n                SELECT id, title, created_date, closed_date, summary_generated\n                FROM conversations\n                WHERE title LIKE ?\n                ORDER BY closed_date DESC\n            ",
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

    def store_conversation(self, title: str, chat_data: dict[str, Any]) -> int:
        """Store a conversation in the database.

        This method now properly handles large conversations by ensuring
        the full JSON data is stored without truncation.
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            created_date = datetime.now().isoformat()
            if "created_date" in chat_data:
                created_date = chat_data["created_date"]
            original_id = chat_data.get("original_conversation_id")
            json_data = json.dumps(chat_data, ensure_ascii=False)
            data_size = len(json_data)
            print(f"DEBUG: Storing conversation with {data_size} bytes of JSON data")
            if original_id:
                cursor.execute(
                    "\n                UPDATE conversations\n                SET chat_data = ?, closed_date = ?, summary_generated = ?\n                WHERE id = ?\n            ",
                    (
                        json_data,
                        datetime.now().isoformat(),
                        int(chat_data.get("summary_generated", False)),
                        original_id,
                    ),
                )
                if cursor.rowcount > 0:
                    conn.commit()
                    print(
                        f"DEBUG: Updated conversation {original_id} with {data_size} bytes",
                    )
                    return original_id
                else:
                    print(
                        f"Warning: Original conversation {original_id} not found, creating new record",
                    )
            cursor.execute(
                "\n            INSERT INTO conversations (title, chat_data, created_date, closed_date, summary_generated, original_id)\n            VALUES (?, ?, ?, ?, ?, ?)\n        ",
                (
                    title,
                    json_data,
                    created_date,
                    datetime.now().isoformat(),
                    int(chat_data.get("summary_generated", False)),
                    original_id,
                ),
            )
            conversation_id = cursor.lastrowid
            conn.commit()
            print(
                f"DEBUG: Stored new conversation {conversation_id} with {data_size} bytes",
            )
            return conversation_id
