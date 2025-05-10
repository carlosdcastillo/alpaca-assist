"""
Ollama API server emulator that routes requests to Claude via the Anthropic API.
This server mimics the Ollama API endpoints but uses Claude for inference.
"""
import datetime
import json
import os
import random
import time
from http.server import BaseHTTPRequestHandler
from http.server import HTTPServer
from typing import Any
from typing import Dict
from typing import Generator
from typing import List
from typing import Optional
from typing import Union
from zoneinfo import ZoneInfo

import requests  # type: ignore
import yaml  # type: ignore

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

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)

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
                request_data: Dict[str, Any] = json.loads(post_data.decode("utf-8"))
                messages: List[Dict[str, str]] = request_data["messages"]

                # Filter and clean messages
                messages_out: List[Dict[str, str]] = []
                for i, item in enumerate(messages):
                    if item["content"] == "":
                        continue
                    # Ensure alternating user/assistant messages
                    if i % 2 == 0 and item["role"] != "user":
                        continue
                    elif i % 2 == 1 and item["role"] != "assistant":
                        continue

                    # Remove tool_calls if present
                    if "tool_calls" in item:
                        del item["tool_calls"]

                    messages_out.append(item)

                # Get response from Claude
                stream: Optional[
                    Generator[Dict[str, Any], None, None]
                ] = client.stream_complete(
                    messages=messages_out,
                    model="claude-3-7-sonnet-20250219",
                    max_tokens=25000,
                    system="You are a an assistant that is helpful.",
                )

                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                self.end_headers()
                response_body: str = ""
                count: int = 0
                if stream:
                    for event in stream:
                        # print(event)
                        # print("new event")
                        val: Dict[str, Any] = event
                        # print(val)
                        if "delta" in val["type"] and "text" in val["delta"]:

                            now: datetime.datetime = datetime.datetime.now(
                                datetime.timezone.utc,
                            ).astimezone()

                            # Format with microseconds (6 digits)
                            local_timestamp: str = now.strftime("%Y-%m-%dT%H:%M:%S.%f")

                            # Pad with zeros to get 9 digits for nanoseconds
                            local_timestamp = local_timestamp[:-3] + "000"

                            # Add timezone offset
                            local_timestamp += now.strftime("%z")

                            # Insert colon in timezone offset
                            local_timestamp = (
                                local_timestamp[:-2] + ":" + local_timestamp[-2:]
                            )
                            # print(local_timestamp)
                            response: Dict[str, Any] = {
                                "model": "codellama:13b",
                                "created_at": local_timestamp,
                                "message": {
                                    "role": "assistant",
                                    "content": val["delta"]["text"],
                                },
                                "done": False,
                            }
                            response_body = response_body + val["delta"]["text"]

                            count = count + 1
                            # print(json.dumps(response).encode())
                            self.wfile.write(json.dumps(response).encode())
                            self.wfile.write("\n".encode())
                            self.wfile.flush()

                now = datetime.datetime.now(datetime.timezone.utc).astimezone()

                # Format with microseconds (6 digits)
                local_timestamp = now.strftime("%Y-%m-%dT%H:%M:%S.%f")

                # Pad with zeros to get 9 digits for nanoseconds
                local_timestamp = local_timestamp[:-3] + "000"

                # Add timezone offset
                local_timestamp += now.strftime("%z")

                # Insert colon in timezone offset
                local_timestamp = local_timestamp[:-2] + ":" + local_timestamp[-2:]
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

                # # Send response
                self.wfile.write(json.dumps(response).encode())
                self.wfile.write("\n".encode())
                self.wfile.flush()
            except json.JSONDecodeError:
                self.send_error(400, "Invalid JSON")
            except Exception as e:
                self.send_error(500, f"Internal Server Error: {str(e)}")
        else:
            self.send_error(404, "Not Found")


class ClaudeClient:
    """Client for interacting with Anthropic's Claude API"""

    def __init__(self, api_key: Optional[str] = None) -> None:
        """Initialize the Claude client with your API key"""
        self.api_key: Optional[str] = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ValueError(
                "Anthropic API key is required. Set it directly or via ANTHROPIC_API_KEY environment variable.",
            )

        self.base_url: str = "https://api.anthropic.com/v1"
        self.headers: Dict[str, str] = {
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
        system: Optional[str] = None,
    ) -> Dict[str, Any]:
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
        messages: List[Dict[str, str]] = [{"role": "user", "content": prompt}]

        payload: Dict[str, Any] = {
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
        messages: List[Dict[str, str]] = [],
        model: str = "claude-3-5-sonnet-20240620",
        max_tokens: int = 8192,
        temperature: float = 0.7,
        system: Optional[str] = None,
    ) -> Generator[Dict[str, Any], None, None]:
        """
        Send a streaming completion request to Claude 3.7 Sonnet

        Args:
            prompt: The user prompt to send to Claude
            model: The Claude model to use (default is claude-3-5-sonnet-20240620)
            max_tokens: Maximum tokens to generate in the response
            temperature: The sampling temperature (0-1)
            system: Optional system prompt to set context

        Yields:
            Chunks of the response as they are received
        """
        url: str = f"{self.base_url}/messages"

        payload: Dict[str, Any] = {
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
                # print(line)
                # Remove "data: " prefix and parse JSON
                line_text: str = line.decode("utf-8")
                if line_text.startswith("data: "):
                    json_str: str = line_text[6:]  # Skip "data: "
                    if json_str.strip() == "[DONE]":
                        break
                    try:
                        chunk: Dict[str, Any] = json.loads(json_str)
                        # print(chunk)
                        yield chunk
                    except json.JSONDecodeError:
                        print(f"Failed to decode JSON: {json_str}")


def run_server(port: int = 11434) -> None:
    """Run the HTTP server."""
    server_address: tuple[str, int] = ("", port)
    httpd: HTTPServer = HTTPServer(server_address, OllamaRequestHandler)
    print(f"Ollama emulator server running on port {port}")
    print(f"Routing requests to Claude the Anthropic API")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server...")
        httpd.server_close()


if __name__ == "__main__":
    client: ClaudeClient = ClaudeClient()
    run_server()
