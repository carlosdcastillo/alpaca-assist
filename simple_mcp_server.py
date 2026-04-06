"""
A simple MCP server that provides basic text and file operations with TRAMP support.
Supports SSH TRAMP filenames like /ssh:user@host:/path/to/file
"""
import asyncio
import datetime
import hashlib
import json
import os
import sys
from collections.abc import Awaitable
from collections.abc import Callable
from typing import Any
from typing import cast
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
import rust_analyzer
from mermaid_wrapper import MermaidError
from mermaid_wrapper import MermaidWrapper

# Cached MermaidWrapper instance to avoid re-verification on every call
_mermaid_wrapper: MermaidWrapper | None = None


def get_mermaid_wrapper() -> MermaidWrapper:
    """Get or create cached MermaidWrapper instance."""
    global _mermaid_wrapper
    if _mermaid_wrapper is None:
        _mermaid_wrapper = MermaidWrapper()
    return _mermaid_wrapper


from rg_wrapper import RipgrepError
from rg_wrapper import RipgrepWrapper
from tramp_handler import get_tramp_handler
from url_to_markdown import CURL_AVAILABLE
from url_to_markdown import fetch_url_as_markdown
from shell_executor import ShellExecutor

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

# Check if cargo is available
import shutil

CARGO_AVAILABLE = shutil.which("cargo") is not None


server = Server("simple-tools")

# Language mapping for syntax highlighting in code fences
LANGUAGE_MAP = {
    ".py": "python",
    ".rs": "rust",
    ".md": "markdown",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".java": "java",
    ".go": "go",
    ".rb": "ruby",
    ".php": "php",
    ".sh": "bash",
    ".bash": "bash",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".xml": "xml",
    ".html": "html",
    ".htm": "html",
    ".css": "css",
    ".sql": "sql",
}


def get_language_from_file(filepath: str) -> str:
    """Get the language identifier for syntax highlighting based on file extension.

    Args:
        filepath: The file path to analyze

    Returns:
        Language identifier for code fence, or empty string if not recognized
    """
    _, ext = os.path.splitext(filepath)
    return LANGUAGE_MAP.get(ext.lower(), "")


