"""Tests for core.tool_output_gate module."""
from __future__ import annotations

from pathlib import Path

import pytest

import core.tool_output_gate as gate_module
from core.tool_output_gate import cleanup_tab_output_dir
from core.tool_output_gate import GATE_THRESHOLD_BYTES
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
