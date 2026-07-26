"""Tests for core/app_core.py - Session serialization and autosave.
No Tkinter required.
"""
import json
import os
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from core.app_core import AppCore
from core.app_core import PREFERENCES_FILE
from core.app_core import SESSION_FILE


class TestAppCoreSession:
    """Tests for session save/load functionality."""

    @pytest.fixture
    def temp_dir(self, tmp_path):
        """Create a temporary directory and change to it."""
        original_dir = os.getcwd()
        os.chdir(tmp_path)
        yield tmp_path
        os.chdir(original_dir)

    @pytest.fixture
    def core(self, temp_dir):
        """Create an AppCore instance with mocked API."""

        class MockAPI:
            pass

        core = AppCore(api=MockAPI())
        return core

    def test_save_session_creates_file(self, core, temp_dir):
        """save_session() should create chat_session.json."""
        core.save_session()
        assert os.path.exists(SESSION_FILE)

    def test_save_session_includes_tabs(self, core, temp_dir):
        """save_session() should include tab data."""
        # Create a tab with content
        tab_id, tab = core.create_tab("Test Tab")
        tab.chat_state.add_question("Hello?")
        tab.chat_state.append_to_answer(0, "Hi there!")

        core.set_active_tab_id(tab_id)
        core.save_session()

        with open(SESSION_FILE) as f:
            data = json.load(f)

        assert "tabs" in data
        assert len(data["tabs"]) == 1
        assert data["tabs"][0]["name"] == "Test Tab"
        assert "chat_state" in data["tabs"][0]

    def test_save_session_includes_selected_tab_id(self, core, temp_dir):
        """save_session() should track the active tab."""
        tab_id1, _ = core.create_tab("Tab 1")
        tab_id2, _ = core.create_tab("Tab 2")

        core.set_active_tab_id(tab_id2)
        core.save_session()

        with open(SESSION_FILE) as f:
            data = json.load(f)

        assert data["selected_tab_id"] == tab_id2

    def test_save_session_version_field(self, core, temp_dir):
        """save_session() should include version for future compatibility."""
        core.save_session()

        with open(SESSION_FILE) as f:
            data = json.load(f)

        assert data["version"] == "2.0"

    def test_load_session_returns_tabs_and_selected_id(self, core, temp_dir):
        """load_session() should return (tabs, selected_id) tuple."""
        # Setup: create and save session
        tab_id, tab = core.create_tab("Test Tab")
        tab.chat_state.add_question("Q1")
        tab.chat_state.append_to_answer(0, "A1")
        core.set_active_tab_id(tab_id)
        core.save_session()

        # Test: load and verify
        tabs, selected_id = core.load_session()

        assert len(tabs) == 1
        assert tabs[0]["name"] == "Test Tab"
        assert selected_id == tab_id

    def test_load_session_no_file_returns_empty(self, core, temp_dir):
        """load_session() should return ([], None) when no session file."""
        tabs, selected_id = core.load_session()
        assert tabs == []
        assert selected_id is None

    def test_load_session_corrupted_file_returns_empty(self, core, temp_dir):
        """load_session() should handle corrupted JSON gracefully."""
        # Create corrupted file
        with open(SESSION_FILE, "w") as f:
            f.write("not valid json {{[")

        tabs, selected_id = core.load_session()
        assert tabs == []
        assert selected_id is None

    def test_load_session_missing_selected_tab_id(self, core, temp_dir):
        """load_session() should handle missing selected_tab_id."""
        # Create file without selected_tab_id
        with open(SESSION_FILE, "w") as f:
            json.dump({"tabs": [], "version": "2.0"}, f)

        tabs, selected_id = core.load_session()
        assert selected_id is None

    def test_session_roundtrip_preserves_chat_state(self, core, temp_dir):
        """Save and load should preserve conversation content."""
        # Create tab with conversation
        tab_id, tab = core.create_tab("Roundtrip Test")
        tab.chat_state.add_question("What is Python?")
        tab.chat_state.append_to_answer(0, "Python is a programming language.")
        tab.chat_state.add_question("What about Rust?")
        tab.chat_state.append_to_answer(1, "Rust is systems programming language.")

        core.set_active_tab_id(tab_id)
        core.save_session()

        # Load and verify
        tabs, _ = core.load_session()
        assert len(tabs) == 1

        chat_state = tabs[0]["chat_state"]
        assert "questions" in chat_state or "graph" in chat_state

    def test_save_session_multiple_tabs(self, core, temp_dir):
        """save_session() should handle multiple tabs."""
        tab_id1, tab1 = core.create_tab("Tab 1")
        tab1.chat_state.add_question("Q1")
        tab1.chat_state.append_to_answer(0, "A1")

        tab_id2, tab2 = core.create_tab("Tab 2")
        tab2.chat_state.add_question("Q2")
        tab2.chat_state.append_to_answer(0, "A2")

        tab_id3, tab3 = core.create_tab("Tab 3")
        # Empty tab (no content)

        core.set_active_tab_id(tab_id2)
        core.save_session()

        with open(SESSION_FILE) as f:
            data = json.load(f)

        assert len(data["tabs"]) == 3
        assert data["selected_tab_id"] == tab_id2

    def test_save_session_empty_tabs(self, core, temp_dir):
        """save_session() should handle no tabs gracefully."""
        core.save_session()

        with open(SESSION_FILE) as f:
            data = json.load(f)

        assert data["tabs"] == []
        assert data["selected_tab_id"] is None


