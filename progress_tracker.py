"""Progress reporting and connection cleanup for tool execution."""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

import requests

from utils import ContentUpdate

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def format_start_message(tool_name: str) -> str:
    """Return the display-only header line shown when a tool starts."""
    return f"\n🔧 Executing tool: {tool_name}\n"


def format_progress_message(elapsed: float, connection_status: str = "") -> str:
    """Return an animated transient progress line.

    The prefix and dot pattern vary with *elapsed* time to give a visual
    sense of how long the tool has been running.  *connection_status* is
    appended verbatim (e.g. ``" 🔗"`` or ``" 🔗❌"``).

    Thresholds:
    - < 5 s  → ``🔧 Processing...``
    - < 15 s → ``⚙️ Working.... (Xs)``
    - < 30 s → ``🔄 Still processing..... (Xs)``
    - ≥ 30 s → ``⏳ Long-running task...... (Xs)``
    """
    if elapsed < 5:
        dots = "." * ((int(elapsed) % 3) + 1)
        return f"🔧 Processing{dots}{connection_status}\n"
    elif elapsed < 15:
        dots = "." * ((int(elapsed) % 4) + 1)
        return f"⚙️ Working{dots} ({elapsed:.0f}s){connection_status}\n"
    elif elapsed < 30:
        dots = "." * ((int(elapsed) % 5) + 1)
        return f"🔄 Still processing{dots} ({elapsed:.0f}s){connection_status}\n"
    else:
        dots = "." * ((int(elapsed) % 6) + 1)
        return f"⏳ Long-running task{dots} ({elapsed:.0f}s){connection_status}\n"


def format_completion_message(
    elapsed: float,
    success: bool,
    error_message: str | None = None,
) -> str:
    """Return a display-only completion or failure line.

    On success: ``✅ Tool completed (X.Xs)``
    On failure with message: ``❌ Tool failed (X.Xs): <error>``
    On failure without message: ``❌ Tool failed (X.Xs)``
    """
    if success:
        return f"✅ Tool completed ({elapsed:.1f}s)\n"
    elif error_message:
        return f"❌ Tool failed ({elapsed:.1f}s): {error_message}\n"
    else:
        return f"❌ Tool failed ({elapsed:.1f}s)\n"


def is_connection_alive(response: requests.Response | None) -> bool:
    """Return ``True`` if *response* appears to have an open connection.

    Returns ``False`` for ``None`` or a response whose raw socket is closed.
    Returns ``True`` when the status cannot be determined (assume alive).
    """
    if response is None:
        return False
    try:
        if hasattr(response, "raw") and response.raw.isclosed():
            return False
        return True
    except Exception:
        return True


# ---------------------------------------------------------------------------
# ToolProgressManager
# ---------------------------------------------------------------------------


class ToolProgressManager:
    """Manage progress reporting and connection cleanup during tool execution.

    Dependencies are injected at construction time so the class can be used
    and tested without a reference to ``ChatTabStreaming``.

    Typical usage::

        pm = ToolProgressManager(tool_name, answer_index, put_update_fn)
        pm.start(response)
        try:
            result = execute_something()
            pm.complete()
        except Exception as e:
            pm.error(str(e))
        finally:
            pm.cleanup()

    Public API (compatible with ``ConnectionAwareToolProgressManager``):
    ``start()``, ``complete()``, ``error()``, ``cleanup()``
    """

    _PROGRESS_INTERVAL: float = 2.0

    def __init__(
        self,
        tool_name: str,
        answer_index: int,
        put_update_fn: Callable[[ContentUpdate], bool],
    ) -> None:
        self.tool_name = tool_name
        self.answer_index = answer_index
        self._put_update = put_update_fn

        self.start_time: float | None = None
        self.active_response: requests.Response | None = None
        self.is_active: bool = False
        self._stop_event = threading.Event()
        self._progress_thread: threading.Thread | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def start(self, response: requests.Response | None = None) -> None:
        """Begin progress reporting; optionally track *response* for cleanup."""
        with self._lock:
            if self.is_active:
                return
            self.start_time = time.time()
            self.is_active = True
            self.active_response = response
            self._stop_event.clear()

        self._send(format_start_message(self.tool_name), display_only=True)

        self._progress_thread = threading.Thread(
            target=self._progress_worker,
            daemon=True,
        )
        self._progress_thread.start()

    def complete(self) -> None:
        """Stop progress, send completion message, and release the connection."""
        with self._lock:
            if not self.is_active:
                return
            self.is_active = False
            self._stop_event.set()
            elapsed = time.time() - self.start_time if self.start_time else 0.0

        self._send(format_completion_message(elapsed, success=True), display_only=True)
        self._join_progress_thread()
        self._close_response()

    def error(self, error_message: str) -> None:
        """Stop progress, send error message, and release the connection."""
        with self._lock:
            if not self.is_active:
                return
            self.is_active = False
            self._stop_event.set()
            elapsed = time.time() - self.start_time if self.start_time else 0.0

        self._send(
            format_completion_message(
                elapsed,
                success=False,
                error_message=error_message,
            ),
            display_only=True,
        )
        self._join_progress_thread()
        self._close_response()

    def cleanup(self) -> None:
        """Force-stop everything regardless of current state."""
        with self._lock:
            self.is_active = False
            self._stop_event.set()

        self._join_progress_thread()
        self._close_response()

    # ------------------------------------------------------------------ #
    # Private helpers                                                      #
    # ------------------------------------------------------------------ #

    def _send(
        self,
        message: str,
        replace_last: bool = False,
        display_only: bool = False,
    ) -> None:
        """Send *message* as a ContentUpdate to the UI queue.

        replace_last=True  → transient progress line (replaces previous in widget,
                              never stored in chat_state or _content_buffer).
        display_only=True  → permanent in _content_buffer but NOT stored in
                              chat_state.components (unused by this class; available
                              for callers that need a persistent but non-saved line).
        """
        try:
            update = ContentUpdate(
                answer_index=self.answer_index,
                content_chunk=message,
                is_done=False,
                is_error=False,
                is_progress_update=replace_last,
                is_display_only=display_only,
            )
            if not self._put_update(update):
                logger.warning(f"Failed to send progress update: {message.strip()}")
        except Exception as e:
            logger.error(f"Error sending progress update: {e}")

    def _join_progress_thread(self) -> None:
        """Wait up to 1 s for the progress thread to exit."""
        thread = self._progress_thread
        if thread and thread.is_alive():
            thread.join(timeout=1.0)

    def _close_response(self) -> None:
        """Consume remaining bytes and close the tracked response."""
        with self._lock:
            response = self.active_response
            self.active_response = None

        if response is None:
            return
        try:
            if hasattr(response, "raw"):
                remaining = response.raw.read(1024)
                if remaining:
                    logger.debug(
                        f"Consumed {len(remaining)} bytes before closing connection",
                    )
            response.close()
        except Exception as e:
            logger.error(f"Error closing connection: {e}")

    def _progress_worker(self) -> None:
        """Periodically send transient progress updates until stopped."""
        while not self._stop_event.wait(self._PROGRESS_INTERVAL):
            if not self.is_active:
                break

            elapsed = time.time() - self.start_time if self.start_time else 0.0
            response = (
                self.active_response
            )  # snapshot to avoid race with _close_response

            status = ""
            try:
                if response is not None:
                    status = " 🔗" if is_connection_alive(response) else " 🔗❌"
            except Exception:
                status = " 🔗❓"

            self._send(format_progress_message(elapsed, status), replace_last=True)
