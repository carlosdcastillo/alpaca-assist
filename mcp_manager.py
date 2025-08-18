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


class MCPManager:
    def __init__(self):
        self.servers: dict[str, dict[str, Any]] = {}
        self.available_tools: dict[str, list[dict[str, Any]]] = {}
        self.server_configs: dict[str, dict[str, Any]] = {}

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

                self.available_tools[name] = [tool.model_dump() for tool in tools.tools]

                logging.info(
                    f"Connected to MCP server '{name}' with {len(tools.tools)} tools",
                )
                return True

            except Exception as e:
                # If initialization fails, clean up the contexts
                try:
                    await session_ctx.__aexit__(type(e), e, e.__traceback__)
                except:
                    pass
                try:
                    await stdio_ctx.__aexit__(type(e), e, e.__traceback__)
                except:
                    pass
                raise

        except Exception as e:
            logging.error(f"Failed to connect to MCP server '{name}': {e}")
            import traceback

            traceback.print_exc()
            return False

    async def call_tool(
        self,
        server_name: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Call a tool on a specific MCP server."""
        if server_name not in self.servers:
            logging.error(f"Server '{server_name}' not found")
            return None

        try:
            server_info = self.servers[server_name]
            session = server_info["session"]

            result = await session.call_tool(tool_name, arguments)
            return result.model_dump()

        except Exception as e:
            logging.error(
                f"Error calling tool '{tool_name}' on server '{server_name}': {e}",
            )
            import traceback

            traceback.print_exc()
            return None

    def get_available_tools(self) -> dict[str, list[dict[str, Any]]]:
        """Get all available tools from all servers."""
        return self.available_tools.copy()

    async def disconnect_server(self, name: str) -> bool:
        """Disconnect from an MCP server."""
        if name in self.servers:
            try:
                server_info = self.servers[name]

                # Properly exit the contexts in reverse order
                try:
                    await server_info["session_ctx"].__aexit__(None, None, None)
                except Exception as e:
                    logging.warning(f"Error closing session context for '{name}': {e}")

                try:
                    await server_info["stdio_ctx"].__aexit__(None, None, None)
                except Exception as e:
                    logging.warning(f"Error closing stdio context for '{name}': {e}")

                # Clean up
                del self.servers[name]
                if name in self.available_tools:
                    del self.available_tools[name]

                logging.info(f"Disconnected from MCP server '{name}'")
                return True

            except Exception as e:
                logging.error(f"Error disconnecting from server '{name}': {e}")
                return False
        return False

    async def shutdown(self):
        """Shutdown all MCP connections."""
        for name in list(self.servers.keys()):
            await self.disconnect_server(name)
