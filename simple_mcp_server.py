"""
A simple MCP server that provides basic text and file operations with TRAMP support.
Supports SSH TRAMP filenames like /ssh:user@host:/path/to/file
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
from tramp_handler import get_tramp_handler

try:
    from radon.complexity import cc_visit
    from radon.visitors import ComplexityVisitor

    RADON_AVAILABLE = True
except ImportError:
    RADON_AVAILABLE = False

try:
    from mypy import api as mypy_api

    MYPY_AVAILABLE = True
except ImportError:
    MYPY_AVAILABLE = False

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


def validate_directory_exists(path: str, tramp):
    """Validate that directory exists for both local and TRAMP paths."""
    if tramp and tramp.is_tramp_path(path):
        if not tramp.exists(path):
            return (False, f"Error: Path '{path}' does not exist")
        if not tramp.is_dir(path):
            return (False, f"Error: '{path}' is not a directory")
    else:
        if not os.path.exists(path):
            return (False, f"Error: Path '{path}' does not exist")
        if not os.path.isdir(path):
            return (False, f"Error: '{path}' is not a directory")
    return (True, "")


def read_file_content(filepath: str, tramp):
    """Read file content for both local and TRAMP files."""
    if tramp and tramp.is_tramp_path(filepath):
        return tramp.read_file(filepath)
    else:
        with open(filepath, encoding="utf-8") as f:
            return f.read()


def write_file_content(filepath: str, content: str, tramp):
    """Write file content for both local and TRAMP files."""
    if tramp and tramp.is_tramp_path(filepath):
        tramp.write_file(filepath, content)
        try:
            actual_size = tramp.get_size(filepath)
            expected_size = len(content.encode("utf-8"))
            size_info = f"Size: {actual_size} bytes (expected: {expected_size} bytes)"
            return f"Successfully wrote to '{filepath}'\\n{size_info}\\n\\nContent written:\\n```\\n{content}\\n```"
        except:
            return f"Successfully wrote to '{filepath}'\\n\\nContent written:\\n```\\n{content}\\n```"
    else:
        abs_path = os.path.abspath(filepath)
        dir_path = os.path.dirname(abs_path)
        if dir_path:
            os.makedirs(dir_path, exist_ok=True)
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        if not os.path.exists(abs_path):
            return f"Error: File write reported success but file doesn't exist at '{abs_path}'"
        actual_size = os.path.getsize(abs_path)
        expected_size = len(content.encode("utf-8"))
        size_info = f"Size: {actual_size} bytes (expected: {expected_size} bytes)"
        return f"Successfully wrote to '{abs_path}'\\n{size_info}\\n\\nContent written:\\n```\\n{content}\\n```"


def log_failed_python_modification(
    code: str,
    error_message: str,
    filepath: str = "",
) -> str:
    """Log failed Python modification attempts to the python_mod_failures directory."""
    import uuid
    import datetime

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    unique_id = str(uuid.uuid4())[:8]
    filename = f"failed_mod_{timestamp}_{unique_id}.py"
    script_dir = os.path.dirname(os.path.abspath(__file__))
    log_dir = os.path.join(script_dir, "python_mod_failures")
    log_filepath = os.path.join(log_dir, filename)
    os.makedirs(log_dir, exist_ok=True)
    log_content = f"# Failed Python Modification Log\n# Timestamp: {datetime.datetime.now().isoformat()}\n# Original filepath: {filepath}\n# Error: {error_message}\n# Recommended: Use write_file tool instead\n\n{code}\n"
    try:
        with open(log_filepath, "w", encoding="utf-8") as f:
            f.write(log_content)
        return log_filepath
    except Exception as e:
        return ""


async def handle_echo_tool(arguments: dict[str, Any]) -> list[TextContent]:
    """Handle echo tool."""
    text = arguments.get("text", "")
    return [TextContent(type="text", text=f"Echo: {text}")]


async def handle_get_time_tool(arguments: dict[str, Any]) -> list[TextContent]:
    """Handle get_time tool."""
    current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return [TextContent(type="text", text=f"Current time: {current_time}")]


async def handle_run_mypy_python_tool(
    arguments: dict[str, Any],
) -> list[TextContent]:
    """Handle run_mypy_python tool."""
    if not MYPY_AVAILABLE:
        return [
            TextContent(
                type="text",
                text="Error: mypy library is not installed. Please install it with: pip install mypy",
            ),
        ]

    tramp, tramp_error = get_tramp_handler_safe()
    try:
        filepath = get_filepath_argument(arguments, required=True)
    except ValueError as e:
        return [TextContent(type="text", text=f"Error: {str(e)}")]

    strict = arguments.get("strict", False)
    additional_args = arguments.get("additional_args", [])

    try:
        # For TRAMP files, we need to create a temporary local file
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
                # Build mypy command arguments
                mypy_args = [tmp_path]
                if strict:
                    mypy_args.append("--strict")
                mypy_args.extend(additional_args)

                # Run mypy
                stdout, stderr, exit_code = mypy_api.run(mypy_args)

                # Replace temp path with original path in output
                stdout = stdout.replace(tmp_path, filepath)
                stderr = stderr.replace(tmp_path, filepath)

                result_lines = [f"Mypy type checking results for '{filepath}':"]
                result_lines.append("=" * 50)

                if exit_code == 0:
                    result_lines.append("✅ Success: No type errors found!")
                else:
                    result_lines.append(
                        f"⚠️  Type checking completed with exit code: {exit_code}",
                    )

                result_lines.append("")
                result_lines.append("Output:")
                result_lines.append(stdout if stdout else "(no output)")

                if stderr:
                    result_lines.append("")
                    result_lines.append("Errors:")
                    result_lines.append(stderr)

                return [TextContent(type="text", text="\n".join(result_lines))]
            finally:
                os.unlink(tmp_path)
        else:
            # Local file
            valid, error_msg = validate_file_exists(filepath, None)
            if not valid:
                return [TextContent(type="text", text=error_msg)]

            valid, error_msg = check_file_size_limit(filepath, None)
            if not valid:
                return [TextContent(type="text", text=error_msg)]

            # Build mypy command arguments
            mypy_args = [filepath]
            if strict:
                mypy_args.append("--strict")
            mypy_args.extend(additional_args)

            # Run mypy
            stdout, stderr, exit_code = mypy_api.run(mypy_args)

            result_lines = [f"Mypy type checking results for '{filepath}':"]
            result_lines.append("=" * 50)

            if exit_code == 0:
                result_lines.append("✅ Success: No type errors found!")
            else:
                result_lines.append(
                    f"⚠️  Type checking completed with exit code: {exit_code}",
                )

            result_lines.append("")
            result_lines.append("Output:")
            result_lines.append(stdout if stdout else "(no output)")

            if stderr:
                result_lines.append("")
                result_lines.append("Errors:")
                result_lines.append(stderr)

            return [TextContent(type="text", text="\n".join(result_lines))]

    except Exception as e:
        return [
            TextContent(
                type="text",
                text=f"Error running mypy: {str(e)}",
            ),
        ]


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
            f"  Average complexity: {sum((f.complexity for f in complexity_results)) / total_functions:.1f}",
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


async def handle_list_files_tool(arguments: dict[str, Any]) -> list[TextContent]:
    """Handle list_files tool."""
    tramp, tramp_error = get_tramp_handler_safe()
    path = arguments.get("path", ".")
    try:
        if tramp and tramp.is_tramp_path(path):
            if not tramp:
                return [TextContent(type="text", text=f"Error: {tramp_error}")]
            valid, error_msg = validate_directory_exists(path, tramp)
            if not valid:
                return [TextContent(type="text", text=error_msg)]
            items = tramp.list_dir(path)
            if not items:
                return [TextContent(type="text", text=f"Directory '{path}' is empty")]
            files = []
            for name, is_dir, size in items:
                if is_dir:
                    files.append(f"📁 {name}/")
                else:
                    files.append(f"📄 {name} ({size} bytes)")
            file_list = "\\n".join(files)
            return [TextContent(type="text", text=f"Files in '{path}':\\n{file_list}")]
        else:
            valid, error_msg = validate_directory_exists(path, None)
            if not valid:
                return [TextContent(type="text", text=error_msg)]
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
            file_list = "\\n".join(files)
            return [TextContent(type="text", text=f"Files in '{path}':\\n{file_list}")]
    except Exception as e:
        return [TextContent(type="text", text=f"Error listing files: {str(e)}")]


async def handle_read_file_tool(arguments: dict[str, Any]) -> list[TextContent]:
    """Handle read_file tool."""
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
            return [
                TextContent(
                    type="text",
                    text=f"Content of '{filepath}':\\n\\n{content}",
                ),
            ]
        else:
            valid, error_msg = validate_file_exists(filepath, None)
            if not valid:
                return [TextContent(type="text", text=error_msg)]
            valid, error_msg = check_file_size_limit(filepath, None)
            if not valid:
                return [TextContent(type="text", text=error_msg)]
            content = read_file_content(filepath, None)
            return [
                TextContent(
                    type="text",
                    text=f"Content of '{filepath}':\\n\\n{content}",
                ),
            ]
    except (UnicodeDecodeError, PermissionError, FileNotFoundError) as e:
        return [TextContent(type="text", text=f"Error reading file: {str(e)}")]
    except Exception as e:
        return [TextContent(type="text", text=f"Error reading file: {str(e)}")]


async def handle_write_file_tool(arguments: dict[str, Any]) -> list[TextContent]:
    """Handle write_file tool."""
    tramp, tramp_error = get_tramp_handler_safe()
    try:
        filepath = get_filepath_argument(arguments, required=True)
    except ValueError as e:
        return [TextContent(type="text", text=f"Error: {str(e)}")]
    content = arguments.get("content", "")
    try:
        if tramp and tramp.is_tramp_path(filepath):
            if not tramp:
                return [TextContent(type="text", text=f"Error: {tramp_error}")]
        result_msg = write_file_content(filepath, content, tramp)
        return [TextContent(type="text", text=result_msg)]
    except (PermissionError, FileNotFoundError) as e:
        return [TextContent(type="text", text=f"Error writing file: {str(e)}")]
    except Exception as e:
        import traceback

        return [
            TextContent(
                type="text",
                text=f"Error writing file: {str(e)}\\nAttempted path: {filepath}\\nTraceback:\\n{traceback.format_exc()}",
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
                        text=f"Summary of '{filepath}':\\n\\n{results}",
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
                    text=f"Summary of '{filepath}':\\n\\n{results}",
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


async def handle_modify_python_file_tool(
    arguments: dict[str, Any],
) -> list[TextContent]:
    """Handle modify_python_file tool."""
    tramp, tramp_error = get_tramp_handler_safe()
    try:
        filepath = get_filepath_argument(arguments, required=True)
    except ValueError as e:
        return [TextContent(type="text", text=f"Error: {str(e)}")]
    code = arguments.get("new_content", "")
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
            import how_to_merge
            import code_merging_tool

            try:
                # Use validate_imports_and_functions to allow multiple functions
                (
                    is_method,
                    function_name,
                ) = code_merging_tool.validate_imports_and_functions(code)
            except ValueError as e:
                log_path = log_failed_python_modification(code, str(e), filepath)
                error_msg = f"Error: Cannot modify function. Use write_file tool."
                if log_path:
                    error_msg += f" Failed attempt logged to: {log_path}"
                return [TextContent(type="text", text=error_msg)]
            location = how_to_merge.determine_merge_location(content, code)
            merger = code_merging_tool.ASTMerger()
            result = merger.merge_ast(content, code, target=location)
            tramp.write_file(filepath, result)
            return [
                TextContent(
                    type="text",
                    text=f"Modified location: {location} of '{filepath}' successfully\\n\\nUpdated file contents:\\n\\n{result}",
                ),
            ]
        else:
            valid, error_msg = validate_file_exists(filepath, None)
            if not valid:
                return [TextContent(type="text", text=error_msg)]
            valid, error_msg = check_file_size_limit(filepath, None)
            if not valid:
                return [TextContent(type="text", text=error_msg)]
            content = read_file_content(filepath, None)
            import how_to_merge
            import code_merging_tool

            try:
                # Use validate_imports_and_functions to allow multiple functions
                (
                    is_method,
                    function_name,
                ) = code_merging_tool.validate_imports_and_functions(code)
            except ValueError as e:
                log_path = log_failed_python_modification(code, str(e), filepath)
                error_msg = f"Error: Cannot modify function. Use write_file tool."
                if log_path:
                    error_msg += f" Failed attempt logged to: {log_path}"
                return [TextContent(type="text", text=error_msg)]
            location = how_to_merge.determine_merge_location(content, code)
            merger = code_merging_tool.ASTMerger()
            result = merger.merge_ast(content, code, target=location)
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(result)
            return [
                TextContent(
                    type="text",
                    text=f"Modified location: {location} of '{filepath}' successfully\\n\\nUpdated file contents:\\n\\n{result}",
                ),
            ]
    except Exception as e:
        log_path = log_failed_python_modification(code, str(e), filepath)
        error_msg = f"Error modifying Python file: {str(e)}"
        if log_path:
            error_msg += f" Failed attempt logged to: {log_path}"
        return [TextContent(type="text", text=error_msg)]


async def handle_remove_python_function_tool(
    arguments: dict[str, Any],
) -> list[TextContent]:
    """Handle remove_python_function tool."""
    tramp, tramp_error = get_tramp_handler_safe()
    try:
        filepath = get_filepath_argument(arguments, required=True)
    except ValueError as e:
        return [TextContent(type="text", text=f"Error: {str(e)}")]
    function_name = arguments.get("function_name", "")
    parameter_list = arguments.get("parameter_list", None)
    if not function_name:
        return [TextContent(type="text", text="Error: 'function_name' is required")]
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
            import code_removal_tool

            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".py",
                delete=False,
                encoding="utf-8",
            ) as tmp:
                tmp.write(content)
                tmp_path = tmp.name
            try:
                modified_code, location = code_removal_tool.remove_function_from_file(
                    tmp_path,
                    function_name,
                    parameter_list,
                )
                tramp.write_file(filepath, modified_code)
                return [
                    TextContent(
                        type="text",
                        text=f"Successfully removed {location} from '{filepath}'\n\nUpdated file contents:\n\n{modified_code}",
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


async def handle_modify_markdown_file_tool(
    arguments: dict[str, Any],
) -> list[TextContent]:
    """Handle modify_markdown_file tool."""
    tramp, tramp_error = get_tramp_handler_safe()
    try:
        filepath = get_filepath_argument(arguments, required=True)
    except ValueError as e:
        return [TextContent(type="text", text=f"Error: {str(e)}")]
    new_content = arguments.get("updated_section_content", "")
    section_name = arguments.get("section_name", "")
    parent_section = arguments.get("parent_section", "")
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
            import md_merging_tool

            editor = md_merging_tool.MarkdownSectionEditor(content)
            updated = editor.update_section(section_name, parent_section, new_content)
            tramp.write_file(filepath, updated)
            return [
                TextContent(
                    type="text",
                    text=f"Modified section '{section_name}' in '{filepath}' successfully\n\nUpdated file contents:\n\n{updated}",
                ),
            ]
        else:
            valid, error_msg = validate_file_exists(filepath, None)
            if not valid:
                return [TextContent(type="text", text=error_msg)]
            valid, error_msg = check_file_size_limit(filepath, None)
            if not valid:
                return [TextContent(type="text", text=error_msg)]
            content = read_file_content(filepath, None)
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
        return [
            TextContent(type="text", text=f"Error modifying markdown file: {str(e)}"),
        ]


async def handle_remove_markdown_section_tool(
    arguments: dict[str, Any],
) -> list[TextContent]:
    """Handle remove_markdown_section tool."""
    tramp, tramp_error = get_tramp_handler_safe()
    try:
        filepath = get_filepath_argument(arguments, required=True)
    except ValueError as e:
        return [TextContent(type="text", text=f"Error: {str(e)}")]
    section_name = arguments.get("section_name", "")
    parent_section = arguments.get("parent_section", None)
    if not section_name:
        return [TextContent(type="text", text="Error: 'section_name' is required")]
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
            import md_merging_tool

            editor = md_merging_tool.MarkdownSectionEditor(content)
            updated = editor.remove_section(section_name, parent_section)
            tramp.write_file(filepath, updated)
            return [
                TextContent(
                    type="text",
                    text=f"Successfully removed section '{section_name}' from '{filepath}'\n\nUpdated file contents:\n\n{updated}",
                ),
            ]
        else:
            valid, error_msg = validate_file_exists(filepath, None)
            if not valid:
                return [TextContent(type="text", text=error_msg)]
            valid, error_msg = check_file_size_limit(filepath, None)
            if not valid:
                return [TextContent(type="text", text=error_msg)]
            content = read_file_content(filepath, None)
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
            name="evaluate_mypy_python",
            description="Run mypy type checking on a Python file. Supports both local paths and TRAMP filenames.",
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Python file path to type check (local or TRAMP format)",
                    },
                    "strict": {
                        "type": "boolean",
                        "description": "Enable strict mode (default: false)",
                        "default": False,
                    },
                    "additional_args": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Additional mypy command line arguments",
                        "default": [],
                    },
                },
                "required": ["file_path"],
            },
        ),
        Tool(
            name="list_files",
            description="List files in a directory. Supports both local paths and TRAMP filenames (e.g., /ssh:user@host:/path)",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Directory path to list files from (local or TRAMP format)",
                        "default": ".",
                    },
                },
                "required": [],
            },
        ),
        Tool(
            name="read_file",
            description="Read contents of a text file. Supports both local paths and TRAMP filenames (e.g., /ssh:user@host:/path/file)",
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "File path to read (local or TRAMP format)",
                    },
                },
                "required": [],
            },
        ),
        Tool(
            name="summarize_python_file",
            description="Read contents of a python file and print classes, methods and functions. Supports both local paths and TRAMP filenames.",
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Python file path (local or TRAMP format)",
                    },
                },
                "required": [],
            },
        ),
        Tool(
            name="summarize_markdown_file",
            description="Read contents of a markdown file and print structural summary. Supports both local paths and TRAMP filenames.",
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Markdown file path (local or TRAMP format)",
                    },
                },
                "required": [],
            },
        ),
        Tool(
            name="modify_python_file",
            description="Replace an existing Python method or function with new implementation. Supports both local paths and TRAMP filenames.",
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Python file path (local or TRAMP format)",
                    },
                    "new_content": {
                        "type": "string",
                        "description": "Full python code of the method or function",
                    },
                },
                "required": ["new_content"],
            },
        ),
        Tool(
            name="remove_python_function",
            description="Remove a method or function from a Python file by name. Supports both local paths and TRAMP filenames.",
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Python file path (local or TRAMP format)",
                    },
                    "function_name": {
                        "type": "string",
                        "description": "Name of the function or method to remove",
                    },
                    "parameter_list": {
                        "type": "string",
                        "description": "Optional comma-separated parameter list to disambiguate overloaded functions",
                    },
                },
                "required": ["function_name"],
            },
        ),
        Tool(
            name="modify_markdown_file",
            description="Modify contents of one markdown section. Supports both local paths and TRAMP filenames.",
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Markdown file path (local or TRAMP format)",
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
            },
        ),
        Tool(
            name="remove_markdown_section",
            description="Remove a section from a markdown file. Supports both local paths and TRAMP filenames.",
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Markdown file path (local or TRAMP format)",
                    },
                    "section_name": {
                        "type": "string",
                        "description": "Name of the section to remove",
                    },
                    "parent_section": {
                        "type": "string",
                        "description": "Optional parent section name to disambiguate",
                    },
                },
                "required": ["section_name"],
            },
        ),
        Tool(
            name="write_file",
            description="Write text content to a file. Supports both local paths and TRAMP filenames (e.g., /ssh:user@host:/path/file)",
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "File path to write (local or TRAMP format)",
                    },
                    "content": {
                        "type": "string",
                        "description": "Content to write to the file",
                    },
                },
                "required": ["content"],
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
    ]


TOOL_HANDLERS = {
    "echo": handle_echo_tool,
    "get_time": handle_get_time_tool,
    "evaluate_mypy_python": handle_run_mypy_python_tool,
    "compute_cyclomatic_complexity": handle_cyclomatic_complexity_tool,
    "list_files": handle_list_files_tool,
    "read_file": handle_read_file_tool,
    "write_file": handle_write_file_tool,
    "summarize_python_file": handle_summarize_python_file_tool,
    "summarize_markdown_file": handle_summarize_markdown_file_tool,
    "modify_python_file": handle_modify_python_file_tool,
    "remove_python_function": handle_remove_python_function_tool,
    "modify_markdown_file": handle_modify_markdown_file_tool,
    "remove_markdown_section": handle_remove_markdown_section_tool,
}


@server.call_tool()
async def handle_call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    """Handle tool calls with TRAMP support using dispatch pattern."""
    handler = TOOL_HANDLERS.get(name)
    if handler:
        return await handler(arguments)
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
