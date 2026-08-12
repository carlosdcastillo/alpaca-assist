"""
Comprehensive tests for chat_state.py module.

This module tests chat state management, answer components, and conversation handling.
"""
import json
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest

from chat_state import ChatState
from chat_state import FullAnswer
from chat_state import ToolCall
from chat_state import ToolResult


class TestToolCall:
    """Tests for ToolCall dataclass."""

    def test_tool_call_creation(self) -> None:
        """Test creating ToolCall instance."""
        tool_call = ToolCall(
            id="123",
            content='{"tool_call": {"name": "test_tool"}}',
        )

        assert tool_call.id == "123"
        assert tool_call.content == '{"tool_call": {"name": "test_tool"}}'


class TestToolResult:
    """Tests for ToolResult dataclass."""

    def test_tool_result_creation(self) -> None:
        """Test creating ToolResult instance."""
        tool_result = ToolResult(
            id="123",
            content="Result content",
        )

        assert tool_result.id == "123"
        assert tool_result.content == "Result content"


class TestFullAnswerInitialization:
    """Tests for FullAnswer initialization."""

    def test_full_answer_empty(self) -> None:
        """Test creating empty FullAnswer."""
        answer = FullAnswer()

        assert answer.components == []

    def test_full_answer_with_components(self) -> None:
        """Test creating FullAnswer with components."""
        components: list[str | ToolCall | ToolResult] = ["Hello", " World"]
        answer = FullAnswer(components=components)

        assert len(answer.components) == 2
        assert answer.components[0] == "Hello"


class TestFullAnswerAddText:
    """Tests for adding text to FullAnswer."""

    def test_add_text(self) -> None:
        """Test adding text to answer."""
        answer = FullAnswer()
        answer.add_text("Hello")

        assert len(answer.components) == 1
        assert answer.components[0] == "Hello"

    def test_add_text_multiple(self) -> None:
        """Test adding multiple text segments."""
        answer = FullAnswer()
        answer.add_text("Hello")
        answer.add_text(" World")

        assert len(answer.components) == 1  # Consolidated
        assert answer.components[0] == "Hello World"

    def test_add_text_empty(self) -> None:
        """Test adding empty text."""
        answer = FullAnswer()
        answer.add_text("")

        assert len(answer.components) == 1
        assert answer.components[0] == ""


class TestFullAnswerToolOperations:
    """Tests for tool operations in FullAnswer."""

    def test_add_tool_call(self) -> None:
        """Test adding tool call."""
        answer = FullAnswer()
        answer.add_tool_call('{"tool_call": {"id": "123"}}', "123")

        assert len(answer.components) == 1
        assert isinstance(answer.components[0], ToolCall)
        assert answer.components[0].id == "123"

    def test_add_tool_result(self) -> None:
        """Test adding tool result."""
        answer = FullAnswer()
        answer.add_tool_result("Tool output", "123")

        assert len(answer.components) == 1
        assert isinstance(answer.components[0], ToolResult)
        assert answer.components[0].id == "123"

    def test_add_tool_call_if_absent_new(self) -> None:
        """Test adding tool call if absent when not present."""
        answer = FullAnswer()
        result = answer.add_tool_call_if_absent('{"tool_call": {"id": "123"}}', "123")

        assert result is True
        assert len(answer.components) == 1

    def test_add_tool_call_if_absent_existing(self) -> None:
        """Test adding tool call if absent when already present."""
        answer = FullAnswer()
        answer.add_tool_call('{"tool_call": {"id": "123"}}', "123")

        result = answer.add_tool_call_if_absent('{"tool_call": {"id": "123"}}', "123")

        assert result is False
        assert len(answer.components) == 1  # Should not add duplicate


class TestFullAnswerGetContent:
    """Tests for getting content from FullAnswer."""

    def test_get_text_content(self) -> None:
        """Test getting text content."""
        answer = FullAnswer()
        answer.add_text("Hello")
        answer.add_tool_call('{"tool_call": {}}', "123")
        answer.add_tool_result("Result", "123")

        text = answer.get_text_content()

        assert "Hello" in text
        assert '{"tool_call": {}}' in text
        assert "Result" in text

    def test_get_text_only_content(self) -> None:
        """Test getting only text content (no tools)."""
        answer = FullAnswer()
        answer.add_text("Hello")
        answer.add_tool_call('{"tool_call": {}}', "123")
        answer.add_text(" World")

        text = answer.get_text_only_content()

        assert text == "Hello World"
        assert "tool_call" not in text

    def test_get_text_content_empty(self) -> None:
        """Test getting text content from empty answer."""
        answer = FullAnswer()

        text = answer.get_text_content()

        assert text == ""


