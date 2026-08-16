"""Wire protocol for Pack tabs: newline-delimited JSON-RPC-2.0-shaped framing.

Bespoke, not MCP's tool-call semantics — a Pack tab needs one request
(send_message) to trigger many asynchronous notifications (streaming
content, tool folds), which is a poor fit for MCP's single-request/
single-result schema.

Shared unmodified between the local side (core/pack_transport.py) and the
remote side (pack_daemon.py) — both import this exact module, so the two
ends can never drift out of sync on wire format.

Message shapes:
    Request:      {"id": <int>, "method": <str>, "params": {...}}
    Response:     {"id": <int>, "result": {...}}
    Error response: {"id": <int>, "error": {"message": <str>}}
    Notification: {"method": <str>, "params": {...}}   (no "id")
"""

from __future__ import annotations

import json
from typing import Any


class ProtocolError(ValueError):
    """Raised when a line cannot be decoded as a Pack protocol message."""


def encode_request(
    request_id: int,
    method: str,
    params: dict[str, Any] | None = None,
) -> str:
    """Encode an outgoing request as one newline-terminated JSON line."""
    return (
        json.dumps({"id": request_id, "method": method, "params": params or {}}) + "\n"
    )


def encode_response(request_id: int, result: Any = None) -> str:
    """Encode a successful response to `request_id`."""
    return json.dumps({"id": request_id, "result": result}) + "\n"


def encode_error_response(request_id: int, message: str) -> str:
    """Encode a failed response to `request_id`."""
    return json.dumps({"id": request_id, "error": {"message": message}}) + "\n"


def encode_notification(method: str, params: dict[str, Any] | None = None) -> str:
    """Encode a one-way, unacknowledged push (no `id`, no response expected)."""
    return json.dumps({"method": method, "params": params or {}}) + "\n"


def decode_line(line: str) -> dict[str, Any]:
    """Parse one line of input into a message dict.

    Raises ProtocolError on empty input, invalid JSON, or a JSON value that
    isn't an object — callers should treat this as a protocol violation
    (log and drop the line / close the connection), not a crash.
    """
    stripped = line.strip()
    if not stripped:
        raise ProtocolError("empty line")
    try:
        msg = json.loads(stripped)
    except json.JSONDecodeError as e:
        raise ProtocolError(f"invalid JSON: {e}") from e
    if not isinstance(msg, dict):
        raise ProtocolError(f"expected a JSON object, got {type(msg).__name__}")
    return msg


def is_request(msg: dict[str, Any]) -> bool:
    """True if `msg` is an incoming request expecting a response."""
    return "id" in msg and "method" in msg


def is_response(msg: dict[str, Any]) -> bool:
    """True if `msg` is a response (success or error) to a prior request."""
    return "id" in msg and "method" not in msg and ("result" in msg or "error" in msg)


def is_notification(msg: dict[str, Any]) -> bool:
    """True if `msg` is a one-way push with no matching request id."""
    return "id" not in msg and "method" in msg
