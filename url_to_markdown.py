"""
URL fetching and HTML to Markdown conversion using curl and LLM.
"""
import os
import shutil
import subprocess
from typing import Any
from typing import Optional
from typing import Tuple

# Check if curl is available
CURL_AVAILABLE = shutil.which("curl") is not None

# Cheap default for HTML-to-Markdown conversion — swap for another Fireworks
# model (e.g. accounts/fireworks/models/kimi-k2p5) if you'd rather use Kimi.
FIREWORKS_MARKDOWN_MODEL = "accounts/fireworks/models/glm-5p2"


def get_llm_client() -> tuple[Any, str]:
    """
    Initialize an LLM client using available methods.

    Prefers Fireworks (cheap GLM/Kimi models) when FIREWORKS_API_KEY is set,
    falling back to Claude via the Anthropic API and then Bedrock.

    Returns:
        Tuple of (client, model_name)

    Raises:
        ValueError: If no client could be initialized
    """
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    client: Any = None
    model: str = ""
    errors_tried: list[str] = []

    # Try 1: Fireworks (cheaper GLM/Kimi models)
    try:
        api_key = os.environ.get("FIREWORKS_API_KEY")
        if api_key:
            from anthropic_ollama_server import FireworksClient

            client = FireworksClient(api_key=api_key)
            model = FIREWORKS_MARKDOWN_MODEL
        else:
            errors_tried.append(
                "FIREWORKS_API_KEY not found in environment or .env file",
            )
    except Exception as e:
        errors_tried.append(f"Failed to initialize FireworksClient: {str(e)}")

    # Try 2: Claude via the Anthropic API
    if client is None:
        try:
            api_key = os.environ.get("ANTHROPIC_API_KEY")
            if api_key:
                from anthropic_ollama_server import (
                    ClaudeClient as AnthropicClaudeClient,
                )
                from anthropic_ollama_server import DEFAULT_MODEL

                client = AnthropicClaudeClient(api_key=api_key)
                model = DEFAULT_MODEL
            else:
                errors_tried.append(
                    "ANTHROPIC_API_KEY not found in environment or .env file",
                )
        except Exception as e:
            errors_tried.append(
                f"Failed to initialize anthropic_ollama_server with API_KEY: {str(e)}",
            )

    # Try 3: Fall back to bedrock_server
    if client is None:
        try:
            from bedrock_server import ClaudeClient as BedrockClaudeClient

            client = BedrockClaudeClient()
            model = "us.anthropic.claude-sonnet-4-20250514-v1:0"
        except Exception as e:
            errors_tried.append(f"Failed to initialize bedrock_server: {str(e)}")

    # If all attempts failed, raise error with details
    if client is None:
        error_msg = "Failed to initialize any LLM client. Attempts made:\n"
        for i, error in enumerate(errors_tried, 1):
            error_msg += f"{i}. {error}\n"
        raise ValueError(error_msg)

    return client, model


def fetch_url_with_curl(url: str, timeout: int = 30) -> str:
    """
    Fetch URL content using curl.

    Args:
        url: The URL to fetch
        timeout: Timeout in seconds (default: 30)

    Returns:
        The fetched content as a string

    Raises:
        RuntimeError: If curl is not available or returns empty/invalid content
        subprocess.TimeoutExpired: If the request times out
        subprocess.CalledProcessError: If curl fails
    """
    if not CURL_AVAILABLE:
        raise RuntimeError("curl is not installed or not in PATH")

    # Use binary mode to avoid encoding issues on Windows, then decode manually
    result = subprocess.run(
        ["curl", "-sL", "--max-time", str(timeout), url],
        capture_output=True,
        timeout=timeout + 5,  # Give subprocess a bit more time than curl
    )

    if result.returncode != 0:
        stderr_text = ""
        if result.stderr:
            try:
                stderr_text = result.stderr.decode("utf-8", errors="replace").strip()
            except Exception:
                stderr_text = str(result.stderr)
        error_msg = (
            stderr_text if stderr_text else f"curl exited with code {result.returncode}"
        )
        raise subprocess.CalledProcessError(result.returncode, "curl", stderr=error_msg)

    # Decode stdout with fallback for encoding issues
    if result.stdout is None or len(result.stdout) == 0:
        raise RuntimeError(
            f"curl returned empty content for {url}. "
            "The site may require JavaScript, block automated requests, "
            "or use redirects that curl cannot follow.",
        )

    # Try UTF-8 first, fall back to latin-1 (which accepts any byte sequence)
    try:
        content = result.stdout.decode("utf-8")
    except UnicodeDecodeError:
        content = result.stdout.decode("latin-1")

    if not content.strip():
        raise RuntimeError(
            f"curl returned empty content for {url}. "
            "The site may require JavaScript, block automated requests, "
            "or use redirects that curl cannot follow.",
        )

    return content


