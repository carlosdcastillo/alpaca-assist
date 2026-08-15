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


# fourccs browsers actually decode inside an MP4 container. Notably absent:
# mp4v (MPEG-4 Part 2) — the ffmpeg/OpenCV default for a bare ".mp4" fourcc,
# and the container magic-byte check alone can't tell it apart from H.264.
_WEB_SAFE_MP4_VIDEO_CODECS = {"avc1", "avc3", "hev1", "hvc1", "av01", "vp09"}


def _find_child_box(data: bytes, name: str) -> bytes | None:
    """Return the content (header stripped) of the first top-level `name` box in `data`."""
    pos = 0
    while pos + 8 <= len(data):
        box_size = int.from_bytes(data[pos : pos + 4], "big")
        box_type = data[pos + 4 : pos + 8].decode("ascii", errors="replace")
        if box_size == 0:
            box_size = len(data) - pos
        if box_size < 8:
            break
        if box_type == name:
            return data[pos + 8 : pos + box_size]
        pos += box_size
    return None


def _mp4_video_codec(filepath: str) -> str | None:
    """Best-effort fourcc of the first video track's sample entry.

    Returns None on any parse miss (including non-video tracks or files this
    can't fully walk) rather than raising — a codec check that fails open is
    safer than one that blocks a file it merely couldn't parse.
    """
    try:
        with open(filepath, "rb") as file:
            file.seek(0, os.SEEK_END)
            file_size = file.tell()
            pos = 0
            moov_data = None
            while pos + 8 <= file_size:
                file.seek(pos)
                header = file.read(8)
                box_size = int.from_bytes(header[:4], "big")
                box_type = header[4:8].decode("ascii", errors="replace")
                if box_size == 0:
                    box_size = file_size - pos
                if box_size < 8:
                    break
                if box_type == "moov":
                    file.seek(pos + 8)
                    moov_data = file.read(box_size - 8)
                    break
                pos += box_size
        if moov_data is None:
            return None
        cursor = moov_data
        for child_name in ("trak", "mdia", "minf", "stbl"):
            child = _find_child_box(cursor, child_name)
            if child is None:
                return None
            cursor = child
        stsd = _find_child_box(cursor, "stsd")
        if stsd is None or len(stsd) < 16:
            return None
        # stsd body: 4-byte version/flags, 4-byte entry count, then entries
        # of (4-byte size, 4-byte fourcc, ...).
        return stsd[12:16].decode("ascii", errors="replace")
    except OSError:
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
    if mime_type == "video/mp4":
        codec = _mp4_video_codec(filepath)
        if codec is not None and codec not in _WEB_SAFE_MP4_VIDEO_CODECS:
            raise ValueError(
                f"'{filepath}' is encoded with the '{codec}' video codec, which "
                "browsers can't play. Re-encode as H.264, e.g. "
                "`ffmpeg -i <input> -c:v libx264 -pix_fmt yuv420p <output>.mp4`, "
                "and try again.",
            )
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
