#!/usr/bin/env python3
"""
A simple MCP server that provides basic text and file operations.
"""
import asyncio
import datetime
import json
import os
import sys
from typing import Any
from typing import Dict
from typing import List

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import EmbeddedResource
from mcp.types import ImageContent
from mcp.types import Resource
from mcp.types import TextContent
from mcp.types import Tool


# Create server instance
server = Server("simple-tools")


@server.list_tools()
async def handle_list_tools() -> list[Tool]:
    """List available tools."""
    return [
        Tool(
            name="echo",
            description="Echo back the provided text",
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "Text to echo back",
                    },
                },
                "required": ["text"],
            },
        ),
        Tool(
            name="get_time",
            description="Get the current date and time",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": [],
            },
        ),
        Tool(
            name="list_files",
            description="List files in a directory",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Directory path to list files from",
                        "default": ".",
                    },
                },
                "required": [],
            },
        ),
        Tool(
            name="read_file",
            description="Read contents of a text file",
            inputSchema={
                "type": "object",
                "properties": {
                    "filepath": {
                        "type": "string",
                        "description": "Path to the file to read",
                    },
                },
                "required": ["filepath"],
            },
        ),
        Tool(
            name="write_file",
            description="Write text content to a file",
            inputSchema={
                "type": "object",
                "properties": {
                    "filepath": {
                        "type": "string",
                        "description": "Path to the file to write",
                    },
                    "content": {
                        "type": "string",
                        "description": "Content to write to the file",
                    },
                },
                "required": ["filepath", "content"],
            },
        ),
    ]


@server.call_tool()
async def handle_call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    """Handle tool calls."""

    if name == "echo":
        text = arguments.get("text", "")
        return [TextContent(type="text", text=f"Echo: {text}")]

    elif name == "get_time":
        current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return [TextContent(type="text", text=f"Current time: {current_time}")]

    elif name == "list_files":
        path = arguments.get("path", ".")
        try:
            if not os.path.exists(path):
                return [
                    TextContent(
                        type="text",
                        text=f"Error: Path '{path}' does not exist",
                    ),
                ]

            if not os.path.isdir(path):
                return [
                    TextContent(
                        type="text",
                        text=f"Error: '{path}' is not a directory",
                    ),
                ]

            files = []
            for item in os.listdir(path):
                item_path = os.path.join(path, item)
                if os.path.isfile(item_path):
                    size = os.path.getsize(item_path)
                    files.append(f"📄 {item} ({size} bytes)")
                elif os.path.isdir(item_path):
                    files.append(f"📁 {item}/")

            if not files:
                return [TextContent(type="text", text=f"Directory '{path}' is empty")]

            file_list = "\n".join(files)
            return [TextContent(type="text", text=f"Files in '{path}':\n{file_list}")]

        except Exception as e:
            return [TextContent(type="text", text=f"Error listing files: {str(e)}")]

    elif name == "read_file":
        filepath = arguments.get("filepath", "")
        try:
            if not os.path.exists(filepath):
                return [
                    TextContent(
                        type="text",
                        text=f"Error: File '{filepath}' does not exist",
                    ),
                ]

            if not os.path.isfile(filepath):
                return [
                    TextContent(type="text", text=f"Error: '{filepath}' is not a file"),
                ]

            # Check file size to avoid reading huge files
            file_size = os.path.getsize(filepath)
            if file_size > 1024 * 1024:  # 1MB limit
                return [
                    TextContent(
                        type="text",
                        text=f"Error: File '{filepath}' is too large ({file_size} bytes). Maximum size is 1MB.",
                    ),
                ]

            with open(filepath, encoding="utf-8") as f:
                content = f.read()

            return [
                TextContent(type="text", text=f"Content of '{filepath}':\n\n{content}"),
            ]

        except UnicodeDecodeError:
            return [
                TextContent(
                    type="text",
                    text=f"Error: File '{filepath}' is not a text file or uses unsupported encoding",
                ),
            ]
        except Exception as e:
            return [TextContent(type="text", text=f"Error reading file: {str(e)}")]

    elif name == "write_file":
        filepath = arguments.get("filepath", "")
        content = arguments.get("content", "")

        try:
            # Create directory if it doesn't exist
            os.makedirs(os.path.dirname(filepath), exist_ok=True)

            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)

            file_size = len(content.encode("utf-8"))
            return [
                TextContent(
                    type="text",
                    text=f"Successfully wrote {file_size} bytes to '{filepath}'",
                ),
            ]

        except Exception as e:
            return [TextContent(type="text", text=f"Error writing file: {str(e)}")]

    else:
        return [TextContent(type="text", text=f"Unknown tool: {name}")]


async def main():
    """Main entry point for the server."""
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    asyncio.run(main())
