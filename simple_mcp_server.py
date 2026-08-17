"""
A simple MCP server that provides basic text and file operations with TRAMP support.
Supports SSH TRAMP filenames like /ssh:user@host:/path/to/file
"""

import asyncio
import os
from collections.abc import Awaitable
from collections.abc import Callable
from typing import Any
from typing import cast

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import ImageContent
from mcp.types import TextContent
from mcp.types import Tool

import markdown_analyzer
import python_analyzer
import rust_analyzer
from mermaid_wrapper import MermaidError
from mermaid_wrapper import MermaidWrapper
from tramp_handler import get_tramp_handler
from url_to_markdown import CURL_AVAILABLE
from url_to_markdown import fetch_url_as_markdown

# Cached MermaidWrapper instance to avoid re-verification on every call
_mermaid_wrapper: MermaidWrapper | None = None


def get_mermaid_wrapper() -> MermaidWrapper:
    """Get or create cached MermaidWrapper instance."""
    global _mermaid_wrapper
    if _mermaid_wrapper is None:
        _mermaid_wrapper = MermaidWrapper()
    return _mermaid_wrapper


try:
    from radon.complexity import cc_visit

    RADON_AVAILABLE = True
except ImportError:
    RADON_AVAILABLE = False


server = Server("simple-tools")


# Language mapping for syntax highlighting in code fences
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


def get_tramp_handler_safe():
    """Safely get TRAMP handler, returning None and error message if unavailable."""
    try:
        return (get_tramp_handler(), None)
    except RuntimeError as e:
        return (None, str(e))


def check_file_size_limit(filepath: str, tramp, max_size: int = 1024 * 1024):
    """Check if file size is within limits for both local and TRAMP files."""
    if tramp and tramp.is_tramp_path(filepath):
        file_size = tramp.get_size(filepath)
    else:
        file_size = os.path.getsize(filepath)
    if file_size > max_size:
        return (
            False,
            f"Error: File '{filepath}' is too large ({file_size} bytes). Maximum size is 1MB.",
        )
    return (True, "")


def validate_file_exists(filepath: str, tramp):
    """Validate that file exists for both local and TRAMP paths."""
    if tramp and tramp.is_tramp_path(filepath):
        if not tramp.exists(filepath):
            return (False, f"Error: File '{filepath}' does not exist")
        if not tramp.is_file(filepath):
            return (False, f"Error: '{filepath}' is not a file")
    else:
        if not os.path.exists(filepath):
            return (False, f"Error: File '{filepath}' does not exist")
        if not os.path.isfile(filepath):
            return (False, f"Error: '{filepath}' is not a file")
    return (True, "")


def read_file_content(filepath: str, tramp):
    """Read file content for both local and TRAMP files."""
    if tramp and tramp.is_tramp_path(filepath):
        return tramp.read_file(filepath)
    else:
        with open(filepath, encoding="utf-8") as f:
            return f.read()


