"""
Ollama API server emulator that routes requests to Claude via the Anthropic API.
This server mimics the Ollama API endpoints but uses Claude for inference.
"""
import base64
import datetime
import json
import math
import os
import queue
import random
import re
import shutil
import subprocess
import sys
import tempfile
import threading
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

import image_tool_result
import video_tool_result
from core.config import MCP_SERVERS_FILE

# This file prints emoji/non-ASCII text throughout (status markers like
# "🔧"/"⚠️"). On Windows, a console attached with a non-UTF-8 codepage
# (cp1252 is the common default) makes those prints raise
# UnicodeEncodeError -- confirmed to crash request handling entirely: a
# bare `print()` inside do_POST's request path raises there, propagates
# up as a 500, and depending on exactly where it happens can leave a
# streaming response's connection open with no further bytes ever sent,
# which then surfaces client-side as a read timeout rather than a clean
# error. Reconfiguring here fixes every print call in this module at
# once rather than hardening each call site individually.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass

SYSTEM_PROMPT = """
You are a highly skilled software engineer with extensive knowledge in many programming languages, frameworks, design patterns, and best practices. You are also an eloquent and professional writer who communicates clearly and effectively.

## Communication

### Tone and Voice

1. Be conversational but professional. Use a friendly tone while maintaining technical accuracy in your explanations.

2. Refer to the user in the second person ("you") and yourself in the first person ("I"). Maintain this consistent voice throughout all interactions.

3. NEVER lie or make things up. If you don't know something, clearly state that rather than providing incorrect information.

4. Refrain from apologizing when results are unexpected. Instead, focus on proceeding with solutions or explaining the circumstances clearly.

### Formatting

5. Format responses in markdown for readability. Use backticks to format `file`, `directory`, `function`, and `class` names when referencing code elements.

6. Prefer prose to bullets; use tables sparingly. When lists are appropriate, prefer bullets to tables.

7. Avoid em-dashes (—) and en-dashes (–). Use commas, semicolons, or rewrite the sentence instead. Dashes are a recognisable marker of LLM-generated text.

8. Always start responses with a literal newline character (a blank line) for consistent client display formatting.

### Tool Usage

9. Do not attempt to predict, fabricate, or simulate tool call outputs. Wait for actual results before proceeding. This is about not inventing results, not about pacing: when multiple tool calls do not depend on each other's results (e.g., reading several files, or running several independent searches), issue them together in the same turn instead of one at a time. Only serialize calls when a later one genuinely needs information a prior one's result would provide.

10. Do not reply in the chat with code unless explicitly told to do so. Use tools to write, read, and modify code instead.

11. When asked to modify a file, modify it in place. Do not create new files with suffixes like `_modified` or `_fixed` (e.g., if asked to modify `config.txt`, do not create `config_modified.txt`) unless explicitly requested.

12. Before answering questions, use tools to verify information rather than relying on assumptions or memory.

13. If you are asked for a coding task and given a directory, always look for `AGENTS.md` or `CLAUDE.md` in that directory tree to get context about the repository. Read that file and understand it carefully before proceeding with the task.

14. Make liberal use of inline images when they improve the answer. After calling `internal_view_image`, show the image in your answer by default, not only in the collapsed tool-result fold, using `![descriptive caption](alpaca://image/<tool_call_id>)` with the *exact, full* `id` string from that call, copied verbatim character-for-character (e.g. `internal_view_image_12`) — never shorten, renumber, or invent a simpler-looking id like `1` or `img1`; anything other than the real id silently fails to resolve. Show every useful screenshot, chart, diagram, or other visual you inspected when it supports the explanation or verification; omit it only when it would be redundant or irrelevant. You may place multiple images throughout the prose. An image reference resolves only within the same answer as its tool call, so emit it in that answer and never reference an image call from an earlier turn.

15. To show a recorded feature demonstration, create an MP4, WebM, or Ogg file and call `internal_view_video` with its local path. To also show a player inline in your answer, write `[caption](alpaca://video/<tool_call_id>)` using that call's *exact, full* `id`, copied verbatim the same way as image references above — never shorten, renumber, or invent one. Video bytes are loaded by the UI and are never added to your context.
"""

MODELS_JSON: str = """
{
  "models": [
    {
      "name": "us.anthropic.claude-opus-4-5-20251101-v1:0",
      "modified_at": "2025-01-08T14:56:49.277302595-07:00",
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
      "name": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
      "modified_at": "2025-01-08T14:56:49.277302595-07:00",
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
      "name": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
      "modified_at": "2025-01-08T14:56:49.277302595-07:00",
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
      "name": "us.anthropic.claude-opus-4-1-20250805-v1:0",
      "modified_at": "2025-01-08T14:56:49.277302595-07:00",
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
      "name": "us.anthropic.claude-sonnet-4-20250514-v1:0",
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
      "name": "us.anthropic.claude-3-7-sonnet-20250219-v1:0",
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
      "name": "us.anthropic.claude-3-5-sonnet-20241022-v2:0",
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
    },
    {
      "name": "kimi-k2p5",
      "modified_at": "2025-07-01T00:00:00.000000000+00:00",
      "size": 0,
      "digest": "0000000000000000000000000000000000000000000000000000000000000000",
      "details": {
        "format": "gguf",
        "family": "kimi",
        "families": null,
        "parameter_size": "1T",
        "quantization_level": "none"
      }
    },
    {
      "name": "kimi-k2p6",
      "modified_at": "2026-01-01T00:00:00.000000000+00:00",
      "size": 0,
      "digest": "0000000000000000000000000000000000000000000000000000000000000000",
      "details": {
        "format": "gguf",
        "family": "kimi",
        "families": null,
        "parameter_size": "1T",
        "quantization_level": "none"
      }
    },
    {
      "name": "kimi-k2p7-code",
      "modified_at": "2026-06-15T00:00:00.000000000+00:00",
      "size": 0,
      "digest": "0000000000000000000000000000000000000000000000000000000000000000",
      "details": {
        "format": "gguf",
        "family": "kimi",
        "families": null,
        "parameter_size": "1T",
        "quantization_level": "none"
      }
    },
    {
      "name": "kimi-k3",
      "modified_at": "2026-07-19T00:00:00.000000000+00:00",
      "size": 0,
      "digest": "0000000000000000000000000000000000000000000000000000000000000000",
      "details": {
        "format": "gguf",
        "family": "kimi",
        "families": null,
        "parameter_size": "2.8T",
        "quantization_level": "none"
      }
    },
        {
      "name": "claude-opus-4-6",
      "modified_at": "2026-01-01T00:00:00.000000000+00:00",
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
      "name": "claude-sonnet-4-6",
      "modified_at": "2026-01-01T00:00:00.000000000+00:00",
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
      "name": "claude-opus-4-7",
      "modified_at": "2026-01-01T00:00:00.000000000+00:00",
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
      "name": "glm-5p1",
      "modified_at": "2025-07-01T00:00:00.000000000+00:00",
      "size": 0,
      "digest": "0000000000000000000000000000000000000000000000000000000000000000",
      "details": {
        "format": "gguf",
        "family": "kimi",
        "families": null,
        "parameter_size": "1T",
        "quantization_level": "none"
      }
    },
    {
      "name": "glm-5p2",
      "modified_at": "2026-06-19T00:00:00.000000000+00:00",
      "size": 0,
      "digest": "0000000000000000000000000000000000000000000000000000000000000000",
      "details": {
        "format": "gguf",
        "family": "kimi",
        "families": null,
        "parameter_size": "1T",
        "quantization_level": "none"
      }
    },
    {
      "name": "qwen3.6:27b",
      "modified_at": "2026-01-01T00:00:00.000000000+00:00",
      "size": 0,
      "digest": "0000000000000000000000000000000000000000000000000000000000000000",
      "details": {
        "format": "gguf",
        "family": "qwen",
        "families": null,
        "parameter_size": "27B",
        "quantization_level": "none"
      }
    },
    {
      "name": "qwen3.6:35b",
      "modified_at": "2026-01-01T00:00:00.000000000+00:00",
      "size": 0,
      "digest": "0000000000000000000000000000000000000000000000000000000000000000",
      "details": {
        "format": "gguf",
        "family": "qwen",
        "families": null,
        "parameter_size": "35B",
        "quantization_level": "none"
      }
    },
    {
      "name": "claude-code/opus",
      "modified_at": "2026-08-13T00:00:00.000000000+00:00",
      "size": 0,
      "digest": "0000000000000000000000000000000000000000000000000000000000000000",
      "details": {
        "format": "gguf",
        "family": "claude-code-cli",
        "families": null,
        "parameter_size": "n/a",
        "quantization_level": "none"
      }
    },
    {
      "name": "claude-code/sonnet",
      "modified_at": "2026-08-13T00:00:00.000000000+00:00",
      "size": 0,
      "digest": "0000000000000000000000000000000000000000000000000000000000000000",
      "details": {
        "format": "gguf",
        "family": "claude-code-cli",
        "families": null,
        "parameter_size": "n/a",
        "quantization_level": "none"
      }
    },
    {
      "name": "claude-code/haiku",
      "modified_at": "2026-08-13T00:00:00.000000000+00:00",
      "size": 0,
      "digest": "0000000000000000000000000000000000000000000000000000000000000000",
      "details": {
        "format": "gguf",
        "family": "claude-code-cli",
        "families": null,
        "parameter_size": "n/a",
        "quantization_level": "none"
      }
    },
    {
      "name": "codex/gpt-5.6-sol",
      "modified_at": "2026-08-13T00:00:00.000000000+00:00",
      "size": 0,
      "digest": "0000000000000000000000000000000000000000000000000000000000000000",
      "details": {
        "format": "gguf",
        "family": "codex-cli",
        "families": null,
        "parameter_size": "n/a",
        "quantization_level": "none"
      }
    },
    {
      "name": "codex/gpt-5.6-terra",
      "modified_at": "2026-08-13T00:00:00.000000000+00:00",
      "size": 0,
      "digest": "0000000000000000000000000000000000000000000000000000000000000000",
      "details": {
        "format": "gguf",
        "family": "codex-cli",
        "families": null,
        "parameter_size": "n/a",
        "quantization_level": "none"
      }
    },
    {
      "name": "codex/gpt-5.6-luna",
      "modified_at": "2026-08-13T00:00:00.000000000+00:00",
      "size": 0,
      "digest": "0000000000000000000000000000000000000000000000000000000000000000",
      "details": {
        "format": "gguf",
        "family": "codex-cli",
        "families": null,
        "parameter_size": "n/a",
        "quantization_level": "none"
      }
    }

  ]
}
"""


