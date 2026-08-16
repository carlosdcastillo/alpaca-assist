from __future__ import annotations

import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from anthropic_ollama_server import _build_claude_mcp_config_file
from anthropic_ollama_server import _codex_mcp_overrides
from anthropic_ollama_server import _run_cli_jsonl
from anthropic_ollama_server import ClaudeCodeCLIClient
from anthropic_ollama_server import CodexCLIClient
from anthropic_ollama_server import OllamaRequestHandler
from core.chat_tab_streaming import StreamingHandler


PROJECT_SYSTEM_BLOCKS = [
    {"type": "text", "text": "Base instructions."},
    {
        "type": "text",
        "text": "RUNBOOK: Always test.\nSPINUP: Install dependencies.",
    },
]


@pytest.mark.parametrize(
    ("client", "model", "executable"),
    [
        (ClaudeCodeCLIClient(), "claude-code/sonnet", "claude"),
        (CodexCLIClient(), "codex/gpt-5.6-sol", "codex"),
    ],
)
def test_cli_clients_receive_project_instructions_and_workspace(
    client: ClaudeCodeCLIClient | CodexCLIClient,
    model: str,
    executable: str,
) -> None:
    workspace = "/srv/workspaces/alpaca-session"
    with (
        patch(
            "anthropic_ollama_server._build_claude_mcp_config_file",
            return_value=None,
        ),
        patch(
            "anthropic_ollama_server._run_cli_jsonl",
            return_value=iter(()),
        ) as run_cli,
    ):
        list(
            client.stream_complete(
                messages=[{"role": "user", "content": "Fix the bug."}],
                model=model,
                system=PROJECT_SYSTEM_BLOCKS,
                working_directory=workspace,
            ),
        )

    command, prompt, actual_workspace = run_cli.call_args.args
    assert command[0] == executable
    assert prompt == "User: Fix the bug."
    assert actual_workspace == workspace
    if executable == "claude":
        system_text = command[command.index("--append-system-prompt") + 1]
    else:
        config_value = command[command.index("-c") + 1]
        key, encoded_system = config_value.split("=", 1)
        assert key == "developer_instructions"
        system_text = json.loads(encoded_system)
    assert "RUNBOOK: Always test." in system_text
    assert "SPINUP: Install dependencies." in system_text


@pytest.mark.parametrize(
    ("client", "model"),
    [
        (ClaudeCodeCLIClient(), "claude-code/sonnet"),
        (CodexCLIClient(), "codex/gpt-5.6-sol"),
    ],
)
def test_cli_clients_forward_heartbeats(
    client: ClaudeCodeCLIClient | CodexCLIClient,
    model: str,
) -> None:
    # A silent CLI tool call (e.g. rendering images/videos) can outlast
    # StreamingHandler.STREAM_TIMEOUT's 120s read timeout; _run_cli_jsonl's
    # heartbeats keep the socket alive, but only if both CLI client classes
    # actually forward them instead of swallowing them as an unknown event.
    with (
        patch(
            "anthropic_ollama_server._build_claude_mcp_config_file",
            return_value=None,
        ),
        patch(
            "anthropic_ollama_server._run_cli_jsonl",
            return_value=iter([{"type": "cli_heartbeat"}]),
        ),
    ):
        events = list(
            client.stream_complete(
                messages=[{"role": "user", "content": "hi"}],
                model=model,
            ),
        )

    assert {"type": "cli_heartbeat"} in events


def test_proxy_dispatches_the_project_workspace_to_the_selected_backend() -> None:
    backend = MagicMock()
    backend.stream_complete.return_value = iter(())
    handler = object.__new__(OllamaRequestHandler)
    handler._process_stream = MagicMock()  # type: ignore[method-assign]

    with patch("anthropic_ollama_server.get_client_for_model", return_value=backend):
        handler._handle_request_with_tools(
            messages=[{"role": "user", "content": "Fix the bug."}],
            tools=[],
            model="codex/gpt-5.6-sol",
            request_system="project instructions",
            working_directory="/srv/workspaces/alpaca-session",
        )

    assert (
        backend.stream_complete.call_args.kwargs["working_directory"]
        == "/srv/workspaces/alpaca-session"
    )


