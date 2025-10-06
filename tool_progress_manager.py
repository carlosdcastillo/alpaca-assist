import threading
import time
from typing import Optional

from utils import ContentUpdate


class ToolProgressManager:
    """Manages progress indication for tool execution with visual feedback."""

    def __init__(self, tool_name: str, chat_streaming: "ChatTabStreaming"):
        self.tool_name = tool_name
        self.chat_streaming = chat_streaming
        self.start_time: float | None = None
        self.progress_thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.is_active = False
        self.answer_index = 0  # Will be set from current streaming context
        self._lock = threading.Lock()

        # Try to get current answer index from chat state
        try:
            if hasattr(chat_streaming, "chat_state") and chat_streaming.chat_state:
                self.answer_index = len(chat_streaming.chat_state.answers) - 1
                if self.answer_index < 0:
                    self.answer_index = 0
        except:
            self.answer_index = 0

    def start(self) -> None:
        """Start progress indication."""
        with self._lock:
            if self.is_active:
                return

            self.start_time = time.time()
            self.is_active = True
            self.stop_event.clear()

            # Send initial progress message
            initial_msg = f"\n🔧 **Executing tool: {self.tool_name}**\n"
            self._send_progress_update(initial_msg)

            # Start progress thread for periodic updates
            self.progress_thread = threading.Thread(
                target=self._progress_worker,
                daemon=True,
            )
            self.progress_thread.start()

    def update_progress(self, elapsed: float, max_wait: float) -> None:
        """Update progress with elapsed time."""
        if not self.is_active:
            return

        progress_percent = min((elapsed / max_wait) * 100, 100)
        dots = "." * (int(elapsed) % 4)
        progress_msg = f"⏳ Running{dots} ({progress_percent:.0f}% - {elapsed:.1f}s)\n"
        self._send_progress_update(progress_msg, replace_last=True)

    def complete(self) -> None:
        """Mark tool execution as complete."""
        with self._lock:
            if not self.is_active:
                return

            self.is_active = False
            self.stop_event.set()

            elapsed = time.time() - self.start_time if self.start_time else 0
            completion_msg = f"✅ **Tool completed** ({elapsed:.1f}s)\n"
            self._send_progress_update(completion_msg, replace_last=True)

    def error(self, error_message: str) -> None:
        """Mark tool execution as failed."""
        with self._lock:
            if not self.is_active:
                return

            self.is_active = False
            self.stop_event.set()

            elapsed = time.time() - self.start_time if self.start_time else 0
            error_msg = f"❌ **Tool failed** ({elapsed:.1f}s): {error_message}\n"
            self._send_progress_update(error_msg, replace_last=True)

    def timeout(self, max_wait: float) -> None:
        """Mark tool execution as timed out."""
        with self._lock:
            if not self.is_active:
                return

            self.is_active = False
            self.stop_event.set()

            timeout_msg = f"⏰ **Tool timed out** after {max_wait}s\n"
            self._send_progress_update(timeout_msg, replace_last=True)

    def cleanup(self) -> None:
        """Clean up resources."""
        with self._lock:
            self.is_active = False
            self.stop_event.set()

            if self.progress_thread and self.progress_thread.is_alive():
                self.progress_thread.join(timeout=1.0)

    def _progress_worker(self) -> None:
        """Background worker for periodic progress updates."""
        update_interval = 2.0  # Update every 2 seconds

        while not self.stop_event.wait(update_interval):
            if not self.is_active:
                break

            elapsed = time.time() - self.start_time if self.start_time else 0

            # Show different messages based on elapsed time
            if elapsed < 5:
                dots = "." * ((int(elapsed) % 3) + 1)
                msg = f"🔧 Processing{dots}\n"
            elif elapsed < 15:
                dots = "." * ((int(elapsed) % 4) + 1)
                msg = f"⚙️ Working{dots} ({elapsed:.0f}s)\n"
            elif elapsed < 30:
                dots = "." * ((int(elapsed) % 5) + 1)
                msg = f"🔄 Still processing{dots} ({elapsed:.0f}s)\n"
            else:
                dots = "." * ((int(elapsed) % 6) + 1)
                msg = f"⏳ Long-running task{dots} ({elapsed:.0f}s)\n"

            self._send_progress_update(msg, replace_last=True)

    def _send_progress_update(self, message: str, replace_last: bool = False) -> None:
        """Send progress update to the chat display."""
        try:
            if replace_last:
                # For replacing updates, we'll just send the new message
                # The actual replacement logic would need to be implemented
                # in the chat display or content update system
                pass

            update = ContentUpdate(
                answer_index=self.answer_index,
                content_chunk=message,
                is_done=False,
                is_error=False,
            )

            # Try to send the update with retry
            if not self.chat_streaming._put_content_update_with_retry(
                update,
                max_retries=2,
            ):
                print(f"Failed to send progress update: {message.strip()}")

        except Exception as e:
            print(f"Error sending progress update: {e}")