class TestFullAnswerTrimAndRemove:
    """Tests for trimming and removing content."""

    def test_trim_text(self) -> None:
        """Test trimming text from answer."""
        answer = FullAnswer()
        answer.add_text("Hello World")

        answer.trim_text(5)

        assert answer.get_text_only_content() == "Hello "

    def test_trim_text_multiple_components(self) -> None:
        """Test trimming text across multiple components."""
        answer = FullAnswer()
        answer.add_text("Hello")
        answer.add_tool_call('{"tool_call": {}}', "123")
        answer.add_text(" World")  # This will be a separate component after tool call

        answer.trim_text(3)

        # Should trim from the last text component
        assert " Wor" not in answer.get_text_only_content()

    def test_remove_tool_components(self) -> None:
        """Test removing tool components."""
        answer = FullAnswer()
        answer.add_text("Hello")
        answer.add_tool_call('{"tool_call": {}}', "123")
        answer.add_tool_result("Result", "123")
        answer.add_text(" World")

        answer.remove_tool_components()

        assert len(answer.components) == 2
        assert all(isinstance(c, str) for c in answer.components)

    def test_consolidate_pre_tool_text(self) -> None:
        """Test consolidating text before tool calls."""
        answer = FullAnswer()
        answer.add_text("Hello")
        answer.add_tool_call('{"tool_call": {}}', "123")

        answer.consolidate_pre_tool_text()

        # Should have text consolidated before tool call
        text_components = [c for c in answer.components if isinstance(c, str)]
        tool_calls = [c for c in answer.components if isinstance(c, ToolCall)]
        assert len(tool_calls) == 1


class TestFullAnswerToolInteractions:
    """Tests for tool interaction queries."""

    def test_has_tool_interactions_true(self) -> None:
        """Test detecting tool interactions."""
        answer = FullAnswer()
        answer.add_tool_call('{"tool_call": {}}', "123")
        answer.add_tool_result("Result", "123")

        assert answer.has_tool_interactions() is True

    def test_has_tool_interactions_false(self) -> None:
        """Test detecting no tool interactions."""
        answer = FullAnswer()
        answer.add_text("Just text")

        assert answer.has_tool_interactions() is False

    def test_get_tool_interactions(self) -> None:
        """Test getting tool interactions list."""
        answer = FullAnswer()
        answer.add_tool_call('{"tool_call": {"name": "tool1"}}', "123")
        answer.add_tool_result("Result", "123")

        interactions = answer.get_tool_interactions()

        assert len(interactions) == 1
        assert interactions[0][0] == '{"tool_call": {"name": "tool1"}}'
        assert interactions[0][1] == "123"
        assert interactions[0][2] == "Result"


class TestFullAnswerSerialization:
    """Tests for FullAnswer serialization."""

    def test_to_dict(self) -> None:
        """Test converting to dictionary."""
        answer = FullAnswer()
        answer.add_text("Hello")
        answer.add_tool_call('{"tool_call": {}}', "123")

        data = answer.to_dict()

        assert "components" in data
        assert len(data["components"]) == 2
        assert data["components"][0]["type"] == "text"

    def test_from_dict(self) -> None:
        """Test creating from dictionary."""
        data = {
            "components": [
                {"type": "text", "content": "Hello"},
                {"type": "tool_call", "content": "{}", "id": "123"},
            ],
        }

        answer = FullAnswer.from_dict(data)

        assert len(answer.components) == 2
        assert answer.components[0] == "Hello"
        assert isinstance(answer.components[1], ToolCall)
        assert answer.components[1].id == "123"

    def test_from_string(self) -> None:
        """Test creating from string."""
        answer = FullAnswer.from_string("Simple text answer")

        assert len(answer.components) == 1
        assert answer.components[0] == "Simple text answer"

    def test_roundtrip_serialization(self) -> None:
        """Test roundtrip serialization."""
        original = FullAnswer()
        original.add_text("Hello")
        original.add_tool_call('{"tool_call": {}}', "123")
        original.add_tool_result("Result", "123")

        data = original.to_dict()
        restored = FullAnswer.from_dict(data)

        assert len(restored.components) == len(original.components)
        assert restored.get_text_content() == original.get_text_content()


class TestFullAnswerCopy:
    """Tests for copying FullAnswer."""

    def test_copy(self) -> None:
        """Test copying answer."""
        original = FullAnswer()
        original.add_text("Hello")
        original.add_tool_call('{"tool_call": {}}', "123")

        copy = original.copy()

        assert copy is not original
        assert len(copy.components) == len(original.components)
        assert copy.get_text_content() == original.get_text_content()

    def test_copy_is_independent(self) -> None:
        """Test that copy is independent of original."""
        original = FullAnswer()
        original.add_text("Hello")

        copy = original.copy()
        copy.add_text(" World")

        assert " World" not in original.get_text_content()
        assert " World" in copy.get_text_content()