MODELS_SHOW: dict[str, Any] = {
    "modelfile": "",
    "parameters": "",
    "template": "{{ if .System }}<|start_header_id|>system<|end_header_id|>\n\n{{ .System }}<|eot_id|>{{ end }}{{ if .Prompt }}<|start_header_id|>user<|end_header_id|>\n\n{{ .Prompt }}<|eot_id|>{{ end }}<|start_header_id|>assistant<|end_header_id|>\n\n{{ .Response }}<|eot_id|>",
    "details": {
        "parent_model": "",
        "format": "gguf",
        "family": "llama",
        "families": ["llama"],
        "parameter_size": "8.0B",
        "quantization_level": "Q4_0",
    },
    "model_info": {
        "general.architecture": "llama",
        "general.file_type": 2,
        "general.parameter_count": 8030261248,
        "general.quantization_version": 2,
        "llama.attention.head_count": 32,
        "llama.attention.head_count_kv": 8,
        "llama.attention.layer_norm_rms_epsilon": 1e-05,
        "llama.block_count": 32,
        "llama.context_length": 8192,
        "llama.embedding_length": 4096,
        "llama.feed_forward_length": 14336,
        "llama.rope.dimension_count": 128,
        "llama.rope.freq_base": 500000,
        "llama.vocab_size": 128256,
        "tokenizer.ggml.bos_token_id": 128000,
        "tokenizer.ggml.eos_token_id": 128009,
        "tokenizer.ggml.merges": [],
        "tokenizer.ggml.model": "gpt2",
        "tokenizer.ggml.pre": "llama-bpe",
        "tokenizer.ggml.token_type": [],
        "tokenizer.ggml.tokens": [],
    },
    "capabilities": ["completion", "vision"],
}


# Default model to use when an unknown model is requested
DEFAULT_MODEL = "claude-sonnet-4-6"


def map_ollama_to_model(ollama_model: str) -> str:
    """Map Ollama model names to Anthropic API model IDs.

    This function handles various model name formats that clients might send,
    including the full Bedrock-style names (us.anthropic.*-v1:0) and maps them
    to the actual Anthropic API model identifiers.

    Args:
        ollama_model: The model name from the Ollama API request

    Returns:
        The Anthropic API model ID to use for inference
    """
    model_mapping = {
        # Claude 4.7 family
        "claude-opus-4-7": "claude-opus-4-7",
        # Claude 4.6 family
        "claude-opus-4-6": "claude-opus-4-6",
        "claude-sonnet-4-6": "claude-sonnet-4-6",
        # Claude 4.5 family
        "us.anthropic.claude-opus-4-5-20251101-v1:0": "claude-opus-4-5-20251101",
        "us.anthropic.claude-sonnet-4-5-20250929-v1:0": "claude-sonnet-4-5-20250929",
        "us.anthropic.claude-haiku-4-5-20251001-v1:0": "claude-haiku-4-5-20251001",
        # Claude 4.1 family
        "us.anthropic.claude-opus-4-1-20250805-v1:0": "claude-opus-4-1-20250805",
        "anthropic.claude-opus-4-1-20250805-v1:0": "claude-opus-4-1-20250805",
        # Claude 4.0 family (retired by Anthropic — fall back to current default)
        "us.anthropic.claude-sonnet-4-20250514-v1:0": DEFAULT_MODEL,
        # Claude 3.7 family
        "us.anthropic.claude-3-7-sonnet-20250219-v1:0": "claude-3-7-sonnet-20250219",
        # Claude 3.5 family
        "us.anthropic.claude-3-5-sonnet-20241022-v2:0": "claude-3-5-sonnet-20241022",
        # Local models (192.168.0.125:11434)
        "qwen3.6:27b": "qwen3.6:27b",
        "qwen3.6:35b": "qwen3.6:35b",
        # Fireworks models
        "kimi-k2p5": "accounts/fireworks/models/kimi-k2p5",
        "kimi-k2p6": "accounts/fireworks/models/kimi-k2p6",
        "kimi-k2p7-code": "accounts/fireworks/models/kimi-k2p7-code",
        "kimi-k3": "accounts/fireworks/models/kimi-k3",
        "glm-5p1": "accounts/fireworks/models/glm-5p1",
        "glm-5p2": "accounts/fireworks/models/glm-5p2",
        # Claude Code / Codex CLI backends (subscription usage, not API-key
        # billed — see ClaudeCodeCLIClient/CodexCLIClient)
        "claude-code/opus": "claude-code/opus",
        "claude-code/sonnet": "claude-code/sonnet",
        "claude-code/haiku": "claude-code/haiku",
        "codex/gpt-5.6-sol": "codex/gpt-5.6-sol",
        "codex/gpt-5.6-terra": "codex/gpt-5.6-terra",
        "codex/gpt-5.6-luna": "codex/gpt-5.6-luna",
        # Legacy placeholder mappings (for backwards compatibility)
        "codellama:13b": DEFAULT_MODEL,
        "llama3:latest": DEFAULT_MODEL,
    }
    if ollama_model.startswith("claude-code/") or ollama_model.startswith("codex/"):
        return ollama_model
    return model_mapping.get(ollama_model, DEFAULT_MODEL)


