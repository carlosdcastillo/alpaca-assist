from __future__ import annotations

import json
from pathlib import Path

import pytest
from mcp.types import ImageContent
from PIL import Image

import cli_media_mcp_server


@pytest.mark.asyncio
async def test_view_image_returns_native_content_and_records_mirror_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_path = tmp_path / "image.png"
    Image.new("RGB", (2, 2), "red").save(image_path)
    event_path = tmp_path / "events.jsonl"
    monkeypatch.setenv("ALPACA_CLI_MEDIA_EVENTS", str(event_path))

    result = await cli_media_mcp_server.call_tool(
        "view_image",
        {"file_path": str(image_path)},
    )

    assert isinstance(result[0], ImageContent)
    event = json.loads(event_path.read_text())
    assert event["name"] == "alpaca_media_view_image"
    assert event["id"] in result[1].text
    assert "@@ALPACA_IMAGE_RESULT@@" in event["result"]


@pytest.mark.asyncio
async def test_view_video_records_chunked_player_reference(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    video_path = tmp_path / "demo.mp4"
    video_path.write_bytes(b"\x00\x00\x00\x18ftypisom\x00\x00\x00\x00")
    event_path = tmp_path / "events.jsonl"
    monkeypatch.setenv("ALPACA_CLI_MEDIA_EVENTS", str(event_path))

    result = await cli_media_mcp_server.call_tool(
        "view_video",
        {"file_path": str(video_path)},
    )

    event = json.loads(event_path.read_text())
    assert event["name"] == "alpaca_media_view_video"
    assert event["id"] in result[0].text
    assert "@@ALPACA_VIDEO_RESULT@@" in event["result"]
