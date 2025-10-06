"""
Ollama API server emulator that routes requests to Claude via the Anthropic API.
This server mimics the Ollama API endpoints but uses Claude for inference.
"""
import datetime
import json
import os
import random
import sys
import time
from collections.abc import Generator
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Union
from zoneinfo import ZoneInfo

import requests
import yaml

SYSTEM_PROMPT = '\nYou are a highly skilled software engineer with extensive knowledge in many programming languages, frameworks, design patterns, and best practices.\n\n## Communication\n\n1. Be conversational but professional. Use a friendly tone while maintaining technical accuracy in your explanations.\n\n2. Refer to the user in the second person ("you") and yourself in the first person ("I"). Maintain this consistent voice throughout all interactions.\n\n3. Format responses in markdown for readability. Use backticks to format `file`, `directory`, `function`, and `class` names when referencing code elements.\n\n4. NEVER lie or make things up. If you don\'t know something, clearly state that rather than providing incorrect information.\n\n5. Refrain from apologizing when results are unexpected. Instead, focus on proceeding with solutions or explaining the circumstances clearly without unnecessary apologies.\n\n6. Always start responses with a newline character for consistent formatting.\n'

MODELS_JSON: str = '\n{\n  "models": [\n    {\n      "name": "codellama:13b",\n      "modified_at": "2023-11-04T14:56:49.277302595-07:00",\n      "size": 7365960935,\n      "digest": "9f438cb9cd581fc025612d27f7c1a6669ff83a8bb0ed86c94fcf4c5440555697",\n      "details": {\n        "format": "gguf",\n        "family": "llama",\n        "families": null,\n        "parameter_size": "13B",\n        "quantization_level": "Q4_0"\n      }\n    },\n    {\n      "name": "llama3:latest",\n      "modified_at": "2023-12-07T09:32:18.757212583-08:00",\n      "size": 3825819519,\n      "digest": "fe938a131f40e6f6d40083c9f0f430a515233eb2edaa6d72eb85c50d64f2300e",\n      "details": {\n        "format": "gguf",\n        "family": "llama",\n        "families": null,\n        "parameter_size": "7B",\n        "quantization_level": "Q4_0"\n      }\n    }\n  ]\n}\n'


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
                tools = request_data.get("tools", [])
                print(f"Received tools: {tools}")
                messages_out: list[dict[str, str]] = []
                for i, item in enumerate(messages):
                    print(item)
                    messages_out.append(item)
                self._handle_request_with_tools(messages_out, tools)
            except json.JSONDecodeError:
                self.send_error(400, "Invalid JSON")
            except Exception as e:
                self.send_error(500, f"Internal Server Error: {str(e)}")
        else:
            self.send_error(404, "Not Found")

    def _send_text_chunk(self, text: str, count: int):
        """Send a regular text chunk."""
        now = datetime.datetime.now(datetime.UTC).astimezone()
        local_timestamp = self._format_timestamp(now)
        response = {
            "model": "codellama:13b",
            "created_at": local_timestamp,
            "message": {"role": "assistant", "content": text},
            "done": False,
        }
        self.wfile.write(json.dumps(response).encode())
        self.wfile.write(b"\n")
        self.wfile.flush()

    def _send_completion_chunk(
        self,
        count: int,
        stop_reason: str = "stop",
        tool_calls: list = None,
    ):
        """Send the final completion chunk."""
        now = datetime.datetime.now(datetime.UTC).astimezone()
        local_timestamp = self._format_timestamp(now)

        message = {
            "role": "assistant",
            "content": "",
        }

        # Add tool calls to the message if they exist
        if tool_calls:
            message["tool_calls"] = tool_calls

        response = {
            "model": "codellama:13b",
            "created_at": local_timestamp,
            "message": message,
            "done": True,
            "done_reason": stop_reason,
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

    def _handle_request_with_tools(
        self,
        messages: list[dict[str, str]],
        tools: list[dict],
    ):
        """Handle requests with optional tools."""
        anthropic_tools = None
        tool_choice = None
        if tools:
            anthropic_tools = self._convert_tools_to_anthropic_format(tools)
            tool_choice = {"type": "auto"}
        stream = client.stream_complete(
            messages=messages,
            model="claude-sonnet-4-20250514",
            max_tokens=40000,
            system=SYSTEM_PROMPT,
            tools=anthropic_tools,
            tool_choice=tool_choice,
        )
        self._process_stream(stream)

    def _convert_tools_to_anthropic_format(self, tools: list[dict]) -> list[dict]:
        """Convert OpenAI-style tools to Anthropic format."""
        anthropic_tools = []
        for tool in tools:
            if "function" in tool:
                func = tool["function"]
                anthropic_tool = {
                    "name": func.get("name", ""),
                    "description": func.get("description", ""),
                    "input_schema": func.get("parameters", {}),
                }
                anthropic_tools.append(anthropic_tool)
        return anthropic_tools

    def _process_stream(self, stream):
        """Process streaming response."""
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.end_headers()

        response_body = ""
        count = 0
        stop_reason = "stop"
        in_tool = False
        tool_accum = ""

        if stream:
            for event in stream:
                print(event)
                val: dict[str, Any] = event

                if (
                    val.get("type") == "content_block_delta"
                    and "delta" in val
                    and ("text" in val["delta"])
                ):
                    text_chunk = val["delta"]["text"]
                    response_body += text_chunk
                    self._send_text_chunk(text_chunk, count)
                    count += 1

                elif (
                    val.get("type") == "content_block_start"
                    and val.get("content_block", {}).get("type") == "tool_use"
                ):
                    tool_block = val["content_block"]
                    # Send tool call start as text
                    tool_name = tool_block.get("name", "")
                    tool_id = tool_block.get("id", "")
                    tool_call_text = f'\n\n{{"tool_call": {{"id": "{tool_id}", "name": "{tool_name}", "arguments": '
                    # self._send_text_chunk(tool_call_text, count)
                    tool_accum = tool_accum + tool_call_text
                    in_tool = True
                    count += 1

                elif (
                    val.get("type") == "content_block_delta"
                    and "delta" in val
                    and ("partial_json" in val["delta"])
                ):
                    # Send tool arguments as text chunks
                    args_chunk = val["delta"]["partial_json"]

                    if in_tool and args_chunk == "":
                        args_chunk = "{}"
                        tool_accum = tool_accum + args_chunk
                    elif in_tool:
                        tool_accum = tool_accum + args_chunk
                    else:
                        self._send_text_chunk(args_chunk, count)
                    count += 1

                elif val.get("type") == "content_block_stop":
                    # Send tool call end as text
                    if in_tool:
                        tool_call_end = "}}"
                        tool_accum = tool_accum + tool_call_end
                        in_tool = False
                        tool_accum = tool_accum.replace(
                            '"arguments": {}{',
                            '"arguments": {',
                        )
                        self._send_text_chunk(tool_accum, count)
                    else:
                        tool_call_end = ""
                        self._send_text_chunk(tool_call_end, count)
                    count += 1

                elif (
                    val.get("type") == "message_delta"
                    and "delta" in val
                    and ("stop_reason" in val["delta"])
                ):
                    stop_reason = val["delta"]["stop_reason"]

        # Send final completion chunk without tool calls
        self._send_completion_chunk(count, stop_reason)

    def _send_completion_chunk(self, count: int, stop_reason: str = "stop"):
        """Send the final completion chunk."""
        now = datetime.datetime.now(datetime.UTC).astimezone()
        local_timestamp = self._format_timestamp(now)

        message = {
            "role": "assistant",
            "content": "",
        }

        response = {
            "model": "codellama:13b",
            "created_at": local_timestamp,
            "message": message,
            "done": True,
            "done_reason": stop_reason,
            "eval_count": count,
        }

        self.wfile.write(json.dumps(response).encode())
        self.wfile.write(b"\n")
        self.wfile.flush()


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
        messages: list[dict[str, str]] = [{"role": "user", "content": prompt}]
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
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
        tools: list[dict] | None = None,
        tool_choice: dict | None = None,
    ) -> Generator[dict[str, Any], None, None]:
        """
        Send a streaming completion request to Claude 3.7 Sonnet

        Args:
            messages: The messages to send to Claude
            model: The Claude model to use (default is claude-3-5-sonnet-20240620)
            max_tokens: Maximum tokens to generate in the response
            temperature: The sampling temperature (0-1)
            system: Optional system prompt to set context
            tools: Optional list of tools in Anthropic format

        Yields:
            Chunks of the response as they are received
        """
        url: str = f"{self.base_url}/messages"

        for item in messages:
            if item["role"] == "tool_use_call":
                item["role"] = "assistant"
                item["content"] = [
                    {
                        "type": "tool_use",
                        "id": item["call"]["id"],
                        "name": item["call"]["name"],
                        "input": item["call"]["arguments"],
                    },
                ]
                item.pop("call", None)

            if item["role"] == "tool_result":
                item["role"] = "user"
                item["content"] = [
                    {
                        "type": "tool_result",
                        "tool_use_id": item["id"],
                        "content": [{"text": item["content"], "type": "text"}],
                    },
                ]
                item.pop("id", None)

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
        }
        print(payload)
        if system:
            payload["system"] = system
        if tools:
            payload["tools"] = tools
        if tool_choice:
            payload["tool_choice"] = tool_choice
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
        for line in response.iter_lines():
            if line:
                line_text: str = line.decode("utf-8")
                if line_text.startswith("data: "):
                    json_str: str = line_text[6:]
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
    httpd: ThreadingHTTPServer = ThreadingHTTPServer(
        server_address,
        OllamaRequestHandler,
    )
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
