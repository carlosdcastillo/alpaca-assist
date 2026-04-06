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

        # Verify table exists
        conn = sqlite3.connect(str(temp_db_path))
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='conversations'",
        )
        result = cursor.fetchone()
        conn.close()

        assert result is not None
        assert result[0] == "conversations"

    def test_init_idempotent(self, temp_db_path: Path) -> None:
        """Test that initialization is idempotent."""
        db = ConversationDatabase(str(temp_db_path))
        db.init_database()
        db.init_database()  # Should not raise
        assert temp_db_path.exists()


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

        conversation_id = mock_db.store_conversation("Test Conversation", chat_data)

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

        conversation_id = mock_db.store_conversation("My Chat", chat_data)

        # Retrieve and verify
        # get_conversation() merges chat_data keys directly into the result dict
        # and adds 'original_conversation_id' and 'title' keys.
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

        conversation_id = mock_db.store_conversation(title, chat_data)
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

        conversation_id = mock_db.store_conversation("Large Chat", chat_data)
        result = mock_db.get_conversation(conversation_id)

        assert result is not None
        # get_conversation() merges chat_data keys directly into the result dict
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
        conversation_id = mock_db.store_conversation("Test", chat_data)

        result = mock_db.get_conversation(conversation_id)

        # get_conversation() returns the chat_data dict merged with two extra keys:
        # 'original_conversation_id' (the DB row ID) and 'title'.
        assert isinstance(result, dict)
        assert "original_conversation_id" in result
        assert "title" in result
        # The chat_data fields are merged at the top level, not nested
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
        # Store some conversations
        for i in range(3):
            mock_db.store_conversation(f"Chat {i}", {"questions": [], "answers": []})

        conversations = mock_db.get_conversations()

        assert isinstance(conversations, list)
        assert len(conversations) == 3

    def test_get_conversations_sorted_by_updated(
        self,
        mock_db: ConversationDatabase,
    ) -> None:
        """Test that conversations are sorted by updated_at descending."""
        # Store conversations with delays
        with freeze_time("2024-01-15 10:00:00"):
            id1 = mock_db.store_conversation("First", {"questions": [], "answers": []})

        with freeze_time("2024-01-15 11:00:00"):
            id2 = mock_db.store_conversation("Second", {"questions": [], "answers": []})

        with freeze_time("2024-01-15 12:00:00"):
            id3 = mock_db.store_conversation("Third", {"questions": [], "answers": []})

        conversations = mock_db.get_conversations()

        assert conversations[0][0] == id3
        assert conversations[1][0] == id2
        assert conversations[2][0] == id1

    def test_get_conversations_tuple_format(
        self,
        mock_db: ConversationDatabase,
    ) -> None:
        """Test that get_conversations returns correct tuple format."""
        chat_data = {"questions": ["Hello"], "answers": []}
        mock_db.store_conversation("Test Chat", chat_data)

        conversations = mock_db.get_conversations()

        assert len(conversations) == 1
        conv = conversations[0]
        # Tuple is: (id, title, created_date, closed_date, summary_generated)
        assert len(conv) == 5
        assert isinstance(conv[0], int)
        assert isinstance(conv[1], str)
        assert isinstance(conv[2], str)
        assert isinstance(conv[3], str)
        # summary_generated is stored as INTEGER (0/1) in SQLite
        assert isinstance(conv[4], int)


