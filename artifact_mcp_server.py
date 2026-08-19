#!/usr/bin/env python3
"""MCP tool for publishing self-contained HTML artifacts from a Pack host."""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent
from mcp.types import Tool

from core.artifact_control import ArtifactControlClient
from core.artifact_protocol import encode_artifact_result

server = Server(
    "alpaca-artifact",
    instructions=(
        "Publish a self-contained HTML file as a crisp, interactive panel for the user. "
        "The file may contain inline CSS and JavaScript, but must not depend on external "
        "network resources or a backend. Use artifact_publish_html after writing the file."
    ),
)


def _client() -> ArtifactControlClient:
    client = ArtifactControlClient.discover()
    if client is None:
        raise RuntimeError("Interactive artifacts need a Pack tab")
    return client


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="artifact_publish_html",
            description=(
                "Snapshot one self-contained HTML file and show it to the user as an "
                "interactive artifact panel. CSS and JavaScript must be inline; external "
                "network requests are blocked."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the .html file"},
                    "title": {"type": "string", "description": "User-visible title"},
                },
                "required": ["path", "title"],
            },
        ),
    ]


def _record_event(
    name: str,
    arguments: dict[str, Any],
    content: list[TextContent],
) -> None:
    path = os.environ.get("ALPACA_CLI_MEDIA_EVENTS")
    if not path:
        return
    event = {
        "type": "alpaca_tool_event",
        "id": f"alpaca_artifact_{name}_{uuid.uuid4().hex}",
        "name": f"alpaca_artifact_{name}",
        "arguments": arguments,
        "result": json.dumps(
            {"content": [{"type": "text", "text": item.text} for item in content]},
        ),
    }
    with open(path, "a", encoding="utf-8") as file:
        file.write(json.dumps(event, separators=(",", ":")) + "\n")


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    try:
        if name != "artifact_publish_html":
            content = [TextContent(type="text", text=f"Unknown tool: {name}")]
        else:
            forwarded = dict(arguments)
            path = Path(str(arguments.get("path", ""))).expanduser()
            if not path.is_absolute():
                path = Path(os.environ.get("ALPACA_WORKSPACE", Path.cwd())) / path
            forwarded["path"] = str(path.resolve())
            result = _client().call(name, forwarded)
            manifest = result["manifest"]
            content = [
                TextContent(
                    type="text",
                    text=(
                        f"Published interactive artifact {manifest['artifact_id']}. "
                        "The user can open it in the artifact panel.\n"
                        + encode_artifact_result(manifest)
                    ),
                ),
            ]
    except Exception as exc:
        content = [TextContent(type="text", text=f"Error: {exc}")]
    _record_event(name, arguments, content)
    return content


async def main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    asyncio.run(main())