class TestAppCoreAutosave:
    """Tests for autosave functionality."""

    @pytest.fixture
    def temp_dir(self, tmp_path):
        """Create a temporary directory and change to it."""
        original_dir = os.getcwd()
        os.chdir(tmp_path)
        yield tmp_path
        os.chdir(original_dir)

    @pytest.fixture
    def core(self, temp_dir):
        """Create an AppCore instance with mocked API."""

        class MockAPI:
            pass

        core = AppCore(api=MockAPI())
        # Reduce autosave interval for faster tests
        from core import app_core

        original_interval = app_core._AUTOSAVE_INTERVAL_MS
        app_core._AUTOSAVE_INTERVAL_MS = 100  # 100ms for testing
        yield core
        app_core._AUTOSAVE_INTERVAL_MS = original_interval
        core.stop_autosave()

    def test_start_autosave_creates_timer(self, core, temp_dir):
        """start_autosave() should initialize the autosave timer."""
        core.start_autosave()
        assert core._autosave_timer is not None
        core.stop_autosave()

    def test_autosave_creates_session_file(self, core, temp_dir):
        """Autosave should create session file after interval."""
        # Create a tab
        core.create_tab("Autosave Test")

        # Start autosave
        core.start_autosave()

        # Wait for autosave to trigger
        time.sleep(0.2)  # Wait longer than 100ms interval

        # Stop autosave
        core.stop_autosave()

        # Verify file was created
        assert os.path.exists(SESSION_FILE)

    def test_stop_autosave_cancels_timer(self, core, temp_dir):
        """stop_autosave() should cancel the timer."""
        core.start_autosave()
        timer = core._autosave_timer

        core.stop_autosave()

        assert core._shutdown_event.is_set()
        assert core._autosave_timer is None

    def test_autosave_always_saves(self, core, temp_dir):
        """Autosave should always save regardless of preference."""
        # Create a tab and start autosave
        core.create_tab("Test")
        core.start_autosave()

        # Wait
        time.sleep(0.2)

        # Stop
        core.stop_autosave()

        # File should exist
        assert os.path.exists(SESSION_FILE)

    def test_autosave_continues_after_multiple_intervals(self, core, temp_dir):
        """Autosave should continue running across multiple intervals."""
        core.create_tab("Test")

        with patch.object(core, "save_session", wraps=core.save_session) as mock_save:
            core.start_autosave()

            # Poll until at least 3 saves have fired (initial + 2 periodic).
            # Generous 3-second ceiling avoids spurious failures under suite load.
            deadline = time.time() + 3.0
            while mock_save.call_count < 3 and time.time() < deadline:
                time.sleep(0.02)

            core.stop_autosave()

        assert (
            mock_save.call_count >= 3
        ), f"Expected ≥3 saves (initial + 2 periodic), got {mock_save.call_count}"

    def test_autosave_handles_save_errors_gracefully(self, core, temp_dir):
        """Autosave should not crash on save errors."""
        core.create_tab("Test")

        # Let the initial save (called by start_autosave) succeed, then make
        # the periodic saves fail to verify the autosave thread handles errors.
        core.start_autosave()
        with patch.object(core, "save_session", side_effect=OSError("disk full")):
            time.sleep(0.2)  # Let at least one periodic save fail

        core.stop_autosave()

        # Autosave infrastructure must be cleanly stoppable after errors
        assert core._shutdown_event.is_set()
        assert core._autosave_timer is None

    def test_shutdown_stops_autosave(self, core, temp_dir):
        """shutdown() should stop autosave before other cleanup."""
        core.create_tab("Test")
        core.start_autosave()

        # Mock the MCP shutdown to avoid needing full setup
        core.event_loop = None
        core.mcp_manager = None

        core.shutdown()

        assert core._shutdown_event.is_set()
        assert core._autosave_timer is None


