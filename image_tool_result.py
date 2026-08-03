"""Shared encode/decode for tool results that carry an image, not just text.

internal_tools.py's view_image tool embeds an image (mime type + base64
data) inside its otherwise-plain-string result via a NUL-byte-delimited
sentinel, so it travels through the existing storage/replay pipeline —
chat_state, the byte-budget clearing in core/chat_tab_tools.py, everything
— exactly like any other tool result string, with no changes needed
anywhere in that path. Only two places need to know the sentinel exists:
gate_tool_output (must never truncate it — a truncated base64 payload is
corrupt, not a useful preview, unlike truncated text) and
_build_anthropic_messages (must expand it into a real image content block
instead of sending the raw marker text to the model as-is).

A NUL byte is used as the delimiter specifically because it's the one
character that can't appear in a normal string tool result already
flowing through this codebase's text-based pipeline, so there's no
ambiguity with legitimate content that happens to contain something
marker-shaped.
"""
from __future__ import annotations

SENTINEL = "\x00ALPACA_IMAGE_RESULT\x00"


def encode_image_result(mime_type: str, base64_data: str, description: str) -> str:
    """Build a tool-result string embedding an image via the sentinel format."""
    return f"{SENTINEL}{mime_type}\x00{base64_data}\x00{description}"


def parse_image_result(content: str) -> tuple[str, str, str] | None:
    """Return (mime_type, base64_data, description) if content embeds an
    image result, else None.

    Searches anywhere in the string, not just the start, since callers
    (e.g. ToolHandler.prepare_continuation_messages) prepend their own
    text — "Tool execution result:\\n" — before the stored content.
    """
    idx = content.find(SENTINEL)
    if idx == -1:
        return None
    payload = content[idx + len(SENTINEL) :]
    parts = payload.split("\x00", 2)
    if len(parts) != 3:
        return None
    mime_type, base64_data, description = parts
    return mime_type, base64_data, description
