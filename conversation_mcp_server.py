"""MCP access to Alpaca Assist's saved conversation history.

CLI-backed models do not receive Alpaca's in-process internal tools, so this
server exposes the existing conversation handlers over the MCP transport they
do receive. The database path is supplied explicitly by the parent process;
the CLI's working directory may be an unrelated project workspace.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent
from mcp.types import Tool

import internal_tools

DATABASE_ENV = "ALPACA_CONVERSATIONS_DB"
TOOL_NAMES = {
    "search_conversations",
    "get_conversation",
    "get_tool_details",
    "dump_conversations",
}

server = Server(
    "conversation-history",
    instructions=(
        "Search and read the user's saved Alpaca Assist conversation history. "
        "When asked about recurring themes, interests, or patterns across the "
        "whole history, use dump_conversations with include_content=true, then "
        "read and analyze the exported JSON. Use search_conversations for a "
        "specific topic and get_conversation for the matching transcripts."
    ),
)


def _schemas() -> dict[str, dict[str, Any]]:
    schemas: dict[str, dict[str, Any]] = {}
    for schema in internal_tools.TOOL_SCHEMAS:
        function = schema.get("function", {})
        internal_name = str(function.get("name", ""))
        if not internal_name.startswith("internal_"):
            continue
        name = internal_name.removeprefix("internal_")
        if name in TOOL_NAMES:
            schemas[name] = function
    return schemas


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name=name,
            description=str(schema.get("description", "")),
            inputSchema=schema.get("parameters", {"type": "object"}),
        )
        for name, schema in _schemas().items()
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    if name not in TOOL_NAMES:
        return [TextContent(type="text", text=f"Unknown tool: {name}")]

    db_path = os.environ.get(DATABASE_ENV)
    if not db_path:
        return [TextContent(type="text", text=f"Error: {DATABASE_ENV} is not set")]

    forwarded = dict(arguments)
    forwarded["_db_path"] = db_path
    result = internal_tools.call_tool(name, forwarded)
    content = result.get("content", [])
    text = "\n".join(
        str(item.get("text", ""))
        for item in content
        if isinstance(item, dict) and item.get("type") == "text"
    )
    return [TextContent(type="text", text=text or "(no output)")]


async def main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    asyncio.run(main())
