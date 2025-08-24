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

import python_analyzer


# Create server instance
server = Server("simple-tools")


def get_filepath_argument(arguments: dict[str, Any], required: bool = True) -> str:
    """Helper function to get filepath from arguments, trying both 'filepath' and 'file_path'."""
    filepath = arguments.get("filepath")
    if filepath is None:
        filepath = arguments.get("file_path")

    if required and filepath is None:
        raise ValueError(
            "Missing required argument: 'filepath' or 'file_path' must be provided",
        )

    return filepath or ""


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
                    "file_path": {
                        "type": "string",
                        "description": "Alternative parameter name for file path",
                    },
                },
                "required": [],
                "oneOf": [
                    {"required": ["filepath"]},
                    {"required": ["file_path"]},
                ],
            },
        ),
        Tool(
            name="summarize_python_file",
            description="Read contents of a python file and print classes, methods and functions",
            inputSchema={
                "type": "object",
                "properties": {
                    "filepath": {
                        "type": "string",
                        "description": "Path to the python file to read",
                    },
                    "file_path": {
                        "type": "string",
                        "description": "Alternative parameter name for file path",
                    },
                },
                "required": [],
                "oneOf": [
                    {"required": ["filepath"]},
                    {"required": ["file_path"]},
                ],
            },
        ),
        Tool(
            name="modify_python_file",
            description="Modify contents of a python method or function, observe parameters: file_path and new_content",
            inputSchema={
                "type": "object",
                "properties": {
                    "filepath": {
                        "type": "string",
                        "description": "Path to the python file to modify",
                    },
                    "file_path": {
                        "type": "string",
                        "description": "Alternative parameter name for file path",
                    },
                    "new_content": {
                        "type": "string",
                        "description": "Full code of the method or function",
                    },
                },
                "required": ["new_content"],
                "oneOf": [
                    {"required": ["filepath"]},
                    {"required": ["file_path"]},
                ],
            },
        ),
        Tool(
            name="write_file",
            description="Write text content to a file, observe parameters file_path and content",
            inputSchema={
                "type": "object",
                "properties": {
                    "filepath": {
                        "type": "string",
                        "description": "Path to the file to write",
                    },
                    "file_path": {
                        "type": "string",
                        "description": "Alternative parameter name for file path",
                    },
                    "content": {
                        "type": "string",
                        "description": "Content to write to the file",
                    },
                },
                "required": ["content"],
                "oneOf": [
                    {"required": ["filepath", "content"]},
                    {"required": ["file_path", "content"]},
                ],
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
        try:
            filepath = get_filepath_argument(arguments, required=True)
        except ValueError as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

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

    elif name == "summarize_python_file":
        try:
            filepath = get_filepath_argument(arguments, required=True)
        except ValueError as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

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

            results = python_analyzer.analyze_python_file(filepath)

            return [
                TextContent(type="text", text=f"Summary of '{filepath}':\n\n{results}"),
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

    elif name == "modify_python_file":
        try:
            filepath = get_filepath_argument(arguments, required=True)
        except ValueError as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

        code = arguments.get("new_content", "")

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

            import how_to_merge

            location = how_to_merge.determine_merge_location(content, code)

            import code_merging_tool

            merger = code_merging_tool.ASTMerger()
            result = merger.merge_ast(content, code, target=location)

            with open(filepath, "w", encoding="utf-8") as f:
                f.write(result)

            return [
                TextContent(
                    type="text",
                    text=f"Modified location: {location} of '{filepath}' successfully':\n\n",
                ),
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
        try:
            filepath = get_filepath_argument(arguments, required=True)
        except ValueError as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]

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