class TestChatStateInitialization:
    """Tests for ChatState initialization."""

    def test_init_empty(self) -> None:
        """Test creating empty ChatState."""
        state = ChatState(questions=[], answers=[])

        assert state.questions == []
        assert state.answers == []
        assert state.current_streaming_index is None


class TestChatStateQuestions:
    """Tests for question operations in ChatState."""

    def test_add_question(self) -> None:
        """Test adding a question."""
        state = ChatState(questions=[], answers=[])
        index = state.add_question("Hello?")

        assert index == 0
        assert len(state.questions) == 1
        assert state.questions[0] == "Hello?"

    def test_add_question_multiple(self) -> None:
        """Test adding multiple questions."""
        state = ChatState(questions=[], answers=[])
        idx1 = state.add_question("First?")
        idx2 = state.add_question("Second?")

        assert idx1 == 0
        assert idx2 == 1
        assert len(state.questions) == 2

    def test_add_question_creates_answer(self) -> None:
        """Test that adding question creates corresponding answer."""
        state = ChatState(questions=[], answers=[])
        state.add_question("Hello?")

        assert len(state.answers) == 1
        assert isinstance(state.answers[0], FullAnswer)

    def test_add_question_sets_streaming(self) -> None:
        """Test that adding question sets streaming index."""
        state = ChatState(questions=[], answers=[])
        state.add_question("Hello?")

        assert state.current_streaming_index == 0


class TestChatStateAnswers:
    """Tests for answer operations in ChatState."""

    def test_append_to_answer(self) -> None:
        """Test appending to answer."""
        state = ChatState(questions=[], answers=[])
        state.add_question("Hello?")
        result = state.append_to_answer(0, "Hi there!")

        assert result is True
        assert "Hi there!" in state.answers[0].get_text_content()

    def test_append_to_answer_invalid_index(self) -> None:
        """Test appending to invalid answer index."""
        state = ChatState(questions=[], answers=[])
        result = state.append_to_answer(0, "Hi")

        assert result is False

    def test_append_to_answer_while_streaming(self) -> None:
        """Test appending while streaming is active."""
        state = ChatState(questions=[], answers=[])
        state.add_question("Hello?")
        state.append_to_answer(0, "Hi")
        state.append_to_answer(0, " there")

        assert state.answers[0].get_text_content() == "Hi there"


class TestChatStateToolOperations:
    """Tests for tool operations in ChatState."""

    def test_add_tool_call_to_answer(self) -> None:
        """Test adding tool call to answer."""
        state = ChatState(questions=[], answers=[])
        state.add_question("Hello?")
        result = state.add_tool_call_to_answer(0, '{"tool_call": {}}', "123")

        assert result is True
        assert state.answers[0].has_tool_interactions()

    def test_add_tool_result_to_answer(self) -> None:
        """Test adding tool result to answer."""
        state = ChatState(questions=[], answers=[])
        state.add_question("Hello?")
        state.add_tool_call_to_answer(0, '{"tool_call": {}}', "123")
        result = state.add_tool_result_to_answer(0, "Result", "123")

        assert result is True
        interactions = state.answers[0].get_tool_interactions()
        assert len(interactions) == 1

    def test_add_tool_to_invalid_index(self) -> None:
        """Test adding tool to invalid answer index."""
        state = ChatState(questions=[], answers=[])

        result = state.add_tool_call_to_answer(0, '{"tool_call": {}}', "123")
        assert result is False


class TestChatStateStreaming:
    """Tests for streaming state management."""

    def test_is_streaming_true(self) -> None:
        """Test checking streaming state when active."""
        state = ChatState(questions=[], answers=[])
        state.add_question("Hello?")

        assert state.is_streaming() is True

    def test_is_streaming_false(self) -> None:
        """Test checking streaming state when not active."""
        state = ChatState(questions=[], answers=[])
        state.add_question("Hello?")
        state.finish_streaming()

        assert state.is_streaming() is False

    def test_finish_streaming(self) -> None:
        """Test finishing streaming."""
        state = ChatState(questions=[], answers=[])
        state.add_question("Hello?")
        state.finish_streaming()

        assert state.current_streaming_index is None

    def test_finish_streaming_not_started(self) -> None:
        """Test finishing streaming when not started."""
        state = ChatState(questions=[], answers=[])
        state.finish_streaming()  # Should not raise

        assert state.current_streaming_index is None


