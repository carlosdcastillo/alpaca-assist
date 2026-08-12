import logging
import queue
import sys
import time
from typing import Any
from typing import NamedTuple

logger = logging.getLogger(__name__)


def is_macos() -> bool:
    return sys.platform == "darwin"


class ContentUpdate(NamedTuple):
    answer_index: int
    content_chunk: str
    is_done: bool = False
    is_error: bool = False
    metrics: dict[
        str,
        Any,
    ] | None = None  # Optional field for invocation metrics from API
    is_tool_result: bool = False
    tool_id: str | None = None  # Tool ID for is_tool_result / is_tool_call updates
    is_tool_call: bool = False
    tool_call_json: str | None = None  # Full JSON body for is_tool_call updates
    is_progress_update: bool = (
        False  # Transient progress line; replace in widget, skip chat_state
    )
    is_display_only: bool = (
        False  # Permanent in widget/_content_buffer but NOT saved to chat_state.
        # Use for execution-indicator lines (🔧/✅) so they appear between folds
        # in the live session without corrupting the component ordering in
        # chat_state (which determines reload display order).
    )
    is_trim: bool = (
        False  # Signal the queue processor to call finalize_rendering() so the
        # display catches up to an in-place chat_state trim already performed
        # by the fabrication-correction loop in the streaming thread.
        # content_chunk must be "" and is_display_only must be True.
    )


def normalize_api_url(api_url: str) -> str:
    """Normalize API URL by removing trailing components.

    Removes trailing slashes and the /api/chat suffix to prepare the URL
    for appending the /api/chat endpoint.

    Args:
        api_url: Raw API URL from preferences.

    Returns:
        Normalized URL ready for /api/chat suffix.
    """
    # Strip trailing slashes
    api_url = api_url.rstrip("/")
    # Remove /api/chat if present
    if api_url.endswith("/api/chat"):
        api_url = api_url[:-9]
    # Final strip
    api_url = api_url.rstrip("/")
    return api_url


def put_content_update_with_retry(
    content_update_queue: queue.Queue[Any],
    update: ContentUpdate,
    max_retries: int = 3,
) -> None:
    """Put a content update on the queue with retry logic.

    Args:
        content_update_queue: The queue to put the update into.
        update: ContentUpdate to queue.
        max_retries: Maximum number of retry attempts.
    """
    for attempt in range(max_retries):
        try:
            content_update_queue.put(update, timeout=1.0)
            return
        except queue.Full:
            if attempt < max_retries - 1:
                time.sleep(0.1 * (attempt + 1))
            else:
                logger.error(f"Failed to queue update after {max_retries} attempts")
