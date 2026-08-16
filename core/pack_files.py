"""Safe, bounded access to files that live on a Pack worker."""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
import stat
from collections import OrderedDict
from pathlib import Path
from typing import Any

MAX_PACK_FILE_BYTES = 100 * 1024 * 1024
PACK_FILE_CHUNK_BYTES = 768 * 1024
MAX_ACTIVE_LOCATORS = 256


def _fingerprint(file_stat: os.stat_result) -> tuple[int, int, int, int]:
    return (
        file_stat.st_dev,
        file_stat.st_ino,
        file_stat.st_size,
        file_stat.st_mtime_ns,
    )


class PackFileStore:
    """Resolve paths into opaque, process-local locators and serve their bytes.

    Locators deliberately do not encode a path. They are issued only after an
    explicit link click and remain valid only while this Pack daemon is alive.
    Each read verifies that the file is still the regular file that was
    resolved, preventing a download from silently combining two revisions.
    """

    def __init__(self) -> None:
        self._files: OrderedDict[
            str,
            tuple[Path, tuple[int, int, int, int]],
        ] = OrderedDict()

    def resolve(
        self,
        reference: str,
        workspace_path: str | None = None,
    ) -> dict[str, Any]:
        if not isinstance(reference, str) or not reference or "\x00" in reference:
            raise ValueError("Invalid Pack file path")

        path = Path(reference).expanduser()
        if not path.is_absolute():
            path = (
                Path(workspace_path).expanduser() / path
                if workspace_path
                else Path.cwd() / path
            )
        try:
            path = path.resolve(strict=True)
            file_stat = path.stat()
        except OSError as exc:
            raise ValueError(f"Pack file not found: {reference}") from exc
        if not stat.S_ISREG(file_stat.st_mode):
            raise ValueError("Pack file reference is not a regular file")
        if file_stat.st_size > MAX_PACK_FILE_BYTES:
            raise ValueError(
                f"Pack file is too large ({file_stat.st_size:,} bytes); "
                f"maximum is {MAX_PACK_FILE_BYTES:,} bytes",
            )

        fingerprint = _fingerprint(file_stat)
        locator = secrets.token_urlsafe(24)
        self._files[locator] = (path, fingerprint)
        if len(self._files) > MAX_ACTIVE_LOCATORS:
            self._files.popitem(last=False)
        identity = hashlib.sha256(
            (str(path) + "\0" + ":".join(str(value) for value in fingerprint)).encode(
                "utf-8",
            ),
        ).hexdigest()
        return {
            "locator": locator,
            "name": path.name,
            "size": file_stat.st_size,
            "identity": identity,
        }

    def read_chunk(self, locator: str, offset: int) -> dict[str, Any]:
        if not isinstance(locator, str) or locator not in self._files:
            raise ValueError("Unknown or expired Pack file locator")
        if not isinstance(offset, int) or offset < 0:
            raise ValueError("Pack file offset must be a non-negative integer")

        path, expected = self._files[locator]
        self._files.move_to_end(locator)
        if offset > expected[2]:
            raise ValueError("Pack file offset is beyond the end of the file")
        try:
            with path.open("rb") as file:
                if _fingerprint(os.fstat(file.fileno())) != expected:
                    raise ValueError("Pack file changed before it could be opened")
                file.seek(offset)
                data = file.read(PACK_FILE_CHUNK_BYTES)
                if _fingerprint(os.fstat(file.fileno())) != expected:
                    raise ValueError("Pack file changed while it was being downloaded")
        except OSError as exc:
            raise ValueError("Pack file is no longer available") from exc

        next_offset = offset + len(data)
        return {
            "size": expected[2],
            "data": base64.b64encode(data).decode("ascii"),
            "next_offset": next_offset,
            "done": next_offset >= expected[2],
        }
