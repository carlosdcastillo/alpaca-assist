"""
Comprehensive tests for mcp_manager.py module.

This module tests MCP manager operations with MINIMAL mocking where possible.
Focus on testing actual behavior, not mock setups.
"""
import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import Mock
from unittest.mock import patch

import pytest
import pytest_asyncio

from mcp_manager import MCPManager


class TestMCPManagerInitialization:
    """Tests for MCP manager initialization - minimal mocking."""

    def test_init_creates_empty_servers_dict(self) -> None:
        """Test that initialization creates an empty servers dictionary."""
        manager = MCPManager()
        # Check actual attribute name from implementation
        assert hasattr(manager, "servers")
        assert isinstance(manager.servers, dict)
        assert len(manager.servers) == 0

    def test_init_creates_available_tools_dict(self) -> None:
        """Test that initialization creates available_tools dictionary."""
        manager = MCPManager()
        assert hasattr(manager, "available_tools")
        assert isinstance(manager.available_tools, dict)

    def test_init_creates_server_locks_dict(self) -> None:
        """Test that initialization creates _server_locks dictionary."""
        manager = MCPManager()
        assert hasattr(manager, "_server_locks")
        assert isinstance(manager._server_locks, dict)

    def test_init_creates_disabled_tools_dict(self) -> None:
        """Test that initialization creates disabled_tools dictionary."""
        manager = MCPManager()
        assert hasattr(manager, "disabled_tools")
        assert isinstance(manager.disabled_tools, dict)