@pytest.mark.parametrize("workspace", [None, "/srv/workspaces/alpaca-session"])
def test_initial_request_sends_only_a_project_workspace(workspace: str | None) -> None:
    chat = SimpleNamespace(
        stop_streaming_flag=threading.Event(),
        preferences={"api_url": "http://example.test", "model": "codex/test"},
        conversation_id=42,
        workspace_path=workspace,
        _streaming=True,
        is_streaming=True,
        on_streaming_complete=MagicMock(),
        on_streaming_error=MagicMock(),
    )
    tool_handler = MagicMock()
    tool_handler.prepare_continuation_messages.return_value = [
        {"role": "user", "content": "Fix the bug."},
    ]
    processor = MagicMock()
    handler = StreamingHandler(chat, tool_handler, processor)  # type: ignore[arg-type]

    with patch(
        "core.chat_tab_streaming.requests.post",
        return_value=MagicMock(),
    ) as post:
        handler._fetch_response(0, {"model": "codex/test"}, [], "project prompt")

    payload = post.call_args.kwargs["json"]
    if workspace:
        assert payload["working_directory"] == workspace
    else:
        assert "working_directory" not in payload


@pytest.mark.parametrize(
    "surface_socket",
    [None, "/home/carlos/.alpaca_pack/s1/surfaces/control.sock"],
)
def test_initial_request_forwards_the_surface_socket(
    surface_socket: str | None,
) -> None:
    """Only a Pack tab's daemon sets tab.surface_socket (pack_daemon.py);
    a plain local tab has no such attribute, and the payload must not
    fabricate one -- surface_mcp_server.py's own "not a Pack tab" message
    depends on that key being genuinely absent, not an empty string.
    """
    chat = SimpleNamespace(
        stop_streaming_flag=threading.Event(),
        preferences={"api_url": "http://example.test", "model": "codex/test"},
        conversation_id=42,
        workspace_path="/srv/workspaces/alpaca-session",
        surface_socket=surface_socket,
        _streaming=True,
        is_streaming=True,
        on_streaming_complete=MagicMock(),
        on_streaming_error=MagicMock(),
    )
    tool_handler = MagicMock()
    tool_handler.prepare_continuation_messages.return_value = [
        {"role": "user", "content": "Fix the bug."},
    ]
    processor = MagicMock()
    handler = StreamingHandler(chat, tool_handler, processor)  # type: ignore[arg-type]

    with patch(
        "core.chat_tab_streaming.requests.post",
        return_value=MagicMock(),
    ) as post:
        handler._fetch_response(0, {"model": "codex/test"}, [], "project prompt")

    payload = post.call_args.kwargs["json"]
    if surface_socket:
        assert payload["surface_socket"] == surface_socket
    else:
        assert "surface_socket" not in payload


def test_continuation_keeps_project_workspace_and_system_prompt() -> None:
    app_core = MagicMock()
    app_core.api = None
    app_core.get_available_mcp_tools.return_value = []
    app_core.get_system_prompt.return_value = "RUNBOOK and first-turn SPINUP"
    chat = SimpleNamespace(
        stop_streaming_flag=threading.Event(),
        preferences={"api_url": "http://example.test", "model": "codex/test"},
        conversation_id=42,
        workspace_path="/srv/workspaces/alpaca-session",
        _app_core=app_core,
        _streaming=True,
        is_streaming=True,
        on_streaming_complete=MagicMock(),
        on_streaming_error=MagicMock(),
    )
    tool_handler = MagicMock()
    tool_handler.prepare_continuation_messages.return_value = [
        {"role": "user", "content": "Fix the bug."},
    ]
    processor = MagicMock()
    handler = StreamingHandler(chat, tool_handler, processor)  # type: ignore[arg-type]

    with patch(
        "core.chat_tab_streaming.requests.post",
        return_value=MagicMock(),
    ) as post:
        handler.continue_streaming(0)

    payload = post.call_args.kwargs["json"]
    assert payload["working_directory"] == "/srv/workspaces/alpaca-session"
    assert payload["system"] == "RUNBOOK and first-turn SPINUP"