def compute_content_hash(content: str) -> str:
    """Compute truncated SHA256 hash for content verification."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


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
    content_hash = compute_content_hash(content)
    content_bytes = len(content.encode("utf-8"))

    if tramp and tramp.is_tramp_path(filepath):
        tramp.write_file(filepath, content)
        try:
            actual_size = tramp.get_size(filepath)
            size_info = f"Size: {actual_size} bytes (expected: {content_bytes} bytes)"
            return f"Successfully wrote to '{filepath}'\n{size_info}\nContent hash: {content_hash}"
        except:
            return f"Successfully wrote to '{filepath}'\nContent hash: {content_hash}"
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
        size_info = f"Size: {actual_size} bytes (expected: {content_bytes} bytes)"
        return f"Successfully wrote to '{abs_path}'\n{size_info}\nContent hash: {content_hash}"


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


async def handle_search_files_for_text_tool(
    arguments: dict[str, Any],
) -> list[TextContent]:
    """Handle search_files_for_text tool using ripgrep."""

    directory = arguments.get("directory", ".")
    search_pattern = arguments.get("search_pattern", "")
    file_pattern = arguments.get("file_pattern")
    case_insensitive = arguments.get("case_insensitive", False)
    whole_word = arguments.get("whole_word", False)
    context_lines = arguments.get("context_lines", 0)
    max_count = arguments.get("max_count")

    if not search_pattern:
        return [
            TextContent(
                type="text",
                text="Error: 'search_pattern' is required",
            ),
        ]

    try:
        # Validate directory exists
        valid, error_msg = validate_directory_exists(directory, None)
        if not valid:
            return [TextContent(type="text", text=error_msg)]

        # Initialize ripgrep wrapper
        wrapper = RipgrepWrapper()

        # Perform search
        results = wrapper.search(
            directory=directory,
            search_pattern=search_pattern,
            file_pattern=file_pattern,
            case_insensitive=case_insensitive,
            whole_word=whole_word,
            max_count=max_count,
            context_lines=context_lines,
        )

        if not results:
            return [
                TextContent(
                    type="text",
                    text=f"No matches found for pattern '{search_pattern}' in '{directory}'",
                ),
            ]

        # Format results
        result_lines = [
            f"Search Results for pattern '{search_pattern}' in '{directory}':",
            "=" * 70,
        ]

        # Group results by file
        files_dict: dict[str, list[dict[str, Any]]] = {}
        for result in results:
            if result.get("type") == "match":
                data = result.get("data", {})
                file_path = data.get("path", {}).get("text", "unknown")
                if file_path not in files_dict:
                    files_dict[file_path] = []
                files_dict[file_path].append(data)

        match_count = 0
        for file_path in sorted(files_dict.keys()):
            matches = files_dict[file_path]
            result_lines.append(f"\n📄 {file_path} ({len(matches)} matches)")
            result_lines.append("-" * 70)

            for match in matches:
                line_num = match.get("line_number", "?")
                lines = match.get("lines", {})
                text = lines.get("text", "")

                # Get line content with highlighting
                if text:
                    # Truncate long lines
                    if len(text) > 100:
                        text = text[:97] + "..."
                    result_lines.append(f"  Line {line_num}: {text}")

                match_count += 1

        result_lines.append("")
        result_lines.append("=" * 70)
        result_lines.append(f"Total matches: {match_count}")

        if file_pattern:
            result_lines.append(f"File pattern filter: {file_pattern}")
        if case_insensitive:
            result_lines.append("Search mode: Case-insensitive")
        if whole_word:
            result_lines.append("Search mode: Whole word match only")
        if context_lines > 0:
            result_lines.append(f"Context lines: {context_lines}")

        return [TextContent(type="text", text="\n".join(result_lines))]

    except ValueError as e:
        return [TextContent(type="text", text=f"Error: {str(e)}")]
    except RipgrepError as e:
        return [TextContent(type="text", text=f"Ripgrep error: {str(e)}")]
    except Exception as e:
        return [TextContent(type="text", text=f"Error performing search: {str(e)}")]


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


async def handle_cargo_check_rust_tool(
    arguments: dict[str, Any],
) -> list[TextContent]:
    """Handle evaluate_cargo_check_rust tool."""
    if not CARGO_AVAILABLE:
        return [
            TextContent(
                type="text",
                text="Error: cargo is not installed or not in PATH. Please install Rust toolchain.",
            ),
        ]

    tramp, tramp_error = get_tramp_handler_safe()
    manifest_path = arguments.get("manifest_path")

    if not manifest_path:
        return [
            TextContent(
                type="text",
                text="Error: 'manifest_path' is required (path to Cargo.toml)",
            ),
        ]

    additional_args = arguments.get("additional_args", [])

    try:
        # For TRAMP files, we need to run cargo check on the remote machine
        if tramp and tramp.is_tramp_path(manifest_path):
            if not tramp:
                return [TextContent(type="text", text=f"Error: {tramp_error}")]

            valid, error_msg = validate_file_exists(manifest_path, tramp)
            if not valid:
                return [TextContent(type="text", text=error_msg)]

            # Parse the TRAMP path to get remote info
            tramp_info = tramp.parse_tramp_path(manifest_path)
            remote_path = tramp_info.path

            # Build cargo check command for remote execution
            cargo_cmd = f"cargo check --manifest-path {remote_path}"
            if additional_args:
                cargo_cmd += " " + " ".join(additional_args)

            # Run cargo check on remote machine via SSH
            result = tramp._run_ssh_command(tramp_info, cargo_cmd)

            stdout = result.stdout
            stderr = result.stderr
            exit_code = result.returncode

            result_lines = [f"Cargo check results for '{manifest_path}':"]
            result_lines.append("=" * 50)

            if exit_code == 0:
                result_lines.append("✅ Success: No compilation errors found!")
            else:
                result_lines.append(
                    f"⚠️  Cargo check completed with exit code: {exit_code}",
                )

            result_lines.append("")
            if stdout:
                result_lines.append("Output:")
                result_lines.append(stdout)

            if stderr:
                result_lines.append("")
                result_lines.append("Compiler messages:")
                result_lines.append(stderr)

            return [TextContent(type="text", text="\n".join(result_lines))]
        else:
            # Local file
            valid, error_msg = validate_file_exists(manifest_path, None)
            if not valid:
                return [TextContent(type="text", text=error_msg)]

            # Build cargo check command
            import subprocess

            cargo_args = ["cargo", "check", "--manifest-path", manifest_path]
            cargo_args.extend(additional_args)

            # Run cargo check
            result = subprocess.run(
                cargo_args,
                capture_output=True,
                text=True,
            )

            stdout = result.stdout
            stderr = result.stderr
            exit_code = result.returncode

            result_lines = [f"Cargo check results for '{manifest_path}':"]
            result_lines.append("=" * 50)

            if exit_code == 0:
                result_lines.append("✅ Success: No compilation errors found!")
            else:
                result_lines.append(
                    f"⚠️  Cargo check completed with exit code: {exit_code}",
                )

            result_lines.append("")
            if stdout:
                result_lines.append("Output:")
                result_lines.append(stdout)

            if stderr:
                result_lines.append("")
                result_lines.append("Compiler messages:")
                result_lines.append(stderr)

            return [TextContent(type="text", text="\n".join(result_lines))]

    except Exception as e:
        return [
            TextContent(
                type="text",
                text=f"Error running cargo check: {str(e)}",
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
            file_list = "\n".join(files)
            return [TextContent(type="text", text=f"Files in '{path}':\n{file_list}")]
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
            file_list = "\n".join(files)
            return [TextContent(type="text", text=f"Files in '{path}':\n{file_list}")]
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
            content_hash = compute_content_hash(content)
            language = get_language_from_file(filepath)
            fence = f"```{language}" if language else "```"
            return [
                TextContent(
                    type="text",
                    text=f"Content of '{filepath}' (hash: {content_hash}):\n\n{fence}\n{content}\n```",
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
            content_hash = compute_content_hash(content)
            language = get_language_from_file(filepath)
            fence = f"```{language}" if language else "```"
            return [
                TextContent(
                    type="text",
                    text=f"Content of '{filepath}' (hash: {content_hash}):\n\n{fence}\n{content}\n```",
                ),
            ]
    except (UnicodeDecodeError, PermissionError, FileNotFoundError) as e:
        return [TextContent(type="text", text=f"Error reading file: {str(e)}")]
    except Exception as e:
        return [TextContent(type="text", text=f"Error reading file: {str(e)}")]


# Per-call cap on the returned slice, regardless of the requested range.
READ_FILE_RANGE_MAX_LINES = 500


async def handle_read_file_range_tool(arguments: dict[str, Any]) -> list[TextContent]:
    """Handle read_file_range tool.

    Unlike read_file (capped at 1MB total), this reads a bounded line slice
    out of a file of any size — meant for paging through files saved by the
    output-truncation gate, which can be much larger than 1MB.
    """
    tramp, tramp_error = get_tramp_handler_safe()
    try:
        filepath = get_filepath_argument(arguments, required=True)
    except ValueError as e:
        return [TextContent(type="text", text=f"Error: {str(e)}")]

    start_line = arguments.get("start_line", 1)
    if not isinstance(start_line, int) or start_line < 1:
        return [
            TextContent(type="text", text="Error: start_line must be an integer >= 1"),
        ]

    end_line = arguments.get("end_line")
    if end_line is None:
        end_line = start_line + READ_FILE_RANGE_MAX_LINES - 1
    elif not isinstance(end_line, int) or end_line < start_line:
        return [
            TextContent(
                type="text",
                text="Error: end_line must be an integer >= start_line",
            ),
        ]
    if end_line - start_line + 1 > READ_FILE_RANGE_MAX_LINES:
        end_line = start_line + READ_FILE_RANGE_MAX_LINES - 1

    try:
        is_tramp_path = bool(tramp and tramp.is_tramp_path(filepath))
        if is_tramp_path and not tramp:
            return [TextContent(type="text", text=f"Error: {tramp_error}")]
        active_tramp = tramp if is_tramp_path else None

        valid, error_msg = validate_file_exists(filepath, active_tramp)
        if not valid:
            return [TextContent(type="text", text=error_msg)]
        # Generous ceiling on the source file (not the returned slice) — this
        # tool exists specifically to page through files past read_file's 1MB
        # whole-file limit.
        valid, error_msg = check_file_size_limit(
            filepath,
            active_tramp,
            max_size=200 * 1024 * 1024,
        )
        if not valid:
            return [TextContent(type="text", text=error_msg)]

        content = read_file_content(filepath, active_tramp)
        lines = content.splitlines()
        total_lines = len(lines)
        if start_line > total_lines:
            return [
                TextContent(
                    type="text",
                    text=(
                        f"Error: start_line {start_line} exceeds total line "
                        f"count {total_lines} for '{filepath}'"
                    ),
                ),
            ]

        selected = lines[start_line - 1 : end_line]
        language = get_language_from_file(filepath)
        fence = f"```{language}" if language else "```"
        header = (
            f"Lines {start_line}-{min(end_line, total_lines)} of '{filepath}' "
            f"({total_lines} lines total):\n\n"
        )
        return [
            TextContent(
                type="text",
                text=f"{header}{fence}\n" + "\n".join(selected) + "\n```",
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
                text=f"Error writing file: {str(e)}\nAttempted path: {filepath}\nTraceback:\n{traceback.format_exc()}",
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


async def handle_run_shell_command_tool(
    arguments: dict[str, Any],
) -> list[TextContent]:
    """Handle run_shell_command tool."""
    command = arguments.get("command", "")
    working_directory = arguments.get("working_directory")
    timeout = arguments.get("timeout", 60)

    if not command:
        return [TextContent(type="text", text="Error: 'command' is required")]

    executor = ShellExecutor()
    result = executor.run(
        command=command,
        working_directory=working_directory,
        timeout=timeout,
    )

    return [TextContent(type="text", text=result.format_output())]


async def handle_modify_file_tool(arguments: dict[str, Any]) -> list[TextContent]:
    """Handle modify_file tool for exact string replacements."""
    tramp, tramp_error = get_tramp_handler_safe()
    try:
        filepath = get_filepath_argument(arguments, required=True)
    except ValueError as e:
        return [TextContent(type="text", text=f"Error: {str(e)}")]

    old_string = arguments.get("old_string", "")
    new_string = arguments.get("new_string", "")
    replace_all = arguments.get("replace_all", False)

    if not old_string:
        return [TextContent(type="text", text="Error: 'old_string' is required")]
    if new_string is None:
        return [TextContent(type="text", text="Error: 'new_string' is required")]
    if old_string == new_string:
        return [
            TextContent(
                type="text",
                text="Error: 'old_string' and 'new_string' must be different",
            ),
        ]

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

            # Check if old_string exists in content
            if old_string not in content:
                return [
                    TextContent(
                        type="text",
                        text=f"Error: 'old_string' not found in file '{filepath}'",
                    ),
                ]

            # Perform replacement
            if replace_all:
                modified_content = content.replace(old_string, new_string)
                occurrence_count = content.count(old_string)
            else:
                # Check if old_string is unique (appears exactly once)
                occurrence_count = content.count(old_string)
                if occurrence_count > 1:
                    return [
                        TextContent(
                            type="text",
                            text=f"Error: 'old_string' appears {occurrence_count} times in file. Use 'replace_all: true' to replace all occurrences or provide a larger string with more context to make it unique.",
                        ),
                    ]
                modified_content = content.replace(old_string, new_string, 1)

            # Write modified content and verify for TRAMP (full symmetry)
            tramp.write_file(filepath, modified_content)
            verified_content = tramp.read_file(filepath)
            verified_hash = compute_content_hash(verified_content)

            return [
                TextContent(
                    type="text",
                    text=f"Successfully modified '{filepath}'\nReplaced {occurrence_count} occurrence(s)\nContent hash: {verified_hash}",
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

            # Check if old_string exists in content
            if old_string not in content:
                return [
                    TextContent(
                        type="text",
                        text=f"Error: 'old_string' not found in file '{filepath}'",
                    ),
                ]

            # Perform replacement
            if replace_all:
                modified_content = content.replace(old_string, new_string)
                occurrence_count = content.count(old_string)
            else:
                # Check if old_string is unique (appears exactly once)
                occurrence_count = content.count(old_string)
                if occurrence_count > 1:
                    return [
                        TextContent(
                            type="text",
                            text=f"Error: 'old_string' appears {occurrence_count} times in file. Use 'replace_all: true' to replace all occurrences or provide a larger string with more context to make it unique.",
                        ),
                    ]
                modified_content = content.replace(old_string, new_string, 1)

            # Write modified content and verify
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(modified_content)
                f.flush()
                os.fsync(f.fileno())

            verified_hash = compute_content_hash(modified_content)
            return [
                TextContent(
                    type="text",
                    text=f"Successfully modified '{filepath}'\nReplaced {occurrence_count} occurrence(s)\nContent hash: {verified_hash}",
                ),
            ]
    except (PermissionError, FileNotFoundError) as e:
        return [TextContent(type="text", text=f"Error modifying file: {str(e)}")]
    except Exception as e:
        return [TextContent(type="text", text=f"Error modifying file: {str(e)}")]


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
            name="search_files_for_text",
            description="Search for text patterns in files within a directory using ripgrep. Supports regex patterns and multiple search options.",
            inputSchema={
                "type": "object",
                "properties": {
                    "directory": {
                        "type": "string",
                        "description": "Directory to recursively search (default: current directory)",
                        "default": ".",
                    },
                    "search_pattern": {
                        "type": "string",
                        "description": "Regular expression pattern to search for (required)",
                    },
                    "file_pattern": {
                        "type": "string",
                        "description": "Optional glob pattern to filter files (e.g., '*.py', '*.txt')",
                    },
                    "case_insensitive": {
                        "type": "boolean",
                        "description": "Search case-insensitively (default: false)",
                        "default": False,
                    },
                    "whole_word": {
                        "type": "boolean",
                        "description": "Match whole words only (default: false)",
                        "default": False,
                    },
                    "context_lines": {
                        "type": "integer",
                        "description": "Number of context lines before and after match (default: 0)",
                        "default": 0,
                    },
                    "max_count": {
                        "type": "integer",
                        "description": "Maximum number of matches to return per file",
                    },
                },
                "required": ["search_pattern"],
            },
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
            name="read_file_range",
            description=(
                "Read a bounded line range from a text file of any size "
                "(read_file is capped at 1MB total). Use this to page through "
                "a large file, such as one referenced by an "
                "'Output truncated' notice from a tool result. Returns at "
                f"most {READ_FILE_RANGE_MAX_LINES} lines per call."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "File path to read (local or TRAMP format)",
                    },
                    "start_line": {
                        "type": "integer",
                        "description": "First line to read, 1-indexed (default: 1)",
                        "default": 1,
                    },
                    "end_line": {
                        "type": "integer",
                        "description": (
                            f"Last line to read, inclusive (default: start_line + "
                            f"{READ_FILE_RANGE_MAX_LINES - 1}; clamped to that "
                            "span regardless of what is requested)"
                        ),
                    },
                },
                "required": ["file_path"],
            },
        ),
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
        Tool(
            name="modify_file",
            description="Performs exact string replacements in files. Supports both local paths and TRAMP filenames.",
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "The absolute path to the file to modify",
                    },
                    "old_string": {
                        "type": "string",
                        "description": "The text to replace",
                    },
                    "new_string": {
                        "type": "string",
                        "description": "The text to replace it with (must be different from old_string)",
                    },
                    "replace_all": {
                        "type": "boolean",
                        "description": "Replace all occurrences of old_string (default false)",
                        "default": False,
                    },
                },
                "required": ["file_path", "old_string", "new_string"],
            },
        ),
        Tool(
            name="evaluate_cargo_check_rust",
            description="Run cargo check on a Rust project. Supports both local paths and TRAMP filenames.",
            inputSchema={
                "type": "object",
                "properties": {
                    "manifest_path": {
                        "type": "string",
                        "description": "Path to Cargo.toml file (local or TRAMP format)",
                    },
                    "additional_args": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Additional cargo check command line arguments",
                        "default": [],
                    },
                },
                "required": ["manifest_path"],
            },
        ),
        Tool(
            name="render_mermaid",
            description="Render a Mermaid diagram from an input file using mermaid-cli (mmdc). Supports flowcharts, sequence diagrams, class diagrams, state diagrams, ER diagrams, Gantt charts, pie charts, and more. Returns the rendered image or saves to a file.",
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
        Tool(
            name="run_shell_command",
            description="Execute an allowlisted shell command. Allowed: python, pip, git, node, npm, npx, cargo, rustc, make, cmake. Extend via config file.",
            inputSchema={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The command to execute (e.g., 'python --version', 'git status')",
                    },
                    "working_directory": {
                        "type": "string",
                        "description": "Directory to run the command in (default: current directory)",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Command timeout in seconds (default: 60, max: 300)",
                        "default": 60,
                    },
                },
                "required": ["command"],
            },
        ),
    ]


TOOL_HANDLERS: dict[str, Callable[[dict[str, Any]], Awaitable[Any]]] = {
    "echo": handle_echo_tool,
    "get_time": handle_get_time_tool,
    "search_files_for_text": handle_search_files_for_text_tool,
    "evaluate_mypy_python": handle_run_mypy_python_tool,
    "evaluate_cargo_check_rust": handle_cargo_check_rust_tool,
    "compute_cyclomatic_complexity": handle_cyclomatic_complexity_tool,
    "list_files": handle_list_files_tool,
    "read_file": handle_read_file_tool,
    "read_file_range": handle_read_file_range_tool,
    "write_file": handle_write_file_tool,
    "summarize_python_file": handle_summarize_python_file_tool,
    "summarize_markdown_file": handle_summarize_markdown_file_tool,
    "summarize_rust_file": handle_summarize_rust_file_tool,
    "modify_file": handle_modify_file_tool,
    "render_mermaid": handle_render_mermaid_tool,
    "fetch_url_as_markdown": handle_fetch_url_as_markdown_tool,
    "run_shell_command": handle_run_shell_command_tool,
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
