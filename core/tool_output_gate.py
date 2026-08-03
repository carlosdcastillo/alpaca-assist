"""Gate large tool outputs out of the model context window.

Every tool result gets replayed verbatim on every subsequent continuation
request (see ToolHandler.prepare_continuation_messages), so a single large
result is far more expensive than its one-time size suggests. Results over
GATE_THRESHOLD_BYTES are written to a per-tab temp file and replaced with a
short preview plus a pointer to that file. The model can pull a slice of the
file back via the read_file_range MCP tool if it needs more than the preview.

Tool *calls* get the same treatment via gate_tool_call_arguments, at a lower
threshold (CALL_ARG_GATE_THRESHOLD_BYTES): unlike results, a tool_use_call
block is never stubbed by the byte-budget clearing in
ToolHandler.prepare_continuation_messages regardless of age, so an
oversized argument — e.g. a write_file call with a huge `content`, or a
shell command with something large inlined into it — would otherwise be
resent in full on every subsequent call for the rest of the conversation
with no relief at all. The tool has already executed with the real, full
arguments by the time this runs; only the stored/replayed copy is capped.

Temp files are deleted when their owning tab closes (cleanup_tab_output_dir)
and orphaned directories from a previous run that didn't exit cleanly are
swept on app startup (sweep_orphaned_output_dirs).
"""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

import image_tool_result

GATE_THRESHOLD_BYTES = 32 * 1024
CALL_ARG_GATE_THRESHOLD_BYTES = 16 * 1024
PREVIEW_MAX_LINES = 100
PREVIEW_MAX_BYTES = 4 * 1024

TOOL_OUTPUT_TEMP_ROOT = Path(tempfile.gettempdir()) / "alpaca_assist_tool_outputs"


def _sanitize_for_filename(value: str) -> str:
    """Strip characters that aren't safe in a filename (ids may be model-controlled)."""
    cleaned = "".join(c if c.isalnum() or c in "-_." else "_" for c in value)
    return cleaned or "tool"


def gate_tool_output(
    text: str,
    tab_id: str,
    tool_id: str,
    tool_name: str,
    threshold: int | None = None,
) -> str:
    """Return ``text`` unchanged if small, otherwise gate it.

    When ``text`` exceeds ``threshold`` (module-level GATE_THRESHOLD_BYTES
    if not given — looked up at call time, not bound as a default, so
    tests/callers can monkeypatch the module constant), it is written in
    full to a per-tab temp file and a truncated preview with a pointer to
    that file is returned instead.

    Never truncates a view_image result (see image_tool_result.py):
    unlike text, a truncated base64 payload is corrupt, not a useful
    preview, so there's nothing sensible to gate down to. view_image
    already bounds its own output size by downscaling before it gets
    here — this is a deliberate bypass of the size check, not a gap in it.
    """
    if image_tool_result.parse_image_result(text) is not None:
        return text
    if threshold is None:
        threshold = GATE_THRESHOLD_BYTES
    encoded = text.encode("utf-8")
    if len(encoded) <= threshold:
        return text

    lines = text.splitlines()
    preview_lines: list[str] = []
    preview_bytes = 0
    for line in lines[:PREVIEW_MAX_LINES]:
        separator_cost = 1 if preview_lines else 0  # joining "\n" before this line
        remaining = PREVIEW_MAX_BYTES - preview_bytes - separator_cost
        if remaining <= 0:
            break
        line_bytes = line.encode("utf-8")
        if len(line_bytes) > remaining:
            # A single line alone exceeds the budget (e.g. minified/single-line
            # content) — cut it short instead of dumping it whole.
            truncated = line_bytes[:remaining].decode("utf-8", errors="ignore")
            preview_lines.append(truncated)
            preview_bytes += separator_cost + len(truncated.encode("utf-8"))
            break
        preview_lines.append(line)
        preview_bytes += separator_cost + len(line_bytes)
    preview = "\n".join(preview_lines)

    tab_dir = TOOL_OUTPUT_TEMP_ROOT / _sanitize_for_filename(tab_id)
    tab_dir.mkdir(parents=True, exist_ok=True)
    file_name = (
        f"{_sanitize_for_filename(tool_id)}_{_sanitize_for_filename(tool_name)}.txt"
    )
    file_path = tab_dir / file_name
    file_path.write_text(text, encoding="utf-8")

    notice = (
        f"[Output truncated: {len(lines)} lines / {len(encoded)} bytes total "
        f"— showing first {len(preview_lines)} lines ({preview_bytes / 1024:.1f} KB).\n"
        f"Full output saved to: {file_path}\n"
        "Use the read_file_range tool to inspect another slice of it.\n"
        "This file is temporary and will be deleted when this tab is closed.]"
    )
    return f"{notice}\n\n{preview}"


def gate_tool_call_arguments(
    tool_json: str,
    tab_id: str,
    tool_id: str,
    tool_name: str,
) -> str:
    """Gate oversized string arguments out of a tool call before it's stored.

    Parses ``tool_json`` (the same {"tool_call": {...}} / {"name", ...}
    shapes ToolHandler._parse_for_message reads back later), gates any
    string argument value over CALL_ARG_GATE_THRESHOLD_BYTES via the same
    preview+temp-file mechanism as gate_tool_output, and re-serializes.
    Returns the input unchanged if it doesn't parse, has no arguments dict,
    or nothing in it is actually oversized.
    """
    try:
        parsed: Any = json.loads(tool_json)
    except (json.JSONDecodeError, TypeError):
        return tool_json

    container = parsed.get("tool_call") if isinstance(parsed, dict) else None
    if not isinstance(container, dict):
        container = parsed if isinstance(parsed, dict) else None
    if container is None:
        return tool_json

    arguments = container.get("arguments")
    if not isinstance(arguments, dict):
        return tool_json

    changed = False
    for key, value in list(arguments.items()):
        if not isinstance(value, str):
            continue
        gated = gate_tool_output(
            value,
            tab_id,
            f"{tool_id}_{key}",
            f"{tool_name}.{key}",
            threshold=CALL_ARG_GATE_THRESHOLD_BYTES,
        )
        if gated != value:
            arguments[key] = gated
            changed = True

    return json.dumps(parsed) if changed else tool_json


def cleanup_tab_output_dir(tab_id: str) -> None:
    """Delete this tab's gated-output temp directory, if any."""
    tab_dir = TOOL_OUTPUT_TEMP_ROOT / _sanitize_for_filename(tab_id)
    shutil.rmtree(tab_dir, ignore_errors=True)


def sweep_orphaned_output_dirs() -> None:
    """Wipe the entire gated-output temp root.

    Called once at app startup. Anything left here belongs to a previous run
    that didn't exit cleanly (crash / force-quit) — tabs always clean up
    their own directory on normal close via cleanup_tab_output_dir.
    """
    shutil.rmtree(TOOL_OUTPUT_TEMP_ROOT, ignore_errors=True)
