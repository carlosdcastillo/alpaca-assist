"""Tests for safe worker-local file resolution and chunking."""
from pathlib import Path

import pytest

from core.pack_files import PackFileStore


def test_resolves_workspace_relative_file_and_uses_opaque_locator(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = workspace / "report.txt"
    source.write_text("remote contents", encoding="utf-8")
    store = PackFileStore()

    result = store.resolve("report.txt", str(workspace))

    assert result["name"] == "report.txt"
    assert result["size"] == len(b"remote contents")
    assert str(source) not in result["locator"]
    assert str(source) not in result["identity"]


def test_reads_file_in_bounded_chunks(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "data.bin"
    source.write_bytes(b"abcdef")
    monkeypatch.setattr("core.pack_files.PACK_FILE_CHUNK_BYTES", 4)
    store = PackFileStore()
    metadata = store.resolve(str(source))

    first = store.read_chunk(metadata["locator"], 0)
    second = store.read_chunk(metadata["locator"], first["next_offset"])

    assert first == {"size": 6, "data": "YWJjZA==", "next_offset": 4, "done": False}
    assert second == {"size": 6, "data": "ZWY=", "next_offset": 6, "done": True}


def test_rejects_non_regular_and_oversized_files(tmp_path: Path, monkeypatch) -> None:
    store = PackFileStore()
    with pytest.raises(ValueError, match="regular file"):
        store.resolve(str(tmp_path))

    source = tmp_path / "large.bin"
    source.write_bytes(b"1234")
    monkeypatch.setattr("core.pack_files.MAX_PACK_FILE_BYTES", 3)
    with pytest.raises(ValueError, match="too large"):
        store.resolve(str(source))


def test_rejects_file_that_changes_after_resolution(tmp_path: Path) -> None:
    source = tmp_path / "data.txt"
    source.write_text("first", encoding="utf-8")
    store = PackFileStore()
    metadata = store.resolve(str(source))
    source.write_text("a different revision", encoding="utf-8")

    with pytest.raises(ValueError, match="changed"):
        store.read_chunk(metadata["locator"], 0)


def test_rejects_unknown_locator_and_invalid_offset(tmp_path: Path) -> None:
    source = tmp_path / "data.txt"
    source.write_text("content", encoding="utf-8")
    store = PackFileStore()
    metadata = store.resolve(str(source))

    with pytest.raises(ValueError, match="Unknown or expired"):
        store.read_chunk("invented", 0)
    with pytest.raises(ValueError, match="non-negative"):
        store.read_chunk(metadata["locator"], -1)
