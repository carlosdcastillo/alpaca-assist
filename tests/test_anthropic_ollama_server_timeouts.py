"""Regression coverage for HTTP timeouts on the non-streaming LLM clients.

These backed convert_html_to_markdown's summarization call with no timeout
at all, so a stalled connection to Anthropic/Fireworks could hang forever
independent of the per-tool-call wait timeout in ToolHandler.
"""

from __future__ import annotations

from unittest.mock import Mock
from unittest.mock import patch

from anthropic_ollama_server import ClaudeClient
from anthropic_ollama_server import FireworksClient


def _mock_response(status_code: int = 200) -> Mock:
    resp = Mock()
    resp.status_code = status_code
    resp.json.return_value = {"content": [{"type": "text", "text": "ok"}]}
    return resp


class TestClaudeClientCompleteTimeout:
    def test_complete_sets_an_http_timeout(self) -> None:
        client = ClaudeClient(api_key="fake-key")
        with patch(
            "anthropic_ollama_server.requests.post",
            return_value=_mock_response(),
        ) as mock_post:
            client.complete(prompt="hi", model="claude-sonnet-4-6")

        assert mock_post.call_args.kwargs["timeout"] is not None
        assert mock_post.call_args.kwargs["timeout"] > 0


class TestFireworksClientCompleteTimeout:
    def test_complete_sets_an_http_timeout(self) -> None:
        client = FireworksClient(api_key="fake-key")
        with patch(
            "anthropic_ollama_server.requests.post",
            return_value=_mock_response(),
        ) as mock_post:
            client.complete(prompt="hi", model="accounts/fireworks/models/glm-5p2")

        assert mock_post.call_args.kwargs["timeout"] is not None
        assert mock_post.call_args.kwargs["timeout"] > 0
