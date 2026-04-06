"""Tests for handoff back-link navigation (navigate_to_conv / perform_handoff).

These tests protect against the regression where ephemeral tab IDs
(e.g. "tab-12-5f6be44b") were embedded in back-links:

  - Tab IDs are reallocated every session
  - After a restart, navigate_to_tab(old_id) found nothing in either
    _app.core.tabs or the DB, so every back-link click showed "not found"

The fix: perform_handoff saves the original tab to the DB immediately,
captures the stable integer conv_id, and embeds alpaca://conv/{conv_id}
in the back-link instead.  navigate_to_conv() then uses
tab.original_conversation_id to locate the tab whether it is open or
has been closed and restored.
"""
from __future__ import annotations

import gc
import os
import platform
import shutil
import sys
import time
from pathlib import Path
from unittest.mock import Mock
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def temp_dir(tmp_path):
    """Change cwd to tmp_path so AppCore's relative SESSION/DB paths land there."""
    original = os.getcwd()
    os.chdir(tmp_path)
    yield tmp_path
    os.chdir(original)
    gc.collect()
    if platform.system() == "Windows":
        for attempt in range(5):
            try:
                shutil.rmtree(tmp_path)
                break
            except PermissionError:
                if attempt < 4:
                    time.sleep(0.1 * (attempt + 1))


@pytest.fixture
def core(temp_dir):
    """Real AppCore (with real SQLite DB in tmp_path).
    Mock() is used as the api so attribute access (on_streaming_end etc.) never
    raises AttributeError during cleanup."""
    from core.app_core import AppCore

    return AppCore(api=Mock())


@pytest.fixture
def api(core):
    """WebViewAPI wrapping the real core; no real webview window needed."""
    from webview_api import WebViewAPI

    mock_app = Mock()
    mock_app.core = core
    return WebViewAPI(mock_app)


@pytest.fixture
def tab_with_content(core):
    """A ChatTab that has one Q/A pair — minimum required for handoff."""
    tab_id, tab = core.create_tab("My Conversation")
    tab.chat_state.add_question("What is the plan?")
    tab.chat_state.append_to_answer(0, "The plan is to write tests.")
    return tab_id, tab


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_api_for_tabs(tabs: dict):
    """Return a (WebViewAPI instance, mock_app) pair with the given open tabs.

    Because WebViewAPI uses __slots__, we cannot assign instance attributes
    that are not slots.  revive_conversation is patched at class level using
    patch.object inside individual tests.
    """
    from webview_api import WebViewAPI

    mock_app = Mock()
    mock_app.core.tabs = tabs
    # db.get_conversation returns None by default (not found)
    mock_app.core.db.get_conversation.return_value = None

    return WebViewAPI(mock_app)


# ---------------------------------------------------------------------------
# Unit tests: navigate_to_conv
# ---------------------------------------------------------------------------


class TestNavigateToConv:
    """navigate_to_conv must use original_conversation_id to locate open tabs
    and fall back to revive_conversation for closed ones."""

    # --- open-tab path ---

    def test_open_tab_returns_switch_action(self):
        """If any open tab has matching original_conversation_id, return switch."""
        from webview_api import WebViewAPI

        mock_tab = Mock()
        mock_tab.original_conversation_id = 42
        api = _make_api_for_tabs({"tab-1-abc": mock_tab})

        with patch.object(WebViewAPI, "revive_conversation") as mock_revive:
            result = api.navigate_to_conv(42)

        assert result["success"] is True
        assert result["action"] == "switch"
        assert result["tab_id"] == "tab-1-abc"
        mock_revive.assert_not_called()

    def test_open_tab_wrong_conv_id_falls_through(self):
        """A tab with a different conv_id does not satisfy the search."""
        from webview_api import WebViewAPI

        mock_tab = Mock()
        mock_tab.original_conversation_id = 99
        api = _make_api_for_tabs({"tab-1-abc": mock_tab})

        with patch.object(
            WebViewAPI,
            "revive_conversation",
            return_value={"success": True, "tab_id": "tab-2-xyz"},
        ) as mock_revive:
            result = api.navigate_to_conv(42)

        assert result["success"] is True
        assert result["action"] == "restore_queued"
        mock_revive.assert_called_once_with(42)

    def test_multiple_open_tabs_finds_correct_one(self):
        """With multiple open tabs, the one whose conv_id matches is returned."""
        from webview_api import WebViewAPI

        t1 = Mock()
        t1.original_conversation_id = 10
        t2 = Mock()
        t2.original_conversation_id = 20
        api = _make_api_for_tabs({"tab-a": t1, "tab-b": t2})

        with patch.object(WebViewAPI, "revive_conversation"):
            result = api.navigate_to_conv(20)

        assert result["success"] is True
        assert result["action"] == "switch"
        assert result["tab_id"] == "tab-b"

    # --- restore path ---

    def test_no_open_tabs_calls_revive(self):
        """With no open tabs, revive_conversation is called."""
        from webview_api import WebViewAPI

        api = _make_api_for_tabs({})

        with patch.object(
            WebViewAPI,
            "revive_conversation",
            return_value={"success": True, "tab_id": "tab-new"},
        ) as mock_revive:
            result = api.navigate_to_conv(7)

        assert result["success"] is True
        assert result["action"] == "restore_queued"
        mock_revive.assert_called_once_with(7)

    def test_revive_failure_returns_failure(self):
        """If revive_conversation fails, navigate_to_conv propagates the failure."""
        from webview_api import WebViewAPI

        api = _make_api_for_tabs({})

        with patch.object(
            WebViewAPI,
            "revive_conversation",
            return_value={"success": False, "error": "Conversation not found"},
        ):
            result = api.navigate_to_conv(999)

        assert result["success"] is False

    # --- conv_id coercion ---

    def test_conv_id_string_coerced_to_int(self):
        """JS always sends numbers as strings; the method must coerce."""
        from webview_api import WebViewAPI

        mock_tab = Mock()
        mock_tab.original_conversation_id = 5
        api = _make_api_for_tabs({"tab-x": mock_tab})

        with patch.object(WebViewAPI, "revive_conversation"):
            result = api.navigate_to_conv("5")

        assert result["success"] is True
        assert result["action"] == "switch"


