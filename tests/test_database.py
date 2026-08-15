"""
Comprehensive tests for database.py module.

This module tests all database operations including conversation storage,
retrieval, deletion, and searching.
"""
import json
import sqlite3
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from freezegun import freeze_time

from database import ConversationDatabase


def _store(
    db: ConversationDatabase,
    title: str,
    data: dict[str, Any] | None = None,
) -> int:
    """Helper: allocate an ID and store a conversation in one call."""
    cid = db.allocate_conversation_id()
    db.store_conversation(cid, title, data or {})
    return cid


class TestDatabaseInitialization:
    """Tests for database initialization."""

    def test_init_creates_database_file(self, temp_dir: Path) -> None:
        """Test that initialization creates the database file."""
        db_path = temp_dir / "test.db"
        db = ConversationDatabase(str(db_path))
        db.init_database()
        assert db_path.exists()

    def test_init_creates_conversations_table(self, temp_db_path: Path) -> None:
        """Test that initialization creates the conversations table."""
        db = ConversationDatabase(str(temp_db_path))
        db.init_database()

        conn = sqlite3.connect(str(temp_db_path))
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='conversations'",
        )
        result = cursor.fetchone()
        conn.close()

        assert result is not None
        assert result[0] == "conversations"

    def test_init_creates_sequences_table(self, temp_db_path: Path) -> None:
        """Test that initialization creates the sequences table."""
        db = ConversationDatabase(str(temp_db_path))
        db.init_database()

        conn = sqlite3.connect(str(temp_db_path))
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='sequences'",
        )
        result = cursor.fetchone()
        conn.close()

        assert result is not None

    def test_init_idempotent(self, temp_db_path: Path) -> None:
        """Test that initialization is idempotent."""
        db = ConversationDatabase(str(temp_db_path))
        db.init_database()
        db.init_database()  # Should not raise
        assert temp_db_path.exists()


class TestConversationIdAllocation:
    """Tests for the permanent conversation ID sequence."""

    def test_allocate_returns_integer(self, mock_db: ConversationDatabase) -> None:
        cid = mock_db.allocate_conversation_id()
        assert isinstance(cid, int)
        assert cid >= 1

    def test_allocate_increments(self, mock_db: ConversationDatabase) -> None:
        id1 = mock_db.allocate_conversation_id()
        id2 = mock_db.allocate_conversation_id()
        id3 = mock_db.allocate_conversation_id()
        assert id2 == id1 + 1
        assert id3 == id2 + 1

    def test_allocate_seeds_above_existing_rows(self, temp_db_path: Path) -> None:
        """Sequence must start above any pre-existing rows so no collision occurs."""
        # Insert raw rows with known IDs to simulate an existing DB.
        conn = sqlite3.connect(str(temp_db_path))
        conn.execute(
            "CREATE TABLE IF NOT EXISTS conversations "
            "(id INTEGER PRIMARY KEY, title TEXT NOT NULL, chat_data TEXT NOT NULL, "
            "created_date TEXT NOT NULL, closed_date TEXT NOT NULL, "
            "summary_generated INTEGER DEFAULT 0, original_id INTEGER DEFAULT NULL)",
        )
        conn.execute(
            "INSERT INTO conversations VALUES (50, 't', '{}', 'x', 'x', 0, NULL)",
        )
        conn.commit()
        conn.close()

        db = ConversationDatabase(str(temp_db_path))
        first_id = db.allocate_conversation_id()
        assert first_id > 50

    def test_store_with_explicit_id(self, mock_db: ConversationDatabase) -> None:
        """store_conversation must use the pre-allocated ID as the primary key."""
        cid = mock_db.allocate_conversation_id()
        mock_db.store_conversation(cid, "My Chat", {"x": 1})

        result = mock_db.get_conversation(cid)
        assert result is not None
        assert result["conversation_id"] == cid
        assert result["title"] == "My Chat"

    def test_upsert_updates_existing_row(self, mock_db: ConversationDatabase) -> None:
        """Storing twice with the same ID must update, not duplicate."""
        cid = mock_db.allocate_conversation_id()
        mock_db.store_conversation(cid, "First Title", {"v": 1})
        mock_db.store_conversation(cid, "Second Title", {"v": 2})

        assert len(mock_db.get_conversations()) == 1
        result = mock_db.get_conversation(cid)
        assert result is not None
        assert result["title"] == "Second Title"
        assert result["v"] == 2

    def test_upsert_preserves_created_date(self, mock_db: ConversationDatabase) -> None:
        """Re-storing a conversation must not reset its created_date."""
        with freeze_time("2024-01-01 10:00:00"):
            cid = mock_db.allocate_conversation_id()
            mock_db.store_conversation(cid, "Original", {})

        original = mock_db.get_conversations()[0]
        original_created = original[2]  # created_date tuple index

        with freeze_time("2024-06-15 20:00:00"):
            mock_db.store_conversation(cid, "Updated", {})

        updated = mock_db.get_conversations()[0]
        assert updated[2] == original_created  # created_date unchanged


