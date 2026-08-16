"""Internal tools — direct Python implementations that bypass MCP.

These tools are called synchronously from a background thread in chat_tab_tools.py
instead of going through the MCP stdio protocol, which removes the per-call process
overhead and makes them available even when no MCP server is configured.

Return format mirrors mcp_manager.call_tool(): {"content": [...], "isError": bool}
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import json
import os
import sqlite3
from typing import Any

import image_tool_result
import video_tool_result
from database import CONVERSATIONS_DB
from rg_wrapper import RipgrepError
from rg_wrapper import RipgrepWrapper
from shell_executor import ShellExecutor
from tramp_handler import get_tramp_handler

READ_FILE_RANGE_MAX_LINES = 500

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


# ---------------------------------------------------------------------------
# Result helpers
# ---------------------------------------------------------------------------


def _ok(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": False}


def _err(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": True}


# ---------------------------------------------------------------------------
# Shared file-access helpers (mirrors simple_mcp_server.py)
# ---------------------------------------------------------------------------


def _get_tramp() -> tuple[Any, str | None]:
    try:
        return get_tramp_handler(), None
    except RuntimeError as e:
        return None, str(e)


def _get_filepath(arguments: dict[str, Any], required: bool = True) -> str:
    fp = arguments.get("filepath") or arguments.get("file_path")
    if required and fp is None:
        raise ValueError(
            "Missing required argument: 'filepath' or 'file_path' must be provided",
        )
    return _workspace_relative(fp or "")


def _workspace_relative(path: str) -> str:
    """Resolve relative project-tool paths inside a configured Pack workspace."""
    workspace = os.environ.get("ALPACA_WORKSPACE")
    if workspace and path and not os.path.isabs(path):
        return os.path.join(workspace, path)
    return path


def _default_tool_directory() -> str:
    return os.environ.get("ALPACA_WORKSPACE") or "."


def _file_exists(filepath: str, tramp: Any) -> tuple[bool, str]:
    if tramp and tramp.is_tramp_path(filepath):
        if not tramp.exists(filepath):
            return False, f"Error: File '{filepath}' does not exist"
        if not tramp.is_file(filepath):
            return False, f"Error: '{filepath}' is not a file"
    else:
        if not os.path.exists(filepath):
            return False, f"Error: File '{filepath}' does not exist"
        if not os.path.isfile(filepath):
            return False, f"Error: '{filepath}' is not a file"
    return True, ""


def _dir_exists(path: str, tramp: Any) -> tuple[bool, str]:
    if tramp and tramp.is_tramp_path(path):
        if not tramp.exists(path):
            return False, f"Error: Path '{path}' does not exist"
        if not tramp.is_dir(path):
            return False, f"Error: '{path}' is not a directory"
    else:
        if not os.path.exists(path):
            return False, f"Error: Path '{path}' does not exist"
        if not os.path.isdir(path):
            return False, f"Error: '{path}' is not a directory"
    return True, ""


def _size_ok(
    filepath: str,
    tramp: Any,
    max_size: int = 1024 * 1024,
) -> tuple[bool, str]:
    if tramp and tramp.is_tramp_path(filepath):
        sz = tramp.get_size(filepath)
    else:
        sz = os.path.getsize(filepath)
    if sz > max_size:
        return (
            False,
            f"Error: File '{filepath}' is too large ({sz} bytes). Maximum size is 1MB.",
        )
    return True, ""


def _read(filepath: str, tramp: Any) -> str:
    if tramp and tramp.is_tramp_path(filepath):
        return str(tramp.read_file(filepath))
    with open(filepath, encoding="utf-8") as f:
        return f.read()


def _hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


def _lang(filepath: str) -> str:
    _, ext = os.path.splitext(filepath)
    return LANGUAGE_MAP.get(ext.lower(), "")


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------


def get_time(arguments: dict[str, Any]) -> dict[str, Any]:
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return _ok(f"Current time: {ts}")


def list_files(arguments: dict[str, Any]) -> dict[str, Any]:
    tramp, tramp_error = _get_tramp()
    path = _workspace_relative(arguments.get("path") or _default_tool_directory())
    try:
        if tramp and tramp.is_tramp_path(path):
            if not tramp:
                return _err(f"Error: {tramp_error}")
            ok, msg = _dir_exists(path, tramp)
            if not ok:
                return _err(msg)
            items = tramp.list_dir(path)
            if not items:
                return _ok(f"Directory '{path}' is empty")
            lines = []
            for name, is_dir, size in items:
                lines.append(f"📁 {name}/" if is_dir else f"📄 {name} ({size} bytes)")
            return _ok(f"Files in '{path}':\n" + "\n".join(lines))
        else:
            ok, msg = _dir_exists(path, None)
            if not ok:
                return _err(msg)
            lines = []
            for item in os.listdir(path):
                item_path = os.path.join(path, item)
                if os.path.isfile(item_path):
                    lines.append(f"📄 {item} ({os.path.getsize(item_path)} bytes)")
                elif os.path.isdir(item_path):
                    lines.append(f"📁 {item}/")
            if not lines:
                return _ok(f"Directory '{path}' is empty")
            return _ok(f"Files in '{path}':\n" + "\n".join(lines))
    except Exception as e:
        return _err(f"Error listing files: {e}")


def read_file(arguments: dict[str, Any]) -> dict[str, Any]:
    tramp, tramp_error = _get_tramp()
    try:
        filepath = _get_filepath(arguments)
    except ValueError as e:
        return _err(str(e))
    try:
        active = tramp if (tramp and tramp.is_tramp_path(filepath)) else None
        if active and not tramp:
            return _err(f"Error: {tramp_error}")
        ok, msg = _file_exists(filepath, active)
        if not ok:
            return _err(msg)
        ok, msg = _size_ok(filepath, active)
        if not ok:
            return _err(msg)
        content = _read(filepath, active)
        lang = _lang(filepath)
        fence = f"```{lang}" if lang else "```"
        return _ok(
            f"Content of '{filepath}' (hash: {_hash(content)}):\n\n{fence}\n{content}\n```",
        )
    except (UnicodeDecodeError, PermissionError, FileNotFoundError) as e:
        return _err(f"Error reading file: {e}")
    except Exception as e:
        return _err(f"Error reading file: {e}")


def read_file_range(arguments: dict[str, Any]) -> dict[str, Any]:
    tramp, tramp_error = _get_tramp()
    try:
        filepath = _get_filepath(arguments)
    except ValueError as e:
        return _err(str(e))

    start_line = arguments.get("start_line", 1)
    if not isinstance(start_line, int) or start_line < 1:
        return _err("Error: start_line must be an integer >= 1")

    end_line = arguments.get("end_line")
    if end_line is None:
        end_line = start_line + READ_FILE_RANGE_MAX_LINES - 1
    elif not isinstance(end_line, int) or end_line < start_line:
        return _err("Error: end_line must be an integer >= start_line")
    if end_line - start_line + 1 > READ_FILE_RANGE_MAX_LINES:
        end_line = start_line + READ_FILE_RANGE_MAX_LINES - 1

    try:
        is_tramp = bool(tramp and tramp.is_tramp_path(filepath))
        if is_tramp and not tramp:
            return _err(f"Error: {tramp_error}")
        active = tramp if is_tramp else None

        ok, msg = _file_exists(filepath, active)
        if not ok:
            return _err(msg)
        ok, msg = _size_ok(filepath, active, max_size=200 * 1024 * 1024)
        if not ok:
            return _err(msg)

        content = _read(filepath, active)
        lines = content.splitlines()
        total = len(lines)
        if start_line > total:
            return _err(
                f"Error: start_line {start_line} exceeds total line count {total} for '{filepath}'",
            )

        selected = lines[start_line - 1 : end_line]
        lang = _lang(filepath)
        fence = f"```{lang}" if lang else "```"
        header = f"Lines {start_line}-{min(end_line, total)} of '{filepath}' ({total} lines total):\n\n"
        return _ok(f"{header}{fence}\n" + "\n".join(selected) + "\n```")
    except (UnicodeDecodeError, PermissionError, FileNotFoundError) as e:
        return _err(f"Error reading file: {e}")
    except Exception as e:
        return _err(f"Error reading file: {e}")


# Long-edge cap for what gets sent to the model — matches Anthropic's own
# documented vision guidance; sending more pixels than this doesn't
# improve what the model can make out and only inflates the payload.
# Tried largest-first, smallest-last, paired with a quality step for
# formats where that applies (JPEG only — PNG is lossless, so only the
# dimension shrinks help there). Multiple steps rather than one fixed
# attempt so the "still too big" refusal below is genuinely rare — an
# image that fails even at the smallest/lowest-quality step is a real
# limit, not this tool giving up early.
VIEW_IMAGE_DIMENSION_STEPS = (1568, 1280, 1024, 768, 512)
VIEW_IMAGE_JPEG_QUALITY_STEPS = (85, 70, 50)
# Hard ceiling on the final encoded payload, checked only after every
# downscale/quality combination above has been tried. Not a soft preview
# like gate_tool_output's text truncation — there's no such thing as a
# useful partial image, so this is a refuse-outright backstop, not a trim.
VIEW_IMAGE_MAX_ENCODED_BYTES = 5 * 1024 * 1024


def _encode_image_under_limit(
    img: Any,
    max_bytes: int,
) -> tuple[bytes, str, tuple[int, int]] | None:
    """Try progressively smaller dimensions (and, for JPEG, quality) until
    the encoded result fits under max_bytes. Returns (encoded_bytes,
    mime_type, final_pixel_size), or None if nothing tried fit.
    """
    import io

    from PIL import Image

    has_alpha = img.mode in ("RGBA", "LA") or (
        img.mode == "P" and "transparency" in img.info
    )
    if not has_alpha and img.mode != "RGB":
        img = img.convert("RGB")

    for max_dim in VIEW_IMAGE_DIMENSION_STEPS:
        # thumbnail() only ever shrinks, never enlarges, so this is safe
        # (a harmless no-op) even when the source is already smaller than
        # max_dim — no need to special-case that; every step is always
        # worth trying, including on an already-small source that still
        # doesn't fit under budget at a higher quality/step.
        candidate = img.copy()
        candidate.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)

        buf = io.BytesIO()
        if has_alpha:
            candidate.save(buf, format="PNG", optimize=True)
            encoded = buf.getvalue()
            if len(encoded) <= max_bytes:
                return encoded, "image/png", candidate.size
        else:
            for quality in VIEW_IMAGE_JPEG_QUALITY_STEPS:
                buf = io.BytesIO()
                candidate.save(buf, format="JPEG", quality=quality)
                encoded = buf.getvalue()
                if len(encoded) <= max_bytes:
                    return encoded, "image/jpeg", candidate.size
    return None


def view_image(arguments: dict[str, Any]) -> dict[str, Any]:
    """Load an image file and return it for the model to actually see.

    Unlike read_file, this doesn't just report bytes on disk exist — it
    decodes the image, downscales it if needed, and returns it in a form
    core/tool_output_gate.py and anthropic_ollama_server.py both know to
    turn into a real image content block instead of raw marker text (see
    image_tool_result.py for why that's a text-string sentinel rather than
    a structural change to how tool results are stored).

    Remote (TRAMP) paths aren't supported — tramp_handler.read_file only
    returns text, and adding binary transfer to it is out of scope here.
    """
    try:
        filepath = _get_filepath(arguments)
    except ValueError as e:
        return _err(str(e))
    tramp, _tramp_error = _get_tramp()
    if tramp and tramp.is_tramp_path(filepath):
        return _err(
            "Error: view_image does not support remote (TRAMP) paths — "
            "only local files.",
        )
    ok, msg = _file_exists(filepath, None)
    if not ok:
        return _err(msg)

    try:
        from PIL import Image

        with open(filepath, "rb") as f:
            raw_bytes = f.read()

        try:
            import io

            img = Image.open(io.BytesIO(raw_bytes))
            img.load()
        except Exception as e:
            return _err(f"Error: '{filepath}' could not be read as an image: {e}")

        orig_format = img.format or "?"
        orig_size = img.size

        fitted = _encode_image_under_limit(img, VIEW_IMAGE_MAX_ENCODED_BYTES)
        if fitted is None:
            return _err(
                f"Error: '{filepath}' cannot be shown — even at the smallest "
                "size and quality this tool tries, it's still over the "
                f"{VIEW_IMAGE_MAX_ENCODED_BYTES} byte limit for what can be "
                "sent to the model. This image is not supported; there is no "
                "way to view part of an image — unlike text files, an image "
                "can't be read in segments or ranges, so a partial view "
                "wouldn't be meaningful and retrying this same file will not "
                "help. Tell the user the image couldn't be shown rather than "
                "retrying.",
            )
        encoded_bytes, mime_type, final_size = fitted

        b64_data = base64.b64encode(encoded_bytes).decode("ascii")
        resized_note = (
            f", downscaled to {final_size[0]}x{final_size[1]}"
            if final_size != orig_size
            else ""
        )
        description = (
            f"Loaded '{filepath}' ({orig_size[0]}x{orig_size[1]} {orig_format}"
            f"{resized_note}, {len(encoded_bytes)} bytes as {mime_type})."
        )
        return _ok(
            image_tool_result.encode_image_result(mime_type, b64_data, description),
        )
    except Exception as e:
        return _err(f"Error loading image: {e}")


def view_video(arguments: dict[str, Any]) -> dict[str, Any]:
    """Expose a generated video to the UI without putting its bytes in context.

    The returned marker contains metadata and an encoded local path only. Pack
    tabs fetch the actual file from their remote daemon in bounded chunks when
    the user opens it.
    """
    try:
        filepath = _get_filepath(arguments)
    except ValueError as e:
        return _err(str(e))
    tramp, _tramp_error = _get_tramp()
    if tramp and tramp.is_tramp_path(filepath):
        return _err(
            "Error: view_video does not support remote (TRAMP) paths; use a "
            "path local to the active tab's host.",
        )
    try:
        mime_type, size = video_tool_result.inspect_video(filepath)
        description = f"Loaded video '{filepath}' ({size:,} bytes as {mime_type})."
        return _ok(
            video_tool_result.encode_video_result(
                mime_type,
                filepath,
                size,
                description,
            ),
        )
    except ValueError as e:
        return _err(f"Error: {e}")
    except Exception as e:
        return _err(f"Error loading video: {e}")


def write_file(arguments: dict[str, Any]) -> dict[str, Any]:
    tramp, tramp_error = _get_tramp()
    try:
        filepath = _get_filepath(arguments)
    except ValueError as e:
        return _err(str(e))
    content = arguments.get("content", "")
    try:
        active = tramp if (tramp and tramp.is_tramp_path(filepath)) else None
        if active and not tramp:
            return _err(f"Error: {tramp_error}")
        content_hash = _hash(content)
        content_bytes = len(content.encode("utf-8"))
        if active:
            active.write_file(filepath, content)
            try:
                actual = active.get_size(filepath)
                return _ok(
                    f"Successfully wrote to '{filepath}'\n"
                    f"Size: {actual} bytes (expected: {content_bytes} bytes)\n"
                    f"Content hash: {content_hash}",
                )
            except Exception:
                return _ok(
                    f"Successfully wrote to '{filepath}'\nContent hash: {content_hash}",
                )
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
                return _err(
                    f"Error: File write reported success but file doesn't exist at '{abs_path}'",
                )
            actual = os.path.getsize(abs_path)
            return _ok(
                f"Successfully wrote to '{abs_path}'\n"
                f"Size: {actual} bytes (expected: {content_bytes} bytes)\n"
                f"Content hash: {content_hash}",
            )
    except (PermissionError, FileNotFoundError) as e:
        return _err(f"Error writing file: {e}")
    except Exception as e:
        import traceback

        return _err(
            f"Error writing file: {e}\nAttempted path: {filepath}\nTraceback:\n{traceback.format_exc()}",
        )


def modify_file(arguments: dict[str, Any]) -> dict[str, Any]:
    tramp, tramp_error = _get_tramp()
    try:
        filepath = _get_filepath(arguments)
    except ValueError as e:
        return _err(str(e))

    old_string = arguments.get("old_string", "")
    new_string = arguments.get("new_string", "")
    replace_all = arguments.get("replace_all", False)

    if not old_string:
        return _err("Error: 'old_string' is required")
    if new_string is None:
        return _err("Error: 'new_string' is required")
    if old_string == new_string:
        return _err("Error: 'old_string' and 'new_string' must be different")

    try:
        active = tramp if (tramp and tramp.is_tramp_path(filepath)) else None
        if active and not tramp:
            return _err(f"Error: {tramp_error}")
        ok, msg = _file_exists(filepath, active)
        if not ok:
            return _err(msg)
        ok, msg = _size_ok(filepath, active)
        if not ok:
            return _err(msg)
        content = _read(filepath, active)

        if old_string not in content:
            return _err(f"Error: 'old_string' not found in file '{filepath}'")

        count = content.count(old_string)
        if replace_all:
            modified = content.replace(old_string, new_string)
        else:
            if count > 1:
                return _err(
                    f"Error: 'old_string' appears {count} times in file. "
                    "Use 'replace_all: true' to replace all occurrences or provide "
                    "a larger string with more context to make it unique.",
                )
            modified = content.replace(old_string, new_string, 1)

        if active:
            active.write_file(filepath, modified)
            verified = active.read_file(filepath)
            verified_hash = _hash(verified)
        else:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(modified)
                f.flush()
                os.fsync(f.fileno())
            verified_hash = _hash(modified)

        return _ok(
            f"Successfully modified '{filepath}'\n"
            f"Replaced {count} occurrence(s)\n"
            f"Content hash: {verified_hash}",
        )
    except (PermissionError, FileNotFoundError) as e:
        return _err(f"Error modifying file: {e}")
    except Exception as e:
        return _err(f"Error modifying file: {e}")


def search_files_for_text(arguments: dict[str, Any]) -> dict[str, Any]:
    directory = _workspace_relative(
        arguments.get("directory") or _default_tool_directory(),
    )
    pattern = arguments.get("search_pattern", "")
    file_pattern = arguments.get("file_pattern")
    case_insensitive = arguments.get("case_insensitive", False)
    whole_word = arguments.get("whole_word", False)
    context_lines = arguments.get("context_lines", 0)
    max_count = arguments.get("max_count")

    if not pattern:
        return _err("Error: 'search_pattern' is required")

    try:
        ok, msg = _dir_exists(directory, None)
        if not ok:
            return _err(msg)

        wrapper = RipgrepWrapper()
        results = wrapper.search(
            directory=directory,
            search_pattern=pattern,
            file_pattern=file_pattern,
            case_insensitive=case_insensitive,
            whole_word=whole_word,
            max_count=max_count,
            context_lines=context_lines,
        )

        if not results:
            return _ok(f"No matches found for pattern '{pattern}' in '{directory}'")

        lines = [f"Search Results for pattern '{pattern}' in '{directory}':", "=" * 70]
        files_dict: dict[str, list[dict[str, Any]]] = {}
        for r in results:
            if r.get("type") == "match":
                data = r.get("data", {})
                fp = data.get("path", {}).get("text", "unknown")
                files_dict.setdefault(fp, []).append(data)

        match_count = 0
        for fp in sorted(files_dict):
            matches = files_dict[fp]
            lines.append(f"\n📄 {fp} ({len(matches)} matches)")
            lines.append("-" * 70)
            for m in matches:
                line_num = m.get("line_number", "?")
                text = m.get("lines", {}).get("text", "")
                if text:
                    if len(text) > 100:
                        text = text[:97] + "..."
                    lines.append(f"  Line {line_num}: {text}")
                match_count += 1

        lines += ["", "=" * 70, f"Total matches: {match_count}"]
        if file_pattern:
            lines.append(f"File pattern filter: {file_pattern}")
        if case_insensitive:
            lines.append("Search mode: Case-insensitive")
        if whole_word:
            lines.append("Search mode: Whole word match only")
        if context_lines > 0:
            lines.append(f"Context lines: {context_lines}")

        return _ok("\n".join(lines))
    except ValueError as e:
        return _err(f"Error: {e}")
    except RipgrepError as e:
        return _err(f"Ripgrep error: {e}")
    except Exception as e:
        return _err(f"Error performing search: {e}")


def run_shell_command(arguments: dict[str, Any]) -> dict[str, Any]:
    command = arguments.get("command", "")
    if not command:
        return _err("Error: 'command' is required")
    executor = ShellExecutor()
    result = executor.run(
        command=command,
        working_directory=arguments.get("working_directory"),
        timeout=arguments.get("timeout", 60),
    )
    return _ok(result.format_output())


# ---------------------------------------------------------------------------
# Dispatch table
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Conversation tools (search / read conversation history)
# ---------------------------------------------------------------------------


def _conv_open_db(db_path: str = CONVERSATIONS_DB) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("PRAGMA query_only = ON")
    conn.row_factory = sqlite3.Row
    return conn


def _conv_parse_id(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    s = str(value).strip()
    for prefix in ("alpaca://conv/", "raven://conversation/", "raven://"):
        if s.startswith(prefix):
            s = s[len(prefix) :]
            break
    try:
        return int(s)
    except (ValueError, TypeError):
        return None


def _conv_tool_name(raw: str) -> str | None:
    try:
        data = json.loads(raw)
        if "tool_call" in data:
            name = data["tool_call"].get("name")
            return str(name) if isinstance(name, str) else None
    except (json.JSONDecodeError, AttributeError, TypeError):
        pass
    return None


def _conv_extract_text_and_tools(
    answer_data: dict[str, Any],
    include_tool_calls: bool = False,
) -> tuple[str, list[str]]:
    components = answer_data.get("components", [])
    text_parts: list[str] = []
    tools_used: list[str] = []
    seen: set[str] = set()
    for comp in components:
        if isinstance(comp, str):
            text_parts.append(comp)
            continue
        if not isinstance(comp, dict):
            continue
        ctype = comp.get("type")
        if ctype == "text":
            text_parts.append(comp.get("content", ""))
        elif ctype == "tool_call":
            name = _conv_tool_name(comp.get("content", ""))
            if name and name not in seen:
                tools_used.append(name)
                seen.add(name)
            if include_tool_calls:
                text_parts.append(f"\n[Tool call: {comp.get('content', '')}]\n")
        elif ctype == "tool_result" and include_tool_calls:
            text_parts.append(f"\n[Tool result: {comp.get('content', '')}]\n")
    return "\n".join(text_parts).strip(), tools_used


_ConvTurn = tuple[str, str, list[str]]  # (question, answer_text, tools_used)


def _conv_turns_from_legacy(
    cs: dict[str, Any],
    include_tool_calls: bool,
) -> list[_ConvTurn]:
    questions = cs.get("questions", [])
    answers = cs.get("answers", [])
    turns: list[_ConvTurn] = []
    for i, question in enumerate(questions):
        if i < len(answers):
            ans = answers[i]
            text, tools = (
                _conv_extract_text_and_tools(ans, include_tool_calls)
                if isinstance(ans, dict)
                else (str(ans), [])
            )
        else:
            text, tools = "", []
        turns.append((question, text, tools))
    return turns


def _conv_turns_from_graph(
    graph: dict[str, Any],
    include_tool_calls: bool,
) -> list[_ConvTurn]:
    nodes: dict[str, dict] = graph.get("nodes", {})
    active_id: str | None = graph.get("active_node_id")
    if not nodes or not active_id:
        return []
    path: list[dict] = []
    cur: str | None = active_id
    while cur:
        node = nodes.get(cur)
        if not node:
            break
        path.append(node)
        cur = node.get("parent_id")
    path.reverse()
    turns: list[_ConvTurn] = []
    i = 0
    while i < len(path):
        node = path[i]
        if node.get("role") == "user":
            question = _conv_extract_text_and_tools(node.get("content", {}))[0]
            if i + 1 < len(path) and path[i + 1].get("role") == "assistant":
                answer_text, tools = _conv_extract_text_and_tools(
                    path[i + 1].get("content", {}),
                    include_tool_calls,
                )
                i += 2
            else:
                answer_text, tools = "", []
                i += 1
            turns.append((question, answer_text, tools))
        else:
            i += 1
    return turns


def _conv_extract_turns(
    chat_data: dict[str, Any],
    include_tool_calls: bool = False,
) -> list[_ConvTurn]:
    cs = chat_data.get("chat_state", chat_data)
    if "graph" in cs:
        return _conv_turns_from_graph(cs["graph"], include_tool_calls)
    return _conv_turns_from_legacy(cs, include_tool_calls)


def _conv_all_text(chat_data: dict[str, Any]) -> str:
    turns = _conv_extract_turns(chat_data, include_tool_calls=False)
    return " ".join(q + " " + a for q, a, _ in turns)


def _conv_format_transcript(
    conv_id: int,
    title: str,
    created_date: str,
    turns: list[_ConvTurn],
) -> str:
    lines = [f'[Conversation #{conv_id} — "{title}" — {created_date[:10]}]\n']
    for i, (q, a, tools) in enumerate(turns):
        lines.append(f"Q{i + 1}: {q}\n")
        lines.append(f"A{i + 1}: {a or '(no text response)'}")
        if tools:
            lines.append(f"    Tools used: {', '.join(tools)}")
        lines.append("")
    return "\n".join(lines)


_ConvRawTurn = tuple[str, list[dict]]  # (question, raw_components)


def _conv_raw_turns_from_legacy(cs: dict[str, Any]) -> list[_ConvRawTurn]:
    questions = cs.get("questions", [])
    answers = cs.get("answers", [])
    result: list[_ConvRawTurn] = []
    for i, q in enumerate(questions):
        comps = (
            answers[i].get("components", [])
            if i < len(answers) and isinstance(answers[i], dict)
            else []
        )
        result.append((q, comps))
    return result


def _conv_raw_turns_from_graph(graph: dict[str, Any]) -> list[_ConvRawTurn]:
    nodes: dict[str, dict] = graph.get("nodes", {})
    active_id: str | None = graph.get("active_node_id")
    if not nodes or not active_id:
        return []
    path: list[dict] = []
    cur: str | None = active_id
    while cur:
        node = nodes.get(cur)
        if not node:
            break
        path.append(node)
        cur = node.get("parent_id")
    path.reverse()
    result: list[_ConvRawTurn] = []
    i = 0
    while i < len(path):
        node = path[i]
        if node.get("role") == "user":
            q = _conv_extract_text_and_tools(node.get("content", {}))[0]
            if i + 1 < len(path) and path[i + 1].get("role") == "assistant":
                comps = path[i + 1].get("content", {}).get("components", [])
                i += 2
            else:
                comps = []
                i += 1
            result.append((q, comps))
        else:
            i += 1
    return result


def _conv_raw_turns(chat_data: dict[str, Any]) -> list[_ConvRawTurn]:
    cs = chat_data.get("chat_state", chat_data)
    if "graph" in cs:
        return _conv_raw_turns_from_graph(cs["graph"])
    return _conv_raw_turns_from_legacy(cs)


def search_conversations(arguments: dict[str, Any]) -> dict[str, Any]:
    query = str(arguments.get("query", "")).strip()
    if not query:
        return _err("'query' argument is required")
    search_content = bool(arguments.get("search_content", False))
    limit = min(int(arguments.get("limit", 20)), 100)
    db_path = str(arguments.get("_db_path", CONVERSATIONS_DB))

    try:
        conn = _conv_open_db(db_path)
        try:
            rows = conn.execute(
                "SELECT id, title, created_date, chat_data FROM conversations"
                " ORDER BY closed_date DESC",
            ).fetchall()
        finally:
            conn.close()
    except Exception as e:
        return _err(f"Error opening database: {e}")

    q_lower = query.lower()
    results: list[dict[str, Any]] = []
    for row in rows:
        if len(results) >= limit:
            break
        title_match = q_lower in row["title"].lower()
        content_match = False
        if search_content and not title_match:
            try:
                content_match = (
                    q_lower in _conv_all_text(json.loads(row["chat_data"])).lower()
                )
            except Exception:
                pass
        if not (title_match or content_match):
            continue
        match_type = (
            "Both"
            if (title_match and content_match)
            else "Title" if title_match else "Content"
        )
        results.append(
            {
                "id": row["id"],
                "title": row["title"],
                "created_date": row["created_date"][:10],
                "match_type": match_type,
            },
        )

    if not results:
        return _ok(f"No conversations found matching '{query}'.")

    lines = [f"Found {len(results)} conversation(s) matching '{query}':\n"]
    for r in results:
        lines.append(
            f"- [{r['title']}](alpaca://conv/{r['id']})"
            f" (#{r['id']}, {r['created_date']}, match: {r['match_type']})",
        )
    return _ok("\n".join(lines))


def get_conversation(arguments: dict[str, Any]) -> dict[str, Any]:
    raw_id = arguments.get("conversation_id")
    conv_id = _conv_parse_id(raw_id)
    if conv_id is None:
        return _err(f"Invalid conversation_id '{raw_id}'")
    include_tool_calls = bool(arguments.get("include_tool_calls", False))
    db_path = str(arguments.get("_db_path", CONVERSATIONS_DB))

    try:
        conn = _conv_open_db(db_path)
        try:
            row = conn.execute(
                "SELECT chat_data, title, created_date FROM conversations WHERE id = ?",
                (conv_id,),
            ).fetchone()
        finally:
            conn.close()
    except Exception as e:
        return _err(f"Error opening database: {e}")

    if row is None:
        return _err(f"Conversation #{conv_id} not found")

    chat_data = json.loads(row["chat_data"])
    turns = _conv_extract_turns(chat_data, include_tool_calls)
    return _ok(
        _conv_format_transcript(conv_id, row["title"], row["created_date"], turns),
    )


def get_tool_details(arguments: dict[str, Any]) -> dict[str, Any]:
    raw_id = arguments.get("conversation_id")
    conv_id = _conv_parse_id(raw_id)
    if conv_id is None:
        return _err(f"Invalid conversation_id '{raw_id}'")

    turn_index = arguments.get("turn_index")
    if turn_index is None:
        return _err("'turn_index' argument is required")
    try:
        turn_index = int(turn_index)
    except (TypeError, ValueError):
        return _err("'turn_index' must be an integer")

    db_path = str(arguments.get("_db_path", CONVERSATIONS_DB))

    try:
        conn = _conv_open_db(db_path)
        try:
            row = conn.execute(
                "SELECT chat_data, title FROM conversations WHERE id = ?",
                (conv_id,),
            ).fetchone()
        finally:
            conn.close()
    except Exception as e:
        return _err(f"Error opening database: {e}")

    if row is None:
        return _err(f"Conversation #{conv_id} not found")

    all_raw = _conv_raw_turns(json.loads(row["chat_data"]))
    if turn_index < 0 or turn_index >= len(all_raw):
        return _err(
            f"turn_index {turn_index} out of range"
            f" (conversation has {len(all_raw)} turn(s))",
        )

    question, components = all_raw[turn_index]
    lines = [
        f'[Conversation #{conv_id} — "{row["title"]}" — Turn {turn_index}]\n',
        f"Q: {question}\n",
        "Tool interactions:",
    ]
    has_tools = False
    for comp in components:
        if not isinstance(comp, dict):
            continue
        ctype = comp.get("type")
        if ctype == "tool_call":
            has_tools = True
            name = _conv_tool_name(comp.get("content", "")) or "(unknown)"
            tid = comp.get("id", "")
            lines.append(f"\n  [CALL id={tid}] {name}")
            try:
                data = json.loads(comp.get("content", "{}"))
                args = data.get("tool_call", {}).get("arguments", {})
                lines.append(f"  Arguments: {json.dumps(args, indent=4)}")
            except Exception:
                lines.append(f"  Raw: {comp.get('content', '')[:500]}")
        elif ctype == "tool_result":
            has_tools = True
            content = comp.get("content", "")
            truncated = content[:1000] + ("..." if len(content) > 1000 else "")
            lines.append(f"\n  [RESULT id={comp.get('id', '')}]")
            lines.append(f"  {truncated}")
    if not has_tools:
        lines.append("  (no tool calls in this turn)")
    return _ok("\n".join(lines))


def dump_conversations(arguments: dict[str, Any]) -> dict[str, Any]:
    output_path = str(arguments.get("output_path", "conversations_export.json"))
    include_content = bool(arguments.get("include_content", True))
    include_tool_names = bool(arguments.get("include_tool_names", False))
    db_path = str(arguments.get("_db_path", CONVERSATIONS_DB))

    try:
        conn = _conv_open_db(db_path)
        try:
            rows = conn.execute(
                "SELECT id, title, created_date, closed_date, chat_data"
                " FROM conversations ORDER BY closed_date DESC",
            ).fetchall()
        finally:
            conn.close()
    except Exception as e:
        return _err(f"Error opening database: {e}")

    export: list[dict[str, Any]] = []
    for row in rows:
        entry: dict[str, Any] = {
            "id": row["id"],
            "title": row["title"],
            "created_date": row["created_date"],
            "closed_date": row["closed_date"],
        }
        if include_content or include_tool_names:
            try:
                turns = _conv_extract_turns(json.loads(row["chat_data"]))
                if include_content:
                    entry["turns"] = [
                        {
                            "turn": i,
                            "question": q,
                            "answer": a,
                            **(
                                {"tools_used": tools}
                                if include_tool_names and tools
                                else {}
                            ),
                        }
                        for i, (q, a, tools) in enumerate(turns)
                    ]
                elif include_tool_names:
                    entry["tools"] = [
                        {"turn": i, "tool": tool}
                        for i, (_, _, tools) in enumerate(turns)
                        for tool in tools
                    ]
            except Exception:
                pass
        export.append(entry)

    try:
        json_str = json.dumps(export, indent=2, ensure_ascii=False)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(json_str)
    except Exception as e:
        return _err(f"Error writing file: {e}")

    size = os.path.getsize(output_path)
    return _ok(
        f"Exported {len(export)} conversation(s) to {output_path}\nFile size: {size:,} bytes",
    )


_HANDLERS: dict[str, Any] = {
    "get_time": get_time,
    "list_files": list_files,
    "read_file": read_file,
    "read_file_range": read_file_range,
    "view_image": view_image,
    "view_video": view_video,
    "write_file": write_file,
    "modify_file": modify_file,
    "search_files_for_text": search_files_for_text,
    "run_shell_command": run_shell_command,
    "search_conversations": search_conversations,
    "get_conversation": get_conversation,
    "get_tool_details": get_tool_details,
    "dump_conversations": dump_conversations,
}


def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    handler = _HANDLERS.get(tool_name)
    if handler is None:
        return _err(f"Unknown internal tool: {tool_name}")
    try:
        return dict(handler(arguments))
    except Exception as e:
        return _err(f"Internal tool error ({tool_name}): {e}")


# ---------------------------------------------------------------------------
# Tool schemas (Ollama-compatible format) for get_available_mcp_tools()
# ---------------------------------------------------------------------------

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "internal_get_time",
            "description": "Get the current date and time",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "internal_list_files",
            "description": "List files in a directory. Supports both local paths and TRAMP filenames (e.g., /ssh:user@host:/path)",
            "parameters": {
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
        },
    },
    {
        "type": "function",
        "function": {
            "name": "internal_read_file",
            "description": "Read contents of a text file. Supports both local paths and TRAMP filenames (e.g., /ssh:user@host:/path/file)",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "File path to read (local or TRAMP format)",
                    },
                },
                "required": ["file_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "internal_read_file_range",
            "description": (
                f"Read a bounded line range from a text file of any size "
                "(internal_read_file is capped at 1MB total). Use this to page through "
                "a large file, such as one referenced by an 'Output truncated' notice. "
                f"Returns at most {READ_FILE_RANGE_MAX_LINES} lines per call."
            ),
            "parameters": {
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
                            f"{READ_FILE_RANGE_MAX_LINES - 1}; clamped to that span)"
                        ),
                    },
                },
                "required": ["file_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "internal_view_image",
            "description": (
                "Load an image file (e.g. a screenshot you just took) so you can "
                "actually see it, not just confirm it exists on disk. Downscales "
                "large images automatically. Local paths only, no TRAMP/remote "
                "support."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Local path to the image file",
                    },
                },
                "required": ["file_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "internal_view_video",
            "description": (
                "Show a generated MP4, WebM, or Ogg video file to the user. Use "
                "this after recording a feature demonstration. The video stays "
                "out of model context and is transferred to the UI only for "
                "playback. Local paths only."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Local path to the generated video file",
                    },
                },
                "required": ["file_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "internal_write_file",
            "description": "Write text content to a file. Supports both local paths and TRAMP filenames (e.g., /ssh:user@host:/path/file)",
            "parameters": {
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
                "required": ["file_path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "internal_modify_file",
            "description": "Performs exact string replacements in files. Supports both local paths and TRAMP filenames.",
            "parameters": {
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
        },
    },
    {
        "type": "function",
        "function": {
            "name": "internal_search_files_for_text",
            "description": "Search for text patterns in files within a directory using ripgrep. Supports regex patterns and multiple search options.",
            "parameters": {
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
        },
    },
    {
        "type": "function",
        "function": {
            "name": "internal_run_shell_command",
            "description": "Execute a command. No shell interpretation — argv passed directly to the process, so pipes, redirects, &&/;, and backticks are literal characters, not operators, and 'grep foo | wc -l' will not work as written. For pipes, redirects, or chaining multiple commands, wrap the whole sequence in bash -c, e.g. command='bash -c \"grep foo file | wc -l\"' — bash itself does the shell parsing, so this works normally.",
            "parameters": {
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
        },
    },
    {
        "type": "function",
        "function": {
            "name": "internal_search_conversations",
            "description": "Search past conversations by keyword. Searches titles by default; set search_content=true to also scan question/answer text. Returns clickable alpaca://conv/N links.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Keyword or phrase to search for",
                    },
                    "search_content": {
                        "type": "boolean",
                        "description": "Also scan question/answer text (default false)",
                        "default": False,
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results to return (default 20, max 100)",
                        "default": 20,
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "internal_get_conversation",
            "description": "Get a readable Q&A transcript of a past conversation. Accepts a bare integer ID or an alpaca://conv/N URI.",
            "parameters": {
                "type": "object",
                "properties": {
                    "conversation_id": {
                        "type": ["integer", "string"],
                        "description": "Conversation ID or alpaca://conv/N URI",
                    },
                    "include_tool_calls": {
                        "type": "boolean",
                        "description": "Include raw tool call/result blocks in the transcript (default false)",
                        "default": False,
                    },
                },
                "required": ["conversation_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "internal_get_tool_details",
            "description": "Show full tool call arguments and results for one turn of a conversation. Use after internal_get_conversation to drill into what a specific tool did.",
            "parameters": {
                "type": "object",
                "properties": {
                    "conversation_id": {
                        "type": ["integer", "string"],
                        "description": "Conversation ID or alpaca://conv/N URI",
                    },
                    "turn_index": {
                        "type": "integer",
                        "description": "Zero-based index of the Q/A turn to inspect",
                    },
                },
                "required": ["conversation_id", "turn_index"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "internal_dump_conversations",
            "description": "Export the full conversation history to a JSON file on disk. Tool results (file contents, command output) are excluded from the export.",
            "parameters": {
                "type": "object",
                "properties": {
                    "output_path": {
                        "type": "string",
                        "description": "File path for the JSON export (default: conversations_export.json)",
                        "default": "conversations_export.json",
                    },
                    "include_content": {
                        "type": "boolean",
                        "description": "Include question/answer text per turn (default true)",
                        "default": True,
                    },
                    "include_tool_names": {
                        "type": "boolean",
                        "description": "Include names of tools used per turn (default false)",
                        "default": False,
                    },
                },
            },
        },
    },
]
