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
                """
                CREATE TABLE IF NOT EXISTS conversations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    chat_data TEXT NOT NULL,
                    created_date TEXT NOT NULL,
                    closed_date TEXT NOT NULL,
                    summary_generated INTEGER DEFAULT 0,
                    original_id INTEGER DEFAULT NULL
                )
            """,
            )
            conn.commit()

    def store_conversation(self, title: str, chat_data: Dict[str, Any]) -> int:
        """Store a conversation in the database."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            # Get created_date from chat_data if available, otherwise use current time
            created_date = datetime.now().isoformat()
            if "created_date" in chat_data:
                created_date = chat_data["created_date"]

            # Check if this conversation has an original_conversation_id (meaning it was revived)
            original_id = chat_data.get("original_conversation_id")

            if original_id:
                # This is a revived conversation being closed again - update the existing record
                cursor.execute(
                    """
                    UPDATE conversations
                    SET chat_data = ?, closed_date = ?, summary_generated = ?
                    WHERE id = ?
                """,
                    (
                        json.dumps(chat_data),
                        datetime.now().isoformat(),
                        int(chat_data.get("summary_generated", False)),
                        original_id,
                    ),
                )

                if cursor.rowcount > 0:
                    conn.commit()
                    return original_id
                else:
                    # Original record not found, create new one
                    print(
                        f"Warning: Original conversation {original_id} not found, creating new record",
                    )

            # Create new conversation record
            cursor.execute(
                """
                INSERT INTO conversations (title, chat_data, created_date, closed_date, summary_generated, original_id)
                VALUES (?, ?, ?, ?, ?, ?)
            """,
                (
                    title,
                    json.dumps(chat_data),
                    created_date,
                    datetime.now().isoformat(),
                    int(chat_data.get("summary_generated", False)),
                    original_id,
                ),
            )

            conversation_id = cursor.lastrowid
            conn.commit()
            return conversation_id

    def get_conversations(self) -> List[Tuple[int, str, str, str, bool]]:
        """Get all conversations ordered by closed_date descending."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, title, created_date, closed_date, summary_generated
                FROM conversations
                ORDER BY closed_date DESC
            """,
            )
            return cursor.fetchall()

    def get_conversation(self, conversation_id: int) -> Optional[Dict[str, Any]]:
        """Get a specific conversation by ID."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT chat_data FROM conversations WHERE id = ?
            """,
                (conversation_id,),
            )

            result = cursor.fetchone()
            if result:
                chat_data = json.loads(result[0])
                # Add the original conversation ID so we can track it when it's closed again
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
    ) -> List[Tuple[int, str, str, str, bool]]:
        """Search conversations by title."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, title, created_date, closed_date, summary_generated
                FROM conversations
                WHERE title LIKE ?
                ORDER BY closed_date DESC
            """,
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