class TestChatStateGetDisplayText:
    """Tests for getting display text."""

    def test_get_display_text_with_tools(self) -> None:
        """Test getting display text including tools."""
        state = ChatState(questions=[], answers=[])
        state.add_question("Hello?")
        state.append_to_answer(0, "Answer")
        state.add_tool_call_to_answer(0, '{"tool_call": {}}', "123")
        state.add_tool_result_to_answer(0, "Result", "123")
        state.finish_streaming()

        text = state.get_display_text(include_tool_content=True)

        assert "Hello?" in text
        assert "Answer" in text
        assert "Result" in text

    def test_get_display_text_without_tools(self) -> None:
        """Test getting display text excluding tools."""
        state = ChatState(questions=[], answers=[])
        state.add_question("Hello?")
        state.append_to_answer(0, "Answer")
        state.add_tool_call_to_answer(0, '{"tool_call": {}}', "123")
        state.add_tool_result_to_answer(0, "Result", "123")
        state.finish_streaming()

        text = state.get_display_text(include_tool_content=False)

        assert "Hello?" in text
        assert "Answer" in text
        assert "Result" not in text

    def test_get_display_text_empty(self) -> None:
        """Test getting display text from empty state."""
        state = ChatState(questions=[], answers=[])

        text = state.get_display_text()

        assert text == ""


class TestChatStateSafeCopy:
    """Tests for safe copy operations."""

    def test_get_safe_copy(self) -> None:
        """Test getting safe copy."""
        state = ChatState(questions=[], answers=[])
        state.add_question("Hello?")
        state.append_to_answer(0, "Answer")
        state.finish_streaming()

        questions, answers, streaming_idx = state.get_safe_copy()

        assert questions == ["Hello?"]
        assert answers == ["Answer"]
        assert streaming_idx is None

    def test_get_safe_copy_full(self) -> None:
        """Test getting full safe copy."""
        state = ChatState(questions=[], answers=[])
        state.add_question("Hello?")
        state.append_to_answer(0, "Answer")
        state.finish_streaming()

        questions, answers, streaming_idx = state.get_safe_copy_full()

        assert questions == ["Hello?"]
        assert len(answers) == 1
        assert isinstance(answers[0], FullAnswer)
        assert streaming_idx is None


class TestChatStatePopAndTruncate:
    """Tests for pop and truncate operations."""

    def test_pop_last_qa_success(self) -> None:
        """Test popping last Q&A pair."""
        state = ChatState(questions=[], answers=[])
        state.add_question("First?")
        state.append_to_answer(0, "First answer")
        state.finish_streaming()
        state.add_question("Second?")
        state.append_to_answer(1, "Second answer")
        state.finish_streaming()

        success, images = state.pop_last_qa()

        assert success is True
        assert len(state.questions) == 1
        assert state.questions[0] == "First?"

    def test_pop_last_qa_empty(self) -> None:
        """Test popping from empty state."""
        state = ChatState(questions=[], answers=[])

        success, images = state.pop_last_qa()

        assert success is False
        assert images == []

    def test_truncate_to_last(self) -> None:
        """Test truncating to last Q&A pair."""
        state = ChatState(questions=[], answers=[])
        state.add_question("First?")
        state.finish_streaming()
        state.add_question("Second?")
        state.finish_streaming()
        state.add_question("Third?")
        state.finish_streaming()

        result = state.truncate_to_last()

        assert result is True
        assert len(state.questions) == 1
        assert state.questions[0] == "Third?"

    def test_truncate_to_last_single(self) -> None:
        """Test truncating with single Q&A pair returns False (nothing to truncate)."""
        state = ChatState(questions=[], answers=[])
        state.add_question("Only?")
        state.finish_streaming()

        result = state.truncate_to_last()

        # With only one pair, there's nothing to truncate
        assert result is False
        assert len(state.questions) == 1

    def test_truncate_to_last_empty(self) -> None:
        """Test truncating empty state."""
        state = ChatState(questions=[], answers=[])

        result = state.truncate_to_last()

        assert result is False


class TestChatStateCompact:
    """Tests for compacting answers."""

    def test_compact_answers(self) -> None:
        """Test compacting answers."""
        state = ChatState(questions=[], answers=[])
        state.add_question("Hello?")
        state.append_to_answer(0, "Answer text")
        state.finish_streaming()

        state.compact_answers()

        # Should consolidate components
        assert len(state.answers[0].components) >= 1

    def test_compact_answers_with_strings(self) -> None:
        """Test compacting answers with provided strings."""
        state = ChatState(questions=[], answers=[])
        state.add_question("Hello?")
        state.append_to_answer(0, "Old answer")
        state.finish_streaming()

        state.compact_answers(["New compacted answer"])

        assert state.answers[0].get_text_only_content() == "New compacted answer"