class TestConversationStorage:
    """Tests for conversation storage operations."""

    @freeze_time("2024-01-15 10:30:00")
    def test_store_conversation_creates_record(
        self,
        mock_db: ConversationDatabase,
    ) -> None:
        """Test storing a conversation creates a database record."""
        chat_data = {
            "questions": ["Hello"],
            "answers": [{"components": [{"type": "text", "content": "Hi!"}]}],
        }

        conversation_id = _store(mock_db, "Test Conversation", chat_data)

        assert conversation_id is not None
        assert isinstance(conversation_id, int)
        assert conversation_id > 0

    @freeze_time("2024-01-15 10:30:00")
    def test_store_conversation_saves_correct_data(
        self,
        mock_db: ConversationDatabase,
    ) -> None:
        """Test that stored conversation data is correct."""
        chat_data = {
            "questions": ["Hello", "How are you?"],
            "answers": [
                {"components": [{"type": "text", "content": "Hi!"}]},
                {"components": [{"type": "text", "content": "I'm good!"}]},
            ],
        }

        conversation_id = _store(mock_db, "My Chat", chat_data)

        # get_conversation() merges chat_data keys into the result dict
        # and adds 'conversation_id' and 'title' keys.
        result = mock_db.get_conversation(conversation_id)
        assert result is not None
        assert result["title"] == "My Chat"
        assert result["questions"] == chat_data["questions"]
        assert result["answers"] == chat_data["answers"]

    def test_store_conversation_with_special_characters(
        self,
        mock_db: ConversationDatabase,
    ) -> None:
        """Test storing conversations with special characters in title."""
        chat_data: dict[str, Any] = {"questions": [], "answers": []}
        title = "Test \"Quote\" & <Special> 'Chars'"

        conversation_id = _store(mock_db, title, chat_data)
        result = mock_db.get_conversation(conversation_id)

        assert result is not None
        assert result["title"] == title

    def test_store_conversation_large_data(self, mock_db: ConversationDatabase) -> None:
        """Test storing conversations with large data."""
        chat_data = {
            "questions": [f"Question {i}" for i in range(100)],
            "answers": [
                {"components": [{"type": "text", "content": f"Answer {i}"}]}
                for i in range(100)
            ],
        }

        conversation_id = _store(mock_db, "Large Chat", chat_data)
        result = mock_db.get_conversation(conversation_id)

        assert result is not None
        assert len(result["questions"]) == 100


