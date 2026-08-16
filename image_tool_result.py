"""Shared encode/decode for tool results that carry an image, not just text.

internal_tools.py's view_image tool embeds an image (mime type + base64
data) inside its otherwise-plain-string result via a delimited sentinel,
so it travels through the existing storage/replay pipeline — chat_state,
the byte-budget clearing in core/chat_tab_tools.py, everything — exactly
like any other tool result string, with no changes needed anywhere in
that path. Only two places need to know the sentinel exists:
gate_tool_output (must never truncate it — a truncated base64 payload is
corrupt, not a useful preview, unlike truncated text) and
_build_anthropic_messages (must expand it into a real image content block
instead of sending the raw marker text to the model as-is).

The delimiters are plain printable ASCII, not NUL bytes. A NUL byte looks
like the obviously-safe choice (guaranteed not to appear in normal text),
and an earlier version of this module used one — but every tool result
passes through json.dumps() before it's stored (core/chat_tab_tools.py's
_execute_tool), and JSON string encoding escapes \\x00 into the six
literal characters \\u0000. A raw-NUL search then never matches anything,
so no image was ever actually recognized as an image after that round
trip — every view_image call silently became a wall of base64 text
instead, for every image, at any size, 100% of the time (confirmed live:
the model receiving it correctly reported seeing raw base64 text with
"ALPACA_IMAGE_RESULT" and "image/jpeg" leaking through as literal visible
substrings, rather than an actual image). Printable ASCII outside `"` and
`\\` — which is what these markers use — passes through json.dumps()
unchanged, so this survives the same round trip that broke the NUL-byte
version.
"""

from __future__ import annotations

SENTINEL = "@@ALPACA_IMAGE_RESULT@@"
_FIELD_SEP = "@@ALPACA_FIELD@@"


def encode_image_result(mime_type: str, base64_data: str, description: str) -> str:
    """Build a tool-result string embedding an image via the sentinel format."""
    return f"{SENTINEL}{mime_type}{_FIELD_SEP}{base64_data}{_FIELD_SEP}{description}"


def parse_image_result(content: str) -> tuple[str, str, str] | None:
    """Return (mime_type, base64_data, description) if content embeds an
    image result, else None.

    Searches anywhere in the string, not just the start, since callers
    (e.g. ToolHandler.prepare_continuation_messages) prepend their own
    text — "Tool execution result:\\n" — before the stored content, and
    the whole thing also normally arrives wrapped in the
    {"content": [{"type": "text", "text": ...}], "isError": ...} JSON
    envelope internal_tools.call_tool() results get stored as.
    """
    idx = content.find(SENTINEL)
    if idx == -1:
        return None
    payload = content[idx + len(SENTINEL) :]
    parts = payload.split(_FIELD_SEP, 2)
    if len(parts) != 3:
        return None
    mime_type, base64_data, description = parts
    # description is the last field, so when the whole thing arrives
    # wrapped in the storage JSON envelope (the common case — see
    # docstring), there's no delimiter marking where its content ends and
    # the envelope's own closing `"...}` resumes; it all reads as part of
    # this field. mime_type/base64_data are unaffected (they're not last),
    # and view_image's own generated descriptions never contain a literal
    # `"` (file paths are quoted with `'`), so a `"` here is reliably that
    # envelope leaking in, not real content — safe to cut there.
    quote_idx = description.find('"')
    if quote_idx != -1:
        description = description[:quote_idx]
    return mime_type, base64_data, description
