"""Tests for surface_mcp_server.py's CLI-backed side channel.

A CLI-backed model's tool_use/tool_result blocks never reach Alpaca's own
chat_state (suppressed to avoid double execution -- see
anthropic_ollama_server.py), so without _record_event a surface tool's
result would only ever exist inside the CLI's own internal context. This
was confirmed live: the model, unable to parse the raw sentinel it got
back, invented its own broken markdown image instead of a real
live-surface card. These tests exercise the recording side of that fix
the same way tests/test_cli_media_mcp_server.py exercises its sibling.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import surface_mcp_server
from core.surface_protocol import parse_surface_result


@pytest.mark.asyncio
async def test_surface_list_is_not_offered_to_the_model() -> None:
    """Dropped once surface_open could take argv directly: the tool
    description no longer needs to send the model to check the profile
    catalog first, and an unknown profile name already comes back with the
    available list from surface_open's own error, so nothing else needed
    a standalone lookup call.
    """
    tools = await surface_mcp_server.list_tools()

    assert "surface_list" not in {tool.name for tool in tools}


@pytest.mark.asyncio
async def test_surface_open_prefers_profile_over_argv_when_both_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ALPACA_CLI_MEDIA_EVENTS", raising=False)
    captured: dict[str, Any] = {}

    def _fake_call(method: str, params: dict[str, Any]) -> dict[str, Any]:
        captured.update(params)
        return {
            "surface_id": "srf_12345678",
            "width": 800,
            "height": 600,
            "description": "editor",
            "seq": 0,
        }

    monkeypatch.setattr(surface_mcp_server, "_call", _fake_call)

    await surface_mcp_server.call_tool(
        "surface_open",
        {"profile": "editor", "argv": ["rm", "-rf", "/"]},
    )

    assert captured["spec"] == {"profile": "editor"}


@pytest.mark.asyncio
async def test_surface_open_forwards_argv_when_no_profile_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Profiles are a curated shortcut, not a security boundary -- the
    model already has unrestricted shell execution via
    internal_run_shell_command, so surface_open accepts a raw argv the
    same way the human-driven panel path always could.
    """
    monkeypatch.delenv("ALPACA_CLI_MEDIA_EVENTS", raising=False)
    captured: dict[str, Any] = {}

    def _fake_call(method: str, params: dict[str, Any]) -> dict[str, Any]:
        captured.update(params)
        return {
            "surface_id": "srf_12345678",
            "width": 800,
            "height": 600,
            "description": "bash",
            "seq": 0,
        }

    monkeypatch.setattr(surface_mcp_server, "_call", _fake_call)

    await surface_mcp_server.call_tool(
        "surface_open",
        {"argv": ["bash", "-c", "echo hi"]},
    )

    assert captured["spec"] == {"argv": ["bash", "-c", "echo hi"]}
    assert captured["source"] == "model"


@pytest.mark.asyncio
async def test_surface_open_records_a_parseable_mirror_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event_path = tmp_path / "events.jsonl"
    monkeypatch.setenv("ALPACA_CLI_MEDIA_EVENTS", str(event_path))
    monkeypatch.setattr(
        surface_mcp_server,
        "_call",
        lambda method, params: {
            "surface_id": "srf_12345678",
            "width": 800,
            "height": 600,
            "description": "xeyes",
            "seq": 0,
        },
    )

    result = await surface_mcp_server.call_tool(
        "surface_open",
        {"profile": "xeyes"},
    )

    assert "srf_12345678" in result[0].text

    event = json.loads(event_path.read_text())
    assert event["name"] == "alpaca_surface_surface_open"
    assert event["arguments"] == {"profile": "xeyes"}

    # The recorded "result" must be the {"content": [...]} envelope shape a
    # real CallToolResult.model_dump() produces (see mcp_manager.py's
    # call_tool) -- parse_surface_result's trailing-quote trimming exists
    # specifically for this envelope, and chat_tab_processor.py's
    # cli_tool_event handler stores this string verbatim as the tool
    # result content, so anything else silently fails to render.
    recorded = json.loads(event["result"])
    assert recorded["content"][0]["text"] == result[0].text

    # Match chat_tab_processor: it stores the serialized result envelope
    # verbatim, so escaped newlines after the descriptor would leak into the
    # card title as a visible ``\n``.
    parsed = parse_surface_result(event["result"])
    assert parsed is not None
    surface_id, width, height, description = parsed
    assert surface_id == "srf_12345678"
    assert (width, height) == (800, 600)
    assert description == "xeyes"


@pytest.mark.asyncio
async def test_surface_snapshot_records_both_content_items(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event_path = tmp_path / "events.jsonl"
    monkeypatch.setenv("ALPACA_CLI_MEDIA_EVENTS", str(event_path))
    monkeypatch.setattr(
        surface_mcp_server,
        "_call",
        lambda method, params: {
            "surface_id": "srf_12345678",
            "width": 800,
            "height": 600,
            "data": "AAAA",
            "mime_type": "image/png",
            "seq": 3,
        },
    )

    await surface_mcp_server.call_tool(
        "surface_snapshot",
        {"surface_id": "srf_12345678"},
    )

    event = json.loads(event_path.read_text())
    recorded = json.loads(event["result"])
    kinds = [item["type"] for item in recorded["content"]]
    assert kinds == ["image", "text"]


@pytest.mark.asyncio
async def test_no_event_recorded_without_the_env_var(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No ALPACA_CLI_MEDIA_EVENTS means a non-CLI model or a plain local
    tab -- the ordinary MCP tool-call path already produces a real fold
    there, so fabricating a second one here would double it up.
    """
    monkeypatch.delenv("ALPACA_CLI_MEDIA_EVENTS", raising=False)
    monkeypatch.setattr(
        surface_mcp_server,
        "_call",
        lambda method, params: {
            "surface_id": "srf_12345678",
            "width": 800,
            "height": 600,
            "description": "xeyes",
            "seq": 0,
        },
    )

    result = await surface_mcp_server.call_tool("surface_open", {"profile": "xeyes"})

    assert "srf_12345678" in result[0].text
    assert not (tmp_path / "events.jsonl").exists()


@pytest.mark.asyncio
async def test_unknown_tool_still_records_an_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The recording wrapper is generic (name/content, not a per-branch
    special case), so it must not silently skip a branch that returns
    plain text -- a future tool added to the dispatch table gets the same
    CLI-visibility guarantee for free.
    """
    event_path = tmp_path / "events.jsonl"
    monkeypatch.setenv("ALPACA_CLI_MEDIA_EVENTS", str(event_path))

    await surface_mcp_server.call_tool("surface_nope", {})

    event = json.loads(event_path.read_text())
    assert event["name"] == "alpaca_surface_surface_nope"