# ---------------------------------------------------------------------------
# Integration tests: perform_handoff conv_id pinning
# ---------------------------------------------------------------------------


class TestPerformHandoffConvIdPinning:
    """perform_handoff must pin the original tab to a stable DB conv_id
    BEFORE registering the back-link callback.

    If the conv_id is not pinned, or if the ephemeral tab_id is used instead,
    the back-link will silently fail after a session restart.
    """

    def test_fresh_tab_gets_conv_id_assigned(self, api, tab_with_content):
        """A fresh tab (original_conversation_id=None) should be saved to the
        DB and have original_conversation_id set to the returned integer."""
        from core.chat_tab import ChatTab

        tab_id, tab = tab_with_content
        assert tab.original_conversation_id is None

        with patch.object(ChatTab, "handle_user_message"):
            api.perform_handoff(tab_id)

        assert tab.original_conversation_id is not None
        assert isinstance(tab.original_conversation_id, int)

    def test_already_pinned_tab_reuses_conv_id(self, api, tab_with_content, core):
        """A tab already linked to a DB record must NOT create a second record;
        the existing conv_id is reused."""
        from core.chat_tab import ChatTab

        tab_id, tab = tab_with_content
        existing_data = tab.get_serializable_data()
        existing_conv_id = core.db.store_conversation("My Conversation", existing_data)
        tab.original_conversation_id = existing_conv_id

        with patch.object(ChatTab, "handle_user_message"):
            api.perform_handoff(tab_id)

        assert tab.original_conversation_id == existing_conv_id

    def test_back_link_uses_alpaca_conv_scheme(self, api, tab_with_content, core):
        """The injected back-link must use alpaca://conv/{int}, NOT alpaca://tab/{str}.

        This is the exact regression that caused "conversation not found":
        alpaca://tab/tab-12-5f6be44b stopped working after a restart because
        that tab_id was reallocated each session.
        """
        from core.chat_tab import ChatTab
        from chat_state import FullAnswer

        tab_id, tab = tab_with_content

        with patch.object(ChatTab, "handle_user_message"):
            api.perform_handoff(tab_id)

        # Locate the new handoff tab
        new_tab = next(t for tid, t in core.tabs.items() if tid != tab_id)

        # Simulate the streaming answer that _on_handoff_complete will annotate
        new_tab.chat_state.answers.append(FullAnswer(["Handoff summary."]))

        # Fire the post-streaming callback directly
        assert new_tab._on_streaming_complete_callback is not None
        new_tab._on_streaming_complete_callback()

        # The back-link must be the first component of the last answer
        back_link = new_tab.chat_state.answers[-1].components[0]
        assert isinstance(back_link, str), "back-link must be a plain string component"
        assert (
            "alpaca://conv/" in back_link
        ), f"back-link must use alpaca://conv/ scheme, got: {back_link!r}"
        assert (
            "alpaca://tab/" not in back_link
        ), f"back-link must NOT use ephemeral tab ID scheme, got: {back_link!r}"

    def test_back_link_conv_id_matches_pinned_id(self, api, tab_with_content, core):
        """The integer in the back-link URL must equal original_conversation_id."""
        from core.chat_tab import ChatTab
        from chat_state import FullAnswer

        tab_id, tab = tab_with_content

        with patch.object(ChatTab, "handle_user_message"):
            api.perform_handoff(tab_id)

        new_tab = next(t for tid, t in core.tabs.items() if tid != tab_id)
        new_tab.chat_state.answers.append(FullAnswer(["Summary."]))
        new_tab._on_streaming_complete_callback()

        back_link = new_tab.chat_state.answers[-1].components[0]
        # Extract the integer from "alpaca://conv/{id})"
        prefix = "alpaca://conv/"
        start = back_link.index(prefix) + len(prefix)
        end = back_link.index(")", start)
        link_conv_id = int(back_link[start:end])

        assert link_conv_id == tab.original_conversation_id

    # --- end-to-end regression test ---

    def test_navigate_to_conv_finds_closed_original_tab(
        self,
        api,
        tab_with_content,
        core,
    ):
        """Full regression test for the bug.

        Flow:
          1. Handoff from Tab A → back-link embedding conv_id is injected
          2. Tab A is closed (saved to DB, removed from core.tabs)
          3. User clicks back-link → navigate_to_conv(conv_id)
          4. Must succeed and locate the conversation in the DB

        This would have failed with the old alpaca://tab/{tab_id} scheme
        because the tab_id is no longer present anywhere after being closed.
        """
        from core.chat_tab import ChatTab
        from chat_state import FullAnswer

        tab_id, tab = tab_with_content

        with patch.object(ChatTab, "handle_user_message"):
            api.perform_handoff(tab_id)

        new_tab = next(t for tid, t in core.tabs.items() if tid != tab_id)
        new_tab.chat_state.answers.append(FullAnswer(["Summary."]))
        new_tab._on_streaming_complete_callback()

        # Extract conv_id from the back-link
        back_link = new_tab.chat_state.answers[-1].components[0]
        prefix = "alpaca://conv/"
        start = back_link.index(prefix) + len(prefix)
        end = back_link.index(")", start)
        conv_id = int(back_link[start:end])

        # Close the original tab — saves to DB, removes from core.tabs
        core.delete_tab(tab_id)
        assert tab_id not in core.tabs

        # navigate_to_conv must find and restore it
        result = api.navigate_to_conv(conv_id)
        assert result["success"] is True