async def handle_cyclomatic_complexity_tool(
    arguments: dict[str, Any],
) -> list[TextContent]:
    """Handle compute_cyclomatic_complexity tool."""
    if not RADON_AVAILABLE:
        return [
            TextContent(
                type="text",
                text="Error: radon library is not installed. Please install it with: pip install radon",
            ),
        ]
    tramp, tramp_error = get_tramp_handler_safe()
    filepath = arguments.get("file_path")
    code = arguments.get("code")
    threshold = arguments.get("threshold", 10)
    if not filepath and (not code):
        return [
            TextContent(
                type="text",
                text="Error: Either file_path or code parameter must be provided",
            ),
        ]
    try:
        if filepath:
            try:
                filepath = get_filepath_argument(arguments, required=False)
            except ValueError:
                pass
            if tramp and tramp.is_tramp_path(filepath):
                if not tramp:
                    return [TextContent(type="text", text=f"Error: {tramp_error}")]
                valid, error_msg = validate_file_exists(filepath, tramp)
                if not valid:
                    return [TextContent(type="text", text=error_msg)]
                valid, error_msg = check_file_size_limit(filepath, tramp)
                if not valid:
                    return [TextContent(type="text", text=error_msg)]
                code = read_file_content(filepath, tramp)
            else:
                valid, error_msg = validate_file_exists(filepath, None)
                if not valid:
                    return [TextContent(type="text", text=error_msg)]
                valid, error_msg = check_file_size_limit(filepath, None)
                if not valid:
                    return [TextContent(type="text", text=error_msg)]
                code = read_file_content(filepath, None)
        complexity_results = cc_visit(code)
        if not complexity_results:
            return [
                TextContent(
                    type="text",
                    text="No functions or methods found in the code",
                ),
            ]
        result_lines = []
        if filepath:
            result_lines.append(f"Cyclomatic Complexity Analysis for '{filepath}':")
        else:
            result_lines.append("Cyclomatic Complexity Analysis:")
        result_lines.append("=" * 50)
        total_functions = len(complexity_results)
        high_complexity_count = 0
        for func in complexity_results:
            complexity = func.complexity
            if complexity >= threshold:
                status = "⚠️  HIGH"
                high_complexity_count += 1
            elif complexity >= 6:
                status = "⚡ MEDIUM"
            else:
                status = "✅ LOW"
            result_lines.append(
                f"{status:12} | {func.name:30} | Complexity: {complexity:2d} | Line: {func.lineno}",
            )
        result_lines.append("=" * 50)
        result_lines.append(f"Summary:")
        result_lines.append(f"  Total functions/methods: {total_functions}")
        result_lines.append(
            f"  High complexity (>={threshold}): {high_complexity_count}",
        )
        result_lines.append(
            f"  Average complexity: {sum(f.complexity for f in complexity_results) / total_functions:.1f}",
        )
        if high_complexity_count > 0:
            result_lines.append("")
            result_lines.append("💡 Recommendations:")
            result_lines.append("  - Consider breaking down high complexity functions")
            result_lines.append("  - Extract nested logic into separate functions")
            result_lines.append("  - Reduce conditional nesting and branching")
        return [TextContent(type="text", text="\n".join(result_lines))]
    except Exception as e:
        return [
            TextContent(
                type="text",
                text=f"Error analyzing cyclomatic complexity: {str(e)}",
            ),
        ]


async def handle_summarize_python_file_tool(
    arguments: dict[str, Any],
) -> list[TextContent]:
    """Handle summarize_python_file tool."""
    tramp, tramp_error = get_tramp_handler_safe()
    try:
        filepath = get_filepath_argument(arguments, required=True)
    except ValueError as e:
        return [TextContent(type="text", text=f"Error: {str(e)}")]
    try:
        if tramp and tramp.is_tramp_path(filepath):
            if not tramp:
                return [TextContent(type="text", text=f"Error: {tramp_error}")]
            valid, error_msg = validate_file_exists(filepath, tramp)
            if not valid:
                return [TextContent(type="text", text=error_msg)]
            valid, error_msg = check_file_size_limit(filepath, tramp)
            if not valid:
                return [TextContent(type="text", text=error_msg)]
            content = read_file_content(filepath, tramp)
            import tempfile

            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".py",
                delete=False,
                encoding="utf-8",
            ) as tmp:
                tmp.write(content)
                tmp_path = tmp.name
            try:
                results = python_analyzer.analyze_python_file(tmp_path)
                return [
                    TextContent(
                        type="text",
                        text=f"Summary of '{filepath}':\n\n{results}",
                    ),
                ]
            finally:
                os.unlink(tmp_path)
        else:
            valid, error_msg = validate_file_exists(filepath, None)
            if not valid:
                return [TextContent(type="text", text=error_msg)]
            valid, error_msg = check_file_size_limit(filepath, None)
            if not valid:
                return [TextContent(type="text", text=error_msg)]
            results = python_analyzer.analyze_python_file(filepath)
            return [
                TextContent(
                    type="text",
                    text=f"Summary of '{filepath}':\n\n{results}",
                ),
            ]
    except Exception as e:
        return [TextContent(type="text", text=f"Error analyzing Python file: {str(e)}")]