class EnhancedToolProgressManager(ToolProgressManager):
    """Enhanced version with more sophisticated progress tracking."""

    def __init__(self, tool_name: str, chat_streaming: "ChatTabStreaming"):
        super().__init__(tool_name, chat_streaming)
        self.last_progress_content = ""
        self.progress_line_count = 0
        self.show_spinner = True

    def start(self) -> None:
        """Start with enhanced progress indication."""
        with self._lock:
            if self.is_active:
                return

            self.start_time = time.time()
            self.is_active = True
            self.stop_event.clear()

            # Send enhanced initial message
            initial_msg = f"\n🚀 **Executing: {self.tool_name}**\n⏳ Initializing...\n"
            self._send_progress_update(initial_msg)
            self.last_progress_content = initial_msg
            self.progress_line_count = initial_msg.count("\n")

            # Start enhanced progress thread
            self.progress_thread = threading.Thread(
                target=self._enhanced_progress_worker,
                daemon=True,
            )
            self.progress_thread.start()

    def _enhanced_progress_worker(self) -> None:
        """Enhanced background worker with spinner and detailed status."""
        update_interval = 1.0
        spinner_chars = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        spinner_index = 0

        while not self.stop_event.wait(update_interval):
            if not self.is_active:
                break

            elapsed = time.time() - self.start_time if self.start_time else 0
            spinner = (
                spinner_chars[spinner_index % len(spinner_chars)]
                if self.show_spinner
                else "⏳"
            )
            spinner_index += 1

            # Create status message based on elapsed time
            if elapsed < 3:
                status = "Starting up"
            elif elapsed < 10:
                status = "Processing request"
            elif elapsed < 20:
                status = "Working on task"
            elif elapsed < 40:
                status = "Complex operation in progress"
            else:
                status = "Long-running operation"

            progress_msg = f"\n🚀 **Executing: {self.tool_name}**\n{spinner} {status}... ({elapsed:.0f}s)\n"

            # Only update if content changed significantly
            if progress_msg != self.last_progress_content:
                self._send_progress_update(progress_msg)
                self.last_progress_content = progress_msg

    def complete(self) -> None:
        """Enhanced completion message."""
        with self._lock:
            if not self.is_active:
                return

            self.is_active = False
            self.stop_event.set()

            elapsed = time.time() - self.start_time if self.start_time else 0

            if elapsed < 1:
                speed_indicator = "⚡"
            elif elapsed < 5:
                speed_indicator = "✅"
            elif elapsed < 15:
                speed_indicator = "🎯"
            else:
                speed_indicator = "🏁"

            completion_msg = f"\n{speed_indicator} **{self.tool_name} completed successfully** ({elapsed:.1f}s)\n"
            self._send_progress_update(completion_msg)

    def error(self, error_message: str) -> None:
        """Enhanced error message."""
        with self._lock:
            if not self.is_active:
                return

            self.is_active = False
            self.stop_event.set()

            elapsed = time.time() - self.start_time if self.start_time else 0

            # Truncate very long error messages
            if len(error_message) > 100:
                error_message = error_message[:97] + "..."

            error_msg = f"\n❌ **{self.tool_name} failed** ({elapsed:.1f}s)\n💥 Error: {error_message}\n"
            self._send_progress_update(error_msg)


# Factory function to create appropriate progress manager
def create_progress_manager(
    tool_name: str,
    chat_streaming: "ChatTabStreaming",
    enhanced: bool = True,
) -> ToolProgressManager:
    """Create a progress manager instance."""
    if enhanced:
        return EnhancedToolProgressManager(tool_name, chat_streaming)
    else:
        return ToolProgressManager(tool_name, chat_streaming)