def _detect_image_format(b64_data: str) -> str | None:
    """Detect image MIME type from the first bytes of base64-encoded image data."""
    try:
        # Need ~24 base64 chars to decode 18 bytes; add padding to be safe
        sample = b64_data[:24]
        padding = "=" * ((4 - len(sample) % 4) % 4)
        header = base64.b64decode(sample + padding)[:18]
        if header[:3] == b"\xff\xd8\xff":
            return "image/jpeg"
        if header[:8] == b"\x89PNG\r\n\x1a\n":
            return "image/png"
        if header[:6] in (b"GIF87a", b"GIF89a"):
            return "image/gif"
        if header[:4] == b"RIFF" and header[8:12] == b"WEBP":
            return "image/webp"
    except Exception:
        pass
    return None


def _build_anthropic_messages(
    messages: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Transform an Ollama-style message list into Anthropic API format.

    Handles tool_use_call, tool_result, user-with-images, and plain messages
    in a single pass without mutating the input.

    Returns:
        (anthropic_messages, pre_stream_errors) — errors is a list of
        human-readable strings for images whose format could not be detected.
    """
    pre_stream_errors: list[str] = []
    anthropic_messages: list[dict[str, Any]] = []
    for item in messages:
        role = item.get("role", "")
        wants_cache = bool(item.get("cache_control"))

        if role == "tool_use_call":
            tool_use_block: dict[str, Any] = {
                "type": "tool_use",
                "id": item["call"]["id"],
                "name": item["call"]["name"],
                "input": item["call"]["arguments"],
            }
            if wants_cache:
                tool_use_block["cache_control"] = {"type": "ephemeral"}
            anthropic_messages.append(
                {"role": "assistant", "content": [tool_use_block]},
            )

        elif role == "tool_result":
            result_text = item.get("content", "")
            image_result = image_tool_result.parse_image_result(result_text)
            if image_result is not None:
                mime_type, img_b64, description = image_result
                result_content: list[dict[str, Any]] = [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": mime_type,
                            "data": img_b64,
                        },
                    },
                    {"type": "text", "text": description},
                ]
            else:
                video_result = video_tool_result.parse_video_result(result_text)
                if video_result is not None:
                    _mime_type, _locator, _size, description = video_result
                    # Video providers do not accept video content blocks here,
                    # and retaining the locator is useless context. The player
                    # fetches bytes independently through the UI bridge.
                    result_text = description
                result_content = [{"type": "text", "text": result_text}]
            tool_result_block: dict[str, Any] = {
                "type": "tool_result",
                "tool_use_id": item["id"],
                "content": result_content,
            }
            if wants_cache:
                tool_result_block["cache_control"] = {"type": "ephemeral"}
            anthropic_messages.append(
                {"role": "user", "content": [tool_result_block]},
            )

        elif role == "user" and "images" in item:
            # Transform Ollama-style images into Claude multimodal content blocks.
            raw_images = item["images"]
            image_blocks: list[dict[str, Any]] = []
            for idx, img_b64 in enumerate(raw_images):
                mime = _detect_image_format(img_b64)
                if mime is None:
                    try:
                        sample = img_b64[:24]
                        padding = "=" * ((4 - len(sample) % 4) % 4)
                        magic = base64.b64decode(sample + padding)[:6]
                        magic_hex = " ".join(f"{b:02X}" for b in magic)
                    except Exception:
                        magic_hex = "unknown"
                    pre_stream_errors.append(
                        f"Image {idx + 1}: Unknown image format"
                        f" (magic bytes: {magic_hex})",
                    )
                    continue
                image_blocks.append(
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": mime,
                            "data": img_b64,
                        },
                    },
                )
            text_content = item.get("content", "")
            anthropic_messages.append(
                {
                    "role": "user",
                    "content": image_blocks + [{"type": "text", "text": text_content}],
                },
            )

        else:
            # Plain text message (user or assistant).
            content = item.get("content", "")
            if wants_cache:
                anthropic_messages.append(
                    {
                        "role": role,
                        "content": [
                            {
                                "type": "text",
                                "text": content,
                                "cache_control": {"type": "ephemeral"},
                            },
                        ],
                    },
                )
            else:
                anthropic_messages.append({"role": role, "content": content})

    return anthropic_messages, pre_stream_errors


class OllamaRequestHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the Ollama API emulator."""

    def _wrap_tool_call(self, tool_call_data: dict, token: str | None) -> str:
        """Wrap a tool call with security tags if token is provided."""
        json_str = json.dumps(tool_call_data, indent=2)
        if token:
            return f"\n<tool:{token}>\n{json_str}\n</tool:{token}>\n"
        else:
            return f"\n{json_str}\n"

    def do_GET(self) -> None:
        """Handle GET requests."""
        if self.path == "/api/tags":
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            self.wfile.write(MODELS_JSON.encode())
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

    def _send_cli_tool_event(self, event: dict[str, Any]) -> None:
        """Forward a CLI-owned media call without asking Alpaca to execute it."""
        response = {
            "model": "codellama:13b",
            "message": {"role": "assistant", "content": ""},
            "done": False,
            "cli_tool_event": event,
        }
        self.wfile.write(json.dumps(response).encode())
        self.wfile.write(b"\n")
        self.wfile.flush()

    def _send_completion_chunk(
        self,
        count: int,
        stop_reason: str = "stop",
        tool_calls: list[Any] | None = None,
        invocation_metrics: dict | None = None,
        error: str | None = None,
    ):
        """Send the final completion chunk."""
        now = datetime.datetime.now(datetime.UTC).astimezone()
        local_timestamp = self._format_timestamp(now)

        message: dict[str, Any] = {
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

        if invocation_metrics:
            response["invocation_metrics"] = invocation_metrics
        if error is not None:
            response["error"] = error

        self.wfile.write(json.dumps(response).encode())
        self.wfile.write(b"\n")
        self.wfile.flush()

    def _format_timestamp(self, dt: datetime.datetime) -> str:
        """Format timestamp in the expected format."""
        local_timestamp = dt.strftime("%Y-%m-%dT%H:%M:%S.%f")
        local_timestamp = local_timestamp[:-3] + "000"
        local_timestamp += dt.strftime("%z")
        return local_timestamp[:-2] + ":" + local_timestamp[-2:]

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

    def _process_stream(self, stream, tool_call_token: str | None = None):
        """Process streaming response."""
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.end_headers()

        response_body = ""
        count = 0
        stop_reason = "stop"
        in_tool = False
        current_tool_id = ""
        current_tool_name = ""
        tool_arguments_buffer = ""
        input_tokens: int | None = None
        output_tokens: int | None = None
        cached_tokens: int = 0
        start_time = time.time()
        stream_error: Exception | None = None

        def _events_tolerating_mid_stream_failure():
            """Stop iteration cleanly on a mid-stream error instead of

            propagating it — a network hiccup, malformed event, or the
            provider dropping the connection must still fall through to
            the token-accounting code below. Everything generated before
            the failure was already billed; losing it from our own count
            entirely (the previous behavior) made the discrepancy between
            what we report and what the provider actually bills far worse
            than a partial/estimated count does.
            """
            nonlocal stream_error
            try:
                yield from stream
            except Exception as e:
                stream_error = e
                print(f"⚠️ Stream processing error after {count} chunks: {e}")

        if stream:
            for event in _events_tolerating_mid_stream_failure():
                print(event)
                val: dict[str, Any] = event

                if val.get("type") == "alpaca_tool_event":
                    self._send_cli_tool_event(val)

                elif val.get("type") == "cli_heartbeat":
                    # Keeps the local socket alive through long silent CLI
                    # tool calls (see _run_cli_jsonl) — an empty chunk is a
                    # no-op for every consumer of this stream.
                    self._send_text_chunk("", count)

                elif val.get("type") == "message_start":
                    usage = val.get("message", {}).get("usage", {})
                    # Anthropic splits input across three fields when prompt caching
                    # is active; sum them for the true total.  Fireworks may send 0
                    # for input_tokens and put the real count in the cache fields.
                    _it = usage.get("input_tokens") or 0
                    _cw = usage.get("cache_creation_input_tokens") or 0
                    _cr = usage.get("cache_read_input_tokens") or 0
                    _total = _it + _cw + _cr
                    input_tokens = _total if _total > 0 else None
                    # cache_read is billed at ~10% of input price, cache_creation
                    # at ~1.25x — both are still "cached" in the sense that the
                    # UI cares about: not fresh full-price input tokens.
                    cached_tokens = _cw + _cr

                elif (
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
                    # Store tool info for later
                    current_tool_name = tool_block.get("name", "")
                    current_tool_id = tool_block.get("id", "")
                    tool_arguments_buffer = ""
                    in_tool = True
                    print(
                        f"🔧 Tool use started: {current_tool_name} (id: {current_tool_id})",
                    )
                    count += 1

                elif (
                    val.get("type") == "content_block_delta"
                    and "delta" in val
                    and ("partial_json" in val["delta"])
                ):
                    # Accumulate tool arguments
                    args_chunk = val["delta"]["partial_json"]

                    if in_tool:
                        tool_arguments_buffer += args_chunk
                    else:
                        self._send_text_chunk(args_chunk, count)
                    count += 1

                elif val.get("type") == "content_block_stop":
                    # Send tool call end as text
                    if in_tool:
                        # Parse accumulated arguments
                        try:
                            if tool_arguments_buffer.strip():
                                arguments = json.loads(tool_arguments_buffer)
                            else:
                                arguments = {}
                        except json.JSONDecodeError as e:
                            print(f"⚠️ Failed to parse tool arguments: {e}")
                            arguments = {}

                        # Build the tool call structure
                        custom_tool_call = {
                            "tool_call": {
                                "name": current_tool_name,
                                "id": current_tool_id,
                                "arguments": arguments,
                            },
                        }

                        # Use _wrap_tool_call for secure tag-based detection
                        tool_call_text = self._wrap_tool_call(
                            custom_tool_call,
                            tool_call_token,
                        )
                        print(
                            f"🔧 Sending tool call (token: {'yes' if tool_call_token else 'no'}): {tool_call_text[:100]}...",
                        )
                        self._send_text_chunk(tool_call_text, count)
                        in_tool = False
                        current_tool_id = ""
                        current_tool_name = ""
                        tool_arguments_buffer = ""
                    else:
                        self._send_text_chunk("", count)
                    count += 1

                elif (
                    val.get("type") == "message_delta"
                    and "delta" in val
                    and ("stop_reason" in val["delta"])
                ):
                    stop_reason = val["delta"]["stop_reason"]
                    usage = val.get("usage", {})
                    # output tokens — prefer Anthropic name, fall back to OpenAI name
                    _out = usage.get("output_tokens") or usage.get("completion_tokens")
                    if _out is not None:
                        output_tokens = _out
                    # input tokens — sum all fields; Fireworks may only fill in the
                    # real value here (sending a placeholder 0 in message_start).
                    _it = usage.get("input_tokens") or 0
                    _cw = usage.get("cache_creation_input_tokens") or 0
                    _cr = usage.get("cache_read_input_tokens") or 0
                    _total = _it + _cw + _cr
                    if _total > 0:
                        input_tokens = _total
                        cached_tokens = _cw + _cr

        elapsed_ms = int((time.time() - start_time) * 1000)

        # output_tokens only ever gets set from the terminal message_delta
        # event above. If the stream ended — cleanly or via the exception
        # handled by _events_tolerating_mid_stream_failure — without one,
        # estimate it from what we actually received rather than reporting
        # nothing for this call. Silently dropping a call's accounting
        # entirely (the previous behavior: no metrics at all unless BOTH
        # values were cleanly present) is worse than an approximate count,
        # since the provider bills for it either way.
        estimated_output = False
        if output_tokens is None and response_body:
            output_tokens = math.ceil(len(response_body) / 4.0)
            estimated_output = True

        invocation_metrics: dict | None = None
        if input_tokens is not None or output_tokens is not None:
            invocation_metrics = {
                "input_token_count": input_tokens or 0,
                "cached_input_token_count": cached_tokens,
                "output_token_count": output_tokens or 0,
                "invocation_latency_ms": elapsed_ms,
            }
            if estimated_output or stream_error is not None:
                print(
                    f"⚠️ Partial/estimated invocation_metrics "
                    f"(estimated_output={estimated_output}, "
                    f"stream_error={stream_error!r}): {invocation_metrics}",
                )

        if stream_error is not None:
            stop_reason = "error"

        self._send_completion_chunk(
            count,
            stop_reason,
            invocation_metrics=invocation_metrics,
            error=str(stream_error) if stream_error is not None else None,
        )

    def do_POST(self) -> None:
        """Handle POST requests."""
        if self.path == "/api/show":
            # Handle model info requests
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            content_length = int(self.headers["Content-Length"])
            post_data = self.rfile.read(content_length)
            request_data = json.loads(post_data.decode("utf-8"))
            print(f"Show request: {request_data}")
            v = json.dumps(MODELS_SHOW)
            self.wfile.write(v.encode())
            self.wfile.write(b"\n")
            self.wfile.flush()
        elif self.path == "/api/chat":
            content_length = int(self.headers["Content-Length"])
            post_data = self.rfile.read(content_length)
            try:
                request_data = json.loads(post_data.decode("utf-8"))
                messages_raw: list[dict[str, Any]] = request_data.get("messages", [])
                tools = request_data.get("tools", [])
                requested_model = request_data.get(
                    "model",
                    DEFAULT_MODEL,
                )
                conversation_id = request_data.get("conversation_id")
                working_directory = request_data.get("working_directory")
                if not isinstance(working_directory, str):
                    working_directory = None

                # Extract tool call token from headers for secure tag-based detection
                tool_call_token = self.headers.get("X-Tool-Call-Token")
                if tool_call_token:
                    print(f"🔐 Received tool call token: {tool_call_token[:8]}...")
                else:
                    print(
                        "⚠️ No tool call token provided - tool calls will use legacy format",
                    )

                # Use the model mapping function to get the Anthropic API model ID
                model = map_ollama_to_model(requested_model)
                print(f"Requested model: {requested_model}")
                print(f"Mapped to Anthropic model: {model}")

                # Extract system prompt from request (Ollama API format)
                request_system = request_data.get("system")
                if request_system:
                    print(
                        f"Received system prompt from request ({len(request_system)} chars)",
                    )

                print(f"Received tools: {tools}")
                messages_out: list[dict[str, Any]] = []
                for _i, item in enumerate(messages_raw):
                    print(item)
                    # Pass through messages as-is to preserve complex content blocks
                    # (tool_use, tool_result, multi-modal, etc.)
                    if isinstance(item, dict):
                        messages_out.append(item)
                self._handle_request_with_tools(
                    messages_out,
                    tools,
                    model,
                    tool_call_token,
                    request_system,
                    conversation_id,
                    working_directory,
                )
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
        model: str = "claude-3-5-sonnet-20240620",
        tool_call_token: str | None = None,
        request_system: str | None = None,
        conversation_id: int | str | None = None,
        working_directory: str | None = None,
    ):
        """Handle requests with optional tools."""
        anthropic_tools = None
        tool_choice = None
        if tools:
            anthropic_tools = self._convert_tools_to_anthropic_format(tools)
            tool_choice = {"type": "auto"}

        # Build system blocks: SYSTEM_PROMPT is always cached as its own block so
        # the cache key is stable even when request_system varies per call.
        system_blocks: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            },
        ]
        if request_system:
            system_blocks.append({"type": "text", "text": request_system})

        backend = get_client_for_model(model)
        stream = backend.stream_complete(
            messages=messages,
            model=model,
            max_tokens=40000,
            system=system_blocks,
            tools=anthropic_tools,
            tool_choice=tool_choice,
            conversation_id=conversation_id,
            working_directory=working_directory,
        )
        self._process_stream(stream, tool_call_token)