async def handle_summarize_markdown_file_tool(
    arguments: dict[str, Any],
) -> list[TextContent]:
    """Handle summarize_markdown_file tool."""
    tramp, tramp_error = get_tramp_handler_safe()
    try:
        filepath = get_filepath_argument(arguments, required=True)
    except ValueError as e:
        return [TextContent(type="text", text=f"Error: {str(e)}")]
    try:
        if tramp and tramp.is_tramp_path(filepath):
            if not tramp:
                return [TextContent(type="text", text=f"Error: {tramp_error}")]
            valid, error_msg = validate_file_exists(filepath, tramp)
            if not valid:
                return [TextContent(type="text", text=error_msg)]
            valid, error_msg = check_file_size_limit(filepath, tramp)
            if not valid:
                return [TextContent(type="text", text=error_msg)]
            content = read_file_content(filepath, tramp)
            import tempfile

            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".md",
                delete=False,
                encoding="utf-8",
            ) as tmp:
                tmp.write(content)
                tmp_path = tmp.name
            try:
                results = markdown_analyzer.analyze_markdown_file(tmp_path)
                return [TextContent(type="text", text=results)]
            finally:
                os.unlink(tmp_path)
        else:
            valid, error_msg = validate_file_exists(filepath, None)
            if not valid:
                return [TextContent(type="text", text=error_msg)]
            valid, error_msg = check_file_size_limit(filepath, None)
            if not valid:
                return [TextContent(type="text", text=error_msg)]
            results = markdown_analyzer.analyze_markdown_file(filepath)
            return [TextContent(type="text", text=results)]
    except Exception as e:
        return [
            TextContent(type="text", text=f"Error analyzing Markdown file: {str(e)}"),
        ]


async def handle_summarize_rust_file_tool(
    arguments: dict[str, Any],
) -> list[TextContent]:
    """Handle summarize_rust_file tool."""
    tramp, tramp_error = get_tramp_handler_safe()
    try:
        filepath = get_filepath_argument(arguments, required=True)
    except ValueError as e:
        return [TextContent(type="text", text=f"Error: {str(e)}")]
    try:
        if tramp and tramp.is_tramp_path(filepath):
            if not tramp:
                return [TextContent(type="text", text=f"Error: {tramp_error}")]
            valid, error_msg = validate_file_exists(filepath, tramp)
            if not valid:
                return [TextContent(type="text", text=error_msg)]
            valid, error_msg = check_file_size_limit(filepath, tramp)
            if not valid:
                return [TextContent(type="text", text=error_msg)]
            content = read_file_content(filepath, tramp)
            import tempfile

            with tempfile.NamedTemporaryFile(
                mode="wb",
                suffix=".rs",
                delete=False,
            ) as tmp:
                tmp.write(content.encode("utf-8"))
                tmp_path = tmp.name
            try:
                results = rust_analyzer.analyze_rust_file(tmp_path)
                return [
                    TextContent(
                        type="text",
                        text=f"Summary of '{filepath}':\n\n{results}",
                    ),
                ]
            finally:
                os.unlink(tmp_path)
        else:
            valid, error_msg = validate_file_exists(filepath, None)
            if not valid:
                return [TextContent(type="text", text=error_msg)]
            valid, error_msg = check_file_size_limit(filepath, None)
            if not valid:
                return [TextContent(type="text", text=error_msg)]
            results = rust_analyzer.analyze_rust_file(filepath)
            return [
                TextContent(
                    type="text",
                    text=f"Summary of '{filepath}':\n\n{results}",
                ),
            ]
    except Exception as e:
        return [TextContent(type="text", text=f"Error analyzing Rust file: {str(e)}")]


