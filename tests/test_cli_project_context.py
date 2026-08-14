from __future__ import annotations

import json
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

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


def test_proxy_dispatches_the_project_workspace_to_the_selected_backend() -> None:
    backend = MagicMock()
    backend.stream_complete.return_value = iter(())
    handler = object.__new__(OllamaRequestHandler)
    handler._process_stream = MagicMock()

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
    handler = StreamingHandler(chat, tool_handler, processor)

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
    handler = StreamingHandler(chat, tool_handler, processor)

    with patch(
        "core.chat_tab_streaming.requests.post",
        return_value=MagicMock(),
    ) as post:
        handler.continue_streaming(0)

    payload = post.call_args.kwargs["json"]
    assert payload["working_directory"] == "/srv/workspaces/alpaca-session"
    assert payload["system"] == "RUNBOOK and first-turn SPINUP"