class TestConversationRetrieval:
    """Tests for conversation retrieval operations."""

    def test_get_conversation_returns_correct_format(
        self,
        mock_db: ConversationDatabase,
    ) -> None:
        """Test that get_conversation returns data in correct format."""
        chat_data = {
            "questions": ["Test"],
            "answers": [{"components": [{"type": "text", "content": "Response"}]}],
        }
        conversation_id = _store(mock_db, "Test", chat_data)

        result = mock_db.get_conversation(conversation_id)

        # get_conversation() returns chat_data merged with 'conversation_id' and 'title'.
        assert isinstance(result, dict)
        assert "conversation_id" in result
        assert result["conversation_id"] == conversation_id
        assert "title" in result
        assert "questions" in result
        assert "answers" in result

    def test_get_conversation_nonexistent_returns_none(
        self,
        mock_db: ConversationDatabase,
    ) -> None:
        """Test that getting a nonexistent conversation returns None."""
        result = mock_db.get_conversation(99999)
        assert result is None

    def test_get_conversation_invalid_id(self, mock_db: ConversationDatabase) -> None:
        """Test that getting a conversation with invalid ID returns None."""
        result = mock_db.get_conversation(-1)
        assert result is None

    def test_get_conversations_returns_list(
        self,
        mock_db: ConversationDatabase,
    ) -> None:
        """Test that get_conversations returns a list."""
        for i in range(3):
            _store(mock_db, f"Chat {i}")

        conversations = mock_db.get_conversations()

        assert isinstance(conversations, list)
        assert len(conversations) == 3

    def test_get_conversations_sorted_by_updated(
        self,
        mock_db: ConversationDatabase,
    ) -> None:
        """Test that conversations are sorted by closed_date descending."""
        with freeze_time("2024-01-15 10:00:00"):
            id1 = _store(mock_db, "First")

        with freeze_time("2024-01-15 11:00:00"):
            id2 = _store(mock_db, "Second")

        with freeze_time("2024-01-15 12:00:00"):
            id3 = _store(mock_db, "Third")

        conversations = mock_db.get_conversations()

        assert conversations[0][0] == id3
        assert conversations[1][0] == id2
        assert conversations[2][0] == id1

    def test_get_conversations_tuple_format(
        self,
        mock_db: ConversationDatabase,
    ) -> None:
        """Test that get_conversations returns correct tuple format."""
        _store(mock_db, "Test Chat", {"questions": ["Hello"], "answers": []})

        conversations = mock_db.get_conversations()

        assert len(conversations) == 1
        conv = conversations[0]
        # Tuple is: (id, title, created_date, closed_date, summary_generated, tab_type)
        assert len(conv) == 6
        assert isinstance(conv[0], int)
        assert isinstance(conv[1], str)
        assert isinstance(conv[2], str)
        assert isinstance(conv[3], str)
        assert isinstance(conv[4], int)
        assert conv[5] is None

    def test_get_conversations_reports_pack_tab_type(
        self,
        mock_db: ConversationDatabase,
    ) -> None:
        """History rows must be able to tell Pack conversations apart from

        regular ones without parsing chat_data's JSON blob on every list
        load (find_conversation_by_tab_id already flags that as unsafe
        for a hot path).
        """
        _store(mock_db, "Local", {"questions": [], "answers": []})
        _store(mock_db, "Remote", {"tab_type": "pack", "host": "user@host"})

        conversations = {conv[1]: conv[5] for conv in mock_db.get_conversations()}

        assert conversations["Local"] is None
        assert conversations["Remote"] == "pack"


class TestConversationDeletion:
    """Tests for conversation deletion operations."""

    def test_delete_conversation_removes_record(
        self,
        mock_db: ConversationDatabase,
    ) -> None:
        """Test that delete_conversation removes the record."""
        conversation_id = _store(mock_db, "To Delete")

        result = mock_db.delete_conversation(conversation_id)

        assert result is True
        assert mock_db.get_conversation(conversation_id) is None

    def test_delete_conversation_nonexistent_returns_false(
        self,
        mock_db: ConversationDatabase,
    ) -> None:
        """Test that deleting a nonexistent conversation returns False."""
        result = mock_db.delete_conversation(99999)
        assert result is False

    def test_delete_conversation_invalid_id(
        self,
        mock_db: ConversationDatabase,
    ) -> None:
        """Test that deleting with invalid ID returns False."""
        result = mock_db.delete_conversation(-1)
        assert result is False

    def test_delete_conversation_does_not_affect_others(
        self,
        mock_db: ConversationDatabase,
    ) -> None:
        """Test that deleting one conversation doesn't affect others."""
        id1 = _store(mock_db, "Keep")
        id2 = _store(mock_db, "Delete")

        mock_db.delete_conversation(id2)

        assert mock_db.get_conversation(id1) is not None
        assert mock_db.get_conversation(id2) is None


