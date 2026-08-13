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

import image_tool_result
import internal_tools
import video_tool_result
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
        "internal_view_image",
        "internal_view_video",
        "internal_write_file",
        "internal_modify_file",
        "internal_search_files_for_text",
        "internal_run_shell_command",
        "internal_search_conversations",
        "internal_get_conversation",
        "internal_get_tool_details",
        "internal_dump_conversations",
    }

    def test_exactly_fourteen_schemas(self) -> None:
        assert len(TOOL_SCHEMAS) == 14

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
# view_image
# ---------------------------------------------------------------------------


def _make_png(tmp_path: Path, name: str, size: tuple[int, int], mode: str = "RGB") -> Path:
    from PIL import Image

    p = tmp_path / name
    color = (255, 0, 0, 128) if mode == "RGBA" else (255, 0, 0)
    Image.new(mode, size, color).save(p, format="PNG")
    return p


class TestViewImage:
    def test_loads_small_png(self, tmp_path: Path) -> None:
        from internal_tools import view_image

        p = _make_png(tmp_path, "small.png", (100, 80))
        result = view_image({"file_path": str(p)})
        assert _ok(result)
        parsed = image_tool_result.parse_image_result(_text(result))
        assert parsed is not None
        mime_type, b64_data, description = parsed
        # Opaque RGB source gets re-encoded as JPEG (smaller for photo-like
        # content); only images with real transparency stay PNG.
        assert mime_type == "image/jpeg"
        assert len(b64_data) > 0
        assert "100x80" in description

    def test_preserves_transparency_as_png(self, tmp_path: Path) -> None:
        from internal_tools import view_image

        p = _make_png(tmp_path, "alpha.png", (50, 50), mode="RGBA")
        result = view_image({"file_path": str(p)})
        assert _ok(result)
        parsed = image_tool_result.parse_image_result(_text(result))
        assert parsed is not None
        mime_type, _b64_data, _description = parsed
        assert mime_type == "image/png"

    def test_downscales_oversized_image(self, tmp_path: Path) -> None:
        from internal_tools import view_image
        from internal_tools import VIEW_IMAGE_DIMENSION_STEPS

        p = _make_png(tmp_path, "huge.png", (3000, 2000))
        result = view_image({"file_path": str(p)})
        assert _ok(result)
        parsed = image_tool_result.parse_image_result(_text(result))
        assert parsed is not None
        _mime_type, b64_data, description = parsed
        assert "downscaled" in description
        # Decode and confirm the actual encoded image respects the largest
        # (first-tried) dimension step.
        import base64
        import io

        from PIL import Image

        decoded = Image.open(io.BytesIO(base64.b64decode(b64_data)))
        assert max(decoded.size) <= VIEW_IMAGE_DIMENSION_STEPS[0]

    def test_small_image_not_downscaled(self, tmp_path: Path) -> None:
        from internal_tools import view_image

        p = _make_png(tmp_path, "small.png", (100, 80))
        result = view_image({"file_path": str(p)})
        parsed = image_tool_result.parse_image_result(_text(result))
        assert parsed is not None
        _mime_type, _b64_data, description = parsed
        assert "downscaled" not in description

    def test_nonexistent_file_errors(self, tmp_path: Path) -> None:
        from internal_tools import view_image

        result = view_image({"file_path": str(tmp_path / "missing.png")})
        assert result["isError"] is True
        assert "does not exist" in _text(result)

    def test_missing_file_path_errors(self) -> None:
        from internal_tools import view_image

        result = view_image({})
        assert result["isError"] is True

    def test_non_image_file_errors_cleanly(self, tmp_path: Path) -> None:
        from internal_tools import view_image

        p = tmp_path / "notanimage.png"
        p.write_text("this is definitely not image bytes", encoding="utf-8")
        result = view_image({"file_path": str(p)})
        assert result["isError"] is True
        assert "could not be read as an image" in _text(result)

    def test_tramp_path_rejected(self, tmp_path: Path) -> None:
        from internal_tools import view_image

        result = view_image({"file_path": "/ssh:user@host:/tmp/screenshot.png"})
        assert result["isError"] is True
        assert "TRAMP" in _text(result) or "remote" in _text(result).lower()

    def test_result_survives_gate_tool_output_unchanged(self, tmp_path: Path) -> None:
        """The whole reason gate_tool_output must special-case the image
        sentinel: without that, a large-enough encoded image would get
        byte-truncated here and the base64 would come out corrupt.
        """
        from core.tool_output_gate import gate_tool_output
        from internal_tools import view_image

        p = _make_png(tmp_path, "img.png", (200, 200))
        result = view_image({"file_path": str(p)})
        raw_text = _text(result)
        gated = gate_tool_output(raw_text, "tab-1", "tool-1", "view_image", threshold=10)
        assert gated == raw_text

    def test_falls_back_to_smaller_step_when_first_does_not_fit(self) -> None:
        """Regression guard for a real bug in the first version of this
        fallback: an already-small source image skipped every step past
        the first because of incorrect "already smaller than this step"
        branching, so it never actually retried at a smaller size even
        when the first attempt didn't fit. thumbnail() is a safe no-op on
        an already-small image, so every step must always be tried.

        Rather than guess a budget against real JPEG compression behavior
        (fragile — noise doesn't compress predictably), measure the actual
        encoded sizes at the two dimension steps directly, then pick a
        budget strictly between them: too small for the first step to
        hit at any quality, but achievable at the second. If the fallback
        didn't actually retry at a smaller size, this budget would be
        unreachable and the function would return None.
        """
        from internal_tools import _encode_image_under_limit
        from internal_tools import VIEW_IMAGE_DIMENSION_STEPS
        from internal_tools import VIEW_IMAGE_JPEG_QUALITY_STEPS
        from PIL import Image
        import io
        import random

        random.seed(0)
        # Must be larger than the *first two* dimension steps, or
        # thumbnail() is a no-op for both and they'd encode identically —
        # nothing to actually compare.
        size = (2000, 2000)
        img = Image.new("RGB", size)
        img.putdata([tuple(random.randint(0, 255) for _ in range(3)) for _ in range(size[0] * size[1])])

        def encoded_size(max_dim: int, quality: int) -> int:
            candidate = img.copy()
            candidate.thumbnail((max_dim, max_dim), Image.LANCZOS)
            buf = io.BytesIO()
            candidate.save(buf, format="JPEG", quality=quality)
            return len(buf.getvalue())

        first_dim, second_dim = VIEW_IMAGE_DIMENSION_STEPS[0], VIEW_IMAGE_DIMENSION_STEPS[1]
        smallest_quality = VIEW_IMAGE_JPEG_QUALITY_STEPS[-1]
        size_at_first_step = encoded_size(first_dim, smallest_quality)
        size_at_second_step = encoded_size(second_dim, smallest_quality)
        assert size_at_second_step < size_at_first_step, (
            "test setup assumption broken: a smaller dimension step should "
            "encode smaller — can't construct a meaningful budget without this"
        )

        budget = (size_at_first_step + size_at_second_step) // 2
        fitted = _encode_image_under_limit(img, budget)
        assert fitted is not None
        _encoded, _mime, final_size = fitted
        assert max(final_size) <= second_dim
        assert max(final_size) < first_dim

    def test_refusal_message_does_not_suggest_unsupported_actions(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The model has no crop/resize tool of its own — telling it to
        'try a smaller image or crop the region' is actionable-*sounding*
        advice with no real tool behind it, exactly the kind of thing that
        drove the real 117-call repeat loop investigated separately. When
        every fallback step still doesn't fit, the message must say so
        plainly and explicitly rule out retrying, not suggest self-service
        actions that don't exist.
        """
        import internal_tools

        p = _make_png(tmp_path, "img.png", (200, 200))
        # No step can possibly fit an impossible budget — forces the
        # genuine "nothing worked" refusal path.
        monkeypatch.setattr(internal_tools, "VIEW_IMAGE_MAX_ENCODED_BYTES", 1)

        result = internal_tools.view_image({"file_path": str(p)})
        assert result["isError"] is True
        text = _text(result)
        assert "not supported" in text
        assert "retrying" in text or "retry" in text
        assert "crop" not in text.lower()

    def test_encode_under_limit_returns_none_when_nothing_fits(self) -> None:
        from internal_tools import _encode_image_under_limit
        from PIL import Image

        img = Image.new("RGB", (100, 100), (255, 0, 0))
        assert _encode_image_under_limit(img, 1) is None

    def test_encode_under_limit_thumbnail_is_safe_noop_on_small_source(self) -> None:
        """thumbnail() must never enlarge — confirms the no-special-casing
        simplification is actually safe for a source already smaller than
        every dimension step.
        """
        from internal_tools import _encode_image_under_limit
        from PIL import Image

        img = Image.new("RGB", (50, 50), (0, 255, 0))
        fitted = _encode_image_under_limit(img, 5 * 1024 * 1024)
        assert fitted is not None
        _encoded, _mime, final_size = fitted
        assert final_size == (50, 50)


class TestViewVideo:
    def test_returns_metadata_reference_without_video_bytes(self, tmp_path: Path) -> None:
        video = tmp_path / "demo.webm"
        video.write_bytes(b"\x1aE\xdf\xa3" + b"video payload")

        result = internal_tools.view_video({"file_path": str(video)})

        assert _ok(result)
        text = _text(result)
        parsed = video_tool_result.parse_video_result(text)
        assert parsed is not None
        mime_type, locator, size, description = parsed
        assert mime_type == "video/webm"
        assert size == video.stat().st_size
        assert "video payload" not in text
        assert "Loaded video" in description
        chunk = video_tool_result.read_video_chunk(locator, 0)
        assert chunk["done"] is True
        assert chunk["size"] == size

    def test_rejects_unknown_container(self, tmp_path: Path) -> None:
        video = tmp_path / "demo.mp4"
        video.write_bytes(b"not really a video")

        result = internal_tools.view_video({"file_path": str(video)})

        assert result["isError"] is True
        assert "Unsupported video format" in _text(result)


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
    def test_runs_command(self) -> None:
        result = run_shell_command({"command": "python --version"})
        assert _ok(result)
        assert "Python" in _text(result)

    def test_missing_command_errors(self) -> None:
        result = run_shell_command({})
        assert "Error" in _text(result)
        assert "command" in _text(result).lower()


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