@pytest.mark.parametrize(
    ("client", "model", "executable"),
    [
        (ClaudeCodeCLIClient(), "claude-code/sonnet", "claude"),
        (CodexCLIClient(), "codex/gpt-5.6-sol", "codex"),
    ],
)
def test_cli_clients_materialize_user_images(
    client: ClaudeCodeCLIClient | CodexCLIClient,
    model: str,
    executable: str,
) -> None:
    # 1x1 PNG
    image = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
        "+A8AAQUBAScY42YAAAAASUVORK5CYII="
    )
    with (
        patch(
            "anthropic_ollama_server._build_claude_mcp_config_file",
            return_value=None,
        ),
        patch(
            "anthropic_ollama_server._run_cli_jsonl",
            return_value=iter(()),
        ) as run_cli,
    ):
        list(
            client.stream_complete(
                messages=[
                    {"role": "user", "content": "Describe it", "images": [image]},
                ],
                model=model,
            ),
        )

    command, prompt, _workspace = run_cli.call_args.args
    assert command[0] == executable
    assert "Attached image:" in prompt
    if executable == "codex":
        assert "--image" in command


def test_cli_mcp_config_always_includes_media_bridge(tmp_path: Path) -> None:
    event_path = str(tmp_path / "events.jsonl")
    config_path = _build_claude_mcp_config_file(event_path, "/workspace")
    try:
        config = json.loads(Path(config_path).read_text())
    finally:
        Path(config_path).unlink()

    media = config["mcpServers"]["alpaca-media"]
    assert media["env"]["ALPACA_CLI_MEDIA_EVENTS"] == event_path
    assert media["env"]["ALPACA_WORKSPACE"] == "/workspace"
    assert media["args"][0].endswith("cli_media_mcp_server.py")

    overrides = _codex_mcp_overrides(event_path, "/workspace")
    # Unquoted: codex's `-c key=value` splits the dotted key on literal
    # dots itself rather than parsing it as TOML, so a quoted name segment
    # ends up with its quote characters baked into the registered server
    # name — silently dropping it from the tools offered to the model
    # (confirmed against a real codex-cli 0.147.0 binary via
    # `codex mcp list --json`).
    assert any("mcp_servers.alpaca-media.command=" in value for value in overrides)
    assert not any('"alpaca-media"' in value for value in overrides)
    assert any("ALPACA_CLI_MEDIA_EVENTS" in value for value in overrides)


def test_cli_mcp_config_surface_server_is_absolute_regardless_of_cwd(
    tmp_path: Path,
) -> None:
    """A bare "python" / relative "surface_mcp_server.py" from a raw
    mcp_servers.json entry would only resolve by accident, since the CLI
    subprocess's cwd is the workspace, not the repo -- confirmed as the
    actual cause of a live failure where the model had no surface_open tool
    and fell back to a raw shell command instead.
    """
    config_path = _build_claude_mcp_config_file(str(tmp_path / "events.jsonl"), None)
    try:
        config = json.loads(Path(config_path).read_text())
    finally:
        Path(config_path).unlink()

    surface = config["mcpServers"]["alpaca-surface"]
    assert surface["command"] == sys.executable
    assert Path(surface["args"][0]).is_absolute()
    assert surface["args"][0].endswith("surface_mcp_server.py")
    assert Path(surface["args"][0]).is_file()


