from __future__ import annotations

import json
from pathlib import Path

import pytest
from mcp.types import ImageContent
from PIL import Image

import cli_media_mcp_server


@pytest.mark.asyncio
async def test_view_image_schema_accepts_pdf_page_without_changing_video() -> None:
    tools = {tool.name: tool for tool in await cli_media_mcp_server.list_tools()}

    assert tools["view_image"].inputSchema["required"] == ["file_path"]
    assert tools["view_image"].inputSchema["properties"]["page"]["minimum"] == 1
    assert "page" not in tools["view_video"].inputSchema["properties"]


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


def test_view_image_on_a_pdf_answers_over_real_stdio(tmp_path: Path) -> None:
    """Regression: over a real stdio pipe on Windows, the lazy numpy import
    behind pypdfium2 deadlocked against the server's pending stdin read, so
    view_image on any PDF never answered and `claude -p` waited forever.
    Calling call_tool in-process can't see that; only a spawned server can.
    It reproduces on the app's Python 3.11 venv (fails after the 30s wait
    without the eager import) but not on every interpreter: Python 3.13
    passed even without the fix.
    """
    import os
    import queue
    import subprocess
    import sys
    import threading

    import pypdfium2 as pdfium

    pdf_path = tmp_path / "page.pdf"
    document = pdfium.PdfDocument.new()
    document.new_page(200, 200)
    document.save(str(pdf_path))
    document.close()

    server_script = Path(cli_media_mcp_server.__file__).resolve()
    env = dict(os.environ, ALPACA_CLI_MEDIA_EVENTS=str(tmp_path / "events.jsonl"))
    proc = subprocess.Popen(
        [sys.executable, str(server_script)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        cwd=str(server_script.parent),
        env=env,
    )
    replies: queue.Queue[dict] = queue.Queue()

    def read_replies() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            replies.put(json.loads(line))

    threading.Thread(target=read_replies, daemon=True).start()

    def send(message: dict) -> None:
        assert proc.stdin is not None
        proc.stdin.write((json.dumps(message) + "\n").encode())
        proc.stdin.flush()

    def reply_to(request_id: int) -> dict:
        while True:
            reply = replies.get(timeout=30)
            if reply.get("id") == request_id:
                return reply

    try:
        send(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "0"},
                },
            },
        )
        reply_to(1)
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        send(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "view_image",
                    "arguments": {"file_path": str(pdf_path)},
                },
            },
        )
        try:
            result = reply_to(2)["result"]
        except queue.Empty:
            pytest.fail("view_image on a PDF never answered over stdio")
        assert result["content"][0]["type"] == "image"
    finally:
        proc.kill()
        proc.wait(timeout=10)
