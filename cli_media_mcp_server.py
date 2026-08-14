"""MCP media tools for CLI-backed Alpaca models.

The CLI receives standards-compliant MCP image content while Alpaca receives a
small JSONL side-channel event that it can persist and render as its normal
tool folds. Video bytes remain on disk and use Alpaca's existing chunked player.
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
import internal_tools
import video_tool_result


server = Server(
    "alpaca-media",
    instructions=(
        "Use view_image to inspect image files and view_video to expose a video "
        "to the user. Each result supplies an exact alpaca:// reference; include "
        "that reference in the answer when the media supports the response."
    ),
)


def _record_event(name: str, arguments: dict[str, Any], result: str) -> str:
    tool_id = f"alpaca_media_{name}_{uuid.uuid4().hex}"
    path = os.environ.get("ALPACA_CLI_MEDIA_EVENTS")
    if path:
        event = {
            "type": "alpaca_tool_event",
            "id": tool_id,
            "name": f"alpaca_media_{name}",
            "arguments": arguments,
            "result": result,
        }
        with open(path, "a", encoding="utf-8") as file:
            file.write(json.dumps(event, separators=(",", ":")) + "\n")
            file.flush()
    return tool_id


@server.list_tools()
async def list_tools() -> list[Tool]:
    path_schema = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": (
                    "Image/video path, relative to the active workspace or absolute"
                ),
            },
        },
        "required": ["file_path"],
    }
    return [
        Tool(
            name="view_image",
            description=(
                "Inspect an image file visually. Returns native image content to the "
                "model and an Alpaca inline-image reference for the final answer."
            ),
            inputSchema=path_schema,
        ),
        Tool(
            name="view_video",
            description=(
                "Expose an MP4, WebM, or Ogg file in Alpaca's video player. Video "
                "content is for user playback and is not added to model context."
            ),
            inputSchema=path_schema,
        ),
    ]


@server.call_tool()
async def call_tool(
    name: str,
    arguments: dict[str, Any],
) -> list[TextContent | ImageContent]:
    if name == "view_image":
        result = internal_tools.view_image(arguments)
        text = json.dumps(result, separators=(",", ":"))
        content = result.get("content", [])
        raw_text = content[0].get("text", "") if content else ""
        parsed = image_tool_result.parse_image_result(raw_text)
        tool_id = _record_event(name, arguments, text)
        if parsed is None:
            return [
                TextContent(
                    type="text",
                    text=raw_text or "Image could not be loaded",
                ),
            ]
        mime_type, data, description = parsed
        reference = f"![image](alpaca://image/{tool_id})"
        return [
            ImageContent(type="image", data=data, mimeType=mime_type),
            TextContent(
                type="text",
                text=f"{description}\nInline reference: {reference}",
            ),
        ]

    if name == "view_video":
        result = internal_tools.view_video(arguments)
        text = json.dumps(result, separators=(",", ":"))
        content = result.get("content", [])
        raw_text = content[0].get("text", "") if content else ""
        tool_id = _record_event(name, arguments, text)
        parsed = video_tool_result.parse_video_result(raw_text)
        if parsed is None:
            return [
                TextContent(
                    type="text",
                    text=raw_text or "Video could not be loaded",
                ),
            ]
        _mime_type, _locator, _size, description = parsed
        reference = f"[video](alpaca://video/{tool_id})"
        return [
            TextContent(
                type="text",
                text=f"{description}\nInline reference: {reference}",
            ),
        ]

    return [TextContent(type="text", text=f"Unknown tool: {name}")]


async def main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    asyncio.run(main())
