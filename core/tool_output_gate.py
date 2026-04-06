"""Gate large tool outputs out of the model context window.

Every tool result gets replayed verbatim on every subsequent continuation
request (see ToolHandler.prepare_continuation_messages), so a single large
result is far more expensive than its one-time size suggests. Results over
GATE_THRESHOLD_BYTES are written to a per-tab temp file and replaced with a
short preview plus a pointer to that file. The model can pull a slice of the
file back via the read_file_range MCP tool if it needs more than the preview.

Temp files are deleted when their owning tab closes (cleanup_tab_output_dir)
and orphaned directories from a previous run that didn't exit cleanly are
swept on app startup (sweep_orphaned_output_dirs).
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

GATE_THRESHOLD_BYTES = 32 * 1024
PREVIEW_MAX_LINES = 100
PREVIEW_MAX_BYTES = 4 * 1024

TOOL_OUTPUT_TEMP_ROOT = Path(tempfile.gettempdir()) / "alpaca_assist_tool_outputs"


def _sanitize_for_filename(value: str) -> str:
    """Strip characters that aren't safe in a filename (ids may be model-controlled)."""
    cleaned = "".join(c if c.isalnum() or c in "-_." else "_" for c in value)
    return cleaned or "tool"


def gate_tool_output(text: str, tab_id: str, tool_id: str, tool_name: str) -> str:
    """Return ``text`` unchanged if small, otherwise gate it.

    When ``text`` exceeds GATE_THRESHOLD_BYTES, it is written in full to a
    per-tab temp file and a truncated preview with a pointer to that file is
    returned instead.
    """
    encoded = text.encode("utf-8")
    if len(encoded) <= GATE_THRESHOLD_BYTES:
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
