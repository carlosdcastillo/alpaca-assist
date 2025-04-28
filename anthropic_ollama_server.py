import json
import random
import time

from flask import Flask
from flask import request
from flask import Response
from flask import stream_with_context

app = Flask(__name__)


@app.route("/api/generate", methods=["POST"])
def generate():
    # Get the JSON data from the request
    data = request.json

    # Extract model and prompt from the request
    model = data.get("model", "default")
    prompt = data.get("prompt", "")
    system = data.get("system", "")
    chat_history_questions = data.get("chat_history_questions", [])
    chat_history_answers = data.get("chat_history_answers", [])

    # Log what we received
    print(f"Received request for model: {model}")
    print(f"System: {system}")
    print(f"Prompt: {prompt}")

    def generate_response():
        """Generate a streaming response similar to Ollama API"""

        # First, send a response to indicate the start
        yield json.dumps(
            {
                "model": model,
                "created_at": int(time.time()),
                "response": "",
                "done": False,
            },
        ) + "\n"

        chars_sent = 0
        for chunk in client.stream_complete(
            prompt=prompt,
            chat_history_questions=chat_history_questions,
            chat_history_answers=chat_history_answers,
            model="claude-3-5-sonnet-20240620",
            max_tokens=8192,
            system="You are a an assistant that is helpful.",
        ):
            if "delta" in chunk and "text" in chunk["delta"]:
                text_chunk = chunk["delta"]["text"]
                chars_sent = chars_sent + len(text_chunk)
                print(text_chunk)

                yield json.dumps(
                    {
                        "model": model,
                        "created_at": int(time.time()),
                        "response": text_chunk,
                        "done": False,
                    },
                ) + "\n"

        # Final response indicating completion
        yield json.dumps(
            {
                "model": model,
                "created_at": int(time.time()),
                "response": "",
                "done": True,
                "context": [random.randint(1, 10000) for _ in range(5)],  # Mock context
                "total_duration": random.uniform(0.5, 2.0),
                "load_duration": random.uniform(0.1, 0.5),
                "prompt_eval_count": len(prompt),
                "prompt_eval_duration": random.uniform(0.1, 0.5),
                "eval_count": chars_sent,
                "eval_duration": random.uniform(0.3, 1.5),
            },
        ) + "\n"

    return Response(
        stream_with_context(generate_response()),
        content_type="application/json",
    )


import os
import requests
import json
from typing import Optional, Dict, Any, List


class ClaudeClient:
    """Client for interacting with Anthropic's Claude API"""

    def __init__(self, api_key: Optional[str] = None):
        """Initialize the Claude client with your API key"""
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ValueError(
                "Anthropic API key is required. Set it directly or via ANTHROPIC_API_KEY environment variable.",
            )

        self.base_url = "https://api.anthropic.com/v1"
        self.headers = {
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
        system: str = None,
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
        url = f"{self.base_url}/messages"

        # Prepare the messages format
        messages = [{"role": "user", "content": prompt}]

        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        # Add system prompt if provided
        if system:
            payload["system"] = system

        response = requests.post(url, headers=self.headers, json=payload)

        if response.status_code != 200:
            print(f"Error: {response.status_code}")
            print(response.text)
            response.raise_for_status()

        return response.json()

    def stream_complete(
        self,
        prompt: str,
        chat_history_questions=[],
        chat_history_answers=[],
        model: str = "claude-3-5-sonnet-20240620",
        max_tokens: int = 8192,
        temperature: float = 0.7,
        system: str = None,
    ):
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
        url = f"{self.base_url}/messages"

        # Prepare the messages format
        messages = []
        print(len(chat_history_questions))
        for (q, a) in zip(chat_history_questions, chat_history_answers):
            messages.append({"role": "user", "content": q})
            messages.append({"role": "assistant", "content": a})

        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
        }

        # Add system prompt if provided
        if system:
            payload["system"] = system

        response = requests.post(url, headers=self.headers, json=payload, stream=True)

        if response.status_code != 200:
            print(f"Error: {response.status_code}")
            print(response.text)
            response.raise_for_status()

        # Process the streaming response
        for line in response.iter_lines():
            if line:
                # Remove "data: " prefix and parse JSON
                line_text = line.decode("utf-8")
                if line_text.startswith("data: "):
                    json_str = line_text[6:]  # Skip "data: "
                    if json_str.strip() == "[DONE]":
                        break
                    try:
                        chunk = json.loads(json_str)
                        yield chunk
                    except json.JSONDecodeError:
                        print(f"Failed to decode JSON: {json_str}")


if __name__ == "__main__":
    print("Starting Ollama API emulator on http://localhost:11435")
    print("Press Ctrl+C to stop the server")
    client = ClaudeClient()
    app.run(host="0.0.0.0", port=11435, debug=True)