def test_cli_mcp_config_surface_server_carries_the_session_socket(
    tmp_path: Path,
) -> None:
    """The CLI subprocess's own cwd is the workspace, not the Pack session
    directory, so SurfaceControlClient.discover() can't find the right
    socket by itself once more than one Pack tab is open on the same host
    (confirmed live: 7 concurrent sessions, discover() returning None for
    every one). The exact socket path must ride along as an env var.
    """
    socket_path = str(tmp_path / "surfaces" / "control.sock")
    config_path = _build_claude_mcp_config_file(
        str(tmp_path / "events.jsonl"),
        "/workspace",
        socket_path,
    )
    try:
        config = json.loads(Path(config_path).read_text())
    finally:
        Path(config_path).unlink()

    surface = config["mcpServers"]["alpaca-surface"]
    assert surface["env"]["ALPACA_SURFACE_SOCKET"] == socket_path

    overrides = _codex_mcp_overrides(
        str(tmp_path / "events.jsonl"),
        "/workspace",
        socket_path,
    )
    assert any(
        f"mcp_servers.alpaca-surface.env.ALPACA_SURFACE_SOCKET={json.dumps(socket_path)}"
        in value
        for value in overrides
    )


def test_cli_mcp_config_surface_server_has_no_env_without_a_socket(
    tmp_path: Path,
) -> None:
    """No session socket to hand over (e.g. a non-Pack local tab) must not
    fabricate an env block the real supervisor never asked for.
    """
    config_path = _build_claude_mcp_config_file(str(tmp_path / "events.jsonl"), None)
    try:
        config = json.loads(Path(config_path).read_text())
    finally:
        Path(config_path).unlink()

    assert "env" not in config["mcpServers"]["alpaca-surface"]


def test_cli_mcp_config_surface_server_overrides_a_raw_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stale hand-written mcp_servers.json entry (bare command, relative
    path) must not win over the absolute one this module always registers.
    """
    mcp_file = tmp_path / "mcp_servers.json"
    mcp_file.write_text(
        json.dumps(
            {"alpaca-surface": {"command": ["python", "surface_mcp_server.py"]}},
        ),
    )
    monkeypatch.setattr(
        "anthropic_ollama_server.MCP_SERVERS_FILE",
        str(mcp_file),
    )

    config_path = _build_claude_mcp_config_file(str(tmp_path / "events.jsonl"), None)
    try:
        config = json.loads(Path(config_path).read_text())
    finally:
        Path(config_path).unlink()

    assert config["mcpServers"]["alpaca-surface"]["command"] == sys.executable


def test_cli_jsonl_forwards_media_side_channel(tmp_path: Path) -> None:
    event_path = tmp_path / "events.jsonl"
    event = {"type": "alpaca_tool_event", "id": "media-1"}
    event_path.write_text(json.dumps(event) + "\n")
    command = [
        sys.executable,
        "-c",
        "import json; print(json.dumps({'type': 'result'}))",
    ]

    output = list(
        _run_cli_jsonl(
            command,
            "",
            media_event_path=str(event_path),
        ),
    )

    assert output == [event, {"type": "result"}]


def test_cli_jsonl_emits_heartbeats_during_a_silent_gap() -> None:
    # StreamingHandler.STREAM_TIMEOUT's 120s read timeout kills the whole
    # stream if the local socket goes quiet that long — exactly what
    # happens when a CLI tool call (e.g. rendering several images/videos)
    # runs for minutes without printing a line. A low heartbeat interval
    # here stands in for the real ~20s one so the test doesn't sleep long.
    command = [
        sys.executable,
        "-c",
        "import time, json; time.sleep(0.3); print(json.dumps({'type': 'result'}))",
    ]

    with patch("anthropic_ollama_server._CLI_HEARTBEAT_INTERVAL_SECS", 0.05):
        output = list(_run_cli_jsonl(command, ""))

    assert output[-1] == {"type": "result"}
    assert output[:-1] == [{"type": "cli_heartbeat"}] * (len(output) - 1)
    assert len(output) > 1