class TestMCPManagerServerManagement:
    """Tests for MCP server management - integration style with real objects where possible."""

    @pytest.mark.asyncio
    async def test_add_server_success(self) -> None:
        """Test successfully adding a server - verifies actual server storage."""
        manager = MCPManager()

        with patch("mcp_manager.ClientSession") as mock_session_class:
            mock_session = AsyncMock()
            mock_session.initialize = AsyncMock()
            # Return realistic tool list structure
            mock_tool = Mock()
            mock_tool.model_dump.return_value = {
                "name": "test_tool",
                "description": "A test tool",
            }
            mock_session.list_tools = AsyncMock(
                return_value=Mock(tools=[mock_tool]),
            )
            mock_session_class.return_value.__aenter__ = AsyncMock(
                return_value=mock_session,
            )
            mock_session_class.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch("mcp_manager.stdio_client") as mock_stdio:
                mock_stdio.return_value.__aenter__ = AsyncMock(
                    return_value=(Mock(), Mock()),
                )
                mock_stdio.return_value.__aexit__ = AsyncMock(return_value=False)

                result = await manager.add_server(
                    "test_server",
                    ["python", "-m", "test_server"],
                )

                # MUST be True, not "True or False"
                assert result is True
                # Verify server was actually stored
                assert "test_server" in manager.servers
                # Verify tools were registered
                assert "test_server" in manager.available_tools
                assert len(manager.available_tools["test_server"]) == 1

    @pytest.mark.asyncio
    async def test_add_server_duplicate_name_replaces(self) -> None:
        """Test adding server with duplicate name replaces existing."""
        manager = MCPManager()

        # Add first server
        mock_server1 = Mock()
        mock_server1.session = AsyncMock()
        mock_server1.command = ["python", "-m", "server1"]
        manager.servers["test_server"] = mock_server1
        manager.available_tools["test_server"] = [{"name": "old_tool"}]

        with patch("mcp_manager.ClientSession") as mock_session_class:
            mock_session = AsyncMock()
            mock_session.initialize = AsyncMock()
            mock_tool = Mock()
            mock_tool.model_dump.return_value = {"name": "new_tool"}
            mock_session.list_tools = AsyncMock(return_value=Mock(tools=[mock_tool]))
            mock_session_class.return_value.__aenter__ = AsyncMock(
                return_value=mock_session,
            )
            mock_session_class.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch("mcp_manager.stdio_client") as mock_stdio:
                mock_stdio.return_value.__aenter__ = AsyncMock(
                    return_value=(Mock(), Mock()),
                )
                mock_stdio.return_value.__aexit__ = AsyncMock(return_value=False)

                result = await manager.add_server(
                    "test_server",
                    ["python", "-m", "new_server"],
                )

                # Should succeed and replace
                assert result is True
                assert "test_server" in manager.servers
                # Verify old tools were replaced
                assert len(manager.available_tools["test_server"]) == 1
                assert manager.available_tools["test_server"][0]["name"] == "new_tool"

    @pytest.mark.asyncio
    async def test_add_server_connection_failure(self) -> None:
        """Test adding a server that fails to connect returns False."""
        manager = MCPManager()

        with patch("mcp_manager.stdio_client") as mock_stdio:
            mock_stdio.return_value.__aenter__ = AsyncMock(
                side_effect=Exception("Connection refused"),
            )
            mock_stdio.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await manager.add_server(
                "failing_server",
                ["python", "-m", "nonexistent"],
            )

            # MUST be False
            assert result is False
            # Verify server was NOT stored
            assert "failing_server" not in manager.servers
            assert "failing_server" not in manager.available_tools

    @pytest.mark.asyncio
    async def test_disconnect_server_closes_session(self) -> None:
        """Test disconnecting exits session/stdio contexts and removes from dicts."""
        manager = MCPManager()
        mock_session_ctx = AsyncMock()
        mock_stdio_ctx = AsyncMock()
        server_info = {
            "session": AsyncMock(),
            "session_ctx": mock_session_ctx,
            "stdio_ctx": mock_stdio_ctx,
            "read": Mock(),
            "write": Mock(),
            "params": Mock(),
        }
        manager.servers["test_server"] = server_info
        manager.available_tools["test_server"] = [{"name": "tool1"}]

        result = await manager.disconnect_server("test_server")

        # disconnect_server exits session_ctx and stdio_ctx (not session.aclose)
        mock_session_ctx.__aexit__.assert_called_once_with(None, None, None)
        mock_stdio_ctx.__aexit__.assert_called_once_with(None, None, None)
        assert result is True
        assert "test_server" not in manager.servers
        assert "test_server" not in manager.available_tools

    @pytest.mark.asyncio
    async def test_disconnect_server_nonexistent_returns_false(self) -> None:
        """Test disconnecting nonexistent server returns False."""
        manager = MCPManager()

        result = await manager.disconnect_server("nonexistent")

        assert result is False

    @pytest.mark.asyncio
    async def test_reconnect_server_disconnects_then_adds(self) -> None:
        """Test reconnect disconnects old and adds new."""
        manager = MCPManager()
        mock_session_ctx = AsyncMock()
        mock_stdio_ctx = AsyncMock()
        server_info = {
            "session": AsyncMock(),
            "session_ctx": mock_session_ctx,
            "stdio_ctx": mock_stdio_ctx,
            "read": Mock(),
            "write": Mock(),
            "params": Mock(),
        }
        manager.servers["test_server"] = server_info
        manager.available_tools["test_server"] = [{"name": "old_tool"}]
        # reconnect_server reads command/args from server_configs, not servers
        manager.server_configs["test_server"] = {
            "command": ["python", "-m", "test_server"],
            "args": [],
        }

        with patch.object(manager, "add_server", new_callable=AsyncMock) as mock_add:
            mock_add.return_value = True

            result = await manager.reconnect_server("test_server")

            # Verify old session context was exited
            mock_session_ctx.__aexit__.assert_called_once_with(None, None, None)
            # Verify add_server was called with params from server_configs
            mock_add.assert_called_once_with(
                "test_server",
                ["python", "-m", "test_server"],
                [],
            )
            assert result is True

    @pytest.mark.asyncio
    async def test_reconnect_server_nonexistent_returns_false(self) -> None:
        """Test reconnecting nonexistent server returns False."""
        manager = MCPManager()

        result = await manager.reconnect_server("nonexistent")

        assert result is False


