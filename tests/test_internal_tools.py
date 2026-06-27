"""Tests for internal_tools.py.

These tools are called directly (sync) from chat_tab_tools._execute_tool
instead of going through MCP. Tests exercise each tool function and the
call_tool dispatcher. Return format is always {"content": [...], "isError": bool}.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import Mock
from unittest.mock import patch

import pytest

import internal_tools
from internal_tools import call_tool
from internal_tools import get_time
from internal_tools import list_files
from internal_tools import modify_file
from internal_tools import read_file
from internal_tools import read_file_range
from internal_tools import READ_FILE_RANGE_MAX_LINES
from internal_tools import run_shell_command
from internal_tools import search_files_for_text
from internal_tools import TOOL_SCHEMAS
from internal_tools import write_file


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _text(result: dict[str, Any]) -> str:
    return str(result["content"][0]["text"])


def _ok(result: dict[str, Any]) -> bool:
    return result.get("isError") is False


def _make_file(tmp_path: Path, n_lines: int) -> Path:
    p = tmp_path / "file.txt"
    p.write_text(
        "\n".join(f"line {i}" for i in range(1, n_lines + 1)),
        encoding="utf-8",
    )
    return p


# ---------------------------------------------------------------------------
# TOOL_SCHEMAS
# ---------------------------------------------------------------------------


class TestToolSchemas:
    EXPECTED_NAMES = {
        "internal_get_time",
        "internal_list_files",
        "internal_read_file",
        "internal_read_file_range",
        "internal_write_file",
        "internal_modify_file",
        "internal_search_files_for_text",
        "internal_run_shell_command",
        "internal_search_conversations",
        "internal_get_conversation",
        "internal_get_tool_details",
        "internal_dump_conversations",
    }

    def test_exactly_twelve_schemas(self) -> None:
        assert len(TOOL_SCHEMAS) == 12

    def test_schema_names_match_expected(self) -> None:
        names = {s["function"]["name"] for s in TOOL_SCHEMAS}
        assert names == self.EXPECTED_NAMES

    def test_each_schema_has_type_function(self) -> None:
        for s in TOOL_SCHEMAS:
            assert s["type"] == "function"

    def test_each_schema_has_parameters(self) -> None:
        for s in TOOL_SCHEMAS:
            assert "parameters" in s["function"]


# ---------------------------------------------------------------------------
# call_tool dispatcher
# ---------------------------------------------------------------------------


class TestCallToolDispatcher:
    def test_unknown_tool_returns_error(self) -> None:
        result = call_tool("nonexistent_tool", {})
        assert result["isError"] is True
        assert "Unknown internal tool" in _text(result)

    def test_dispatches_get_time(self) -> None:
        result = call_tool("get_time", {})
        assert _ok(result)
        assert "Current time" in _text(result)

    def test_exception_in_handler_returns_error(self) -> None:
        # _HANDLERS holds a direct reference; patch the dict entry, not the module attr
        original = internal_tools._HANDLERS["get_time"]
        try:
            internal_tools._HANDLERS["get_time"] = Mock(
                side_effect=RuntimeError("boom"),
            )
            result = call_tool("get_time", {})
        finally:
            internal_tools._HANDLERS["get_time"] = original
        assert result["isError"] is True
        assert "boom" in _text(result)


# ---------------------------------------------------------------------------
# get_time
# ---------------------------------------------------------------------------


class TestGetTime:
    def test_returns_current_time_string(self) -> None:
        result = get_time({})
        assert _ok(result)
        text = _text(result)
        assert "Current time:" in text

    def test_time_format_is_datetime(self) -> None:
        import re

        result = get_time({})
        text = _text(result)
        assert re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", text)


# ---------------------------------------------------------------------------
# list_files
# ---------------------------------------------------------------------------


class TestListFiles:
    def test_lists_files_in_directory(self, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_text("x", encoding="utf-8")
        (tmp_path / "b.txt").write_text("x", encoding="utf-8")

        result = list_files({"path": str(tmp_path)})
        assert _ok(result)
        text = _text(result)
        assert "a.txt" in text
        assert "b.txt" in text

    def test_shows_subdirectory_with_slash(self, tmp_path: Path) -> None:
        (tmp_path / "subdir").mkdir()

        result = list_files({"path": str(tmp_path)})
        text = _text(result)
        assert "subdir/" in text

    def test_empty_directory(self, tmp_path: Path) -> None:
        result = list_files({"path": str(tmp_path)})
        assert "empty" in _text(result).lower()

    def test_nonexistent_directory(self, tmp_path: Path) -> None:
        result = list_files({"path": str(tmp_path / "nope")})
        assert "Error" in _text(result)

    def test_defaults_to_current_directory(self) -> None:
        result = list_files({})
        assert _ok(result)


# ---------------------------------------------------------------------------
# read_file
# ---------------------------------------------------------------------------


class TestReadFile:
    def test_reads_file_content(self, tmp_path: Path) -> None:
        p = tmp_path / "hello.txt"
        p.write_text("hello world", encoding="utf-8")

        result = read_file({"file_path": str(p)})
        assert _ok(result)
        assert "hello world" in _text(result)

    def test_includes_content_hash(self, tmp_path: Path) -> None:
        p = tmp_path / "f.txt"
        p.write_text("content", encoding="utf-8")

        result = read_file({"file_path": str(p)})
        assert "hash:" in _text(result)

    def test_adds_syntax_fence_for_python(self, tmp_path: Path) -> None:
        p = tmp_path / "script.py"
        p.write_text("x = 1", encoding="utf-8")

        result = read_file({"file_path": str(p)})
        assert "```python" in _text(result)

    def test_missing_file_path_errors(self) -> None:
        result = read_file({})
        assert result["isError"] is True

    def test_nonexistent_file_errors(self, tmp_path: Path) -> None:
        result = read_file({"file_path": str(tmp_path / "missing.txt")})
        assert "does not exist" in _text(result)

    def test_file_over_1mb_rejected(self, tmp_path: Path) -> None:
        p = tmp_path / "big.txt"
        p.write_bytes(b"x" * (1024 * 1024 + 1))

        result = read_file({"file_path": str(p)})
        assert "too large" in _text(result)


# ---------------------------------------------------------------------------
# read_file_range
# ---------------------------------------------------------------------------


class TestReadFileRange:
    def test_default_range_starts_at_line_one(self, tmp_path: Path) -> None:
        p = _make_file(tmp_path, 1000)
        result = read_file_range({"file_path": str(p)})
        text = _text(result)
        assert "line 1" in text
        assert f"line {READ_FILE_RANGE_MAX_LINES}" in text
        assert f"line {READ_FILE_RANGE_MAX_LINES + 1}" not in text

    def test_explicit_range_returned(self, tmp_path: Path) -> None:
        p = _make_file(tmp_path, 1000)
        result = read_file_range(
            {"file_path": str(p), "start_line": 501, "end_line": 510},
        )
        text = _text(result)
        assert "line 501" in text
        assert "line 510" in text
        assert "line 500" not in text
        assert "line 511" not in text

    def test_range_clamped_to_max_lines(self, tmp_path: Path) -> None:
        p = _make_file(tmp_path, 10_000)
        result = read_file_range(
            {"file_path": str(p), "start_line": 1, "end_line": 9000},
        )
        text = _text(result)
        content_lines = [l for l in text.splitlines() if l.startswith("line ")]
        assert len(content_lines) <= READ_FILE_RANGE_MAX_LINES

    def test_header_reports_total_line_count(self, tmp_path: Path) -> None:
        p = _make_file(tmp_path, 250)
        result = read_file_range({"file_path": str(p), "start_line": 1, "end_line": 5})
        assert "250 lines total" in _text(result)

    def test_start_line_past_eof_errors(self, tmp_path: Path) -> None:
        p = _make_file(tmp_path, 10)
        result = read_file_range({"file_path": str(p), "start_line": 100})
        assert "exceeds total line count" in _text(result)

    def test_missing_file_path_errors(self) -> None:
        result = read_file_range({})
        assert result["isError"] is True

    def test_nonexistent_file_errors(self, tmp_path: Path) -> None:
        result = read_file_range({"file_path": str(tmp_path / "nope.txt")})
        assert "does not exist" in _text(result)

    def test_invalid_start_line_errors(self, tmp_path: Path) -> None:
        p = _make_file(tmp_path, 10)
        result = read_file_range({"file_path": str(p), "start_line": 0})
        assert "start_line" in _text(result)

    def test_end_line_before_start_line_errors(self, tmp_path: Path) -> None:
        p = _make_file(tmp_path, 10)
        result = read_file_range({"file_path": str(p), "start_line": 5, "end_line": 2})
        assert "end_line" in _text(result)

    def test_reads_file_over_1mb(self, tmp_path: Path) -> None:
        p = tmp_path / "huge.txt"
        p.write_text("x" * (2 * 1024 * 1024) + "\nlast line", encoding="utf-8")
        result = read_file_range({"file_path": str(p), "start_line": 2, "end_line": 2})
        assert "last line" in _text(result)


# ---------------------------------------------------------------------------
# write_file
# ---------------------------------------------------------------------------


class TestWriteFile:
    def test_writes_content_to_file(self, tmp_path: Path) -> None:
        p = tmp_path / "out.txt"
        result = write_file({"file_path": str(p), "content": "hello"})
        assert _ok(result)
        assert p.read_text(encoding="utf-8") == "hello"

    def test_success_message_includes_hash(self, tmp_path: Path) -> None:
        p = tmp_path / "out.txt"
        result = write_file({"file_path": str(p), "content": "data"})
        assert "hash:" in _text(result).lower()

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        p = tmp_path / "a" / "b" / "out.txt"
        result = write_file({"file_path": str(p), "content": "x"})
        assert _ok(result)
        assert p.exists()

    def test_overwrites_existing_file(self, tmp_path: Path) -> None:
        p = tmp_path / "f.txt"
        p.write_text("old", encoding="utf-8")
        write_file({"file_path": str(p), "content": "new"})
        assert p.read_text(encoding="utf-8") == "new"

    def test_missing_file_path_errors(self) -> None:
        result = write_file({})
        assert result["isError"] is True


# ---------------------------------------------------------------------------
# modify_file
# ---------------------------------------------------------------------------


class TestModifyFile:
    def test_replaces_string(self, tmp_path: Path) -> None:
        p = tmp_path / "f.txt"
        p.write_text("hello world", encoding="utf-8")

        result = modify_file(
            {
                "file_path": str(p),
                "old_string": "world",
                "new_string": "there",
            },
        )
        assert _ok(result)
        assert p.read_text(encoding="utf-8") == "hello there"

    def test_reports_occurrence_count(self, tmp_path: Path) -> None:
        p = tmp_path / "f.txt"
        p.write_text("a a a", encoding="utf-8")

        result = modify_file(
            {
                "file_path": str(p),
                "old_string": "a",
                "new_string": "b",
                "replace_all": True,
            },
        )
        assert "3" in _text(result)
        assert p.read_text(encoding="utf-8") == "b b b"

    def test_ambiguous_string_without_replace_all_errors(self, tmp_path: Path) -> None:
        p = tmp_path / "f.txt"
        p.write_text("foo foo", encoding="utf-8")

        result = modify_file(
            {
                "file_path": str(p),
                "old_string": "foo",
                "new_string": "bar",
            },
        )
        assert "appears 2 times" in _text(result)

    def test_old_string_not_found_errors(self, tmp_path: Path) -> None:
        p = tmp_path / "f.txt"
        p.write_text("hello", encoding="utf-8")

        result = modify_file(
            {
                "file_path": str(p),
                "old_string": "xyz",
                "new_string": "abc",
            },
        )
        assert "not found" in _text(result)

    def test_same_old_and_new_string_errors(self, tmp_path: Path) -> None:
        p = tmp_path / "f.txt"
        p.write_text("hello", encoding="utf-8")

        result = modify_file(
            {
                "file_path": str(p),
                "old_string": "hello",
                "new_string": "hello",
            },
        )
        assert "must be different" in _text(result)

    def test_missing_old_string_errors(self, tmp_path: Path) -> None:
        p = tmp_path / "f.txt"
        p.write_text("x", encoding="utf-8")
        result = modify_file({"file_path": str(p), "old_string": "", "new_string": "y"})
        assert "Error" in _text(result)

    def test_nonexistent_file_errors(self, tmp_path: Path) -> None:
        result = modify_file(
            {
                "file_path": str(tmp_path / "nope.txt"),
                "old_string": "x",
                "new_string": "y",
            },
        )
        assert "does not exist" in _text(result)


# ---------------------------------------------------------------------------
# search_files_for_text
# ---------------------------------------------------------------------------


class TestSearchFilesForText:
    def test_finds_matching_content(self, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_text("hello world\nfoo bar", encoding="utf-8")

        result = search_files_for_text(
            {
                "directory": str(tmp_path),
                "search_pattern": "hello",
            },
        )
        assert _ok(result)
        assert "hello" in _text(result)

    def test_no_matches_returns_message(self, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_text("foo bar", encoding="utf-8")

        result = search_files_for_text(
            {
                "directory": str(tmp_path),
                "search_pattern": "zzznomatch",
            },
        )
        assert "Total matches: 0" in _text(result)

    def test_missing_pattern_errors(self, tmp_path: Path) -> None:
        result = search_files_for_text({"directory": str(tmp_path)})
        assert "search_pattern" in _text(result)

    def test_nonexistent_directory_errors(self, tmp_path: Path) -> None:
        result = search_files_for_text(
            {
                "directory": str(tmp_path / "nope"),
                "search_pattern": "x",
            },
        )
        assert "Error" in _text(result)

    def test_file_pattern_filter(self, tmp_path: Path) -> None:
        (tmp_path / "match.py").write_text("needle", encoding="utf-8")
        (tmp_path / "skip.txt").write_text("needle", encoding="utf-8")

        result = search_files_for_text(
            {
                "directory": str(tmp_path),
                "search_pattern": "needle",
                "file_pattern": "*.py",
            },
        )
        text = _text(result)
        assert "match.py" in text
        assert "skip.txt" not in text


# ---------------------------------------------------------------------------
# run_shell_command
# ---------------------------------------------------------------------------


class TestRunShellCommand:
    def test_runs_allowed_command(self) -> None:
        result = run_shell_command({"command": "python --version"})
        assert _ok(result)
        assert "Python" in _text(result)

    def test_missing_command_errors(self) -> None:
        result = run_shell_command({})
        assert "Error" in _text(result)
        assert "command" in _text(result).lower()

    def test_disallowed_command_rejected(self) -> None:
        result = run_shell_command({"command": "rm -rf /"})
        text = _text(result)
        assert "allowlist" in text.lower()


# ---------------------------------------------------------------------------
# Internal routing in chat_tab_tools
# ---------------------------------------------------------------------------


class TestInternalToolRouting:
    """Verify that server_name == 'internal' bypasses MCP and calls internal_tools."""

    def _make_handler(self):
        from core.chat_tab_tools import ToolHandler
        import threading

        mock_chat = Mock()
        mock_chat.chat_state = Mock()
        mock_chat._app_core = Mock()
        mock_chat._app_core.api = Mock()
        mock_chat._app_core.api.wait_for_fold_rendered.return_value = True
        mock_chat.stop_streaming_flag = threading.Event()
        mock_chat.content_update_queue = Mock()
        callback = Mock()
        handler = ToolHandler(mock_chat, callback)
        handler._pending_count = 1
        handler._has_tool_calls = True
        return handler, mock_chat

    def test_internal_tool_does_not_call_mcp(self) -> None:
        handler, mock_chat = self._make_handler()

        with patch.object(
            internal_tools,
            "call_tool",
            return_value={
                "content": [{"type": "text", "text": "2026-01-01 00:00:00"}],
                "isError": False,
            },
        ) as mock_call:
            handler._execute_tool("internal", "get_time", {}, 0, "tid-1")

        mock_chat._app_core.call_mcp_tool.assert_not_called()
        mock_call.assert_called_once_with("get_time", {})

    def test_internal_tool_result_persisted(self) -> None:
        handler, mock_chat = self._make_handler()

        with patch.object(
            internal_tools,
            "call_tool",
            return_value={
                "content": [{"type": "text", "text": "the time is now"}],
                "isError": False,
            },
        ):
            handler._execute_tool("internal", "get_time", {}, 0, "tid-2")

        mock_chat.chat_state.add_tool_result_to_answer.assert_called_once()

    def test_non_internal_tool_uses_mcp(self) -> None:
        handler, mock_chat = self._make_handler()
        import threading

        def instant_mcp(server, tool, args, cb):
            cb({"content": [{"type": "text", "text": "ok"}]})

        mock_chat._app_core.call_mcp_tool.side_effect = instant_mcp

        with patch.object(internal_tools, "call_tool") as mock_internal:
            handler._execute_tool(
                "simple-tools",
                "summarize_python_file",
                {},
                0,
                "tid-3",
            )

        mock_internal.assert_not_called()
        mock_chat._app_core.call_mcp_tool.assert_called_once()
