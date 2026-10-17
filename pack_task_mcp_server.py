"""MCP adapter for the owning Alpaca process's Pack task service."""

from __future__ import annotations

import asyncio
import json
import os

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent
from mcp.types import Tool

from core.pack_tasks import TOOLS
from core.pack_tasks import call_control

server = Server("alpaca-pack-tasks")


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [Tool(**tool) for tool in TOOLS]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    with open(os.environ["ALPACA_PACK_CONTROL_FILE"], encoding="utf-8") as file:
        control = json.load(file)
    result = await asyncio.to_thread(
        call_control,
        control,
        name,
        arguments,
    )
    return [TextContent(type="text", text=json.dumps(result))]


async def main() -> None:
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
