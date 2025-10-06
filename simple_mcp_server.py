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

import markdown_analyzer
import python_analyzer

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
                    "text": {"type": "string", "description": "Text to echo back"},
                },
                "required": ["text"],
            },
        ),
        Tool(
            name="get_time",
            description="Get the current date and time",
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        Tool(
            name="list_files",
            description="List files in a directory, observe the input parameter is path",
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
                    # "filepath": {
                    #     "type": "string",
                    #     "description": "Path to the file to read",
                    # },
                    "file_path": {
                        "type": "string",
                        "description": "Alternative parameter name for file path",
                    },
                },
                "required": [],
                # "oneOf": [{"required": ["filepath"]}, {"required": ["file_path"]}],
            },
        ),
        Tool(
            name="summarize_python_file",
            description="Read contents of a python file and print classes, methods and functions, observe this works only for python files",
            inputSchema={
                "type": "object",
                "properties": {
                    # "filepath": {
                    #     "type": "string",
                    #     "description": "Path to the python file to read",
                    # },
                    "file_path": {
                        "type": "string",
                        "description": "Alternative parameter name for file path",
                    },
                },
                "required": [],
                # "oneOf": [{"required": ["filepath"]}, {"required": ["file_path"]}],
            },
        ),
        Tool(
            name="summarize_markdown_file",
            description="Read contents of a markdown file and print structural summary including headings, code blocks, lists, tables, links and images, observe this works only for markdown files",
            inputSchema={
                "type": "object",
                "properties": {
                    # "filepath": {
                    #     "type": "string",
                    #     "description": "Path to the markdown file to read",
                    # },
                    "file_path": {
                        "type": "string",
                        "description": "Alternative parameter name for file path",
                    },
                },
                "required": [],
                # "oneOf": [{"required": ["filepath"]}, {"required": ["file_path"]}],
            },
        ),
        Tool(
            name="modify_python_file",
            description="Replace an existing Python method or function with new implementation. Requires file_path and new_content parameters. The new_content must contain the complete method or function definition including proper indentation. The tool identifies the target method/function by name from the provided new_content. Only works with Python files.",
            inputSchema={
                "type": "object",
                "properties": {
                    # "filepath": {
                    #     "type": "string",
                    #     "description": "Path to the python file to modify",
                    # },
                    "file_path": {
                        "type": "string",
                        "description": "Alternative parameter name for python file path",
                    },
                    "new_content": {
                        "type": "string",
                        "description": "Full python code of the method or function",
                    },
                },
                "required": ["new_content"],
                # "oneOf": [{"required": ["filepath"]}, {"required": ["file_path"]}],
            },
        ),
        Tool(
            name="remove_python_function",
            description="Remove a method or function from a Python file by name. Parameters: file_path (required), function_name (required), and parameter_list (optional). The function_name should be the exact name without parentheses. Use parameter_list when multiple functions/methods share the same name - provide a comma-separated list of parameter names (e.g., 'self, x, y' for methods or 'a, b, c' for functions) to disambiguate which specific function to remove. Works with standalone functions, class methods, static methods, and class methods.",
            inputSchema={
                "type": "object",
                "properties": {
                    # "filepath": {
                    #     "type": "string",
                    #     "description": "Path to the python file to modify",
                    # },
                    "file_path": {
                        "type": "string",
                        "description": "Alternative parameter name for python file path",
                    },
                    "function_name": {
                        "type": "string",
                        "description": "Name of the function or method to remove",
                    },
                    "parameter_list": {
                        "type": "string",
                        "description": "Optional comma-separated parameter list to disambiguate overloaded functions (e.g., 'self, x, y')",
                    },
                },
                "required": ["function_name"],
                # "oneOf": [{"required": ["filepath"]}, {"required": ["file_path"]}],
            },
        ),
        Tool(
            name="modify_markdown_file",
            description="Modify contents of one markdown section, observe parameters: file_path, section_name and updated_section_content, observe this works only for markdown files",
            inputSchema={
                "type": "object",
                "properties": {
                    # "filepath": {
                    #     "type": "string",
                    #     "description": "Path to the markdown file to modify",
                    # },
                    "file_path": {
                        "type": "string",
                        "description": "Alternative parameter name for markdown file path",
                    },
                    "section_name": {
                        "type": "string",
                        "description": "Section name to modify",
                    },
                    "parent_section": {
                        "type": "string",
                        "description": "Parent of the section name to modify",
                    },
                    "updated_section_content": {
                        "type": "string",
                        "description": "Markdown content only of the section to modify",
                    },
                },
                "required": ["section_name", "updated_section_content"],
                # "oneOf": [{"required": ["filepath"]}, {"required": ["file_path"]}],
            },
        ),
        Tool(
            name="remove_markdown_section",
            description="Remove a section from a markdown file, observe parameters: file_path and section_name, if there is ambiguity provide parent_section",
            inputSchema={
                "type": "object",
                "properties": {
                    # "filepath": {
                    #     "type": "string",
                    #     "description": "Path to the markdown file to modify",
                    # },
                    "file_path": {
                        "type": "string",
                        "description": "Alternative parameter name for markdown file path",
                    },
                    "section_name": {
                        "type": "string",
                        "description": "Name of the section to remove",
                    },
                    "parent_section": {
                        "type": "string",
                        "description": "Optional parent section name to disambiguate if multiple sections with same name exist",
                    },
                },
                "required": ["section_name"],
                # "oneOf": [{"required": ["filepath"]}, {"required": ["file_path"]}],
            },
        ),
        Tool(
            name="write_file",
            description="Write text content to a file, observe parameters file_path and content",
            inputSchema={
                "type": "object",
                "properties": {
                    # "filepath": {
                    #     "type": "string",
                    #     "description": "Path to the file to write",
                    # },
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
                # "oneOf": [
                #     {"required": ["filepath", "content"]},
                #     {"required": ["file_path", "content"]},
                # ],
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
            file_size = os.path.getsize(filepath)
            if file_size > 1024 * 1024:
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
            file_size = os.path.getsize(filepath)
            if file_size > 1024 * 1024:
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
    elif name == "summarize_markdown_file":
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
            file_size = os.path.getsize(filepath)
            if file_size > 1024 * 1024:
                return [
                    TextContent(
                        type="text",
                        text=f"Error: File '{filepath}' is too large ({file_size} bytes). Maximum size is 1MB.",
                    ),
                ]
            results = markdown_analyzer.analyze_markdown_file(filepath)
            return [TextContent(type="text", text=results)]
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
            file_size = os.path.getsize(filepath)
            if file_size > 1024 * 1024:
                return [
                    TextContent(
                        type="text",
                        text=f"Error: File '{filepath}' is too large ({file_size} bytes). Maximum size is 1MB.",
                    ),
                ]
            with open(filepath, encoding="utf-8") as f:
                content = f.read()
            import how_to_merge

            import code_merging_tool

            try:
                (
                    is_method,
                    function_name,
                ) = code_merging_tool.validate_single_function_or_method(code)
            except ValueError as e:
                return [
                    TextContent(
                        type="text",
                        text=f"Error: Cannot modify function. Use write_file tool.",
                    ),
                ]
            location = how_to_merge.determine_merge_location(content, code)

            merger = code_merging_tool.ASTMerger()
            result = merger.merge_ast(content, code, target=location)
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(result)
            with open(filepath, encoding="utf-8") as f:
                updated_content = f.read()
            return [
                TextContent(
                    type="text",
                    text=f"Modified location: {location} of '{filepath}' successfully\n\nUpdated file contents:\n\n{updated_content}",
                    # text=f"Modified location: {location} of '{filepath}' successfully\n",
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
    elif name == "remove_python_function":
        try:
            filepath = get_filepath_argument(arguments, required=True)
        except ValueError as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]
        function_name = arguments.get("function_name", "")
        parameter_list = arguments.get("parameter_list", None)
        if not function_name:
            return [TextContent(type="text", text="Error: 'function_name' is required")]
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
            file_size = os.path.getsize(filepath)
            if file_size > 1024 * 1024:
                return [
                    TextContent(
                        type="text",
                        text=f"Error: File '{filepath}' is too large ({file_size} bytes). Maximum size is 1MB.",
                    ),
                ]
            import code_removal_tool

            modified_code, location = code_removal_tool.remove_function_from_file(
                filepath,
                function_name,
                parameter_list,
            )
            return [
                TextContent(
                    type="text",
                    text=f"Successfully removed {location} from '{filepath}'\n\nUpdated file contents:\n\n{modified_code}",
                ),
            ]
        except UnicodeDecodeError:
            return [
                TextContent(
                    type="text",
                    text=f"Error: File '{filepath}' is not a text file or uses unsupported encoding",
                ),
            ]
        except ValueError as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error removing function: {str(e)}")]
    elif name == "modify_markdown_file":
        try:
            filepath = get_filepath_argument(arguments, required=True)
        except ValueError as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]
        new_content = arguments.get("updated_section_content", "")
        section_name = arguments.get("section_name", "")
        parent_section = arguments.get("parent_section", "")
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
            file_size = os.path.getsize(filepath)
            if file_size > 1024 * 1024:
                return [
                    TextContent(
                        type="text",
                        text=f"Error: File '{filepath}' is too large ({file_size} bytes). Maximum size is 1MB.",
                    ),
                ]
            with open(filepath, encoding="utf-8") as f:
                content = f.read()
            import md_merging_tool

            editor = md_merging_tool.MarkdownSectionEditor(content)
            updated = editor.update_section(section_name, parent_section, new_content)
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(updated)
            with open(filepath, encoding="utf-8") as f:
                updated_content = f.read()
            return [
                TextContent(
                    type="text",
                    text=f"Modified section '{section_name}' in '{filepath}' successfully\n\nUpdated file contents:\n\n{updated_content}",
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
    elif name == "remove_markdown_section":
        try:
            filepath = get_filepath_argument(arguments, required=True)
        except ValueError as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]
        section_name = arguments.get("section_name", "")
        parent_section = arguments.get("parent_section", None)
        if not section_name:
            return [TextContent(type="text", text="Error: 'section_name' is required")]
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
            file_size = os.path.getsize(filepath)
            if file_size > 1024 * 1024:
                return [
                    TextContent(
                        type="text",
                        text=f"Error: File '{filepath}' is too large ({file_size} bytes). Maximum size is 1MB.",
                    ),
                ]
            with open(filepath, encoding="utf-8") as f:
                content = f.read()
            import md_merging_tool

            editor = md_merging_tool.MarkdownSectionEditor(content)
            updated = editor.remove_section(section_name, parent_section)
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(updated)
            with open(filepath, encoding="utf-8") as f:
                updated_content = f.read()
            return [
                TextContent(
                    type="text",
                    text=f"Successfully removed section '{section_name}' from '{filepath}'\n\nUpdated file contents:\n\n{updated_content}",
                ),
            ]
        except ValueError as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]
        except UnicodeDecodeError:
            return [
                TextContent(
                    type="text",
                    text=f"Error: File '{filepath}' is not a text file or uses unsupported encoding",
                ),
            ]
        except Exception as e:
            return [TextContent(type="text", text=f"Error removing section: {str(e)}")]
    elif name == "write_file":
        try:
            filepath = get_filepath_argument(arguments, required=True)
        except ValueError as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]
        content = arguments.get("content", "")
        try:
            # Get absolute path for clarity
            abs_path = os.path.abspath(filepath)

            # Create directory if it doesn't exist
            dir_path = os.path.dirname(abs_path)
            if dir_path:
                os.makedirs(dir_path, exist_ok=True)

            # Write the file with explicit flush
            with open(abs_path, "w", encoding="utf-8") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())  # Force write to disk

            # Verify the file exists
            if not os.path.exists(abs_path):
                return [
                    TextContent(
                        type="text",
                        text=f"Error: File write reported success but file doesn't exist at '{abs_path}'",
                    ),
                ]

            # Get actual file size
            actual_size = os.path.getsize(abs_path)
            expected_size = len(content.encode("utf-8"))

            # Verify size matches
            size_match = actual_size == expected_size
            size_info = (
                f"Size: {actual_size} bytes (expected: {expected_size} bytes)"
                if size_match
                else f"WARNING: Size mismatch! Actual: {actual_size} bytes, Expected: {expected_size} bytes"
            )

            # Truncate content for display if it's too long
            display_content = content

            return [
                TextContent(
                    type="text",
                    text=f"Successfully wrote to '{abs_path}'\n{size_info}\n\nContent written:\n```\n{display_content}\n```",
                ),
            ]
        except Exception as e:
            import traceback

            return [
                TextContent(
                    type="text",
                    text=f"Error writing file: {str(e)}\nAttempted path: {filepath}\nTraceback:\n{traceback.format_exc()}",
                ),
            ]
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