class TestChatStateSerialization:
    """Tests for ChatState serialization."""

    def test_to_dict(self) -> None:
        """Test converting to dictionary."""
        state = ChatState(questions=[], answers=[])
        state.add_question("Hello?")
        state.append_to_answer(0, "Answer")
        state.finish_streaming()

        data = state.to_dict()

        assert "questions" in data
        assert "answers" in data
        assert data["questions"] == ["Hello?"]

    def test_from_dict(self) -> None:
        """Test creating from dictionary."""
        data = {
            "questions": ["Hello?"],
            "answers": [{"components": [{"type": "text", "content": "Answer"}]}],
            "current_streaming_index": None,
        }

        state = ChatState.from_dict(data)

        assert len(state.questions) == 1
        assert state.questions[0] == "Hello?"
        assert len(state.answers) == 1

    def test_roundtrip_serialization(self) -> None:
        """Test roundtrip serialization."""
        original = ChatState(questions=[], answers=[])
        original.add_question("Hello?")
        original.append_to_answer(0, "Answer")
        original.finish_streaming()

        data = original.to_dict()
        restored = ChatState.from_dict(data)

        assert restored.questions == original.questions
        assert len(restored.answers) == len(original.answers)


class TestChatStateEdgeCases:
    """Tests for edge cases."""

    def test_append_to_answer_while_not_streaming(self) -> None:
        """Test appending when not streaming."""
        state = ChatState(questions=[], answers=[])
        state.add_question("Hello?")
        state.finish_streaming()

        result = state.append_to_answer(0, "More text")

        # Should still work
        assert result is True
        assert "More text" in state.answers[0].get_text_content()

    def test_add_question_while_streaming(self) -> None:
        """Test adding question while streaming."""
        state = ChatState(questions=[], answers=[])
        state.add_question("First?")
        # Don't finish streaming

        # Adding another question should update streaming index
        state.add_question("Second?")

        assert state.current_streaming_index == 1
        assert len(state.questions) == 2

    def test_large_content_handling(self) -> None:
        """Test handling large content."""
        state = ChatState(questions=[], answers=[])
        large_text = "x" * 10000

        state.add_question(large_text)
        state.append_to_answer(0, large_text)
        state.finish_streaming()

        assert state.questions[0] == large_text
        assert large_text in state.answers[0].get_text_content()

    def test_special_characters_handling(self) -> None:
        """Test handling special characters."""
        state = ChatState(questions=[], answers=[])
        special_text = "Special: <>&\"' ñ 中文 🎉"

        state.add_question(special_text)
        state.append_to_answer(0, special_text)
        state.finish_streaming()

        assert special_text in state.questions[0]
        assert special_text in state.answers[0].get_text_content()

    def test_concurrent_modifications(self) -> None:
        """Test handling concurrent-like modifications."""
        state = ChatState(questions=[], answers=[])
        state.add_question("Hello?")

        # Multiple appends
        for i in range(100):
            state.append_to_answer(0, f" {i}")

        content = state.answers[0].get_text_content()
        assert " 0" in content
        assert " 99" in content


class TestChatStateIntegration:
    """Integration tests for ChatState."""

    def test_full_conversation_flow(self) -> None:
        """Test complete conversation flow."""
        state = ChatState(questions=[], answers=[])

        # First Q&A
        state.add_question("What is Python?")
        state.append_to_answer(0, "Python is a programming language.")
        state.finish_streaming()

        # Second Q&A
        state.add_question("What are its features?")
        state.append_to_answer(1, "It has many features including-")
        state.add_tool_call_to_answer(
            1,
            '{"tool_call": {"name": "list_features"}}',
            "tool-1",
        )
        state.add_tool_result_to_answer(
            1,
            "Features: simple, readable, versatile",
            "tool-1",
        )
        state.append_to_answer(1, " simple syntax, readability, and versatility.")
        state.finish_streaming()

        # Verify state
        assert len(state.questions) == 2
        assert len(state.answers) == 2
        assert state.answers[1].has_tool_interactions()

        # Get display text
        text = state.get_display_text()
        assert "What is Python?" in text
        assert "What are its features?" in text
        assert "versatility" in text

        # Serialize and restore
        data = state.to_dict()
        restored = ChatState.from_dict(data)

        assert len(restored.questions) == 2
        assert restored.answers[1].has_tool_interactions()