# ---------------------------------------------------------------------------
# Unit tests: clone_conversation
# ---------------------------------------------------------------------------


class TestCloneConversation:
    """clone_conversation must duplicate a tab's content into a new tab

    without disturbing the original, and without triggering an unwanted
    auto-generated title on the clone."""

    def test_clone_creates_new_tab_with_same_content(
        self,
        api,
        tab_with_content,
        core,
    ):
        tab_id, tab = tab_with_content

        result = api.clone_conversation(tab_id)

        assert result["success"] is True
        new_tab_id = result["tab_id"]
        assert new_tab_id != tab_id
        new_tab = core.tabs[new_tab_id]
        assert new_tab.chat_state.questions == tab.chat_state.questions

    def test_clone_does_not_modify_original_tab(self, api, tab_with_content, core):
        tab_id, tab = tab_with_content
        original_questions = list(tab.chat_state.questions)

        api.clone_conversation(tab_id)

        assert tab_id in core.tabs
        assert tab.chat_state.questions == original_questions

    def test_clone_title_is_derived_from_original(self, api, tab_with_content, core):
        """Regression test: load_from_data() restores the *original* title
        from the serialized payload after creation, so clone_conversation
        must reassert the "Clone: " title afterwards or it silently reverts
        to the source tab's exact title."""
        tab_id, tab = tab_with_content

        result = api.clone_conversation(tab_id)

        new_tab = core.tabs[result["tab_id"]]
        assert new_tab.title == f"Clone: {tab.title}"
        assert new_tab.title != tab.title

    def test_clone_suppresses_auto_title_generation(
        self,
        api,
        tab_with_content,
        core,
    ):
        """The clone already has a meaningful title; it must not be overwritten
        by the auto-summary-title generator."""
        tab_id, _ = tab_with_content

        result = api.clone_conversation(tab_id)

        new_tab = core.tabs[result["tab_id"]]
        assert new_tab._summary_handler._generated is True

    def test_clone_nonexistent_tab_returns_error(self, api):
        result = api.clone_conversation("no-such-tab")

        assert result["success"] is False
        assert "not found" in result["error"].lower()

    def test_clone_while_streaming_returns_error(self, api, tab_with_content):
        tab_id, tab = tab_with_content
        tab.is_streaming = True

        result = api.clone_conversation(tab_id)

        assert result["success"] is False
        assert "streaming" in result["error"].lower()