async def handle_render_mermaid_tool(
    arguments: dict[str, Any],
) -> list[TextContent | ImageContent]:
    """Handle render_mermaid tool using mermaid-cli (mmdc)."""

    input_file = arguments.get("input_file", "")
    output_format = arguments.get("output_format", "png")
    theme = arguments.get("theme", "default")
    background_color = arguments.get("background_color")
    width = arguments.get("width")
    height = arguments.get("height")
    scale = arguments.get("scale")
    output_path = arguments.get("output_path")

    if not input_file:
        return [
            TextContent(
                type="text",
                text="Error: 'input_file' is required (path to .mmd or .mermaid file)",
            ),
        ]

    # Validate input file exists
    valid, error_msg = validate_file_exists(input_file, None)
    if not valid:
        return [TextContent(type="text", text=error_msg)]

    try:
        # Get cached mermaid wrapper
        wrapper = get_mermaid_wrapper()

        # Render from input file
        content, path = wrapper.render_from_file(
            input_file=input_file,
            output_format=output_format,
            theme=theme,
            background_color=background_color,
            width=width,
            height=height,
            scale=scale,
            output_path=output_path,
        )

        result_lines = [
            f"✅ Mermaid diagram rendered successfully!",
            f"Input file: {input_file}",
            f"Output file: {path}",
            f"Format: {output_format}",
            f"Theme: {theme}",
            f"Size: {len(content)} bytes",
        ]

        if background_color:
            result_lines.append(f"Background: {background_color}")
        if width:
            result_lines.append(f"Width: {width}px")
        if height:
            result_lines.append(f"Height: {height}px")
        if scale:
            result_lines.append(f"Scale: {scale}x")

        # If no output_path was specified, also return the image content
        if not output_path:
            # Determine MIME type
            mime_types = {
                "png": "image/png",
                "svg": "image/svg+xml",
                "pdf": "application/pdf",
            }
            mime_type = mime_types.get(output_format, "image/png")

            import base64

            base64_data = base64.b64encode(content).decode("utf-8")

            # Clean up temporary output file
            try:
                os.unlink(path)
            except OSError:
                pass

            # Return both text description and image content
            return [
                TextContent(type="text", text="\n".join(result_lines)),
                ImageContent(type="image", data=base64_data, mimeType=mime_type),
            ]
        else:
            return [TextContent(type="text", text="\n".join(result_lines))]

    except MermaidError as e:
        return [TextContent(type="text", text=f"Mermaid error: {str(e)}")]
    except FileNotFoundError as e:
        return [TextContent(type="text", text=f"Error: {str(e)}")]
    except ValueError as e:
        return [TextContent(type="text", text=f"Error: {str(e)}")]
    except Exception as e:
        return [
            TextContent(type="text", text=f"Error rendering mermaid diagram: {str(e)}"),
        ]


async def handle_fetch_url_as_markdown_tool(
    arguments: dict[str, Any],
) -> list[TextContent]:
    """Handle fetch_url_as_markdown tool."""
    if not CURL_AVAILABLE:
        return [
            TextContent(
                type="text",
                text="Error: curl is not installed or not in PATH",
            ),
        ]

    url = arguments.get("url", "")
    summarize_if_long = arguments.get("summarize_if_long", True)
    max_content_length = arguments.get("max_content_length", 50000)
    curl_timeout = arguments.get("curl_timeout", 30)

    if not url:
        return [
            TextContent(
                type="text",
                text="Error: 'url' is required",
            ),
        ]

    try:
        markdown_content = fetch_url_as_markdown(
            url=url,
            summarize_if_long=summarize_if_long,
            max_content_length=max_content_length,
            curl_timeout=curl_timeout,
        )

        result_lines = [
            f"Fetched and converted URL: {url}",
            "=" * 50,
            "",
            markdown_content,
        ]

        return [TextContent(type="text", text="\n".join(result_lines))]

    except RuntimeError as e:
        return [TextContent(type="text", text=f"Error: {str(e)}")]
    except ValueError as e:
        return [TextContent(type="text", text=f"LLM initialization error: {str(e)}")]
    except Exception as e:
        return [TextContent(type="text", text=f"Error fetching URL: {str(e)}")]


