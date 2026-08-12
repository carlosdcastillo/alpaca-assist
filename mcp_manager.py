import asyncio
import json
import logging
import subprocess
import sys
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

from mcp import ClientSession
from mcp import StdioServerParameters
from mcp.client.stdio import stdio_client

logger = logging.getLogger(__name__)


class MCPManager:
    def __init__(self) -> None:
        self.servers: dict[str, dict[str, Any]] = {}
        self.available_tools: dict[str, list[dict[str, Any]]] = {}
        self.server_configs: dict[str, dict[str, Any]] = {}
        self._server_locks: dict[str, asyncio.Lock] = {}
        # Per-server set of tool names the user has disabled.
        # Keys that are absent or map to an empty set mean "all tools enabled".
        self.disabled_tools: dict[str, set[str]] = {}

    def _get_server_lock(self, name: str) -> asyncio.Lock:
        """Return (creating if needed) the per-server asyncio Lock.

        Called from within the event loop, so Lock() is always created in the
        correct loop context.
        """
        if name not in self._server_locks:
            self._server_locks[name] = asyncio.Lock()
        return self._server_locks[name]

    async def add_server(
        self,
        name: str,
        command: list[str],
        args: list[str] | None = None,
    ) -> bool:
        """Add and connect to an MCP server."""
        try:
            server_params = StdioServerParameters(
                command=command[0],
                args=command[1:] + (args or []),
            )

            # Create the stdio client context manager
            stdio_ctx = stdio_client(server_params)

            # Enter the stdio context
            read, write = await stdio_ctx.__aenter__()

            # Create the session context manager
            session_ctx = ClientSession(read, write)

            # Enter the session context
            session = await session_ctx.__aenter__()

            try:
                # Initialize the server
                await session.initialize()

                # Get available tools
                tools = await session.list_tools()

                # Store everything we need including context managers for proper cleanup
                self.servers[name] = {
                    "session": session,
                    "session_ctx": session_ctx,
                    "stdio_ctx": stdio_ctx,
                    "read": read,
                    "write": write,
                    "params": server_params,
                }
                self.server_configs[name] = {"command": command, "args": args}

                self.available_tools[name] = [tool.model_dump() for tool in tools.tools]

                logger.info(
                    f"Connected to MCP server '{name}' with {len(tools.tools)} tools",
                )
                return True

            except Exception as e:
                # If initialization fails, clean up the contexts
                try:
                    await session_ctx.__aexit__(type(e), e, e.__traceback__)
                except Exception as cleanup_err:
                    logger.warning(
                        f"Error cleaning up session context for '{name}': {cleanup_err}",
                    )
                try:
                    await stdio_ctx.__aexit__(type(e), e, e.__traceback__)
                except Exception as cleanup_err:
                    logger.warning(
                        f"Error cleaning up stdio context for '{name}': {cleanup_err}",
                    )
                raise

        except Exception as e:
            logger.exception(f"Failed to connect to MCP server '{name}': {e}")
            return False

    async def call_tool(
        self,
        server_name: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Call a tool on a specific MCP server.

        Disabled tools are refused immediately as a defense-in-depth measure.
        """
        async with self._get_server_lock(server_name):
            if server_name not in self.servers:
                logger.error(f"Server '{server_name}' not found")
                return None

            if tool_name in self.disabled_tools.get(server_name, set()):
                logger.warning(
                    f"Tool '{tool_name}' on server '{server_name}' is disabled; refusing call",
                )
                return {"error": f"Tool '{tool_name}' is disabled"}

            try:
                server_info = self.servers[server_name]
                session = server_info["session"]

                result = await session.call_tool(tool_name, arguments)
                result_dict: dict[str, Any] = result.model_dump()
                return result_dict

            except Exception as e:
                logger.exception(
                    f"Error calling tool '{tool_name}' on server '{server_name}': {e}",
                )
                return None

    def get_available_tools(self) -> dict[str, list[dict[str, Any]]]:
        """Get all enabled tools from all connected servers.

        Tools whose names appear in the server's disabled_tools set are excluded.
        """
        result: dict[str, list[dict[str, Any]]] = {}
        for server_name, tools in self.available_tools.items():
            disabled = self.disabled_tools.get(server_name, set())
            if disabled:
                result[server_name] = [
                    t for t in tools if t.get("name") not in disabled
                ]
            else:
                result[server_name] = list(tools)
        return result

    def set_tool_enabled(self, server_name: str, tool_name: str, enabled: bool) -> None:
        """Enable or disable a single tool by name.

        This updates the in-memory state only.  Callers are responsible for
        persisting the change via save_mcp_config().
        """
        if enabled:
            if server_name in self.disabled_tools:
                self.disabled_tools[server_name].discard(tool_name)
                if not self.disabled_tools[server_name]:
                    del self.disabled_tools[server_name]
        else:
            self.disabled_tools.setdefault(server_name, set()).add(tool_name)

    async def disconnect_server(self, name: str) -> bool:
        """Disconnect from an MCP server."""
        if name in self.servers:
            try:
                server_info = self.servers[name]

                # Properly exit the contexts in reverse order
                try:
                    await server_info["session_ctx"].__aexit__(None, None, None)
                except Exception as e:
                    logger.warning(f"Error closing session context for '{name}': {e}")

                try:
                    await server_info["stdio_ctx"].__aexit__(None, None, None)
                except Exception as e:
                    logger.warning(f"Error closing stdio context for '{name}': {e}")

                # Clean up runtime state but preserve disabled_tools —
                # the user's tool selections must survive reconnects.
                del self.servers[name]
                if name in self.available_tools:
                    del self.available_tools[name]

                logger.info(f"Disconnected from MCP server '{name}'")
                return True

            except Exception as e:
                logger.error(f"Error disconnecting from server '{name}': {e}")
                return False
        return False

    async def shutdown(self) -> None:
        """Shutdown all MCP connections."""
        for name in list(self.servers.keys()):
            await self.disconnect_server(name)
