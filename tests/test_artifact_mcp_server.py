from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

import artifact_mcp_server
from core.artifact_protocol import parse_artifact_result


@pytest.mark.asyncio
async def test_publish_returns_descriptor_and_records_cli_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "demo.html").write_text("<canvas></canvas>")
    event_path = tmp_path / "events.jsonl"
    monkeypatch.setenv("ALPACA_WORKSPACE", str(workspace))
    monkeypatch.setenv("ALPACA_CLI_MEDIA_EVENTS", str(event_path))
    client = Mock()
    manifest = {
        "version": 1,
        "artifact_id": "art_12345678",
        "kind": "html",
        "title": "Demo",
        "revision": 1,
        "renderer": "client_html",
        "capabilities": {"backend": False, "network": False, "user_input": True},
    }
    client.call.return_value = {"manifest": manifest}
    monkeypatch.setattr(artifact_mcp_server, "_client", lambda: client)

    result = await artifact_mcp_server.call_tool(
        "artifact_publish_html",
        {"path": "demo.html", "title": "Demo"},
    )

    assert parse_artifact_result(result[0].text) == manifest
    assert client.call.call_args.args[1]["path"] == str(workspace / "demo.html")
    event = json.loads(event_path.read_text())
    assert parse_artifact_result(event["result"]) == manifest
