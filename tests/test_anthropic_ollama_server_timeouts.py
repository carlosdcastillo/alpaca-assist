"""Regression coverage for HTTP timeouts on provider LLM clients.

The non-streaming clients back convert_html_to_markdown's summarization call,
and the Fireworks streaming client backs interactive GLM/Kimi turns. Without
timeouts, a stalled provider connection can hang forever independent of the
app's timeout on its separate connection to the local proxy.
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

    def test_stream_complete_sets_connect_and_read_timeouts(self) -> None:
        client = FireworksClient(api_key="fake-key")
        response = _mock_response()
        response.iter_lines.return_value = []
        with patch(
            "anthropic_ollama_server.requests.post",
            return_value=response,
        ) as mock_post:
            list(
                client.stream_complete(
                    messages=[{"role": "user", "content": "hi"}],
                    model="accounts/fireworks/models/glm-5p3",
                ),
            )

        connect_timeout, read_timeout = mock_post.call_args.kwargs["timeout"]
        assert connect_timeout > 0
        assert read_timeout > 0
