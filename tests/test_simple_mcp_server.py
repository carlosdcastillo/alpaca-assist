"""Tests for simple_mcp_server.py — the 6 remaining MCP tools.

Note: read_file_range and the other primitive tools moved to internal_tools.py;
see test_internal_tools.py for their tests.
"""

from __future__ import annotations

import pytest

from simple_mcp_server import TOOL_HANDLERS
from simple_mcp_server import handle_list_tools


class TestSimpleMcpServerToolRegistry:
    """Verify that the server exposes exactly the 6 expected tools."""

    EXPECTED_TOOLS = {
        "summarize_python_file",
        "summarize_markdown_file",
        "summarize_rust_file",
        "compute_cyclomatic_complexity",
        "render_mermaid",
        "fetch_url_as_markdown",
    }

    @pytest.mark.asyncio
    async def test_list_tools_returns_exactly_six(self) -> None:
        tools = await handle_list_tools()
        assert len(tools) == 6

    @pytest.mark.asyncio
    async def test_list_tools_names_match_expected(self) -> None:
        tools = await handle_list_tools()
        names = {t.name for t in tools}
        assert names == self.EXPECTED_TOOLS

    def test_tool_handlers_keys_match_expected(self) -> None:
        assert set(TOOL_HANDLERS.keys()) == self.EXPECTED_TOOLS

    def test_no_internal_tools_in_registry(self) -> None:
        removed = {
            "echo",
            "get_time",
            "read_file",
            "read_file_range",
            "write_file",
            "modify_file",
            "list_files",
            "search_files_for_text",
            "run_shell_command",
            "evaluate_mypy_python",
            "evaluate_cargo_check_rust",
        }
        assert not removed & set(TOOL_HANDLERS.keys())
