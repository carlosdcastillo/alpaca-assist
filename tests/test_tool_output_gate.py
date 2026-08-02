"""Tests for core.tool_output_gate module."""
from __future__ import annotations

from pathlib import Path

import pytest

import json

import core.tool_output_gate as gate_module
from core.tool_output_gate import CALL_ARG_GATE_THRESHOLD_BYTES
from core.tool_output_gate import cleanup_tab_output_dir
from core.tool_output_gate import GATE_THRESHOLD_BYTES
from core.tool_output_gate import gate_tool_call_arguments
from core.tool_output_gate import gate_tool_output
from core.tool_output_gate import PREVIEW_MAX_BYTES
from core.tool_output_gate import PREVIEW_MAX_LINES
from core.tool_output_gate import sweep_orphaned_output_dirs


@pytest.fixture(autouse=True)
def _isolated_temp_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the gate's temp root at a pytest tmp_path instead of the real OS temp dir."""
    root = tmp_path / "alpaca_assist_tool_outputs"
    monkeypatch.setattr(gate_module, "TOOL_OUTPUT_TEMP_ROOT", root)
    return root


class TestGateToolOutputUnderThreshold:
    """Small results should pass through untouched."""

    def test_short_text_returned_unchanged(self) -> None:
        result = gate_tool_output("short result", "tab-1", "tool-1", "echo")
        assert result == "short result"

    def test_text_exactly_at_threshold_returned_unchanged(self) -> None:
        text = "x" * GATE_THRESHOLD_BYTES
        result = gate_tool_output(text, "tab-1", "tool-1", "echo")
        assert result == text

    def test_no_file_written_when_under_threshold(self, tmp_path: Path) -> None:
        gate_tool_output("short result", "tab-1", "tool-1", "echo")
        assert not gate_module.TOOL_OUTPUT_TEMP_ROOT.exists()


class TestGateToolOutputOverThreshold:
    """Large results should be truncated, with the full text saved to disk."""

    def test_oversized_text_is_truncated(self) -> None:
        text = "x" * (GATE_THRESHOLD_BYTES + 1)
        result = gate_tool_output(text, "tab-1", "tool-1", "echo")
        assert len(result.encode("utf-8")) < len(text.encode("utf-8"))

    def test_notice_mentions_truncation_and_pointer(self) -> None:
        text = "\n".join(f"line {i}" for i in range(10_000))
        result = gate_tool_output(text, "tab-1", "tool-1", "search_files_for_text")
        assert "Output truncated" in result
        assert "Full output saved to:" in result
        assert "read_file_range" in result

    def test_full_content_written_to_temp_file(self) -> None:
        text = "\n".join(f"line {i}" for i in range(10_000))
        gate_tool_output(text, "tab-42", "tool-99", "search_files_for_text")

        tab_dir = gate_module.TOOL_OUTPUT_TEMP_ROOT / "tab-42"
        files = list(tab_dir.glob("*.txt"))
        assert len(files) == 1
        assert files[0].read_text(encoding="utf-8") == text

    def test_temp_file_name_includes_tool_id_and_name(self) -> None:
        text = "x" * (GATE_THRESHOLD_BYTES + 1)
        gate_tool_output(text, "tab-1", "tool-abc123", "fetch_url_as_markdown")

        tab_dir = gate_module.TOOL_OUTPUT_TEMP_ROOT / "tab-1"
        files = list(tab_dir.glob("*.txt"))
        assert len(files) == 1
        assert "tool-abc123" in files[0].name
        assert "fetch_url_as_markdown" in files[0].name

    def test_preview_capped_at_max_lines(self) -> None:
        text = "\n".join(f"line {i}" for i in range(10_000))
        result = gate_tool_output(text, "tab-1", "tool-1", "search_files_for_text")

        # Preview body is everything after the notice's blank-line separator.
        preview = result.split("\n\n", 1)[1]
        assert len(preview.splitlines()) <= PREVIEW_MAX_LINES

    def test_preview_capped_at_max_bytes_for_long_single_line(self) -> None:
        # One giant line — a pure line-count cap would dump it whole.
        text = "y" * (GATE_THRESHOLD_BYTES + 1)
        result = gate_tool_output(text, "tab-1", "tool-1", "echo")

        preview = result.split("\n\n", 1)[1]
        assert len(preview.encode("utf-8")) <= PREVIEW_MAX_BYTES

    def test_unsafe_id_sanitized_in_filename(self) -> None:
        text = "x" * (GATE_THRESHOLD_BYTES + 1)
        gate_tool_output(text, "tab/../1", "tool/../id", "echo")

        # Should not have escaped the temp root via path traversal.
        all_files = list(gate_module.TOOL_OUTPUT_TEMP_ROOT.rglob("*.txt"))
        assert len(all_files) == 1
        for f in all_files:
            assert gate_module.TOOL_OUTPUT_TEMP_ROOT in f.parents


