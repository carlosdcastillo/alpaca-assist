"""Tests for _get_export_content_and_title in AppCore.

Regressions caught:
  1. ChatState format was never handled — only ConversationGraph was.
     Result: empty export for most conversations.
  2. Function returned HTML instead of markdown.
     Result: export_and_open() double-processed it through the markdown
     converter, producing garbled repeated lines.
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

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def temp_dir(tmp_path):
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
    from core.app_core import AppCore

    return AppCore(api=Mock())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_tab_with_chat_state(
    core,
    questions: list[str],
    answers: list[str],
    title="Test Conv",
):
    """Create a tab backed by ChatState with the given Q/A pairs."""
    tab_id, tab = core.create_tab(title)
    for i, q in enumerate(questions):
        tab.chat_state.add_question(q)
        if i < len(answers):
            tab.chat_state.append_to_answer(i, answers[i])
    return tab


def _make_tab_with_conv_graph(
    core,
    questions: list[str],
    answers: list[str],
    title="Test Conv",
):
    """Create a tab backed by ConversationGraph with the given Q/A pairs."""
    from conversation_graph import ConversationGraph

    tab_id, tab = core.create_tab(title)
    tab.chat_state = ConversationGraph()
    for i, q in enumerate(questions):
        tab.chat_state.add_question(q)
        if i < len(answers):
            tab.chat_state.append_to_answer(i, answers[i])
    return tab


# ---------------------------------------------------------------------------
# ChatState format
# ---------------------------------------------------------------------------


class TestExportChatState:
    """_get_export_content_and_title must handle ChatState (the default format)."""

    def test_returns_markdown_not_html(self, core):
        """Output must be markdown, not HTML.

        The original bug: the function returned HTML like '<h1>Title</h1>',
        which export_and_open() passed through a markdown converter a second
        time, producing garbled doubled output.
        """
        tab = _make_tab_with_chat_state(core, ["Hello"], ["World"])
        content, _ = core._get_export_content_and_title(tab)

        assert not content.startswith("<!DOCTYPE"), "must not return HTML document"
        assert not content.startswith("<html"), "must not return HTML document"
        assert "<h1>" not in content, "must not contain raw HTML tags"
        assert "<h2>" not in content, "must not contain raw HTML tags"

    def test_title_appears_in_output(self, core):
        tab = _make_tab_with_chat_state(core, ["Hi"], ["Hello"], title="My Chat")
        content, returned_title = core._get_export_content_and_title(tab)

        # Title is returned separately; the HTML template adds it to avoid duplication.
        assert returned_title == "My Chat"

    def test_question_appears_in_output(self, core):
        """The original bug: ChatState was never handled, so questions were missing."""
        tab = _make_tab_with_chat_state(core, ["What is the answer?"], ["42"])
        content, _ = core._get_export_content_and_title(tab)

        assert "What is the answer?" in content

    def test_answer_appears_in_output(self, core):
        tab = _make_tab_with_chat_state(core, ["Q"], ["The answer is 42."])
        content, _ = core._get_export_content_and_title(tab)

        assert "The answer is 42." in content

    def test_multiple_qa_pairs_all_present(self, core):
        questions = ["First question", "Second question", "Third question"]
        answers = ["First answer", "Second answer", "Third answer"]
        tab = _make_tab_with_chat_state(core, questions, answers)
        content, _ = core._get_export_content_and_title(tab)

        for q in questions:
            assert q in content, f"question {q!r} missing from export"
        for a in answers:
            assert a in content, f"answer {a!r} missing from export"

    def test_empty_conversation_does_not_crash(self, core):
        tab = _make_tab_with_chat_state(core, [], [])
        content, title = core._get_export_content_and_title(tab)
        assert isinstance(content, str)
        assert isinstance(title, str)

    def test_uses_markdown_headings(self, core):
        """Content must use ## headings, not <h2> tags — export_and_open converts them."""
        tab = _make_tab_with_chat_state(core, ["Q"], ["A"])
        content, _ = core._get_export_content_and_title(tab)

        assert "## You" in content or "## you" in content.lower()
        assert "## Assistant" in content or "## assistant" in content.lower()


# ---------------------------------------------------------------------------
# ConversationGraph format
# ---------------------------------------------------------------------------


class TestExportConversationGraph:
    """_get_export_content_and_title must also handle ConversationGraph."""

    def test_question_and_answer_present(self, core):
        tab = _make_tab_with_conv_graph(core, ["Graph question"], ["Graph answer"])
        content, _ = core._get_export_content_and_title(tab)

        assert "Graph question" in content
        assert "Graph answer" in content

    def test_returns_markdown_not_html(self, core):
        tab = _make_tab_with_conv_graph(core, ["Q"], ["A"])
        content, _ = core._get_export_content_and_title(tab)

        assert "<h1>" not in content
        assert not content.startswith("<html")

    def test_multiple_qa_pairs(self, core):
        questions = ["CG Q1", "CG Q2"]
        answers = ["CG A1", "CG A2"]
        tab = _make_tab_with_conv_graph(core, questions, answers)
        content, _ = core._get_export_content_and_title(tab)

        for q in questions:
            assert q in content
        for a in answers:
            assert a in content


# ---------------------------------------------------------------------------
# FullAnswer with tool calls
# ---------------------------------------------------------------------------


class TestExportToolCallFiltering:
    """Tool call XML must not appear in export output."""

    def test_tool_calls_excluded_from_export(self, core):
        """get_text_only_content() strips tool calls; export must use it."""
        from chat_state import FullAnswer
        from chat_state import ToolCall

        tab_id, tab = core.create_tab("Tool Conv")
        tab.chat_state.add_question("Run the tool")

        answer = tab.chat_state.answers[0]
        answer.add_text("Before tool. ")
        answer.components.append(ToolCall(content="<tool_use>...</tool_use>", id="tc1"))
        answer.add_text("After tool.")

        content, _ = core._get_export_content_and_title(tab)

        assert "Before tool." in content
        assert "After tool." in content
        assert "<tool_use>" not in content

    def test_text_only_answer_exported_fully(self, core):
        """Plain text FullAnswer must be fully exported."""
        tab_id, tab = core.create_tab("Plain")
        tab.chat_state.add_question("Simple question")
        tab.chat_state.append_to_answer(0, "Simple complete answer text.")

        content, _ = core._get_export_content_and_title(tab)
        assert "Simple complete answer text." in content


# ---------------------------------------------------------------------------
# Regression guard: no double-HTML
# ---------------------------------------------------------------------------


class TestNoDoubleProcessing:
    """Guard against the regression where HTML output caused double-processing."""

    def test_content_does_not_contain_html_entities(self, core):
        """If content is HTML-encoded (e.g. &amp;), it was processed already."""
        tab = _make_tab_with_chat_state(core, ["5 & 3"], ["Result: 8"])
        content, _ = core._get_export_content_and_title(tab)

        # Plain markdown output should preserve & literally, not as &amp;
        assert "&amp;" not in content

    def test_long_conversation_no_repeated_lines(self, core):
        """Each piece of text appears exactly once — no double-rendering."""
        unique_questions = [f"Unique question number {i}" for i in range(5)]
        unique_answers = [f"Unique answer number {i}" for i in range(5)]
        tab = _make_tab_with_chat_state(core, unique_questions, unique_answers)
        content, _ = core._get_export_content_and_title(tab)

        for text in unique_questions + unique_answers:
            assert (
                content.count(text) == 1
            ), f"{text!r} appeared {content.count(text)} times (expected 1)"