def convert_html_to_markdown(
    html_content: str,
    summarize_if_long: bool = True,
    max_content_length: int = 50000,
) -> str:
    """
    Convert HTML content to Markdown using an LLM.

    Args:
        html_content: The HTML content to convert
        summarize_if_long: Whether to summarize if content exceeds max_content_length
        max_content_length: Character threshold before summarization

    Returns:
        Markdown formatted content

    Raises:
        ValueError: If LLM client cannot be initialized
        Exception: If LLM call fails
    """
    client, model = get_llm_client()

    # Determine if we need to summarize
    needs_summary = summarize_if_long and len(html_content) > max_content_length

    # Truncate if extremely long to avoid token limits
    max_input_length = 100000
    if len(html_content) > max_input_length:
        html_content = (
            html_content[:max_input_length] + "\n\n[Content truncated due to length...]"
        )
        needs_summary = True

    # Build the prompt
    if needs_summary:
        prompt = f"""Convert the following HTML content to clean, readable Markdown.

Since the content is long, please also provide a concise summary at the beginning.

Format your response as:
## Summary
[A brief 2-4 sentence summary of the main content]

## Content
[The converted markdown content - you may abbreviate repetitive sections]

HTML content:
```html
{html_content}
```"""
    else:
        prompt = f"""Convert the following HTML content to clean, readable Markdown.

- Remove all HTML tags and convert to proper Markdown syntax
- Preserve the document structure (headings, lists, links, etc.)
- Remove navigation elements, ads, and boilerplate if present
- Focus on the main content

HTML content:
```html
{html_content}
```"""

    response = client.complete(
        prompt=prompt,
        model=model,
        max_tokens=4096,
        temperature=0.1,
    )

    # Some models (e.g. glm-5p2) emit a leading "thinking" block before the
    # actual answer, so content[0] isn't reliably the text block — collect
    # every text block instead of assuming position.
    text_blocks = [
        block["text"]
        for block in response.get("content", [])
        if block.get("type") == "text"
    ]
    return "\n".join(text_blocks).strip()


def fetch_url_as_markdown(
    url: str,
    summarize_if_long: bool = True,
    max_content_length: int = 50000,
    curl_timeout: int = 30,
) -> str:
    """
    Fetch a URL and convert its content to Markdown.

    This is the main entry point that combines fetching and conversion.

    Args:
        url: The URL to fetch
        summarize_if_long: Whether to summarize if content is too long
        max_content_length: Character threshold before summarization
        curl_timeout: Timeout for curl request in seconds

    Returns:
        Markdown formatted content

    Raises:
        RuntimeError: If curl is not available
        ValueError: If LLM client cannot be initialized
        subprocess.CalledProcessError: If curl fails
        Exception: If LLM call fails
    """
    # Fetch the URL
    html_content = fetch_url_with_curl(url, timeout=curl_timeout)

    # Convert to markdown
    markdown_content = convert_html_to_markdown(
        html_content,
        summarize_if_long=summarize_if_long,
        max_content_length=max_content_length,
    )

    return markdown_content