class ClaudeClient:
    """Client for interacting with Anthropic's Claude API"""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = "https://api.anthropic.com/v1",
    ) -> None:
        """Initialize the Claude client with your API key"""
        self.api_key: str | None = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ValueError(
                "Anthropic API key is required. Set it directly or via ANTHROPIC_API_KEY environment variable.",
            )
        self.base_url: str = base_url
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
        }
        if system:
            payload["system"] = system
        response: requests.Response = requests.post(
            url,
            headers=self.headers,
            json=payload,
            timeout=120,
        )
        if response.status_code != 200:
            print(f"Error: {response.status_code}")
            print(response.text)
            response.raise_for_status()
        result: dict[str, Any] = response.json()
        return result

    def stream_complete(
        self,
        messages: list[dict[str, str]] = [],
        model: str = "claude-3-5-sonnet-20240620",
        max_tokens: int = 8192,
        temperature: float = 0.7,
        system: str | list[dict[str, Any]] | None = None,
        tools: list[dict] | None = None,
        tool_choice: dict | None = None,
        conversation_id: int | str | None = None,
        working_directory: str | None = None,
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
            conversation_id: Unused here — Anthropic's own cache is a
                distributed hash lookup with no replica affinity to pin
                (see FireworksClient.stream_complete, which does need it).
                Accepted for signature parity with FireworksClient since
                both are called interchangeably via get_client_for_model().

        Yields:
            Chunks of the response as they are received
        """
        url: str = f"{self.base_url}/messages"

        anthropic_messages, pre_stream_errors = _build_anthropic_messages(messages)

        if pre_stream_errors:
            error_text = "\n".join(pre_stream_errors) + "\n\n"
            yield {
                "type": "content_block_delta",
                "delta": {"text": error_text},
            }

        payload: dict[str, Any] = {
            "model": model,
            "messages": anthropic_messages,
            "max_tokens": max_tokens,
            "stream": True,
        }
        print(payload)
        if system:
            if isinstance(system, list):
                payload["system"] = system
            else:
                payload["system"] = [
                    {
                        "type": "text",
                        "text": system,
                        "cache_control": {"type": "ephemeral"},
                    },
                ]
        if tools:
            tools_with_cache = list(tools)
            tools_with_cache[-1] = {
                **tools_with_cache[-1],
                "cache_control": {"type": "ephemeral"},
            }
            payload["tools"] = tools_with_cache
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


class FireworksClient:
    """Client for Fireworks AI via its Anthropic-compatible Messages API."""

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or os.environ.get("FIREWORKS_API_KEY")
        if not self.api_key:
            raise ValueError(
                "Fireworks API key is required. Set it via FIREWORKS_API_KEY environment variable.",
            )
        self.base_url: str = "https://api.fireworks.ai/inference/v1"
        self.headers: dict[str, str] = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

    def complete(
        self,
        prompt: str,
        model: str = "accounts/fireworks/models/kimi-k2p5",
        max_tokens: int = 1024,
        temperature: float = 0.7,
        system: str | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}/messages"
        messages: list[dict[str, str]] = [{"role": "user", "content": prompt}]
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if system:
            payload["system"] = system
        response = requests.post(url, headers=self.headers, json=payload, timeout=120)
        if response.status_code != 200:
            print(f"Error: {response.status_code}")
            print(response.text)
            response.raise_for_status()
        result: dict[str, Any] = response.json()
        return result

    def stream_complete(
        self,
        messages: list[dict[str, str]] = [],
        model: str = "accounts/fireworks/models/kimi-k2p5",
        max_tokens: int = 8192,
        temperature: float = 0.7,
        system: str | list[dict[str, Any]] | None = None,
        tools: list[dict] | None = None,
        tool_choice: dict | None = None,
        conversation_id: int | str | None = None,
        working_directory: str | None = None,
    ) -> Generator[dict[str, Any], None, None]:
        url = f"{self.base_url}/messages"

        anthropic_messages, pre_stream_errors = _build_anthropic_messages(messages)

        if pre_stream_errors:
            error_text = "\n".join(pre_stream_errors) + "\n\n"
            yield {
                "type": "content_block_delta",
                "delta": {"text": error_text},
            }

        payload: dict[str, Any] = {
            "model": model,
            "messages": anthropic_messages,
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
        # Fireworks' prompt cache is local to whichever replica wrote it —
        # unlike Anthropic's own hash-keyed distributed cache, there's no
        # affinity by default, so a multi-call tool loop (each request
        # potentially hitting a different replica) gets near-zero cache
        # hits despite resending an otherwise-identical prefix. Pinning
        # all calls for one conversation to the same replica is what
        # makes the cache_control breakpoints upstream actually pay off.
        request_headers = self.headers
        if conversation_id is not None:
            request_headers = {
                **self.headers,
                "x-session-affinity": str(conversation_id),
            }
        response = requests.post(
            url,
            headers=request_headers,
            json=payload,
            stream=True,
        )
        if response.status_code != 200:
            print(f"Error: {response.status_code}")
            print(response.text)
            response.raise_for_status()
        for line in response.iter_lines():
            if not line:
                continue
            line_text = line.decode("utf-8")
            if not line_text.startswith("data:"):
                continue
            json_str = line_text[5:].lstrip()
            if json_str.strip() == "[DONE]":
                break
            try:
                yield json.loads(json_str)
            except json.JSONDecodeError:
                print(f"Failed to decode JSON: {json_str}")


def _flatten_transcript(messages: list[dict[str, Any]]) -> str:
    """Turn an Ollama-style message list into a plain-text transcript.

    Used for the CLI-backed clients, which take one prompt per turn
    rather than a structured message array. This intentionally discards
    tool_use_call/tool_result blocks — under the CLI backends, tool
    execution happens inside the CLI's own agent loop (see
    ClaudeCodeCLIClient/CodexCLIClient), not via messages this app sent,
    so any such blocks in history came from a different backend and
    aren't reproducible here. Best-effort str() rendering keeps those
    turns visible rather than dropping them silently.
    """
    lines: list[str] = []
    for item in messages:
        if not isinstance(item, dict):
            continue
        role = item.get("role", "user")
        content = item.get("content", "")
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict):
                    parts.append(block.get("text") or str(block))
                else:
                    parts.append(str(block))
            content = "\n".join(parts)
        label = "Assistant" if role == "assistant" else "User"
        lines.append(f"{label}: {content}")
    return "\n\n".join(lines)


def _cli_mcp_servers(
    media_event_path: str,
    working_directory: str | None,
) -> dict[str, dict[str, Any]]:
    """Return configured MCP servers plus Alpaca's CLI media bridge."""
    servers: dict[str, dict[str, Any]] = {}
    if os.path.exists(MCP_SERVERS_FILE):
        try:
            with open(MCP_SERVERS_FILE, encoding="utf-8") as file:
                raw = json.load(file)
            for name, spec in raw.items():
                if not isinstance(spec, dict):
                    continue
                command_list = spec.get("command", [])
                if not command_list:
                    continue
                servers[name] = {
                    "command": command_list[0],
                    "args": list(command_list[1:]) + list(spec.get("args", [])),
                }
                if isinstance(spec.get("env"), dict):
                    servers[name]["env"] = dict(spec["env"])
        except (OSError, json.JSONDecodeError) as error:
            print(f"[warn] Could not read {MCP_SERVERS_FILE} for CLI MCP: {error}")

    media_env = {"ALPACA_CLI_MEDIA_EVENTS": media_event_path}
    if working_directory:
        media_env["ALPACA_WORKSPACE"] = working_directory
    servers["alpaca-media"] = {
        "command": sys.executable,
        "args": [os.path.join(os.path.dirname(__file__), "cli_media_mcp_server.py")],
        "env": media_env,
    }

    # Unlike a raw mcp_servers.json entry (left as-is above, matching alpaca's
    # own flat-command format), this one must survive the CLI subprocess's
    # cwd being the *workspace*, not the repo -- a bare "python" or a
    # relative "surface_mcp_server.py" would only resolve by accident, the
    # same class of bug pack_daemon.py's _absolutize_mcp_config exists to
    # prevent for the non-CLI path. Always registered, same as alpaca-media:
    # a host with no display just gets "no display available" at call time.
    surface_script = os.path.join(os.path.dirname(__file__), "surface_mcp_server.py")
    if os.path.isfile(surface_script):
        servers["alpaca-surface"] = {
            "command": sys.executable,
            "args": [surface_script],
        }
    return servers