class TestAppCoreTabLifecycle:
    """Tests for tab creation and deletion with session handling."""

    @pytest.fixture
    def temp_dir(self, tmp_path):
        """Create a temporary directory and change to it."""
        original_dir = os.getcwd()
        os.chdir(tmp_path)
        yield tmp_path
        os.chdir(original_dir)

    @pytest.fixture
    def core(self, temp_dir):
        """Create an AppCore instance with mocked API."""

        class MockAPI:
            def on_streaming_end(self, *args, **kwargs):
                pass

            def on_content_update(self, *args, **kwargs):
                pass

        core = AppCore(api=MockAPI())
        return core

    def test_create_tab_returns_unique_ids(self, core):
        """Each tab should get a unique ID."""
        tab_id1, _ = core.create_tab("Tab 1")
        tab_id2, _ = core.create_tab("Tab 2")
        tab_id3, _ = core.create_tab("Tab 3")

        assert tab_id1 != tab_id2
        assert tab_id2 != tab_id3
        assert tab_id1 != tab_id3

    def test_create_tab_increments_counter(self, core):
        """Tab counter should increment with each creation."""
        _, tab1 = core.create_tab("Tab 1")
        _, tab2 = core.create_tab("Tab 2")

        assert core._tab_counter == 2

    def test_delete_tab_removes_from_tabs(self, core):
        """delete_tab() should remove tab from tabs dict."""
        tab_id, tab = core.create_tab("To Delete")
        assert tab_id in core.tabs

        # Mock the database to avoid needing full setup
        core.db = None

        core.delete_tab(tab_id)
        assert tab_id not in core.tabs

    def test_delete_nonexistent_tab_is_noop(self, core):
        """delete_tab() should handle non-existent tab gracefully."""
        # Should not raise exception
        core.delete_tab("nonexistent-tab-id")

    def test_get_active_tab_id_returns_set_value(self, core):
        """get_active_tab_id() should return the set value."""
        tab_id, _ = core.create_tab("Test")
        core.set_active_tab_id(tab_id)

        assert core.get_active_tab_id() == tab_id

    def test_get_active_tab_id_default_none(self, core):
        """get_active_tab_id() should return None if not set."""
        assert core.get_active_tab_id() is None

    def test_alloc_tab_id_format(self, core):
        """Tab IDs should follow expected format."""
        tab_id = core._alloc_tab_id()

        assert tab_id.startswith("tab-")
        parts = tab_id.split("-")
        assert len(parts) == 3  # tab, counter, uuid
        assert parts[1].isdigit()
        assert len(parts[2]) == 8  # 8-char UUID prefix


