"""Tool execution: JSON parsing, MCP dispatch, and result waiting."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import time
from concurrent.futures import Future
from typing import Any
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from mcp_manager import MCPManager

WHITESPACE_PATTERN = re.compile(r"\s+")


def parse_tool_call_json(
    text: str,
    start_pos: int,
    end_pos: int,
) -> dict[str, Any] | None:
    """Parse tool call JSON from ``text[start_pos:end_pos]`` with multiple fallbacks.

    Three variants are tried in order:
    1. The raw slice as-is.
    2. Whitespace collapsed to single spaces (handles pretty-printed JSON).
    3. The collapsed variant with one ``}`` appended (recovers a common streaming
       truncation where the outermost closing brace is missing).

    Returns the parsed ``dict`` on success, or ``None`` if all variants fail.
    The returned dict is not validated — callers should check for the expected
    keys (e.g. ``"tool_call"``) themselves.
    """
    json_str = text[start_pos:end_pos]
    json_str_clean = WHITESPACE_PATTERN.sub(" ", json_str)
    json_variants = [json_str, json_str_clean]

    if json_str_clean.endswith("}") and json_str_clean.count(
        "{",
    ) > json_str_clean.count("}"):
        json_variants.append(json_str_clean + "}")
        logger.debug("Added missing closing brace to tool call JSON for execution")

    for variant in json_variants:
        try:
            parsed: dict[str, Any] | list[
                Any
            ] | str | int | float | bool | None = json.loads(variant)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            continue

    return None


class ToolExecutor:
    """Execute MCP tools from a worker thread and wait for their results.

    Stateless: no per-instance data.  Dependencies (mcp_manager, event_loop)
    are passed at call time so the caller decides how to source them.
    """

    MAX_WAIT_SECONDS: int = 300
    POLL_INTERVAL_SECONDS: float = 0.1

    def execute_mcp(
        self,
        server_name: str,
        actual_tool_name: str,
        arguments: dict[str, Any],
        mcp_manager: MCPManager | None,
        event_loop: asyncio.AbstractEventLoop | None,
        stop_flag: threading.Event | None = None,
    ) -> str:
        """Submit *actual_tool_name* to *mcp_manager* via *event_loop* and return the result."""
        if not (mcp_manager and event_loop):
            logger.error("execute_mcp called but mcp_manager or event_loop is None")
            return "MCP Manager not available"

        future = asyncio.run_coroutine_threadsafe(
            mcp_manager.call_tool(server_name, actual_tool_name, arguments),
            event_loop,
        )
        return self.wait_for_result(future, stop_flag=stop_flag)

    def wait_for_result(
        self,
        future: Future[Any],
        stop_flag: threading.Event | None = None,
    ) -> str:
        """Block until *future* resolves, times out, or raises, and return a string result.

        If *stop_flag* (a ``threading.Event``) is set while waiting, the future
        is cancelled and ``"Tool execution cancelled"`` is returned immediately.
        """
        if stop_flag and stop_flag.is_set():
            future.cancel()
            logger.info("Tool execution cancelled by stop flag (pre-check)")
            return "Tool execution cancelled"

        try:
            result = future.result(timeout=0.1)
            return (
                json.dumps(result, indent=2) if result else "Tool execution completed"
            )
        except TimeoutError:
            pass

        elapsed = 0.0
        while elapsed < self.MAX_WAIT_SECONDS:
            if stop_flag and stop_flag.is_set():
                future.cancel()
                logger.info("Tool execution cancelled by stop flag")
                return "Tool execution cancelled"
            if future.done():
                try:
                    result = future.result(timeout=0.01)
                    return (
                        json.dumps(result, indent=2)
                        if result
                        else "Tool execution completed"
                    )
                except Exception as e:
                    logger.error(f"Tool execution raised: {e}")
                    return f"Error executing tool: {str(e)}"
            time.sleep(self.POLL_INTERVAL_SECONDS)
            elapsed += self.POLL_INTERVAL_SECONDS

        future.cancel()
        logger.warning(f"Tool execution timed out after {self.MAX_WAIT_SECONDS}s")
        return f"Tool execution timed out after {self.MAX_WAIT_SECONDS} seconds"
