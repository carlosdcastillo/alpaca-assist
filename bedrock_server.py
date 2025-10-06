import json
from collections.abc import Generator
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

import boto3
import yaml


class ClaudeClient:
    """Client for interacting with Claude via AWS Bedrock"""

    def __init__(
        self,
        profile_name: str = None,
        region_name: str = "us-east-1",
        config_file: str = "claude_config.yaml",
    ) -> None:
        """Initialize the Claude client with AWS Bedrock configuration"""

        # Load configuration from YAML file if it exists
        self.config = self._load_config(config_file)

        # Use config values or defaults
        profile = profile_name or self.config.get("bedrock_config", {}).get(
            "ada_profile",
        )
        region = region_name or self.config.get("bedrock_config", {}).get(
            "region",
            "us-east-1",
        )

        # Create AWS session
        if profile:
            self.session = boto3.Session(profile_name=profile, region_name=region)
        else:
            self.session = boto3.Session(region_name=region)

        self.bedrock_runtime = self.session.client("bedrock-runtime")

    def _load_config(self, config_file: str) -> dict[str, Any]:
        """Load configuration from YAML file."""
        try:
            with open(config_file) as f:
                return yaml.safe_load(f)
        except FileNotFoundError:
            print(f"Config file {config_file} not found, using defaults")
            return {}
        except Exception as e:
            print(f"Error loading config: {e}")
            return {}

    def complete(
        self,
        prompt: str,
        model: str = "us.anthropic.claude-sonnet-4-20250514-v1:0",
        max_tokens: int = 8192,
        temperature: float = 0.7,
        system: str | None = None,
    ) -> dict[str, Any]:
        """
        Send a completion request to Claude via Bedrock

        Args:
            prompt: The user prompt to send to Claude
            model: The Claude model ID for Bedrock
            max_tokens: Maximum tokens to generate in the response
            temperature: The sampling temperature (0-1)
            system: Optional system prompt to set context

        Returns:
            Dict containing the API response
        """
        # Prepare the messages format
        messages: list[dict[str, str]] = [{"role": "user", "content": prompt}]

        request_body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": messages,
        }

        # Add system prompt if provided
        if system:
            request_body["system"] = system

        try:
            response = self.bedrock_runtime.invoke_model(
                modelId=model,
                body=json.dumps(request_body),
            )

            # Parse the response
            response_body = json.loads(response["body"].read())
            return response_body

        except Exception as e:
            print(f"Error calling Bedrock: {e}")
            raise

    def stream_complete(
        self,
        messages: list[dict[str, str]] = [],
        model: str = "us.anthropic.claude-sonnet-4-20250514-v1:0",
        max_tokens: int = 8192,
        temperature: float = 0.7,
        system: str | None = None,
    ) -> Generator[dict[str, Any], None, None]:
        """
        Send a streaming completion request to Claude via Bedrock

        Args:
            messages: The messages to send to Claude
            model: The Claude model ID for Bedrock
            max_tokens: Maximum tokens to generate in the response
            temperature: The sampling temperature (0-1)
            system: Optional system prompt to set context

        Yields:
            Chunks of the response as they are received
        """
        request_body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": messages,
        }

        # Add system prompt if provided
        if system:
            request_body["system"] = system

        try:
            response = self.bedrock_runtime.invoke_model_with_response_stream(
                modelId=model,
                body=json.dumps(request_body),
            )

            # Process the streaming response
            stream = response["body"]
            for event in stream:
                if "chunk" in event:
                    chunk = event["chunk"]
                    if "bytes" in chunk:
                        try:
                            chunk_data = json.loads(chunk["bytes"].decode())
                            event_type = chunk_data.get("type", "unknown")

                            if event_type == "content_block_delta":
                                delta = chunk_data.get("delta", {})
                                if delta.get("type") == "text_delta":
                                    # Format to match the expected structure
                                    yield {
                                        "type": "delta",
                                        "delta": {
                                            "text": delta.get("text", ""),
                                        },
                                    }
                            elif event_type == "message_stop":
                                # Signal completion
                                yield {
                                    "type": "message_stop",
                                }
                                break

                        except json.JSONDecodeError as e:
                            print(f"JSON decode error: {e}")
                            continue
                        except Exception as e:
                            print(f"Error processing chunk: {e}")
                            continue

        except Exception as e:
            print(f"Error calling Bedrock streaming: {e}")
            raise

    def stream_complete_with_tools(
        self,
        messages: list[dict[str, str]],
        tools: list[dict],
        model: str = "us.anthropic.claude-sonnet-4-20250514-v1:0",
        max_tokens: int = 8192,
        temperature: float = 0.7,
        system: str | None = None,
    ) -> Generator[dict[str, Any], None, None]:
        """
        Send a streaming completion request to Claude via Bedrock with tools support

        Args:
            messages: The messages to send to Claude
            tools: List of available tools
            model: The Claude model ID for Bedrock
            max_tokens: Maximum tokens to generate in the response
            temperature: The sampling temperature (0-1)
            system: Optional system prompt to set context

        Yields:
            Chunks of the response as they are received
        """
        request_body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": messages,
            "tools": tools,
        }

        # Add system prompt if provided
        if system:
            request_body["system"] = system

        print("🚀 BEDROCK REQUEST BODY:")
        print(json.dumps(request_body, indent=2))

        try:
            response = self.bedrock_runtime.invoke_model_with_response_stream(
                modelId=model,
                body=json.dumps(request_body),
            )

            print("✅ Bedrock request successful")

            # Process the streaming response
            stream = response["body"]
            for event in stream:
                if "chunk" in event:
                    chunk = event["chunk"]
                    if "bytes" in chunk:
                        try:
                            chunk_data = json.loads(chunk["bytes"].decode())
                            event_type = chunk_data.get("type", "unknown")

                            if event_type == "content_block_delta":
                                delta = chunk_data.get("delta", {})
                                if delta.get("type") == "text_delta":
                                    yield {
                                        "type": "content_block_delta",
                                        "delta": {
                                            "type": "text_delta",
                                            "text": delta.get("text", ""),
                                        },
                                    }
                            elif event_type == "content_block_start":
                                content_block = chunk_data.get("content_block", {})
                                if content_block.get("type") == "tool_use":
                                    yield {
                                        "type": "content_block_start",
                                        "content_block": content_block,
                                    }
                            elif event_type == "message_stop":
                                yield {
                                    "type": "message_stop",
                                }
                                break

                        except json.JSONDecodeError as e:
                            print(f"JSON decode error: {e}")
                            continue
                        except Exception as e:
                            print(f"Error processing chunk: {e}")
                            continue

        except Exception as e:
            print(f"❌ Bedrock request failed: {e}")
            import traceback

            traceback.print_exc()
            raise