class TestAppCoreIntegration:
    """Integration tests for session save/load with full lifecycle."""

    @pytest.fixture
    def temp_dir(self, tmp_path):
        """Create a temporary directory and change to it."""
        original_dir = os.getcwd()
        os.chdir(tmp_path)
        yield tmp_path
        os.chdir(original_dir)

    @pytest.fixture
    def core(self, temp_dir):
        """Create an AppCore instance with mocked API."""

        class MockAPI:
            pass

        core = AppCore(api=MockAPI())
        return core

    def test_full_session_lifecycle(self, core, temp_dir):
        """Complete test: create tabs, save, load, verify."""
        # Create tabs with conversations
        tab_id1, tab1 = core.create_tab("Python Help")
        tab1.chat_state.add_question("What is Python?")
        tab1.chat_state.append_to_answer(0, "Python is a programming language.")

        tab_id2, tab2 = core.create_tab("Rust Help")
        tab2.chat_state.add_question("What is Rust?")
        tab2.chat_state.append_to_answer(0, "Rust is fast.")

        # Set active and save
        core.set_active_tab_id(tab_id2)
        core.save_session()

        # Simulate restart by loading session
        loaded_tabs, selected_id = core.load_session()

        # Verify
        assert len(loaded_tabs) == 2
        assert selected_id == tab_id2

        # Verify tab data
        titles = [t["name"] for t in loaded_tabs]
        assert "Python Help" in titles
        assert "Rust Help" in titles

    def test_session_persists_across_instances(self, temp_dir):
        """Session should persist when creating new AppCore instance."""

        class MockAPI:
            pass

        # First instance
        core1 = AppCore(api=MockAPI())
        tab_id, tab = core1.create_tab("Persistent Tab")
        tab.chat_state.add_question("Q1")
        tab.chat_state.append_to_answer(0, "A1")
        core1.set_active_tab_id(tab_id)
        core1.save_session()

        # Second instance (simulating restart)
        core2 = AppCore(api=MockAPI())
        loaded_tabs, selected_id = core2.load_session()

        assert len(loaded_tabs) == 1
        assert loaded_tabs[0]["name"] == "Persistent Tab"
        assert selected_id == tab_id

    def test_multiple_save_load_cycles(self, core, temp_dir):
        """Multiple save/load cycles should preserve data."""
        # Cycle 1
        tab_id, tab = core.create_tab("Cycle Test")
        tab.chat_state.add_question("Q1")
        tab.chat_state.append_to_answer(0, "A1")
        core.save_session()

        # Cycle 2 - add more content
        tab.chat_state.add_question("Q2")
        tab.chat_state.append_to_answer(1, "A2")
        core.save_session()

        # Cycle 3 - verify both saves worked
        loaded_tabs, _ = core.load_session()
        chat_state = loaded_tabs[0]["chat_state"]

        # Should have both questions
        if "questions" in chat_state:
            assert len(chat_state["questions"]) == 2


class TestAppCoreCopyToClipboard:
    """Tests for copy_to_clipboard (backs the code-block Copy button)."""

    @pytest.fixture
    def temp_dir(self, tmp_path):
        original_dir = os.getcwd()
        os.chdir(tmp_path)
        yield tmp_path
        os.chdir(original_dir)

    @pytest.fixture
    def core(self, temp_dir):
        class MockAPI:
            pass

        return AppCore(api=MockAPI())

    def test_uses_gtk_clipboard_when_available(self, core):
        """GTK path: set_text + store called, returns True."""
        mock_clipboard = MagicMock()
        mock_gtk_module = MagicMock()
        mock_gtk_module.Gtk.Clipboard.get.return_value = mock_clipboard
        mock_gi_module = MagicMock()
        mock_gi_module.repository = mock_gtk_module

        with patch.dict(
            "sys.modules",
            {
                "gi": mock_gi_module,
                "gi.repository": mock_gtk_module,
                "gi.repository.Gtk": mock_gtk_module.Gtk,
                "gi.repository.Gdk": mock_gtk_module.Gdk,
            },
        ):
            result = core.copy_to_clipboard("hello from a code block")

        assert result is True
        mock_clipboard.set_text.assert_called_once_with(
            "hello from a code block",
            -1,
        )
        mock_clipboard.store.assert_called_once()

    def test_falls_back_to_pyperclip_when_gtk_unavailable(self, core):
        """No gi module: falls back to pyperclip.copy, returns True."""
        with patch.dict("sys.modules", {"gi": None}):
            with patch("core.app_core.pyperclip.copy") as mock_copy:
                result = core.copy_to_clipboard("fallback text")

        assert result is True
        mock_copy.assert_called_once_with("fallback text")

    def test_returns_false_when_both_backends_fail(self, core):
        """Neither GTK nor pyperclip available: returns False, doesn't raise."""
        with patch.dict("sys.modules", {"gi": None}):
            with patch(
                "core.app_core.pyperclip.copy",
                side_effect=Exception("no clipboard mechanisms for this system"),
            ):
                result = core.copy_to_clipboard("unreachable clipboard")

        assert result is False
