"""MCP tools for driving a live app surface.

This is an ordinary stdio MCP server listed in mcp_servers.json like any
other. That is the whole trick behind it needing no new transport: for a Pack
tab, the ChatTab and its entire tool stack already run inside pack_daemon.py
*on the remote host*, and the daemon snapshots mcp_servers.json into its
session directory and launches those servers there. So this process starts on
the machine that has the display, and reaches the supervisor over a Unix
socket in that same directory (core/surface_control.py).

On a local (Windows) tab there is no supervisor and no display, so every tool
here reports that plainly instead of failing to start.

surface_open takes a named profile or a raw argv, same as the human-driven
panel path. Profiles are a curated shortcut, not a security boundary: the
model already has unrestricted shell execution on this host via
internal_run_shell_command, and xterm is itself a permitted profile, so
refusing a model-supplied argv here would not have prevented anything --
it would only have made this path more annoying than the other two ways
to the same result.
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import ImageContent
from mcp.types import TextContent
from mcp.types import Tool

import image_tool_result
from core.surface_control import SurfaceControlClient
from core.surface_protocol import encode_surface_result

server = Server(
    "alpaca-surface",
    instructions=(
        "Live GUI application surfaces on this host. surface_open starts a "
        "named app on a fresh headless display that the user can watch and "
        "drive from their conversation panel; surface_snapshot shows you what "
        "is on it; surface_click/surface_type/surface_key drive it. Always "
        "snapshot before acting on coordinates — input carries the seq it was "
        "computed against and stale coordinates are refused. The user's own "
        "input always takes priority over yours. surface_open's result "
        "already renders as a live interactive panel in the conversation — "
        "do not add a markdown image reference like "
        "`![...](alpaca://image/...)` for it, unlike internal_view_image; "
        "no such reference exists for a surface and it will only show up "
        "broken. Just tell the user in plain text that the surface is open."
    ),
)


def _client() -> SurfaceControlClient:
    client = SurfaceControlClient.discover()
    if client is None:
        raise RuntimeError(
            "No display available: this conversation is not running on a host "
            "with a surface supervisor. Live app surfaces need a Pack tab.",
        )
    return client


def _call(method: str, params: dict[str, Any]) -> Any:
    return _client().call(method, params)


def _text(payload: str) -> list[TextContent | ImageContent]:
    return [TextContent(type="text", text=payload)]


_SURFACE_ID_SCHEMA = {
    "surface_id": {
        "type": "string",
        "description": "Surface id returned by surface_open",
    },
}
_COORD_SCHEMA = {
    "x": {"type": "integer", "description": "X coordinate in surface pixels"},
    "y": {"type": "integer", "description": "Y coordinate in surface pixels"},
    "seq": {
        "type": "integer",
        "description": (
            "The seq from the snapshot these coordinates were read off. "
            "Stale values are refused rather than clicked blindly."
        ),
    },
}


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="surface_open",
            description=(
                "Start an application on a fresh headless display and show it "
                "to the user as a live, interactive panel. Returns a surface "
                "id. Give either a named profile or argv directly; an unknown "
                "profile name is refused with the list of what's configured."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "profile": {
                        "type": "string",
                        "description": "Name of a configured app profile",
                    },
                    "argv": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Command and arguments to launch directly, when no "
                            "profile fits. Give either profile or argv, not both."
                        ),
                    },
                    "width": {"type": "integer", "default": 1280},
                    "height": {"type": "integer", "default": 800},
                },
            },
        ),
        Tool(
            name="surface_snapshot",
            description=(
                "Capture what is currently on a surface as an image. Do this "
                "before every click: the returned seq is what makes coordinates "
                "valid."
            ),
            inputSchema={
                "type": "object",
                "properties": dict(_SURFACE_ID_SCHEMA),
                "required": ["surface_id"],
            },
        ),
        Tool(
            name="surface_click",
            description=(
                "Click at a point on the surface. Refused if the user currently "
                "holds control, or if seq is stale."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    **_SURFACE_ID_SCHEMA,
                    **_COORD_SCHEMA,
                    "button": {"type": "integer", "default": 1},
                    "double": {"type": "boolean", "default": False},
                },
                "required": ["surface_id", "x", "y"],
            },
        ),
        Tool(
            name="surface_type",
            description="Type text into whatever on the surface has focus.",
            inputSchema={
                "type": "object",
                "properties": {
                    **_SURFACE_ID_SCHEMA,
                    "text": {"type": "string"},
                },
                "required": ["surface_id", "text"],
            },
        ),
        Tool(
            name="surface_key",
            description=(
                "Send a key combination, in xdotool syntax — 'Return', "
                "'ctrl+s', 'shift+Tab'."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    **_SURFACE_ID_SCHEMA,
                    "keys": {"type": "string"},
                },
                "required": ["surface_id", "keys"],
            },
        ),
        Tool(
            name="surface_close",
            description=(
                "Stop the application and destroy its display. Surfaces are "
                "ephemeral; reopening later means a fresh surface_open."
            ),
            inputSchema={
                "type": "object",
                "properties": dict(_SURFACE_ID_SCHEMA),
                "required": ["surface_id"],
            },
        ),
    ]


def _input_result(surface_id: str, events: list[dict[str, Any]], seq: Any) -> str:
    result = _call(
        "surface_input",
        {
            "surface_id": surface_id,
            "events": events,
            "holder": "model",
            "seq": seq,
        },
    )
    return f"Done. The surface is now at seq {result['seq']}; snapshot again before the next click."


def _record_event(
    name: str,
    arguments: dict[str, Any],
    content: list[TextContent | ImageContent],
) -> None:
    """Mirror cli_media_mcp_server.py's side channel for CLI-backed models.

    A CLI-backed model's tool_use/tool_result blocks are suppressed before
    they reach Alpaca's own chat_state (see anthropic_ollama_server.py,
    "avoid double execution") -- without this, surface_open's sentinel-
    encoded card would only ever exist inside the CLI's own internal
    context, and the model, unable to parse it, would invent its own text
    instead (confirmed live: a broken markdown image). ALPACA_CLI_MEDIA_
    EVENTS is unset for a non-CLI model (the ordinary MCP tool-call path
    already produces a real fold there) and for a plain local tab.
    """
    path = os.environ.get("ALPACA_CLI_MEDIA_EVENTS")
    if not path:
        return
    content_dicts: list[dict[str, Any]] = []
    for item in content:
        if isinstance(item, TextContent):
            content_dicts.append({"type": "text", "text": item.text})
        elif isinstance(item, ImageContent):
            content_dicts.append(
                {"type": "image", "data": item.data, "mimeType": item.mimeType},
            )
    event = {
        "type": "alpaca_tool_event",
        "id": f"alpaca_surface_{name}_{uuid.uuid4().hex}",
        "name": f"alpaca_surface_{name}",
        "arguments": arguments,
        "result": json.dumps({"content": content_dicts}),
    }
    with open(path, "a", encoding="utf-8") as file:
        file.write(json.dumps(event, separators=(",", ":")) + "\n")
        file.flush()


@server.call_tool()
async def call_tool(
    name: str,
    arguments: dict[str, Any],
) -> list[TextContent | ImageContent]:
    content = await _dispatch(name, arguments)
    _record_event(name, arguments, content)
    return content


async def _dispatch(
    name: str,
    arguments: dict[str, Any],
) -> list[TextContent | ImageContent]:
    try:
        if name == "surface_open":
            spec: dict[str, Any] = (
                {"profile": arguments["profile"]}
                if arguments.get("profile")
                else {"argv": arguments.get("argv")}
            )
            result = _call(
                "surface_open",
                {
                    "spec": spec,
                    "width": arguments.get("width", 1280),
                    "height": arguments.get("height", 800),
                    "source": "model",
                },
            )
            # The sentinel is what makes this render as a live-surface card in
            # the transcript rather than a wall of JSON. Only a descriptor is
            # stored — never a session — so a conversation reopened next week
            # shows the card and reports the surface is gone.
            return _text(
                encode_surface_result(
                    result["surface_id"],
                    result["width"],
                    result["height"],
                    result["description"],
                )
                + f"\nSurface {result['surface_id']} is live at seq {result['seq']}. "
                "The user can see and drive it now.",
            )

        if name == "surface_snapshot":
            result = _call("surface_snapshot", {"surface_id": arguments["surface_id"]})
            description = (
                f"Surface {result['surface_id']} ({result['width']}x"
                f"{result['height']}) at seq {result['seq']}. Use this seq when "
                "clicking."
            )
            # Two representations of the same image on purpose: the sentinel
            # text is what Alpaca's own pipeline expands into a real image
            # block for the model (image_tool_result), while ImageContent is
            # what a standards-compliant MCP client consumes.
            return [
                ImageContent(
                    type="image",
                    data=result["data"],
                    mimeType=result["mime_type"],
                ),
                TextContent(
                    type="text",
                    text=image_tool_result.encode_image_result(
                        result["mime_type"],
                        result["data"],
                        description,
                    ),
                ),
            ]

        if name == "surface_click":
            event: dict[str, Any] = {
                "type": "doubleclick" if arguments.get("double") else "click",
                "x": arguments["x"],
                "y": arguments["y"],
                "button": arguments.get("button", 1),
            }
            return _text(
                _input_result(arguments["surface_id"], [event], arguments.get("seq")),
            )

        if name == "surface_type":
            return _text(
                _input_result(
                    arguments["surface_id"],
                    [{"type": "type", "text": arguments["text"]}],
                    arguments.get("seq"),
                ),
            )

        if name == "surface_key":
            return _text(
                _input_result(
                    arguments["surface_id"],
                    [{"type": "key", "keys": arguments["keys"]}],
                    arguments.get("seq"),
                ),
            )

        if name == "surface_close":
            _call("surface_close", {"surface_id": arguments["surface_id"]})
            return _text(f"Surface {arguments['surface_id']} is stopped.")

        return _text(f"Unknown tool: {name}")
    except Exception as exc:
        # Returned as text rather than raised: a refused lease or a stale seq
        # is information the model should act on, not a tool crash.
        return _text(f"Error: {exc}")


async def main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    asyncio.run(main())