def _build_claude_mcp_config_file(
    media_event_path: str,
    working_directory: str | None,
) -> str:
    """Translate alpaca-assist's mcp_servers.json into the schema `claude
    --mcp-config` actually expects.

    alpaca's own format (read directly by mcp_manager.py) is a flat
    {name: {command: [...], args: [...]}} map. Claude Code's MCP config
    schema is {"mcpServers": {name: {"command": <string>, "args": [...]}}}
    — confirmed against a real `claude -p --mcp-config` run, which
    rejects alpaca's raw file with "mcpServers: Invalid input: expected
    record, received undefined". The built-in media bridge means this always
    returns a config path, even when mcp_servers.json is absent. The caller
    must delete the temporary file.
    """
    fd, path = tempfile.mkstemp(prefix="alpaca_claude_mcp_", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(
            {"mcpServers": _cli_mcp_servers(media_event_path, working_directory)},
            f,
        )
    return path


def _codex_mcp_overrides(
    media_event_path: str,
    working_directory: str | None,
) -> list[str]:
    """Build `-c mcp_servers.<name>...` overrides for `codex exec`.

    `codex exec -c key=value` splits `key` on literal dots itself rather
    than parsing it as TOML, so a JSON/TOML-quoted name segment (e.g.
    `"alpaca-media"`) doesn't get its quotes stripped the way a real TOML
    dotted-key parser would — they end up baked into the registered server
    name (confirmed via `codex mcp list --json`: a quoted override produces
    the literal name `"alpaca-media"`, quote characters included), which
    silently drops it from the tools offered to the model. TOML bare keys
    already allow `[A-Za-z0-9_-]`, which covers every server name this app
    produces (the built-in "alpaca-media" and user keys from
    mcp_servers.json), so names are passed unquoted; anything outside that
    set is skipped rather than emitted broken.
    """
    overrides: list[str] = []
    for name, spec in _cli_mcp_servers(media_event_path, working_directory).items():
        if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
            print(
                f"[warn] Skipping MCP server {name!r} for codex: name isn't a valid TOML bare key",
            )
            continue
        prefix = f"mcp_servers.{name}"
        overrides.extend(("-c", f"{prefix}.command={json.dumps(spec['command'])}"))
        overrides.extend(("-c", f"{prefix}.args={json.dumps(spec.get('args', []))}"))
        for env_name, value in spec.get("env", {}).items():
            overrides.extend(
                ("-c", f"{prefix}.env.{env_name}={json.dumps(value)}"),
            )
    return overrides


def _prepare_cli_images(
    messages: list[dict[str, Any]],
) -> tuple[str, str | None, list[str]]:
    """Materialize base64 attachments for CLIs, returning prompt/tempdir/paths."""
    temp_dir: str | None = None
    paths: list[str] = []
    prepared: list[dict[str, Any]] = []
    for message_index, message in enumerate(messages):
        item = dict(message)
        image_lines: list[str] = []
        for image_index, image in enumerate(item.get("images", [])):
            try:
                mime_type = _detect_image_format(image)
                if mime_type is None:
                    continue
                if temp_dir is None:
                    temp_dir = tempfile.mkdtemp(prefix="alpaca_cli_images_")
                extension = {
                    "image/jpeg": ".jpg",
                    "image/png": ".png",
                    "image/gif": ".gif",
                    "image/webp": ".webp",
                }[mime_type]
                path = os.path.join(
                    temp_dir,
                    f"message-{message_index + 1}-image-{image_index + 1}{extension}",
                )
                with open(path, "wb") as file:
                    file.write(base64.b64decode(image, validate=True))
                paths.append(path)
                image_lines.append(f"Attached image: {path}")
            except (KeyError, OSError, ValueError):
                continue
        if image_lines:
            item["content"] = f"{item.get('content', '')}\n\n" + "\n".join(image_lines)
        prepared.append(item)
    return _flatten_transcript(prepared), temp_dir, paths


# Well under StreamingHandler.STREAM_TIMEOUT's 120s read timeout
# (core/chat_tab_streaming.py) — CLI backends can go silent for minutes
# while a tool call (e.g. rendering several images/videos) runs with no
# stdout in between, unlike token-streaming models where the assumption
# "at least one byte per token" holds. A heartbeat this frequent keeps the
# local HTTP socket alive through those gaps without the client ever
# needing to know a CLI subprocess is involved.
_CLI_HEARTBEAT_INTERVAL_SECS = 20


def _run_cli_jsonl(
    cmd: list[str],
    prompt: str,
    working_directory: str | None = None,
    media_event_path: str | None = None,
) -> Generator[dict[str, Any], None, None]:
    """Run a CLI subprocess, feed it `prompt` on stdin, yield parsed JSONL stdout lines.

    stdout is read on a background thread so this can poll with a timeout
    and yield {"type": "cli_heartbeat"} during long silent gaps — a plain
    `for line in proc.stdout` blocks indefinitely and can't interleave
    anything while waiting on the next line.
    """
    # This server's own process always has ANTHROPIC_API_KEY set (required
    # for the raw-API ClaudeClient path — see ClaudeClient.__init__), and
    # subprocess.Popen inherits the full parent environment by default.
    # Left alone, `claude` sees that key and prefers it over the logged-in
    # subscription session ("claude.ai connectors are disabled because
    # ANTHROPIC_API_KEY ... takes precedence over your claude.ai login"),
    # silently billing every claude-code/* request to the metered API
    # instead of spending subscription usage — defeating the entire point
    # of this backend. Strip it for the child only.
    child_env = dict(os.environ)
    child_env.pop("ANTHROPIC_API_KEY", None)

    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        encoding="utf-8",
        cwd=working_directory,
        env=child_env,
    )
    try:
        assert proc.stdin is not None and proc.stdout is not None

        def _write_stdin() -> None:
            # Runs on its own thread so a large prompt (a long transcript
            # with embedded base64 image/video tool results can reach
            # multiple MB) can't block on a full OS pipe buffer before the
            # heartbeat loop below even starts — mirrors the standard
            # Popen.communicate() pattern for avoiding subprocess deadlock
            # between writer and reader. BrokenPipeError is expected if the
            # child exits before consuming all of stdin; the reader
            # thread's sentinel + proc.wait() below already surface that.
            assert proc.stdin is not None
            try:
                proc.stdin.write(prompt)
            except (BrokenPipeError, OSError):
                pass
            finally:
                try:
                    proc.stdin.close()
                except OSError:
                    pass

        threading.Thread(target=_write_stdin, daemon=True).start()
        event_offset = 0

        def read_media_events() -> list[dict[str, Any]]:
            nonlocal event_offset
            if not media_event_path:
                return []
            try:
                with open(media_event_path, encoding="utf-8") as event_file:
                    event_file.seek(event_offset)
                    events: list[dict[str, Any]] = []
                    while row := event_file.readline():
                        if row.strip():
                            events.append(json.loads(row))
                    event_offset = event_file.tell()
                    return events
            except (OSError, json.JSONDecodeError):
                return []

        line_queue: queue.Queue[str | None] = queue.Queue()

        def _read_stdout() -> None:
            # mypy can't carry the `proc.stdout is not None` assert above
            # into this closure (proc.stdout is typed as Optional on the
            # Popen object itself, not narrowed per-closure).
            assert proc.stdout is not None
            try:
                for raw_line in proc.stdout:
                    line_queue.put(raw_line)
            finally:
                line_queue.put(None)  # sentinel: stdout closed (process exiting)

        reader_thread = threading.Thread(target=_read_stdout, daemon=True)
        reader_thread.start()

        while True:
            try:
                raw_line = line_queue.get(timeout=_CLI_HEARTBEAT_INTERVAL_SECS)
            except queue.Empty:
                yield from read_media_events()
                yield {"type": "cli_heartbeat"}
                continue

            if raw_line is None:
                break

            yield from read_media_events()
            line = raw_line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                print(f"[warn] Non-JSON line from {cmd[0]}: {line[:200]}")
        yield from read_media_events()
    finally:
        stderr_output = proc.stderr.read() if proc.stderr else ""
        returncode = proc.wait()
        if returncode != 0:
            msg = f"[warn] {cmd[0]} exited {returncode}: {stderr_output[:2000]}"
            try:
                print(msg)
            except UnicodeEncodeError:
                # Windows consoles are often cp1252 — the CLI's own stderr
                # (e.g. claude's "⚠" advisory text) can contain characters
                # that can't encode there. Losing the warning entirely is
                # worse than a lossy fallback when diagnosing a failure.
                print(msg.encode("ascii", errors="replace").decode("ascii"))


