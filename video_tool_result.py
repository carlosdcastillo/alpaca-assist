"""Small, context-safe references to video files produced by tools.

Unlike images, videos are never embedded in conversation state.  The tool
result stores only a MIME type, an opaque encoded local path, byte size, and
description.  The web UI asks Python for bounded chunks when it needs to play
the video; Pack tabs proxy those chunk requests to their remote daemon.
"""
from __future__ import annotations

import base64
import os
from typing import Any

SENTINEL = "@@ALPACA_VIDEO_RESULT@@"
FIELD_SEP = "@@ALPACA_FIELD@@"
MAX_VIDEO_BYTES = 100 * 1024 * 1024
CHUNK_BYTES = 768 * 1024


def _encode_locator(filepath: str) -> str:
    return base64.urlsafe_b64encode(filepath.encode("utf-8")).decode("ascii")


def _decode_locator(locator: str) -> str:
    return base64.urlsafe_b64decode(locator.encode("ascii")).decode("utf-8")


def encode_video_result(
    mime_type: str,
    filepath: str,
    size: int,
    description: str,
) -> str:
    """Encode metadata only. Video bytes deliberately stay on disk."""
    locator = _encode_locator(os.path.abspath(filepath))
    return FIELD_SEP.join((f"{SENTINEL}{mime_type}", locator, str(size), description))


def parse_video_result(content: str) -> tuple[str, str, int, str] | None:
    """Return ``(mime_type, locator, size, description)`` when recognized."""
    idx = content.find(SENTINEL)
    if idx == -1:
        return None
    parts = content[idx + len(SENTINEL) :].split(FIELD_SEP, 3)
    if len(parts) != 4:
        return None
    mime_type, locator, raw_size, description = parts
    try:
        size = int(raw_size)
    except ValueError:
        return None
    quote_idx = description.find('"')
    if quote_idx != -1:
        description = description[:quote_idx].removesuffix("\\")
    if not mime_type.startswith("video/") or size < 0:
        return None
    return mime_type, locator, size, description


def detect_video_mime(header: bytes) -> str | None:
    """Recognize browser-playable containers from magic bytes."""
    if len(header) >= 12 and header[4:8] == b"ftyp":
        return "video/mp4"
    if header[:4] == b"\x1aE\xdf\xa3":
        return "video/webm"
    if header[:4] == b"OggS":
        return "video/ogg"
    return None


def inspect_video(filepath: str) -> tuple[str, int]:
    """Validate a video path and return its MIME type and size."""
    if not os.path.isfile(filepath):
        raise ValueError(f"File '{filepath}' does not exist or is not a file")
    size = os.path.getsize(filepath)
    if size > MAX_VIDEO_BYTES:
        raise ValueError(
            f"Video is too large ({size} bytes); maximum is {MAX_VIDEO_BYTES} bytes",
        )
    with open(filepath, "rb") as file:
        mime_type = detect_video_mime(file.read(16))
    if mime_type is None:
        raise ValueError("Unsupported video format; use MP4, WebM, or Ogg")
    return mime_type, size


def read_video_chunk(locator: str, offset: int) -> dict[str, Any]:
    """Read one bounded base64 chunk from a previously encoded locator."""
    if not isinstance(offset, int) or offset < 0:
        raise ValueError("Video offset must be a non-negative integer")
    try:
        filepath = _decode_locator(locator)
    except Exception as exc:
        raise ValueError("Invalid video locator") from exc
    mime_type, size = inspect_video(filepath)
    if offset > size:
        raise ValueError("Video offset is beyond the end of the file")
    with open(filepath, "rb") as file:
        file.seek(offset)
        data = file.read(CHUNK_BYTES)
    next_offset = offset + len(data)
    return {
        "mime_type": mime_type,
        "size": size,
        "data": base64.b64encode(data).decode("ascii"),
        "next_offset": next_offset,
        "done": next_offset >= size,
    }