class TestConversationSearch:
    """Tests for conversation search operations."""

    def test_search_conversations_finds_by_title(
        self,
        mock_db: ConversationDatabase,
    ) -> None:
        """Test searching by title."""
        _store(mock_db, "Python Tutorial")
        _store(mock_db, "JavaScript Guide")

        results = mock_db.search_conversations("Python")

        assert len(results) == 1
        assert results[0][1] == "Python Tutorial"

    def test_search_conversations_finds_in_content(
        self,
        mock_db: ConversationDatabase,
    ) -> None:
        """Search includes readable conversation content, not only titles."""
        _store(
            mock_db,
            "Language notes",
            {"questions": ["What is Python?"], "answers": []},
        )
        _store(
            mock_db,
            "JavaScript Chat",
            {"questions": ["What is JavaScript?"], "answers": []},
        )

        results = mock_db.search_conversations("Python")

        assert len(results) == 1
        assert results[0][1] == "Language notes"

    def test_history_records_include_preview_and_metadata(
        self,
        mock_db: ConversationDatabase,
    ) -> None:
        conversation_id = _store(
            mock_db,
            "Planning",
            {"questions": ["Plan the database migration"], "answers": []},
        )
        mock_db.update_history_metadata(
            conversation_id,
            pinned=True,
            folder="Work",
            tags=["database", "planning"],
        )

        record = mock_db.get_history_records()[0]

        assert record["preview"] == "Plan the database migration"
        assert record["pinned"] is True
        assert record["folder"] == "Work"
        assert record["tags"] == ["database", "planning"]

    def test_graph_history_preview_excludes_internal_ids_and_preserves_markdown(
        self,
        mock_db: ConversationDatabase,
    ) -> None:
        user_id = "ec7b4cc5-5d40-4ef1-8fdf-60364e45994d"
        assistant_id = "2699effa-3237-405a-96ac-2542fcbf38f6"
        _store(
            mock_db,
            "Markdown notes",
            {
                "tab_id": "tab-5f7a6918-6cd5-4053-821f-a7210995018d",
                "chat_state": {
                    "graph": {
                        "id": "08664096-d317-4ccc-899a-dcfdd2667f4d",
                        "active_node_id": assistant_id,
                        "nodes": {
                            user_id: {
                                "id": user_id,
                                "parent_id": None,
                                "role": "user",
                                "content": {
                                    "components": [
                                        {
                                            "type": "text",
                                            "content": "Make a **list**",
                                        },
                                    ],
                                },
                            },
                            assistant_id: {
                                "id": assistant_id,
                                "parent_id": user_id,
                                "role": "assistant",
                                "content": {
                                    "components": [
                                        {"type": "text", "content": "- One\n- Two"},
                                        {
                                            "type": "tool_result",
                                            "content": "secret internal output",
                                            "id": "tool-id",
                                        },
                                    ],
                                },
                            },
                        },
                    },
                },
            },
        )

        record = mock_db.get_history_records()[0]

        assert record["preview"] == "Make a **list** - One - Two"
        assert record["preview_markdown"] == (
            "### You\n\nMake a **list**\n\n---\n\n### Assistant\n\n- One\n- Two"
        )
        combined = record["preview"] + record["preview_markdown"]
        assert user_id not in combined
        assert assistant_id not in combined
        assert "secret internal output" not in combined

    def test_history_metadata_survives_reclosing_conversation(
        self,
        mock_db: ConversationDatabase,
    ) -> None:
        conversation_id = _store(mock_db, "Original", {"questions": ["First"]})
        mock_db.update_history_metadata(
            conversation_id,
            title="Renamed",
            pinned=True,
            folder="Work",
            tags=["keep"],
            archived=True,
        )

        mock_db.store_conversation(
            conversation_id,
            "Renamed",
            {"questions": ["Updated content"]},
        )

        record = mock_db.get_history_records(archived=True)[0]
        assert record["title"] == "Renamed"
        assert record["pinned"] is True
        assert record["folder"] == "Work"
        assert record["tags"] == ["keep"]
        assert record["preview"] == "Updated content"

    def test_export_import_history_round_trip(
        self,
        temp_dir: Path,
    ) -> None:
        source = ConversationDatabase(str(temp_dir / "source.db"))
        conversation_id = _store(source, "Backup me", {"questions": ["Keep this"]})
        source.update_history_metadata(
            conversation_id,
            folder="Saved",
            tags=["important"],
        )

        target = ConversationDatabase(str(temp_dir / "target.db"))
        imported = target.import_history(source.export_history())

        assert imported == 1
        record = target.get_history_records()[0]
        assert record["title"] == "Backup me"
        assert record["folder"] == "Saved"
        assert record["tags"] == ["important"]
        assert target.search_conversations("Keep this")

    def test_search_conversations_case_insensitive(
        self,
        mock_db: ConversationDatabase,
    ) -> None:
        """Test that search is case insensitive."""
        _store(mock_db, "PYTHON Chat")

        results_lower = mock_db.search_conversations("python")
        results_upper = mock_db.search_conversations("PYTHON")

        assert len(results_lower) == 1
        assert len(results_upper) == 1

    def test_search_conversations_no_results(
        self,
        mock_db: ConversationDatabase,
    ) -> None:
        """Test searching with no matches."""
        _store(mock_db, "Chat 1")

        results = mock_db.search_conversations("nonexistent")

        assert len(results) == 0
        assert isinstance(results, list)

    def test_search_conversations_empty_term(
        self,
        mock_db: ConversationDatabase,
    ) -> None:
        """Test searching with empty term."""
        _store(mock_db, "Chat 1")
        _store(mock_db, "Chat 2")

        results = mock_db.search_conversations("")

        assert len(results) == 2

    def test_search_conversations_special_characters(
        self,
        mock_db: ConversationDatabase,
    ) -> None:
        """Test searching with special characters."""
        _store(mock_db, 'Chat with "quotes"')

        results = mock_db.search_conversations('"quotes"')

        assert len(results) == 1