class ClaudeCodeCLIClient:
    """Routes chat turns through the locally-installed `claude` CLI in
    headless mode, spending the logged-in Claude Code subscription's
    usage instead of a metered ANTHROPIC_API_KEY.

    Tool calls are NOT executed by this app for these models. `claude -p`
    receives the app's MCP config and runs its own agent loop. Ordinary CLI
    tool events remain hidden to avoid double execution; the built-in media
    MCP server emits a separate completed-call event so Alpaca can mirror its
    image/video result without executing it again.

    Runs with --dangerously-skip-permissions because headless mode has no
    TTY to answer an interactive permission prompt on — it would just
    hang. --setting-sources "" and --strict-mcp-config keep it isolated
    from whatever personal Claude Code config exists on this machine
    (skills, memory, other MCP servers), so the app's behavior doesn't
    depend on who's logged into `claude` on the host running this server.
    """

    def stream_complete(
        self,
        messages: list[dict[str, Any]] = [],
        model: str = "sonnet",
        max_tokens: int = 8192,
        temperature: float = 0.7,
        system: str | list[dict[str, Any]] | None = None,
        tools: list[dict] | None = None,
        tool_choice: dict | None = None,
        conversation_id: int | str | None = None,
        working_directory: str | None = None,
    ) -> Generator[dict[str, Any], None, None]:
        alias = model.split("/", 1)[1] if "/" in model else model

        system_text = SYSTEM_PROMPT
        if isinstance(system, list):
            system_text = "\n\n".join(
                b.get("text", "") for b in system if isinstance(b, dict)
            )
        elif isinstance(system, str):
            system_text = system

        prompt, image_temp_dir, image_paths = _prepare_cli_images(messages)
        event_fd, media_event_path = tempfile.mkstemp(
            prefix="alpaca_cli_media_",
            suffix=".jsonl",
        )
        os.close(event_fd)

        cmd = [
            "claude",
            "-p",
            "--output-format",
            "stream-json",
            "--include-partial-messages",
            "--verbose",
            "--model",
            alias,
            "--append-system-prompt",
            system_text,
            "--setting-sources",
            "",
            "--no-session-persistence",
            "--dangerously-skip-permissions",
        ]
        mcp_config_path = _build_claude_mcp_config_file(
            media_event_path,
            working_directory,
        )
        cmd += ["--strict-mcp-config", "--mcp-config", mcp_config_path]

        if image_paths:
            prompt += (
                "\n\nThe attached image paths above are user-provided inputs. "
                "Inspect them with your image-reading capability before answering."
            )

        text_block_indices: set[int] = set()

        try:
            for line in _run_cli_jsonl(
                cmd,
                prompt,
                working_directory,
                media_event_path=media_event_path,
            ):
                line_type = line.get("type")

                if line_type == "alpaca_tool_event":
                    yield line
                    continue

                if line_type == "cli_heartbeat":
                    yield line
                    continue

                if line_type == "result":
                    if line.get("is_error"):
                        yield {
                            "type": "content_block_delta",
                            "delta": {
                                "text": f"\n\n[claude CLI error: {line.get('result') or line.get('subtype')}]\n",
                            },
                        }
                    continue

                if line_type != "stream_event":
                    continue

                event = line.get("event", {})
                event_type = event.get("type")
                index = event.get("index")

                if event_type == "message_start":
                    yield event
                elif event_type == "content_block_start":
                    block_type = event.get("content_block", {}).get("type")
                    if block_type == "text":
                        text_block_indices.add(index)
                        yield event
                    # tool_use blocks: recorded as NOT text, silently
                    # consumed below — the CLI already executed them
                    # internally.
                elif event_type == "content_block_delta":
                    if index in text_block_indices and "text" in event.get(
                        "delta",
                        {},
                    ):
                        yield event
                elif event_type == "content_block_stop":
                    if index in text_block_indices:
                        yield event
                    text_block_indices.discard(index)
                elif event_type in ("message_delta", "message_stop"):
                    yield event
        finally:
            if mcp_config_path:
                try:
                    os.remove(mcp_config_path)
                except OSError:
                    pass
            try:
                os.remove(media_event_path)
            except OSError:
                pass
            if image_temp_dir:
                shutil.rmtree(image_temp_dir, ignore_errors=True)