@server.list_tools()
async def handle_list_tools() -> list[Tool]:
    """List available tools."""
    return [
        Tool(
            name="summarize_python_file",
            description="Read contents of a python (.py) file and print classes, methods and functions. Supports both local paths and TRAMP filenames.",
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Python (.py) file path (local or TRAMP format)",
                    },
                },
                "required": [],
            },
        ),
        Tool(
            name="summarize_markdown_file",
            description="Read contents of a markdown (.md) file and print structural summary. Supports both local paths and TRAMP filenames.",
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Markdown (.md) file path (local or TRAMP format)",
                    },
                },
                "required": [],
            },
        ),
        Tool(
            name="summarize_rust_file",
            description="Read contents of a rust (.rs) file and print structs, enums, traits, impl blocks, and functions. Supports both local paths and TRAMP filenames.",
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Rust (.rs) file path (local or TRAMP format)",
                    },
                },
                "required": [],
            },
        ),
        Tool(
            name="compute_cyclomatic_complexity",
            description="Compute cyclomatic complexity for Python code using radon. Supports both local paths and TRAMP filenames.",
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Python file path to analyze (local or TRAMP format)",
                    },
                    "code": {
                        "type": "string",
                        "description": "Python code string to analyze (alternative to file_path)",
                    },
                    "threshold": {
                        "type": "integer",
                        "description": "Complexity threshold for highlighting (default: 10)",
                        "default": 10,
                    },
                },
                "required": [],
            },
        ),
        Tool(
            name="render_mermaid",
            description="Render a Mermaid diagram from an input file using mermaid-cli (mmdc). Returns the rendered image or saves to a file.",
            inputSchema={
                "type": "object",
                "properties": {
                    "input_file": {
                        "type": "string",
                        "description": "Path to the input file containing Mermaid diagram code (.mmd or .mermaid file)",
                    },
                    "output_format": {
                        "type": "string",
                        "enum": ["svg", "png", "pdf"],
                        "description": "Output format for the diagram (default: png)",
                        "default": "png",
                    },
                    "theme": {
                        "type": "string",
                        "enum": ["default", "forest", "dark", "neutral"],
                        "description": "Mermaid theme to use (default: default)",
                        "default": "default",
                    },
                    "background_color": {
                        "type": "string",
                        "description": "Background color (e.g., 'transparent', '#ffffff')",
                    },
                    "width": {
                        "type": "integer",
                        "description": "Output width in pixels",
                    },
                    "height": {
                        "type": "integer",
                        "description": "Output height in pixels",
                    },
                    "scale": {
                        "type": "integer",
                        "description": "Scale factor for the output",
                    },
                    "output_path": {
                        "type": "string",
                        "description": "Optional file path to save the rendered diagram. If not provided, returns the image data directly.",
                    },
                },
                "required": ["input_file"],
            },
        ),
        Tool(
            name="fetch_url_as_markdown",
            description="Fetch a URL using curl and convert the HTML content to clean Markdown using an LLM. Optionally summarizes long content.",
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The URL to fetch",
                    },
                    "summarize_if_long": {
                        "type": "boolean",
                        "description": "Whether to summarize if content exceeds max_content_length (default: true)",
                        "default": True,
                    },
                    "max_content_length": {
                        "type": "integer",
                        "description": "Character threshold before summarization kicks in (default: 50000)",
                        "default": 50000,
                    },
                    "curl_timeout": {
                        "type": "integer",
                        "description": "Timeout for curl request in seconds (default: 30)",
                        "default": 30,
                    },
                },
                "required": ["url"],
            },
        ),
    ]


TOOL_HANDLERS: dict[str, Callable[[dict[str, Any]], Awaitable[Any]]] = {
    "summarize_python_file": handle_summarize_python_file_tool,
    "summarize_markdown_file": handle_summarize_markdown_file_tool,
    "summarize_rust_file": handle_summarize_rust_file_tool,
    "compute_cyclomatic_complexity": handle_cyclomatic_complexity_tool,
    "render_mermaid": handle_render_mermaid_tool,
    "fetch_url_as_markdown": handle_fetch_url_as_markdown_tool,
}


@server.call_tool()
async def handle_call_tool(
    name: str,
    arguments: dict[str, Any],
) -> list[TextContent | ImageContent]:
    """Handle tool calls with TRAMP support using dispatch pattern."""
    handler = TOOL_HANDLERS.get(name)
    if handler:
        return cast(list[TextContent | ImageContent], await handler(arguments))
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
