#!/usr/bin/env python3
"""MCP tool for publishing self-contained HTML artifacts."""

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
from core.artifact_store import LOCAL_ARTIFACT_ROOT_ENV
from core.artifact_store import ArtifactStore
from core.artifact_store import local_artifact_root

server = Server(
    "alpaca-artifact",
    instructions=(
        "Publish a self-contained HTML file as a crisp, interactive panel for the user. "
        "The file may contain inline CSS and JavaScript, but must not depend on external "
        "network resources or a backend. Use artifact_publish_html after writing the file."
    ),
)


class _LocalStoreBackend:
    """Same ``call`` surface as ArtifactControlClient, minus the socket.

    A Pack session needs the socket because the store is *owned* by the
    daemon process; a local tab has no such owner, so the MCP subprocess
    writes the artifact directory itself. Kept behind the same one-method
    interface so call_tool doesn't have to know which world it's in.
    """

    def __init__(self, session_dir: str) -> None:
        self._store = ArtifactStore(session_dir)

    def call(self, method: str, params: dict[str, Any]) -> Any:
        return self._store.dispatch(method, params)


def _client() -> ArtifactControlClient | _LocalStoreBackend:
    # Explicit env wins over discovery in both directions. Pack sets the
    # socket path (see anthropic_ollama_server._cli_mcp_servers); a local
    # tab sets the root. Checking discover() first would let a local tab on
    # Linux silently latch onto some unrelated Pack session's socket via
    # the "exactly one session on this host" fallback.
    socket_path = os.environ.get("ALPACA_ARTIFACT_SOCKET")
    if socket_path:
        return ArtifactControlClient(socket_path)
    if os.environ.get(LOCAL_ARTIFACT_ROOT_ENV):
        return _LocalStoreBackend(str(local_artifact_root()))
    client = ArtifactControlClient.discover()
    if client is not None:
        return client
    return _LocalStoreBackend(str(local_artifact_root()))


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