class TestMCPManagerToolOperations:
    """Tests for MCP tool operations with real assertions."""

    @pytest.mark.asyncio
    async def test_call_tool_success_returns_result(self) -> None:
        """Test calling tool returns actual result from server."""
        manager = MCPManager()
        expected_result = {"content": [{"type": "text", "text": "Tool output"}]}
        # call_tool calls result.model_dump() on the return value
        mock_call_result = MagicMock()
        mock_call_result.model_dump.return_value = expected_result
        mock_session = AsyncMock()
        mock_session.call_tool = AsyncMock(return_value=mock_call_result)
        # servers stores plain dicts, not objects with .session attribute
        server_info = {
            "session": mock_session,
            "session_ctx": AsyncMock(),
            "stdio_ctx": AsyncMock(),
            "read": Mock(),
            "write": Mock(),
            "params": Mock(),
        }
        manager.servers["test_server"] = server_info

        result = await manager.call_tool(
            "test_server",
            "test_tool",
            {"arg1": "value1"},
        )

        assert result == expected_result
        mock_session.call_tool.assert_called_once_with(
            "test_tool",
            {"arg1": "value1"},
        )

    @pytest.mark.asyncio
    async def test_call_tool_nonexistent_server_returns_none(self) -> None:
        """Test calling tool on nonexistent server returns None."""
        manager = MCPManager()

        result = await manager.call_tool(
            "nonexistent",
            "test_tool",
            {"arg1": "value1"},
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_call_tool_execution_error_returns_none(self) -> None:
        """Test calling tool that raises exception returns None."""
        manager = MCPManager()
        mock_session = AsyncMock()
        mock_session.call_tool = AsyncMock(
            side_effect=Exception("Tool execution failed"),
        )
        server_info = {
            "session": mock_session,
            "session_ctx": AsyncMock(),
            "stdio_ctx": AsyncMock(),
            "read": Mock(),
            "write": Mock(),
            "params": Mock(),
        }
        manager.servers["test_server"] = server_info

        result = await manager.call_tool(
            "test_server",
            "failing_tool",
            {"arg1": "value1"},
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_call_tool_respects_disabled_tools(self) -> None:
        """Test calling disabled tool returns an error dict without calling server."""
        manager = MCPManager()
        mock_session = AsyncMock()
        mock_session.call_tool = AsyncMock()
        server_info = {
            "session": mock_session,
            "session_ctx": AsyncMock(),
            "stdio_ctx": AsyncMock(),
            "read": Mock(),
            "write": Mock(),
            "params": Mock(),
        }
        manager.servers["test_server"] = server_info
        # Disable the tool
        manager.disabled_tools["test_server"] = {"disabled_tool"}

        result = await manager.call_tool(
            "test_server",
            "disabled_tool",
            {"arg": "value"},
        )

        # Actual behavior: returns an error dict, not None
        assert result == {"error": "Tool 'disabled_tool' is disabled"}
        mock_session.call_tool.assert_not_called()

    def test_get_available_tools_empty_returns_empty_dict(self) -> None:
        """Test getting tools with no servers returns empty dict."""
        manager = MCPManager()
        tools = manager.get_available_tools()

        assert isinstance(tools, dict)
        assert len(tools) == 0

    def test_get_available_tools_filters_disabled(self) -> None:
        """Test getting tools excludes disabled tools."""
        manager = MCPManager()
        manager.available_tools["server1"] = [
            {"name": "enabled_tool"},
            {"name": "disabled_tool"},
            {"name": "another_enabled"},
        ]
        manager.disabled_tools["server1"] = {"disabled_tool"}

        tools = manager.get_available_tools()

        assert "server1" in tools
        # Should only have 2 tools (disabled filtered out)
        assert len(tools["server1"]) == 2
        tool_names = [t["name"] for t in tools["server1"]]
        assert "enabled_tool" in tool_names
        assert "another_enabled" in tool_names
        assert "disabled_tool" not in tool_names

    def test_set_tool_enabled_adds_to_disabled(self) -> None:
        """Test disabling tool adds to disabled_tools set."""
        manager = MCPManager()
        manager.available_tools["test_server"] = [
            {"name": "tool1"},
            {"name": "tool2"},
        ]

        manager.set_tool_enabled("test_server", "tool1", False)

        assert "test_server" in manager.disabled_tools
        assert "tool1" in manager.disabled_tools["test_server"]

    def test_set_tool_enabled_removes_from_disabled(self) -> None:
        """Test enabling tool removes from disabled_tools set."""
        manager = MCPManager()
        manager.disabled_tools["test_server"] = {"tool1", "tool2"}

        manager.set_tool_enabled("test_server", "tool1", True)

        assert "tool1" not in manager.disabled_tools["test_server"]
        assert "tool2" in manager.disabled_tools["test_server"]

    def test_set_tool_enabled_nonexistent_server_no_error(self) -> None:
        """Test setting tool on nonexistent server doesn't raise, but does create entry."""
        manager = MCPManager()

        # Should not raise
        manager.set_tool_enabled("nonexistent", "tool1", False)

        # Actual behavior: disabling always adds to disabled_tools regardless of
        # whether the server exists in self.servers
        assert "nonexistent" in manager.disabled_tools
        assert "tool1" in manager.disabled_tools["nonexistent"]

    def test_set_tool_enabled_nonexistent_tool_no_error(self) -> None:
        """Test setting nonexistent tool creates disabled entry anyway."""
        manager = MCPManager()
        manager.available_tools["test_server"] = [{"name": "existing_tool"}]

        # Should not raise
        manager.set_tool_enabled("test_server", "nonexistent_tool", False)

        # Creates entry anyway (implementation detail)
        assert "test_server" in manager.disabled_tools
        assert "nonexistent_tool" in manager.disabled_tools["test_server"]


class TestMCPManagerShutdown:
    """Tests for MCP manager shutdown with real assertions."""

    @pytest.mark.asyncio
    async def test_shutdown_closes_all_server_sessions(self) -> None:
        """Test shutdown exits all server contexts and clears dicts."""
        manager = MCPManager()

        mock_session_ctx1 = AsyncMock()
        mock_stdio_ctx1 = AsyncMock()
        server_info1 = {
            "session": AsyncMock(),
            "session_ctx": mock_session_ctx1,
            "stdio_ctx": mock_stdio_ctx1,
            "read": Mock(),
            "write": Mock(),
            "params": Mock(),
        }

        mock_session_ctx2 = AsyncMock()
        mock_stdio_ctx2 = AsyncMock()
        server_info2 = {
            "session": AsyncMock(),
            "session_ctx": mock_session_ctx2,
            "stdio_ctx": mock_stdio_ctx2,
            "read": Mock(),
            "write": Mock(),
            "params": Mock(),
        }

        manager.servers["server1"] = server_info1
        manager.servers["server2"] = server_info2
        manager.available_tools["server1"] = [{"name": "tool1"}]
        manager.available_tools["server2"] = [{"name": "tool2"}]

        await manager.shutdown()

        # Verify all session/stdio contexts were exited
        mock_session_ctx1.__aexit__.assert_called_once_with(None, None, None)
        mock_stdio_ctx1.__aexit__.assert_called_once_with(None, None, None)
        mock_session_ctx2.__aexit__.assert_called_once_with(None, None, None)
        mock_stdio_ctx2.__aexit__.assert_called_once_with(None, None, None)
        assert len(manager.servers) == 0
        assert len(manager.available_tools) == 0

    @pytest.mark.asyncio
    async def test_shutdown_with_no_servers_completes(self) -> None:
        """Test shutdown with no servers completes without error."""
        manager = MCPManager()

        # Should not raise
        await manager.shutdown()

        # Verify state is still clean
        assert len(manager.servers) == 0

    @pytest.mark.asyncio
    async def test_shutdown_handles_session_close_errors(self) -> None:
        """Test shutdown continues even if a context exit fails."""
        manager = MCPManager()

        mock_session_ctx1 = AsyncMock()
        mock_session_ctx1.__aexit__ = AsyncMock(side_effect=Exception("Close failed"))
        mock_stdio_ctx1 = AsyncMock()
        server_info1 = {
            "session": AsyncMock(),
            "session_ctx": mock_session_ctx1,
            "stdio_ctx": mock_stdio_ctx1,
            "read": Mock(),
            "write": Mock(),
            "params": Mock(),
        }

        mock_session_ctx2 = AsyncMock()
        mock_stdio_ctx2 = AsyncMock()
        server_info2 = {
            "session": AsyncMock(),
            "session_ctx": mock_session_ctx2,
            "stdio_ctx": mock_stdio_ctx2,
            "read": Mock(),
            "write": Mock(),
            "params": Mock(),
        }

        manager.servers["server1"] = server_info1
        manager.servers["server2"] = server_info2

        # Should not raise despite error in server1's session_ctx exit
        await manager.shutdown()

        # Both server contexts should have been attempted
        mock_session_ctx1.__aexit__.assert_called_once()
        mock_stdio_ctx1.__aexit__.assert_called_once_with(None, None, None)
        mock_session_ctx2.__aexit__.assert_called_once_with(None, None, None)
        assert len(manager.servers) == 0


class TestMCPManagerLockManagement:
    """Tests for lock management with real assertions."""

    def test_get_server_lock_creates_new_lock(self) -> None:
        """Test _get_server_lock creates asyncio.Lock for new server."""
        manager = MCPManager()
        lock = manager._get_server_lock("new_server")

        assert isinstance(lock, asyncio.Lock)
        assert "new_server" in manager._server_locks
        assert manager._server_locks["new_server"] is lock

    def test_get_server_lock_returns_same_lock(self) -> None:
        """Test _get_server_lock returns same lock for existing server."""
        manager = MCPManager()
        lock1 = manager._get_server_lock("test_server")
        lock2 = manager._get_server_lock("test_server")

        assert lock1 is lock2
        assert "test_server" in manager._server_locks


class TestMCPManagerIntegration:
    """Integration tests with minimal mocking."""

    @pytest.mark.asyncio
    async def test_full_server_lifecycle(self) -> None:
        """Test complete lifecycle: add, use tools, modify tools, disconnect."""
        manager = MCPManager()

        with patch("mcp_manager.ClientSession") as mock_session_class:
            mock_session = AsyncMock()
            mock_session.initialize = AsyncMock()
            mock_tool = Mock()
            mock_tool.model_dump.return_value = {
                "name": "test_tool",
                "description": "Test",
            }
            mock_session.list_tools = AsyncMock(return_value=Mock(tools=[mock_tool]))
            # call_tool result must support .model_dump() since call_tool() calls result.model_dump()
            mock_call_result = MagicMock()
            mock_call_result.model_dump.return_value = {"result": "success"}
            mock_session.call_tool = AsyncMock(return_value=mock_call_result)
            mock_session.aclose = AsyncMock()
            mock_session_class.return_value.__aenter__ = AsyncMock(
                return_value=mock_session,
            )
            mock_session_class.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch("mcp_manager.stdio_client") as mock_stdio:
                mock_stdio.return_value.__aenter__ = AsyncMock(
                    return_value=(Mock(), Mock()),
                )
                mock_stdio.return_value.__aexit__ = AsyncMock(return_value=False)

                # 1. Add server
                add_result = await manager.add_server(
                    "lifecycle_server",
                    ["python", "-m", "server"],
                )
                assert add_result is True
                assert "lifecycle_server" in manager.servers

                # 2. Get available tools
                tools = manager.get_available_tools()
                assert "lifecycle_server" in tools
                assert len(tools["lifecycle_server"]) == 1

                # 3. Disable a tool
                manager.set_tool_enabled("lifecycle_server", "test_tool", False)
                assert "test_tool" in manager.disabled_tools["lifecycle_server"]

                # 4. Try to call disabled tool (returns error dict, not None)
                disabled_result = await manager.call_tool(
                    "lifecycle_server",
                    "test_tool",
                    {},
                )
                assert disabled_result == {"error": "Tool 'test_tool' is disabled"}

                # 5. Enable tool again
                manager.set_tool_enabled("lifecycle_server", "test_tool", True)
                # When the last disabled tool is re-enabled the server key is removed entirely
                assert "test_tool" not in manager.disabled_tools.get(
                    "lifecycle_server",
                    set(),
                )

                # 6. Call enabled tool
                call_result = await manager.call_tool(
                    "lifecycle_server",
                    "test_tool",
                    {"arg": "value"},
                )
                assert call_result is not None
                assert call_result["result"] == "success"

                # 7. Disconnect
                disconnect_result = await manager.disconnect_server("lifecycle_server")
                assert disconnect_result is True
                assert "lifecycle_server" not in manager.servers

    @pytest.mark.asyncio
    async def test_concurrent_tool_calls_serialized_by_lock(self) -> None:
        """Test that concurrent calls to same server are serialized via lock.

        This test verifies the locking mechanism actually serializes calls,
        not just that asyncio.gather works.
        """
        manager = MCPManager()

        # Track call timing
        call_times = []

        async def tracked_call(*args, **kwargs):
            start = asyncio.get_event_loop().time()
            call_times.append(start)
            await asyncio.sleep(0.05)  # 50ms delay
            mock_result = MagicMock()
            mock_result.model_dump.return_value = {"result": "ok"}
            return mock_result

        mock_session = AsyncMock()
        mock_session.call_tool = tracked_call
        server_info = {
            "session": mock_session,
            "session_ctx": AsyncMock(),
            "stdio_ctx": AsyncMock(),
            "read": Mock(),
            "write": Mock(),
            "params": Mock(),
        }
        manager.servers["test_server"] = server_info

        # Make concurrent calls
        start_time = asyncio.get_event_loop().time()
        tasks = [manager.call_tool("test_server", "tool", {"arg": i}) for i in range(3)]
        results = await asyncio.gather(*tasks)
        end_time = asyncio.get_event_loop().time()

        # All should succeed
        assert all(r is not None for r in results)

        # If properly serialized, calls should be spaced by ~50ms
        # If not serialized, they'd all start at nearly the same time
        if len(call_times) >= 2:
            time_diffs = [
                call_times[i + 1] - call_times[i] for i in range(len(call_times) - 1)
            ]
            # At least some spacing should exist if serialized
            assert any(
                d > 0.01 for d in time_diffs
            ), "Calls appear to not be serialized"


class TestMCPManagerErrorHandling:
    """Tests for error handling with real assertions."""

    @pytest.mark.asyncio
    async def test_add_server_with_invalid_command(self) -> None:
        """Test adding server with command that fails immediately."""
        manager = MCPManager()

        with patch("mcp_manager.stdio_client") as mock_stdio:
            # Simulate immediate failure
            mock_stdio.return_value.__aenter__ = AsyncMock(
                side_effect=FileNotFoundError("Command not found"),
            )

            result = await manager.add_server(
                "bad_server",
                ["nonexistent_command"],
            )

            assert result is False
            assert "bad_server" not in manager.servers

    @pytest.mark.asyncio
    async def test_add_server_timeout_during_init(self) -> None:
        """Test handling timeout during server initialization."""
        manager = MCPManager()

        with patch("mcp_manager.ClientSession") as mock_session_class:
            mock_session = AsyncMock()
            # Simulate timeout
            mock_session.initialize = AsyncMock(side_effect=asyncio.TimeoutError())
            mock_session_class.return_value.__aenter__ = AsyncMock(
                return_value=mock_session,
            )
            mock_session_class.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch("mcp_manager.stdio_client") as mock_stdio:
                mock_stdio.return_value.__aenter__ = AsyncMock(
                    return_value=(Mock(), Mock()),
                )
                mock_stdio.return_value.__aexit__ = AsyncMock(return_value=False)

                result = await manager.add_server(
                    "slow_server",
                    ["python", "-m", "slow"],
                )

                assert result is False
                assert "slow_server" not in manager.servers
