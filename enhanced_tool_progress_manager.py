import threading
import time
from typing import Any
from typing import Dict
from typing import Optional

import requests

from utils import ContentUpdate


class ConnectionAwareToolProgressManager:
    """Enhanced ToolProgressManager that can manage HTTP connections during long tool execution."""

    def __init__(
        self,
        tool_name: str,
        chat_streaming: "ChatTabStreaming",
        connection_id: str | None = None,
    ):
        self.tool_name = tool_name
        self.chat_streaming = chat_streaming
        self.connection_id = connection_id
        self.start_time: float | None = None
        self.progress_thread: threading.Thread | None = None
        self.heartbeat_thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.is_active = False
        self.answer_index = 0
        self._lock = threading.Lock()

        # Connection management
        self.active_response: requests.Response | None = None
        self.heartbeat_interval = 30.0  # Send heartbeat every 30 seconds
        self.connection_timeout = 300.0  # 5 minutes

        # Get current answer index
        try:
            if hasattr(chat_streaming, "chat_state") and chat_streaming.chat_state:
                self.answer_index = len(chat_streaming.chat_state.answers) - 1
                if self.answer_index < 0:
                    self.answer_index = 0
        except:
            self.answer_index = 0

    def start(self, response: requests.Response | None = None) -> None:
        """Start progress indication and optionally manage a connection."""
        with self._lock:
            if self.is_active:
                return

            self.start_time = time.time()
            self.is_active = True
            self.stop_event.clear()
            self.active_response = response

            # Send initial progress message
            initial_msg = (
                f"\n🔧 **Executing tool: {self.tool_name}**\n⏳ Starting execution...\n"
            )
            self._send_progress_update(initial_msg)

            # Start progress thread
            self.progress_thread = threading.Thread(
                target=self._progress_worker,
                daemon=True,
            )
            self.progress_thread.start()

            # Start heartbeat thread if we have a connection to manage
            if self.active_response and self.connection_id:
                self.heartbeat_thread = threading.Thread(
                    target=self._heartbeat_worker,
                    daemon=True,
                )
                self.heartbeat_thread.start()

    def update_progress(self, elapsed: float, max_wait: float) -> None:
        """Update progress with elapsed time and connection status."""
        if not self.is_active:
            return

        progress_percent = min((elapsed / max_wait) * 100, 100)

        # Add connection status indicator
        connection_status = ""
        if self.active_response:
            try:
                # Check if connection is still alive
                if (
                    hasattr(self.active_response, "raw")
                    and self.active_response.raw.isclosed()
                ):
                    connection_status = " 🔌❌"
                else:
                    connection_status = " 🔌✅"
            except:
                connection_status = " 🔌❓"

        dots = "." * (int(elapsed) % 4)
        progress_msg = f"⏳ Executing{dots} ({progress_percent:.0f}% - {elapsed:.1f}s){connection_status}\n"
        self._send_progress_update(progress_msg, replace_last=True)

    def complete(self) -> None:
        """Mark tool execution as complete and cleanup connection."""
        with self._lock:
            if not self.is_active:
                return

            self.is_active = False
            self.stop_event.set()

            elapsed = time.time() - self.start_time if self.start_time else 0
            completion_msg = f"✅ **Tool completed** ({elapsed:.1f}s)\n"
            self._send_progress_update(completion_msg, replace_last=True)

            self._cleanup_connection()

    def error(self, error_message: str) -> None:
        """Mark tool execution as failed and cleanup connection."""
        with self._lock:
            if not self.is_active:
                return

            self.is_active = False
            self.stop_event.set()

            elapsed = time.time() - self.start_time if self.start_time else 0
            error_msg = f"❌ **Tool failed** ({elapsed:.1f}s): {error_message}\n"
            self._send_progress_update(error_msg, replace_last=True)

            self._cleanup_connection()

    def timeout(self, max_wait: float) -> None:
        """Mark tool execution as timed out and cleanup connection."""
        with self._lock:
            if not self.is_active:
                return

            self.is_active = False
            self.stop_event.set()

            timeout_msg = f"⏰ **Tool timed out** after {max_wait}s\n"
            self._send_progress_update(timeout_msg, replace_last=True)

            self._cleanup_connection()

    def cleanup(self) -> None:
        """Clean up all resources including connection."""
        with self._lock:
            self.is_active = False
            self.stop_event.set()

            if self.progress_thread and self.progress_thread.is_alive():
                self.progress_thread.join(timeout=1.0)

            if self.heartbeat_thread and self.heartbeat_thread.is_alive():
                self.heartbeat_thread.join(timeout=1.0)

            self._cleanup_connection()

    def _cleanup_connection(self) -> None:
        """Safely cleanup the managed connection."""
        if self.active_response:
            try:
                # Consume any remaining data before closing
                if hasattr(self.active_response, "raw"):
                    remaining = self.active_response.raw.read(1024)
                    if remaining:
                        print(
                            f"Consumed {len(remaining)} bytes before closing connection",
                        )

                self.active_response.close()
                print(f"Connection {self.connection_id} closed after tool execution")
            except Exception as e:
                print(f"Error closing connection {self.connection_id}: {e}")
            finally:
                self.active_response = None

    def _progress_worker(self) -> None:
        """Background worker for periodic progress updates."""
        update_interval = 2.0

        while not self.stop_event.wait(update_interval):
            if not self.is_active:
                break

            elapsed = time.time() - self.start_time if self.start_time else 0

            # Connection health check
            connection_health = ""
            if self.active_response:
                try:
                    if (
                        hasattr(self.active_response, "raw")
                        and not self.active_response.raw.isclosed()
                    ):
                        connection_health = " 🔗"
                    else:
                        connection_health = " 🔗❌"
                except:
                    connection_health = " 🔗❓"

            # Progressive status messages
            if elapsed < 5:
                dots = "." * ((int(elapsed) % 3) + 1)
                msg = f"🔧 Processing{dots}{connection_health}\n"
            elif elapsed < 15:
                dots = "." * ((int(elapsed) % 4) + 1)
                msg = f"⚙️ Working{dots} ({elapsed:.0f}s){connection_health}\n"
            elif elapsed < 30:
                dots = "." * ((int(elapsed) % 5) + 1)
                msg = f"🔄 Still processing{dots} ({elapsed:.0f}s){connection_health}\n"
            else:
                dots = "." * ((int(elapsed) % 6) + 1)
                msg = f"⏳ Long-running task{dots} ({elapsed:.0f}s){connection_health}\n"

            self._send_progress_update(msg, replace_last=True)

    def _heartbeat_worker(self) -> None:
        """Send periodic heartbeats to keep the connection alive."""
        while not self.stop_event.wait(self.heartbeat_interval):
            if not self.is_active or not self.active_response:
                break

            try:
                # Send a minimal heartbeat request using a separate session
                heartbeat_payload = {
                    "model": "llama3.2:1b",  # Use smallest available model
                    "messages": [{"role": "user", "content": "ping"}],
                    "stream": False,
                }

                heartbeat_response = requests.post(
                    "http://localhost:11434/api/chat",
                    json=heartbeat_payload,
                    timeout=10,
                )

                if heartbeat_response.status_code == 200:
                    print(f"Heartbeat sent for connection {self.connection_id}")
                    # Update progress to show heartbeat
                    elapsed = time.time() - self.start_time if self.start_time else 0
                    heartbeat_msg = f"💓 Heartbeat sent ({elapsed:.0f}s)\n"
                    self._send_progress_update(heartbeat_msg, replace_last=True)
                else:
                    print(
                        f"Heartbeat failed with status {heartbeat_response.status_code}",
                    )

                heartbeat_response.close()

            except Exception as e:
                print(f"Heartbeat error for connection {self.connection_id}: {e}")
                # Don't break on heartbeat errors, just log them

    def _send_progress_update(self, message: str, replace_last: bool = False) -> None:
        """Send progress update to the chat display."""
        try:
            update = ContentUpdate(
                answer_index=self.answer_index,
                content_chunk=message,
                is_done=False,
                is_error=False,
            )

            if not self.chat_streaming._put_content_update_with_retry(
                update,
                max_retries=2,
            ):
                print(f"Failed to send progress update: {message.strip()}")

        except Exception as e:
            print(f"Error sending progress update: {e}")


class StreamingConnectionManager:
    """Manages streaming connections during tool execution."""

    def __init__(self):
        self.active_connections: dict[str, requests.Response] = {}
        self.connection_managers: dict[str, ConnectionAwareToolProgressManager] = {}
        self._lock = threading.Lock()

    def register_connection(
        self,
        connection_id: str,
        response: requests.Response,
        progress_manager: ConnectionAwareToolProgressManager,
    ) -> None:
        """Register a connection for management during tool execution."""
        with self._lock:
            self.active_connections[connection_id] = response
            self.connection_managers[connection_id] = progress_manager
            print(f"Registered connection {connection_id} for management")

    def release_connection(self, connection_id: str) -> requests.Response | None:
        """Release a connection from management."""
        with self._lock:
            response = self.active_connections.pop(connection_id, None)
            self.connection_managers.pop(connection_id, None)
            if response:
                print(f"Released connection {connection_id}")
            return response

    def cleanup_all(self) -> None:
        """Cleanup all managed connections."""
        with self._lock:
            for connection_id, manager in self.connection_managers.items():
                try:
                    manager.cleanup()
                except Exception as e:
                    print(f"Error cleaning up connection {connection_id}: {e}")

            self.active_connections.clear()
            self.connection_managers.clear()