class TestTabIdLookup:
    """Tests for tab ID lookup operations."""

    def test_find_conversation_by_tab_id(self, mock_db: ConversationDatabase) -> None:
        """Test finding conversation by tab ID."""
        conversation_id = _store(mock_db, "Test", {"tab_id": "tab-123"})

        result = mock_db.find_conversation_by_tab_id("tab-123")

        assert result == conversation_id

    def test_find_conversation_by_tab_id_not_found(
        self,
        mock_db: ConversationDatabase,
    ) -> None:
        """Test finding conversation by nonexistent tab ID."""
        result = mock_db.find_conversation_by_tab_id("nonexistent-tab")
        assert result is None

    def test_find_conversation_by_tab_id_no_tab_id(
        self,
        mock_db: ConversationDatabase,
    ) -> None:
        """Test finding conversation without tab_id field."""
        _store(mock_db, "Test")

        result = mock_db.find_conversation_by_tab_id("any-tab")
        assert result is None


class TestImageDetection:
    """Tests for image data storage in conversations.

    Note: get_conversations() returns tuples of
    (id, title, created_date, closed_date, summary_generated).
    Image data is stored inside chat_data JSON and is accessible via
    get_conversation(), not via the tuple returned by get_conversations().
    """

    def test_image_data_stored_in_chat_data(
        self,
        mock_db: ConversationDatabase,
    ) -> None:
        """Test that image URLs are preserved in stored chat_data."""
        conv_id = _store(
            mock_db,
            "With Images",
            {"question_images": [["http://example.com/image.png"]]},
        )

        result = mock_db.get_conversation(conv_id)

        assert result is not None
        assert result["question_images"] == [["http://example.com/image.png"]]

    def test_no_images_stored_in_chat_data(
        self,
        mock_db: ConversationDatabase,
    ) -> None:
        """Test that conversations without images have no question_images key."""
        conv_id = _store(mock_db, "Without Images")

        result = mock_db.get_conversation(conv_id)

        assert result is not None
        assert "question_images" not in result

    def test_empty_images_stored_in_chat_data(
        self,
        mock_db: ConversationDatabase,
    ) -> None:
        """Test that empty image arrays are preserved in stored chat_data."""
        conv_id = _store(mock_db, "Empty Images", {"question_images": [[], []]})

        result = mock_db.get_conversation(conv_id)

        assert result is not None
        assert result["question_images"] == [[], []]


class TestDatabaseEdgeCases:
    """Tests for edge cases and error handling."""

    def test_store_conversation_empty_title(
        self,
        mock_db: ConversationDatabase,
    ) -> None:
        """Test storing conversation with empty title."""
        conversation_id = _store(mock_db, "")

        result = mock_db.get_conversation(conversation_id)
        assert result is not None
        assert result["title"] == ""

    def test_store_conversation_none_in_data(
        self,
        mock_db: ConversationDatabase,
    ) -> None:
        """Test storing conversation with None values in data."""
        conversation_id = _store(
            mock_db,
            "Test",
            {"questions": ["Test"], "answers": [None]},
        )

        result = mock_db.get_conversation(conversation_id)
        assert result is not None

    def test_database_error_handling(self, temp_dir: Path) -> None:
        """Test database error handling."""
        db_path = temp_dir / "readonly.db"
        db = ConversationDatabase(str(db_path))
        db.init_database()

        conversation_id = _store(db, "Test")
        assert conversation_id is not None

    def test_concurrent_access(self, mock_db: ConversationDatabase) -> None:
        """Test basic concurrent access handling."""
        for i in range(10):
            _store(mock_db, f"Chat {i}")

        conversations = mock_db.get_conversations()
        assert len(conversations) == 10


