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

_HISTORY_METADATA_COLUMNS = {
    "pinned": "INTEGER NOT NULL DEFAULT 0",
    "folder": "TEXT NOT NULL DEFAULT ''",
    "tags": "TEXT NOT NULL DEFAULT '[]'",
    "archived": "INTEGER NOT NULL DEFAULT 0",
    "search_text": "TEXT NOT NULL DEFAULT ''",
    "preview": "TEXT NOT NULL DEFAULT ''",
    "preview_markdown": "TEXT NOT NULL DEFAULT ''",
}


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
            history_preview_needs_backfill = "preview_markdown" not in existing_columns
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
            for column, definition in _HISTORY_METADATA_COLUMNS.items():
                if column not in existing_columns:
                    cursor.execute(
                        f"ALTER TABLE conversations ADD COLUMN {column} {definition}",
                    )
            history_backfill_where = (
                "" if history_preview_needs_backfill else "WHERE search_text = ''"
            )
            for row_id, chat_data_json in cursor.execute(
                f"SELECT id, chat_data FROM conversations {history_backfill_where}",
            ).fetchall():
                try:
                    chat_data = json.loads(chat_data_json)
                    search_text, preview = self._history_text(chat_data)
                    preview_markdown = self._history_markdown(chat_data)
                except json.JSONDecodeError:
                    continue
                cursor.execute(
                    """
                    UPDATE conversations
                    SET search_text = ?, preview = ?, preview_markdown = ?
                    WHERE id = ?
                    """,
                    (search_text, preview, preview_markdown, row_id),
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
        """Search conversations by title or readable conversation content."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "\n                SELECT id, title, created_date, closed_date, summary_generated, tab_type\n                FROM conversations\n                WHERE title LIKE ? OR search_text LIKE ?\n                ORDER BY closed_date DESC\n            ",
                (f"%{search_term}%", f"%{search_term}%"),
            )
            return cursor.fetchall()

    @staticmethod
    def _history_messages(chat_data: dict[str, Any]) -> list[tuple[str, str]]:
        """Extract only user-visible messages from legacy or graph chat state."""

        def text_content(value: Any) -> str:
            if isinstance(value, str):
                return value
            if not isinstance(value, dict):
                return ""
            components = value.get("components", [])
            return "".join(
                (
                    component
                    if isinstance(component, str)
                    else component.get("content", "")
                )
                for component in components
                if isinstance(component, str)
                or (
                    isinstance(component, dict)
                    and component.get("type") == "text"
                    and isinstance(component.get("content"), str)
                )
            )

        state = chat_data.get("chat_state", chat_data)
        if not isinstance(state, dict):
            return []

        graph = state.get("graph")
        if isinstance(graph, dict):
            nodes = graph.get("nodes", {})
            if not isinstance(nodes, dict):
                return []
            active_path: list[dict[str, Any]] = []
            node_id = graph.get("active_node_id")
            seen: set[str] = set()
            while isinstance(node_id, str) and node_id not in seen:
                seen.add(node_id)
                node = nodes.get(node_id)
                if not isinstance(node, dict):
                    break
                active_path.append(node)
                node_id = node.get("parent_id")
            active_path.reverse()
            return [
                (node["role"], content)
                for node in active_path
                if node.get("role") in {"user", "assistant"}
                and (content := text_content(node.get("content"))).strip()
            ]

        questions = state.get("questions", [])
        answers = state.get("answers", [])
        messages: list[tuple[str, str]] = []
        if not isinstance(questions, list) or not isinstance(answers, list):
            return messages
        for index, question in enumerate(questions):
            if isinstance(question, str) and question.strip():
                messages.append(("user", question))
            if index < len(answers):
                answer = text_content(answers[index])
                if answer.strip():
                    messages.append(("assistant", answer))
        return messages

    @classmethod
    def _history_text(cls, chat_data: dict[str, Any]) -> tuple[str, str]:
        """Build searchable plain text and a compact list-row preview."""
        messages = cls._history_messages(chat_data)
        normalized = " ".join(" ".join(text for _, text in messages).split())
        preview = normalized[:280]
        if len(normalized) > 280:
            preview += "…"
        return normalized, preview

    @classmethod
    def _history_markdown(cls, chat_data: dict[str, Any]) -> str:
        """Build a bounded, markdown-preserving conversation preview."""
        messages = cls._history_messages(chat_data)[-6:]
        sections = [
            f"### {'You' if role == 'user' else 'Assistant'}\n\n{text.strip()}"
            for role, text in messages
        ]
        markdown = "\n\n---\n\n".join(sections)
        if len(markdown) > 12_000:
            markdown = markdown[:12_000].rstrip() + "\n\n…"
        return markdown

    def get_history_records(
        self,
        search_term: str = "",
        folder: str | None = None,
        archived: bool = False,
    ) -> list[dict[str, Any]]:
        """Return rich history records for the history manager UI."""
        where = ["archived = ?"]
        params: list[Any] = [int(archived)]
        if folder is not None:
            where.append("folder = ?")
            params.append(folder)
        if search_term:
            where.append("(title LIKE ? OR search_text LIKE ? OR tags LIKE ?)")
            pattern = f"%{search_term}%"
            params.extend((pattern, pattern, pattern))
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f"""
                SELECT id, title, created_date, closed_date, tab_type,
                       pinned, folder, tags, archived, preview, preview_markdown
                FROM conversations
                WHERE {' AND '.join(where)}
                ORDER BY pinned DESC, closed_date DESC
                """,
                params,
            ).fetchall()
        records = []
        for row in rows:
            record = dict(row)
            record["pinned"] = bool(record["pinned"])
            record["archived"] = bool(record["archived"])
            try:
                record["tags"] = json.loads(record["tags"])
            except (json.JSONDecodeError, TypeError):
                record["tags"] = []
            records.append(record)
        return records

    def get_history_facets(self) -> dict[str, list[str]]:
        with sqlite3.connect(self.db_path) as conn:
            folders = [
                row[0]
                for row in conn.execute(
                    "SELECT DISTINCT folder FROM conversations WHERE folder != '' ORDER BY folder",
                )
            ]
        return {"folders": folders}

    def update_history_metadata(self, conversation_id: int, **changes: Any) -> bool:
        allowed = {"title", "pinned", "folder", "tags", "archived"}
        values = {key: value for key, value in changes.items() if key in allowed}
        if not values:
            return False
        if "tags" in values:
            values["tags"] = json.dumps(values["tags"], ensure_ascii=False)
        with sqlite3.connect(self.db_path) as conn:
            if "title" in values:
                row = conn.execute(
                    "SELECT chat_data FROM conversations WHERE id = ?",
                    (conversation_id,),
                ).fetchone()
                if row:
                    chat_data = json.loads(row[0])
                    chat_data["name"] = values["title"]
                    chat_data["title"] = values["title"]
                    values["chat_data"] = json.dumps(chat_data, ensure_ascii=False)
            assignments = ", ".join(f"{key} = ?" for key in values)
            cursor = conn.execute(
                f"UPDATE conversations SET {assignments} WHERE id = ?",
                (*values.values(), conversation_id),
            )
            conn.commit()
            return cursor.rowcount > 0

    def delete_conversations(self, conversation_ids: list[int]) -> int:
        if not conversation_ids:
            return 0
        placeholders = ",".join("?" for _ in conversation_ids)
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                f"DELETE FROM conversations WHERE id IN ({placeholders})",
                conversation_ids,
            )
            conn.commit()
            return cursor.rowcount

    def export_history(
        self,
        conversation_ids: list[int] | None = None,
    ) -> dict[str, Any]:
        where = ""
        params: list[Any] = []
        if conversation_ids:
            where = f"WHERE id IN ({','.join('?' for _ in conversation_ids)})"
            params = conversation_ids
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f"""SELECT id, title, chat_data, created_date, closed_date,
                            summary_generated, original_id, tab_type, pinned,
                            folder, tags, archived FROM conversations {where}
                     ORDER BY closed_date""",
                params,
            ).fetchall()
        conversations = []
        for row in rows:
            item = dict(row)
            item["chat_data"] = json.loads(item["chat_data"])
            item["tags"] = json.loads(item["tags"] or "[]")
            conversations.append(item)
        return {
            "format": "alpaca-assist-history",
            "version": 1,
            "conversations": conversations,
        }

    def import_history(self, backup: dict[str, Any]) -> int:
        if backup.get("format") != "alpaca-assist-history" or not isinstance(
            backup.get("conversations"),
            list,
        ):
            raise ValueError("Not an Alpaca Assist history backup")
        imported = 0
        for item in backup["conversations"]:
            if not isinstance(item, dict) or not isinstance(
                item.get("chat_data"),
                dict,
            ):
                raise ValueError("Backup contains an invalid conversation")
            conversation_id = self.allocate_conversation_id()
            self.store_conversation(
                conversation_id,
                str(item.get("title") or "Imported conversation"),
                item["chat_data"],
            )
            self.update_history_metadata(
                conversation_id,
                pinned=bool(item.get("pinned")),
                folder=str(item.get("folder") or ""),
                tags=[str(tag) for tag in item.get("tags", [])],
                archived=bool(item.get("archived")),
            )
            imported += 1
        return imported

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
            search_text, preview = self._history_text(chat_data)
            preview_markdown = self._history_markdown(chat_data)
            logger.debug(
                f"Storing conversation {conversation_id} with {len(json_data)} bytes",
            )
            cursor.execute(
                """
                INSERT OR REPLACE INTO conversations
                    (id, title, chat_data, created_date, closed_date, summary_generated,
                     tab_type, pinned, folder, tags, archived, search_text, preview,
                     preview_markdown)
                VALUES (
                    ?,
                    ?,
                    ?,
                    COALESCE((SELECT created_date FROM conversations WHERE id = ?), ?),
                    ?,
                    ?,
                    ?,
                    COALESCE((SELECT pinned FROM conversations WHERE id = ?), 0),
                    COALESCE((SELECT folder FROM conversations WHERE id = ?), ''),
                    COALESCE((SELECT tags FROM conversations WHERE id = ?), '[]'),
                    COALESCE((SELECT archived FROM conversations WHERE id = ?), 0),
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
                    conversation_id,
                    conversation_id,
                    conversation_id,
                    conversation_id,
                    search_text,
                    preview,
                    preview_markdown,
                ),
            )
            conn.commit()
            logger.debug(f"Stored conversation {conversation_id}")
            return conversation_id