class TestCleanupTabOutputDir:
    """cleanup_tab_output_dir should remove a tab's directory, called on tab close."""

    def test_removes_existing_tab_dir(self) -> None:
        text = "x" * (GATE_THRESHOLD_BYTES + 1)
        gate_tool_output(text, "tab-1", "tool-1", "echo")
        tab_dir = gate_module.TOOL_OUTPUT_TEMP_ROOT / "tab-1"
        assert tab_dir.exists()

        cleanup_tab_output_dir("tab-1")

        assert not tab_dir.exists()

    def test_nonexistent_tab_dir_does_not_raise(self) -> None:
        cleanup_tab_output_dir("never-created-tab")

    def test_does_not_affect_other_tabs(self) -> None:
        text = "x" * (GATE_THRESHOLD_BYTES + 1)
        gate_tool_output(text, "tab-1", "tool-1", "echo")
        gate_tool_output(text, "tab-2", "tool-1", "echo")

        cleanup_tab_output_dir("tab-1")

        assert not (gate_module.TOOL_OUTPUT_TEMP_ROOT / "tab-1").exists()
        assert (gate_module.TOOL_OUTPUT_TEMP_ROOT / "tab-2").exists()


class TestSweepOrphanedOutputDirs:
    """sweep_orphaned_output_dirs should wipe the whole root, called on app startup."""

    def test_removes_entire_root(self) -> None:
        text = "x" * (GATE_THRESHOLD_BYTES + 1)
        gate_tool_output(text, "tab-1", "tool-1", "echo")
        gate_tool_output(text, "tab-2", "tool-1", "echo")
        assert gate_module.TOOL_OUTPUT_TEMP_ROOT.exists()

        sweep_orphaned_output_dirs()

        assert not gate_module.TOOL_OUTPUT_TEMP_ROOT.exists()

    def test_missing_root_does_not_raise(self) -> None:
        sweep_orphaned_output_dirs()


class TestGateToolCallArguments:
    """Oversized tool-call arguments should be gated before storage; unlike
    results they're never stubbed by KEEP_LAST_N_TOOL_PAIRS regardless of
    age, so this is the only relief they get.
    """

    def test_small_arguments_returned_unchanged(self) -> None:
        tool_json = json.dumps(
            {"tool_call": {"name": "internal_write_file", "id": "t1", "arguments": {"content": "hi"}}},
        )
        result = gate_tool_call_arguments(tool_json, "tab-1", "t1", "internal_write_file")
        assert result == tool_json

    def test_oversized_argument_is_gated_nested_format(self) -> None:
        big = "x" * (CALL_ARG_GATE_THRESHOLD_BYTES + 1)
        tool_json = json.dumps(
            {
                "tool_call": {
                    "name": "internal_write_file",
                    "id": "t2",
                    "arguments": {"file_path": "/tmp/f.txt", "content": big},
                },
            },
        )
        result = gate_tool_call_arguments(tool_json, "tab-1", "t2", "internal_write_file")
        assert result != tool_json
        parsed = json.loads(result)
        gated_content = parsed["tool_call"]["arguments"]["content"]
        assert "truncated" in gated_content
        assert len(gated_content) < len(big)
        # Untouched fields survive the round trip.
        assert parsed["tool_call"]["arguments"]["file_path"] == "/tmp/f.txt"

    def test_oversized_argument_is_gated_flat_format(self) -> None:
        big = "y" * (CALL_ARG_GATE_THRESHOLD_BYTES + 1)
        tool_json = json.dumps(
            {"name": "internal_run_shell_command", "id": "t3", "arguments": {"command": big}},
        )
        result = gate_tool_call_arguments(tool_json, "tab-1", "t3", "internal_run_shell_command")
        parsed = json.loads(result)
        assert "truncated" in parsed["arguments"]["command"]

    def test_full_content_recoverable_from_temp_file(self, tmp_path: Path) -> None:
        big = "z" * (CALL_ARG_GATE_THRESHOLD_BYTES + 500)
        tool_json = json.dumps(
            {"tool_call": {"name": "internal_write_file", "id": "t4", "arguments": {"content": big}}},
        )
        result = gate_tool_call_arguments(tool_json, "tab-1", "t4", "internal_write_file")
        parsed = json.loads(result)
        gated = parsed["tool_call"]["arguments"]["content"]
        path_line = next(line for line in gated.splitlines() if line.startswith("Full output saved to:"))
        saved_path = Path(path_line.split("Full output saved to: ", 1)[1])
        assert saved_path.read_text(encoding="utf-8") == big

    def test_non_string_arguments_untouched(self) -> None:
        tool_json = json.dumps(
            {"tool_call": {"name": "internal_search", "id": "t5", "arguments": {"max_count": 500, "recursive": True}}},
        )
        result = gate_tool_call_arguments(tool_json, "tab-1", "t5", "internal_search")
        assert result == tool_json

    def test_malformed_json_returned_unchanged(self) -> None:
        broken = "{not valid json"
        assert gate_tool_call_arguments(broken, "tab-1", "t6", "x") == broken

    def test_no_arguments_key_returned_unchanged(self) -> None:
        tool_json = json.dumps({"tool_call": {"name": "internal_list_files", "id": "t7"}})
        assert gate_tool_call_arguments(tool_json, "tab-1", "t7", "internal_list_files") == tool_json