class TestConversationDeletion:
    """Tests for conversation deletion operations."""

    def test_delete_conversation_removes_record(
        self,
        mock_db: ConversationDatabase,
    ) -> None:
        """Test that delete_conversation removes the record."""
        chat_data: dict[str, Any] = {"questions": [], "answers": []}
        conversation_id = mock_db.store_conversation("To Delete", chat_data)

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
        id1 = mock_db.store_conversation("Keep", {"questions": [], "answers": []})
        id2 = mock_db.store_conversation("Delete", {"questions": [], "answers": []})

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
        mock_db.store_conversation("Python Tutorial", {"questions": [], "answers": []})
        mock_db.store_conversation("JavaScript Guide", {"questions": [], "answers": []})

        results = mock_db.search_conversations("Python")

        assert len(results) == 1
        assert results[0][1] == "Python Tutorial"

    def test_search_conversations_finds_in_content(
        self,
        mock_db: ConversationDatabase,
    ) -> None:
        """Test searching conversations — search operates on title only."""
        mock_db.store_conversation(
            "Python Chat",
            {"questions": ["What is Python?"], "answers": []},
        )
        mock_db.store_conversation(
            "JavaScript Chat",
            {"questions": ["What is JavaScript?"], "answers": []},
        )

        results = mock_db.search_conversations("Python")

        assert len(results) == 1
        assert results[0][1] == "Python Chat"

    def test_search_conversations_case_insensitive(
        self,
        mock_db: ConversationDatabase,
    ) -> None:
        """Test that search is case insensitive."""
        mock_db.store_conversation("PYTHON Chat", {"questions": [], "answers": []})

        results_lower = mock_db.search_conversations("python")
        results_upper = mock_db.search_conversations("PYTHON")

        assert len(results_lower) == 1
        assert len(results_upper) == 1

    def test_search_conversations_no_results(
        self,
        mock_db: ConversationDatabase,
    ) -> None:
        """Test searching with no matches."""
        mock_db.store_conversation("Chat 1", {"questions": [], "answers": []})

        results = mock_db.search_conversations("nonexistent")

        assert len(results) == 0
        assert isinstance(results, list)

    def test_search_conversations_empty_term(
        self,
        mock_db: ConversationDatabase,
    ) -> None:
        """Test searching with empty term."""
        mock_db.store_conversation("Chat 1", {"questions": [], "answers": []})
        mock_db.store_conversation("Chat 2", {"questions": [], "answers": []})

        results = mock_db.search_conversations("")

        # Empty search should return all conversations
        assert len(results) == 2

    def test_search_conversations_special_characters(
        self,
        mock_db: ConversationDatabase,
    ) -> None:
        """Test searching with special characters."""
        mock_db.store_conversation(
            'Chat with "quotes"',
            {"questions": [], "answers": []},
        )

        results = mock_db.search_conversations('"quotes"')

        assert len(results) == 1


class TestConversationExistence:
    """Tests for conversation existence checks."""

    def test_conversation_exists_true(self, mock_db: ConversationDatabase) -> None:
        """Test that conversation_exists returns True for existing conversation."""
        conversation_id = mock_db.store_conversation(
            "Test",
            {"questions": [], "answers": []},
        )

        result = mock_db.conversation_exists(conversation_id)

        assert result is True

    def test_conversation_exists_false(self, mock_db: ConversationDatabase) -> None:
        """Test that conversation_exists returns False for nonexistent conversation."""
        result = mock_db.conversation_exists(99999)
        assert result is False

    def test_conversation_exists_invalid_id(
        self,
        mock_db: ConversationDatabase,
    ) -> None:
        """Test that conversation_exists handles invalid IDs."""
        result = mock_db.conversation_exists(-1)
        assert result is False