class CodexCLIClient:
    """Routes chat turns through the locally-installed `codex` CLI in
    headless mode (`codex exec --json`), spending ChatGPT subscription
    usage instead of a metered API key.

    Verified against a real codex-cli 0.147.0 binary: plain text turns,
    and a real shell-tool round trip (item.completed/command_execution
    items get filtered out by _extract_codex_text; only
    item.completed/agent_message text surfaces), both confirmed by
    actually running the subprocess and inspecting the JSONL output —
    not just read off docs. --ask-for-approval doesn't exist on this
    version's `codex exec` (would have hard-failed every call) and
    --sandbox conflicts with --approve-for-me; both caught by running it.

    Codex has no per-invocation --mcp-config file flag, so this translates
    alpaca-assist's MCP config (plus the built-in media bridge) into repeated
    `-c mcp_servers.<name>...` overrides for each run.

    Same rationale as ClaudeCodeCLIClient for suppressing tool-call
    content: tool execution happens inside codex's own agent loop, not
    this app's tool executor.
    """

    def stream_complete(
        self,
        messages: list[dict[str, Any]] = [],
        model: str = "gpt-5.6-sol",
        max_tokens: int = 8192,
        temperature: float = 0.7,
        system: str | list[dict[str, Any]] | None = None,
        tools: list[dict] | None = None,
        tool_choice: dict | None = None,
        conversation_id: int | str | None = None,
        working_directory: str | None = None,
    ) -> Generator[dict[str, Any], None, None]:
        alias = model.split("/", 1)[1] if "/" in model else model
        prompt, image_temp_dir, image_paths = _prepare_cli_images(messages)
        event_fd, media_event_path = tempfile.mkstemp(
            prefix="alpaca_cli_media_",
            suffix=".jsonl",
        )
        os.close(event_fd)

        system_text = SYSTEM_PROMPT
        if isinstance(system, list):
            system_text = "\n\n".join(
                block.get("text", "") for block in system if isinstance(block, dict)
            )
        elif isinstance(system, str):
            system_text = system

        # No --ask-for-approval on `codex exec` in this CLI version (0.147.0)
        # — verified against `codex exec --help`, which has no such flag at
        # all. --approve-for-me auto-approves within its own workspace-write
        # sandbox rather than hanging waiting for a prompt that can never be
        # answered (no TTY in headless mode) — it's mutually exclusive with
        # an explicit --sandbox flag (verified: the CLI rejects combining
        # them). --dangerously-bypass-approvals-and-sandbox exists for a
        # stronger no-sandbox-at-all mode if wanted later; not used here
        # since codex's own docs call it "intended solely for externally
        # sandboxed environments."
        cmd = [
            "codex",
            "exec",
            "--json",
            "--approve-for-me",
            "--skip-git-repo-check",
            "-m",
            alias,
            "-c",
            f"developer_instructions={json.dumps(system_text)}",
        ]
        for image_path in image_paths:
            cmd.extend(("--image", image_path))
        cmd.extend(_codex_mcp_overrides(media_event_path, working_directory))

        yielded_start = False
        try:
            for line in _run_cli_jsonl(
                cmd,
                prompt,
                working_directory,
                media_event_path=media_event_path,
            ):
                if line.get("type") == "cli_heartbeat":
                    yield line
                    continue

                if not yielded_start:
                    yield {"type": "message_start", "message": {"usage": {}}}
                    yielded_start = True

                if line.get("type") == "alpaca_tool_event":
                    yield line
                    continue

                text = _extract_codex_text(line)
                if text:
                    yield {"type": "content_block_delta", "delta": {"text": text}}

                if line.get("type") == "error":
                    err = line.get("message") or line.get("error") or str(line)
                    yield {
                        "type": "content_block_delta",
                        "delta": {"text": f"\n\n[codex CLI error: {err}]\n"},
                    }

            yield {"type": "message_delta", "delta": {"stop_reason": "end_turn"}}
            yield {"type": "message_stop"}
        finally:
            try:
                os.remove(media_event_path)
            except OSError:
                pass
            if image_temp_dir:
                shutil.rmtree(image_temp_dir, ignore_errors=True)