class TestDatabaseMigration:
    """Tests for database migration scenarios."""

    def test_handle_old_format_data(self, mock_db: ConversationDatabase) -> None:
        """Test handling of old format conversation data."""
        old_format_data = {
            "messages": [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi!"},
            ],
        }

        conversation_id = _store(mock_db, "Old Format", old_format_data)
        result = mock_db.get_conversation(conversation_id)

        assert result is not None
        assert "messages" in result

    def test_handle_mixed_format_data(self, mock_db: ConversationDatabase) -> None:
        """Test handling of mixed format conversation data."""
        mixed_data = {
            "questions": ["Hello"],
            "answers": [{"content": "Hi!"}],
            "messages": [{"role": "user", "content": "Hello"}],
        }

        conversation_id = _store(mock_db, "Mixed Format", mixed_data)
        result = mock_db.get_conversation(conversation_id)

        assert result is not None

    def test_adds_tab_type_column_to_a_pre_existing_database(
        self,
        temp_db_path: Path,
    ) -> None:
        """A DB file created before the tab_type column existed must be

        migrated in place on the next open, with existing rows intact,
        and existing Pack conversations backfilled from their chat_data
        blob rather than only picking up tab_type for rows written after
        the migration.
        """
        with sqlite3.connect(str(temp_db_path)) as conn:
            conn.execute(
                """
                CREATE TABLE conversations (
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
            conn.execute(
                "INSERT INTO conversations "
                "(id, title, chat_data, created_date, closed_date) "
                "VALUES (1, 'Pre-migration Local', '{}', '2026-01-01', '2026-01-01')",
            )
            conn.execute(
                "INSERT INTO conversations "
                "(id, title, chat_data, created_date, closed_date) "
                "VALUES (2, 'Pre-migration Pack', ?, '2026-01-01', '2026-01-01')",
                (
                    json.dumps(
                        {
                            "tab_type": "pack",
                            "chat_state": {
                                "graph": {
                                    "id": "internal-graph-uuid",
                                    "active_node_id": "internal-node-uuid",
                                    "nodes": {
                                        "internal-node-uuid": {
                                            "id": "internal-node-uuid",
                                            "parent_id": None,
                                            "role": "user",
                                            "content": {
                                                "components": [
                                                    {
                                                        "type": "text",
                                                        "content": "Visible **Markdown**",
                                                    },
                                                ],
                                            },
                                        },
                                    },
                                },
                            },
                        },
                    ),
                ),
            )
            conn.commit()

        db = ConversationDatabase(str(temp_db_path))

        by_title = {c[1]: c[5] for c in db.get_conversations()}
        assert len(by_title) == 2
        assert by_title["Pre-migration Local"] is None
        assert by_title["Pre-migration Pack"] == "pack"

        migrated_record = next(
            record
            for record in db.get_history_records()
            if record["title"] == "Pre-migration Pack"
        )
        assert migrated_record["preview"] == "Visible **Markdown**"
        assert "internal-node-uuid" not in migrated_record["preview_markdown"]

        # And the migrated column is actually usable going forward.
        _store(db, "New Pack Tab", {"tab_type": "pack"})
        by_title = {c[1]: c[5] for c in db.get_conversations()}
        assert by_title["New Pack Tab"] == "pack"


class TestDatabasePerformance:
    """Tests for database performance characteristics."""

    def test_large_conversation_storage(self, mock_db: ConversationDatabase) -> None:
        """Test storage of very large conversations."""
        large_content = "x" * 100000  # 100KB of text
        chat_data = {
            "questions": [large_content],
            "answers": [{"components": [{"type": "text", "content": large_content}]}],
        }

        conversation_id = _store(mock_db, "Large", chat_data)
        result = mock_db.get_conversation(conversation_id)

        assert result is not None
        assert len(result["questions"][0]) == 100000

    def test_many_conversations(self, mock_db: ConversationDatabase) -> None:
        """Test handling many conversations."""
        for i in range(100):
            _store(
                mock_db,
                f"Chat {i}",
                {"questions": [f"Question {i}"], "answers": []},
            )

        conversations = mock_db.get_conversations()
        assert len(conversations) == 100

        results = mock_db.search_conversations("Chat 50")
        assert len(results) == 1
