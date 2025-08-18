"""
Ollama API server emulator that routes requests to Claude via the Anthropic API.
This server mimics the Ollama API endpoints but uses Claude for inference.
"""
import datetime
import json
import os
import random
import time
from collections.abc import Generator
from http.server import BaseHTTPRequestHandler
from http.server import HTTPServer
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Union
from zoneinfo import ZoneInfo

import requests  # type: ignore
import yaml  # type: ignore

SYSTEM_PROMPT = """
You are a highly skilled software engineer with extensive knowledge in many programming languages, frameworks, design patterns, and best practices.

## Communication

1. Be conversational but professional. Use a friendly tone while maintaining technical accuracy in your explanations.

2. Refer to the user in the second person ("you") and yourself in the first person ("I"). Maintain this consistent voice throughout all interactions.

3. Format responses in markdown for readability. Use backticks to format `file`, `directory`, `function`, and `class` names when referencing code elements.

4. NEVER lie or make things up. If you don't know something, clearly state that rather than providing incorrect information.

5. Refrain from apologizing when results are unexpected. Instead, focus on proceeding with solutions or explaining the circumstances clearly without unnecessary apologies.

6. Always start responses with a newline character for consistent formatting.
"""

# Mock models response for the /api/tags endpoint
MODELS_JSON: str = """
{
  "models": [
    {
      "name": "codellama:13b",
      "modified_at": "2023-11-04T14:56:49.277302595-07:00",
      "size": 7365960935,
      "digest": "9f438cb9cd581fc025612d27f7c1a6669ff83a8bb0ed86c94fcf4c5440555697",
      "details": {
        "format": "gguf",
        "family": "llama",
        "families": null,
        "parameter_size": "13B",
        "quantization_level": "Q4_0"
      }
    },
    {
      "name": "llama3:latest",
      "modified_at": "2023-12-07T09:32:18.757212583-08:00",
      "size": 3825819519,
      "digest": "fe938a131f40e6f6d40083c9f0f430a515233eb2edaa6d72eb85c50d64f2300e",
      "details": {
        "format": "gguf",
        "family": "llama",
        "families": null,
        "parameter_size": "7B",
        "quantization_level": "Q4_0"
      }
    }
  ]
}
"""


class OllamaRequestHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the Ollama API emulator."""

    def do_GET(self) -> None:
        """Handle GET requests."""
        if self.path == "/api/tags":
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            self.wfile.write(MODELS_JSON.encode())
        else:
            self.send_error(404, "Not Found")

    def do_POST(self) -> None:
        """Handle POST requests."""
        if self.path == "/api/chat":
            content_length: int = int(self.headers["Content-Length"])
            post_data: bytes = self.rfile.read(content_length)

            try:
                request_data: dict[str, Any] = json.loads(post_data.decode("utf-8"))
                messages: list[dict[str, str]] = request_data["messages"]

                # Extract tools if present in the request
                tools = request_data.get("tools", [])

                print(f"Received tools: {tools}")

                # Filter and clean messages
                messages_out: list[dict[str, str]] = []
                for i, item in enumerate(messages):
                    print(item)

                    # Handle tool call responses
                    # if item.get("role") == "tool":
                    #     # Convert tool response to assistant message
                    #     messages_out.append(
                    #         {
                    #             "role": "tool",
                    #             "content": f"Tool result: {item['content']}",
                    #         },
                    #     )

                    # Remove tool_calls if present (we'll handle them differently)
                    # clean_item = {k: v for k, v in item.items() if k != "tool_calls"}
                    messages_out.append(item)

                # Send to Claude with tools if provided
                self._handle_request_with_tools(messages_out, tools)

            except json.JSONDecodeError:
                self.send_error(400, "Invalid JSON")
            except Exception as e:
                self.send_error(500, f"Internal Server Error: {str(e)}")
        else:
            self.send_error(404, "Not Found")

    def _handle_request_with_tools(
        self,
        messages: list[dict[str, str]],
        tools: list[dict],
    ):
        """Handle requests with optional tools."""

        # Create enhanced system prompt if tools are available
        if tools:
            tools_description = "You have access to the following tools:\n"
            for tool in tools:
                func = tool.get("function", {})
                tools_description += (
                    f"- {func.get('name', 'unknown')}: {func.get('description', '')}\n"
                )

            tools_description += (
                "\nTo use a tool, respond with a JSON object in this format:\n"
            )
            tools_description += """
                {
                   "tool_call": {
                      "name": "tool_name",
                      "arguments": {
                         ...
                      }
                   }
                }
                """
            tools_description += "If you don't need to use tools, respond normally. Don't do both at the same time."

            enhanced_system = f"{SYSTEM_PROMPT}\n\n{tools_description}"
        else:
            enhanced_system = SYSTEM_PROMPT

        # Get response from Claude
        stream = client.stream_complete(
            messages=messages,
            model="claude-sonnet-4-20250514",
            max_tokens=40000,
            system=enhanced_system,
        )

        self._process_stream(stream)

    def _process_stream(self, stream):
        """Process streaming response."""
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.end_headers()

        response_body = ""
        count = 0

        if stream:
            for event in stream:
                print(event)
                val: dict[str, Any] = event
                if "delta" in val["type"] and "text" in val["delta"]:
                    text_chunk = val["delta"]["text"]
                    response_body += text_chunk

                    # Send the chunk immediately
                    self._send_text_chunk(text_chunk, count)
                    count += 1

        self._send_completion_chunk(count)

    def _send_text_chunk(self, text: str, count: int):
        """Send a regular text chunk."""
        now = datetime.datetime.now(datetime.UTC).astimezone()
        local_timestamp = self._format_timestamp(now)

        response = {
            "model": "codellama:13b",
            "created_at": local_timestamp,
            "message": {
                "role": "assistant",
                "content": text,
            },
            "done": False,
        }

        self.wfile.write(json.dumps(response).encode())
        self.wfile.write(b"\n")
        self.wfile.flush()

    def _send_completion_chunk(self, count: int):
        """Send the final completion chunk."""
        now = datetime.datetime.now(datetime.UTC).astimezone()
        local_timestamp = self._format_timestamp(now)

        response = {
            "model": "codellama:13b",
            "created_at": local_timestamp,
            "message": {
                "role": "assistant",
                "content": "",
            },
            "done": True,
            "done_reason": "stop",
            "eval_count": count,
        }

        self.wfile.write(json.dumps(response).encode())
        self.wfile.write(b"\n")
        self.wfile.flush()

    def _format_timestamp(self, dt: datetime.datetime) -> str:
        """Format timestamp in the expected format."""
        local_timestamp = dt.strftime("%Y-%m-%dT%H:%M:%S.%f")
        local_timestamp = local_timestamp[:-3] + "000"
        local_timestamp += dt.strftime("%z")
        return local_timestamp[:-2] + ":" + local_timestamp[-2:]


class ClaudeClient:
    """Client for interacting with Anthropic's Claude API"""

    def __init__(self, api_key: str | None = None) -> None:
        """Initialize the Claude client with your API key"""
        self.api_key: str | None = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ValueError(
                "Anthropic API key is required. Set it directly or via ANTHROPIC_API_KEY environment variable.",
            )

        self.base_url: str = "https://api.anthropic.com/v1"
        self.headers: dict[str, str] = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

    def complete(
        self,
        prompt: str,
        model: str = "claude-3-5-sonnet-20240620",
        max_tokens: int = 8192,
        temperature: float = 0.7,
        system: str | None = None,
    ) -> dict[str, Any]:
        """
        Send a completion request to Claude 3.7 Sonnet

        Args:
            prompt: The user prompt to send to Claude
            model: The Claude model to use (default is claude-3-5-sonnet-20240620)
            max_tokens: Maximum tokens to generate in the response
            temperature: The sampling temperature (0-1)
            system: Optional system prompt to set context

        Returns:
            Dict containing the API response
        """
        url: str = f"{self.base_url}/messages"

        # Prepare the messages format
        messages: list[dict[str, str]] = [{"role": "user", "content": prompt}]

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        # Add system prompt if provided
        if system:
            payload["system"] = system

        response: requests.Response = requests.post(
            url,
            headers=self.headers,
            json=payload,
        )

        if response.status_code != 200:
            print(f"Error: {response.status_code}")
            print(response.text)
            response.raise_for_status()

        return response.json()

    def stream_complete(
        self,
        messages: list[dict[str, str]] = [],
        model: str = "claude-3-5-sonnet-20240620",
        max_tokens: int = 8192,
        temperature: float = 0.7,
        system: str | None = None,
    ) -> Generator[dict[str, Any], None, None]:
        """
        Send a streaming completion request to Claude 3.7 Sonnet

        Args:
            messages: The messages to send to Claude
            model: The Claude model to use (default is claude-3-5-sonnet-20240620)
            max_tokens: Maximum tokens to generate in the response
            temperature: The sampling temperature (0-1)
            system: Optional system prompt to set context

        Yields:
            Chunks of the response as they are received
        """
        url: str = f"{self.base_url}/messages"

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
        }
        print(payload)

        # Add system prompt if provided
        if system:
            payload["system"] = system

        response: requests.Response = requests.post(
            url,
            headers=self.headers,
            json=payload,
            stream=True,
        )

        if response.status_code != 200:
            print(f"Error: {response.status_code}")
            print(response.text)
            response.raise_for_status()

        # Process the streaming response
        for line in response.iter_lines():
            if line:
                # Remove "data: " prefix and parse JSON
                line_text: str = line.decode("utf-8")
                if line_text.startswith("data: "):
                    json_str: str = line_text[6:]  # Skip "data: "
                    if json_str.strip() == "[DONE]":
                        break
                    try:
                        chunk: dict[str, Any] = json.loads(json_str)
                        yield chunk
                    except json.JSONDecodeError:
                        print(f"Failed to decode JSON: {json_str}")


def run_server(port: int = 11434) -> None:
    """Run the HTTP server."""
    server_address: tuple[str, int] = ("", port)
    httpd: HTTPServer = HTTPServer(server_address, OllamaRequestHandler)
    print(f"Ollama emulator server running on port {port}")
    print(f"Routing requests to Claude via Anthropic API")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server...")
        httpd.server_close()


if __name__ == "__main__":
    client: ClaudeClient = ClaudeClient()
    run_server()
