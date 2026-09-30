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


@pytest.mark.asyncio
async def test_publish_writes_the_store_directly_without_a_socket(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A local tab can't use ArtifactControlServer at all — socket.AF_UNIX
    doesn't exist on Windows — but nothing crossing that socket is remote,
    so the MCP subprocess just writes the store itself.
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "demo.html").write_text("<canvas></canvas>")
    root = tmp_path / "root"
    monkeypatch.setenv("ALPACA_WORKSPACE", str(workspace))
    monkeypatch.setenv("ALPACA_ARTIFACT_ROOT", str(root))
    monkeypatch.delenv("ALPACA_ARTIFACT_SOCKET", raising=False)
    monkeypatch.delenv("ALPACA_CLI_MEDIA_EVENTS", raising=False)

    result = await artifact_mcp_server.call_tool(
        "artifact_publish_html",
        {"path": "demo.html", "title": "Demo"},
    )

    manifest = parse_artifact_result(result[0].text)
    assert manifest is not None
    published = root / "artifacts" / manifest["artifact_id"] / "index.html"
    assert "<canvas></canvas>" in published.read_text(encoding="utf-8")


def test_an_explicit_socket_still_wins_over_a_local_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pack's ownership model must not be bypassed just because a root
    happens to be set in the same environment.
    """
    from core.artifact_control import ArtifactControlClient

    monkeypatch.setenv("ALPACA_ARTIFACT_SOCKET", str(tmp_path / "control.sock"))
    monkeypatch.setenv("ALPACA_ARTIFACT_ROOT", str(tmp_path / "root"))

    assert isinstance(artifact_mcp_server._client(), ArtifactControlClient)


def test_a_local_root_wins_over_socket_discovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ArtifactControlClient.discover()'s "exactly one session on this host"
    fallback would otherwise let a local tab latch onto an unrelated Pack
    session's socket.
    """
    monkeypatch.delenv("ALPACA_ARTIFACT_SOCKET", raising=False)
    monkeypatch.setenv("ALPACA_ARTIFACT_ROOT", str(tmp_path / "root"))
    monkeypatch.setattr(
        artifact_mcp_server.ArtifactControlClient,
        "discover",
        classmethod(lambda cls: Mock()),
    )

    assert isinstance(
        artifact_mcp_server._client(),
        artifact_mcp_server._LocalStoreBackend,
    )