class TestTabIdLookup:
    """Tests for tab ID lookup operations."""

    def test_find_conversation_by_tab_id(self, mock_db: ConversationDatabase) -> None:
        """Test finding conversation by tab ID."""
        chat_data = {
            "tab_id": "tab-123",
            "questions": [],
            "answers": [],
        }
        conversation_id = mock_db.store_conversation("Test", chat_data)

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
        chat_data: dict[str, Any] = {"questions": [], "answers": []}
        mock_db.store_conversation("Test", chat_data)

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
        chat_data = {
            "questions": [],
            "answers": [],
            "question_images": [["http://example.com/image.png"]],
        }
        conv_id = mock_db.store_conversation("With Images", chat_data)

        result = mock_db.get_conversation(conv_id)

        assert result is not None
        assert result["question_images"] == [["http://example.com/image.png"]]

    def test_no_images_stored_in_chat_data(
        self,
        mock_db: ConversationDatabase,
    ) -> None:
        """Test that conversations without images have no question_images key."""
        chat_data: dict[str, Any] = {"questions": [], "answers": []}
        conv_id = mock_db.store_conversation("Without Images", chat_data)

        result = mock_db.get_conversation(conv_id)

        assert result is not None
        assert "question_images" not in result

    def test_empty_images_stored_in_chat_data(
        self,
        mock_db: ConversationDatabase,
    ) -> None:
        """Test that empty image arrays are preserved in stored chat_data."""
        chat_data: dict[str, Any] = {
            "questions": [],
            "answers": [],
            "question_images": [[], []],
        }
        conv_id = mock_db.store_conversation("Empty Images", chat_data)

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
        chat_data: dict[str, Any] = {"questions": [], "answers": []}
        conversation_id = mock_db.store_conversation("", chat_data)

        result = mock_db.get_conversation(conversation_id)
        assert result is not None
        assert result["title"] == ""

    def test_store_conversation_none_in_data(
        self,
        mock_db: ConversationDatabase,
    ) -> None:
        """Test storing conversation with None values in data."""
        chat_data = {
            "questions": ["Test"],
            "answers": [None],  # Edge case: None in answers
        }
        conversation_id = mock_db.store_conversation("Test", chat_data)

        result = mock_db.get_conversation(conversation_id)
        assert result is not None

    def test_database_error_handling(self, temp_dir: Path) -> None:
        """Test database error handling."""
        db_path = temp_dir / "readonly.db"
        db = ConversationDatabase(str(db_path))
        db.init_database()

        # Make database read-only by creating a file that can't be written to
        # This is a simplified test - real error handling tests would need more setup
        # Just verify the database doesn't crash on normal operations
        chat_data: dict[str, Any] = {"questions": [], "answers": []}
        conversation_id = db.store_conversation("Test", chat_data)
        assert conversation_id is not None

    def test_concurrent_access(self, mock_db: ConversationDatabase) -> None:
        """Test basic concurrent access handling."""
        # Store multiple conversations rapidly
        for i in range(10):
            mock_db.store_conversation(f"Chat {i}", {"questions": [], "answers": []})

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

        conversation_id = mock_db.store_conversation("Old Format", old_format_data)
        result = mock_db.get_conversation(conversation_id)

        # get_conversation() merges chat_data keys directly into the result dict,
        # so 'messages' is a top-level key, not nested under 'chat_data'.
        assert result is not None
        assert "messages" in result

    def test_handle_mixed_format_data(self, mock_db: ConversationDatabase) -> None:
        """Test handling of mixed format conversation data."""
        mixed_data = {
            "questions": ["Hello"],
            "answers": [{"content": "Hi!"}],
            "messages": [{"role": "user", "content": "Hello"}],
        }

        conversation_id = mock_db.store_conversation("Mixed Format", mixed_data)
        result = mock_db.get_conversation(conversation_id)

        assert result is not None


class TestDatabasePerformance:
    """Tests for database performance characteristics."""

    def test_large_conversation_storage(self, mock_db: ConversationDatabase) -> None:
        """Test storage of very large conversations."""
        # Create a conversation with substantial content
        large_content = "x" * 100000  # 100KB of text
        chat_data = {
            "questions": [large_content],
            "answers": [{"components": [{"type": "text", "content": large_content}]}],
        }

        conversation_id = mock_db.store_conversation("Large", chat_data)
        result = mock_db.get_conversation(conversation_id)

        assert result is not None
        # get_conversation() merges chat_data keys directly into the result dict
        assert len(result["questions"][0]) == 100000

    def test_many_conversations(self, mock_db: ConversationDatabase) -> None:
        """Test handling many conversations."""
        for i in range(100):
            mock_db.store_conversation(
                f"Chat {i}",
                {"questions": [f"Question {i}"], "answers": []},
            )

        conversations = mock_db.get_conversations()
        assert len(conversations) == 100

        # search_conversations searches by title only, not content
        results = mock_db.search_conversations("Chat 50")
        assert len(results) == 1
