"""Tests for the read_file_range tool in simple_mcp_server.py."""
from __future__ import annotations

from pathlib import Path

import pytest

from simple_mcp_server import handle_read_file_range_tool
from simple_mcp_server import READ_FILE_RANGE_MAX_LINES


def _make_file(tmp_path: Path, n_lines: int) -> Path:
    file_path = tmp_path / "big.txt"
    file_path.write_text(
        "\n".join(f"line {i}" for i in range(1, n_lines + 1)),
        encoding="utf-8",
    )
    return file_path


class TestReadFileRangeTool:
    @pytest.mark.asyncio
    async def test_default_range_starts_at_line_one(self, tmp_path: Path) -> None:
        file_path = _make_file(tmp_path, 1000)

        result = await handle_read_file_range_tool({"file_path": str(file_path)})

        text = result[0].text
        assert "line 1" in text
        assert f"line {READ_FILE_RANGE_MAX_LINES}" in text
        assert f"line {READ_FILE_RANGE_MAX_LINES + 1}" not in text

    @pytest.mark.asyncio
    async def test_explicit_range_returned(self, tmp_path: Path) -> None:
        file_path = _make_file(tmp_path, 1000)

        result = await handle_read_file_range_tool(
            {"file_path": str(file_path), "start_line": 501, "end_line": 510},
        )

        text = result[0].text
        assert "line 501" in text
        assert "line 510" in text
        assert "line 500" not in text
        assert "line 511" not in text

    @pytest.mark.asyncio
    async def test_range_clamped_to_max_lines(self, tmp_path: Path) -> None:
        file_path = _make_file(tmp_path, 10_000)

        result = await handle_read_file_range_tool(
            {"file_path": str(file_path), "start_line": 1, "end_line": 9000},
        )

        text = result[0].text
        returned_lines = [
            line for line in text.splitlines() if line.startswith("line ")
        ]
        assert len(returned_lines) <= READ_FILE_RANGE_MAX_LINES

    @pytest.mark.asyncio
    async def test_header_reports_total_line_count(self, tmp_path: Path) -> None:
        file_path = _make_file(tmp_path, 250)

        result = await handle_read_file_range_tool(
            {"file_path": str(file_path), "start_line": 1, "end_line": 5},
        )

        assert "250 lines total" in result[0].text

    @pytest.mark.asyncio
    async def test_start_line_past_end_of_file_errors(self, tmp_path: Path) -> None:
        file_path = _make_file(tmp_path, 10)

        result = await handle_read_file_range_tool(
            {"file_path": str(file_path), "start_line": 100},
        )

        assert "exceeds total line count" in result[0].text

    @pytest.mark.asyncio
    async def test_missing_file_path_errors(self) -> None:
        result = await handle_read_file_range_tool({})
        assert "Error" in result[0].text

    @pytest.mark.asyncio
    async def test_nonexistent_file_errors(self, tmp_path: Path) -> None:
        result = await handle_read_file_range_tool(
            {"file_path": str(tmp_path / "does_not_exist.txt")},
        )
        assert "does not exist" in result[0].text

    @pytest.mark.asyncio
    async def test_invalid_start_line_errors(self, tmp_path: Path) -> None:
        file_path = _make_file(tmp_path, 10)

        result = await handle_read_file_range_tool(
            {"file_path": str(file_path), "start_line": 0},
        )

        assert "start_line" in result[0].text

    @pytest.mark.asyncio
    async def test_end_line_before_start_line_errors(self, tmp_path: Path) -> None:
        file_path = _make_file(tmp_path, 10)

        result = await handle_read_file_range_tool(
            {"file_path": str(file_path), "start_line": 5, "end_line": 2},
        )

        assert "end_line" in result[0].text

    @pytest.mark.asyncio
    async def test_reads_file_larger_than_one_megabyte(self, tmp_path: Path) -> None:
        # read_file rejects anything over 1MB outright; read_file_range must not.
        file_path = tmp_path / "huge.txt"
        file_path.write_text("x" * (2 * 1024 * 1024) + "\nlast line", encoding="utf-8")

        result = await handle_read_file_range_tool(
            {"file_path": str(file_path), "start_line": 2, "end_line": 2},
        )

        assert "last line" in result[0].text