def _extract_codex_text(line: dict[str, Any]) -> str | None:
    """Best-effort extraction of assistant text from a codex --json event.

    UNVERIFIED shape — see CodexCLIClient docstring.
    """
    item = line.get("item")
    if isinstance(item, dict) and item.get("type") in (
        "agent_message",
        "assistant_message",
    ):
        return item.get("text") or item.get("content") or item.get("message")
    return None


# Model IDs served by the local Anthropic-compatible endpoint at 192.168.0.125:11434.
LOCAL_MODELS: frozenset[str] = frozenset(["qwen3.6:27b", "qwen3.6:35b"])

# Module-level client instances — set in __main__ before the server starts.
anthropic_client: "ClaudeClient | None" = None
fireworks_client: "FireworksClient | None" = None
claude_code_cli_client: "ClaudeCodeCLIClient | None" = None
codex_cli_client: "CodexCLIClient | None" = None
local_client: "ClaudeClient | None" = None


def get_client_for_model(
    model_id: str,
) -> "ClaudeClient | FireworksClient | ClaudeCodeCLIClient | CodexCLIClient":
    """Return the appropriate backend client for the given model ID."""
    if model_id in LOCAL_MODELS:
        if local_client is None:
            raise RuntimeError(
                "Local model requested but local client is not initialized.",
            )
        return local_client
    if model_id.startswith("accounts/fireworks/"):
        if fireworks_client is None:
            raise RuntimeError(
                "Fireworks model requested but FIREWORKS_API_KEY is not set.",
            )
        return fireworks_client
    if model_id.startswith("claude-code/"):
        if claude_code_cli_client is None:
            raise RuntimeError(
                "claude-code model requested but the `claude` CLI was not found on PATH.",
            )
        return claude_code_cli_client
    if model_id.startswith("codex/"):
        if codex_cli_client is None:
            raise RuntimeError(
                "codex model requested but the `codex` CLI was not found on PATH.",
            )
        return codex_cli_client
    if anthropic_client is None:
        raise RuntimeError(
            "Anthropic model requested but ANTHROPIC_API_KEY is not set.",
        )
    return anthropic_client


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
    import shutil

    anthropic_client = ClaudeClient()

    try:
        fireworks_client = FireworksClient()
        print("Fireworks client initialized (kimi-k2p5 available)")
    except ValueError as e:
        print(f"Warning: {e} — kimi-k2p5 will not be available")

    if shutil.which("claude"):
        claude_code_cli_client = ClaudeCodeCLIClient()
        print("claude CLI found on PATH — claude-code/* models available")
    else:
        print(
            "Warning: `claude` not found on PATH — claude-code/* models will not be available",
        )

    if shutil.which("codex"):
        codex_cli_client = CodexCLIClient()
        print("codex CLI found on PATH — codex/* models available (UNVERIFIED backend)")
    else:
        print(
            "Warning: `codex` not found on PATH — codex/* models will not be available",
        )

    local_client = ClaudeClient(
        api_key="local",
        base_url="http://192.168.0.125:11434/v1",
    )
    print("Local client initialized (qwen3.6:27b available via 192.168.0.125:11434)")

    run_server()
