"""Local-side SSH transport for Pack tabs.

Spawns pack_bridge.py on the remote host over SSH, frames outgoing
requests / incoming responses and notifications as newline-delimited JSON
(core/pack_protocol.py), and correlates each outgoing request to its
matching response by id — mirroring the threading.Event-based
wait-for-async-result pattern already used for tool execution in
core/chat_tab_tools.py (`callback_event`/`TOOL_EXECUTION_TIMEOUT_SECONDS`),
generalized here to support multiple concurrent in-flight requests.
"""

from __future__ import annotations

import itertools
import logging
import shlex
import subprocess
import threading
from collections.abc import Callable
from typing import Any

from core import pack_protocol

logger = logging.getLogger(__name__)

DEFAULT_REQUEST_TIMEOUT = 30.0
SSH_OPTIONS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=8"]
# Explicit .venv-sys interpreter, matching the deployment convention already
# established in agents/RUNBOOK.md/SETUP.md — a bare `python3` depends on
# non-interactive SSH's (often minimal) PATH, which may not include the
# venv's shims or even the same Python that has this repo's dependencies
# installed (pyperclip, mcp, etc., all live in .venv-sys, not system python).
REMOTE_BRIDGE_COMMAND = "~/pywebview_demo/.venv-sys/bin/python3 ~/pywebview_demo/pack_bridge.py {session_id}"


class PackTransportError(RuntimeError):
    """Raised when a request fails, times out, or the transport isn't connected."""


class PackTransport:
    """One SSH-spawned, persistent JSON-RPC channel to a remote pack_daemon.py."""

    def __init__(self, host: str, session_id: str) -> None:
        self.host = host
        self.session_id = session_id
        self.connected = False
        self._process: subprocess.Popen[bytes] | None = None
        self._reader_thread: threading.Thread | None = None
        self._id_counter = itertools.count(1)
        self._pending_lock = threading.Lock()
        self._pending_events: dict[int, threading.Event] = {}
        self._pending_results: dict[int, tuple[bool, Any]] = {}  # (is_error, value)
        self._notification_handlers: dict[str, Callable[[dict[str, Any]], None]] = {}
        self._disconnect_handlers: list[Callable[[], None]] = []
        self._write_lock = threading.Lock()
        self._teardown_done = False

    def on_notification(
        self,
        method: str,
        handler: Callable[[dict[str, Any]], None],
    ) -> None:
        """Register a handler for a given notification method name."""
        self._notification_handlers[method] = handler

    def on_disconnect(self, handler: Callable[[], None]) -> None:
        """Register a handler fired once when the connection is lost, for

        any reason (remote closed it, ssh exited, close() was called).
        """
        self._disconnect_handlers.append(handler)

    def connect(self, model: str | None = None) -> None:
        """Spawn the ssh subprocess and start reading.

        model is only meaningful the first time a given session_id's
        daemon is ever spawned — pack_bridge.py forwards it to
        pack_daemon.py's --model flag, which seeds first-launch
        preferences (see pack_daemon.py's docstring). Attaching to an
        already-running daemon ignores it; that daemon has its own
        preferences already.

        Raises PackTransportError only if the ssh binary itself can't be
        spawned locally (rare). Actual connection failures (unreachable
        host, auth failure, remote daemon spawn timeout) surface later as
        an ssh process exit, caught by the reader thread's teardown path
        and reported via disconnect handlers / a failed first request
        instead — ssh itself may take the full ConnectTimeout to fail.
        """
        remote_cmd = REMOTE_BRIDGE_COMMAND.format(session_id=self.session_id)
        if model:
            remote_cmd += f" {shlex.quote(model)}"
        argv = ["ssh", *SSH_OPTIONS, self.host, remote_cmd]
        try:
            self._process = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except OSError as e:
            raise PackTransportError(f"could not start ssh: {e}") from e

        self.connected = True
        self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._reader_thread.start()

    def _read_loop(self) -> None:
        assert self._process is not None
        stdout = self._process.stdout
        assert stdout is not None
        try:
            for raw_line in stdout:
                try:
                    line = raw_line.decode("utf-8")
                except UnicodeDecodeError:
                    continue
                try:
                    msg = pack_protocol.decode_line(line)
                except pack_protocol.ProtocolError as e:
                    logger.warning(f"Bad message from pack daemon: {e}")
                    continue
                if pack_protocol.is_response(msg):
                    self._handle_response(msg)
                elif pack_protocol.is_notification(msg):
                    self._handle_notification(msg)
        finally:
            self._teardown()

    def _handle_response(self, msg: dict[str, Any]) -> None:
        request_id = msg["id"]
        with self._pending_lock:
            event = self._pending_events.get(request_id)
            if event is None:
                return
            if "error" in msg:
                self._pending_results[request_id] = (
                    True,
                    msg["error"].get("message", "unknown error"),
                )
            else:
                self._pending_results[request_id] = (False, msg.get("result"))
        event.set()

    def _handle_notification(self, msg: dict[str, Any]) -> None:
        handler = self._notification_handlers.get(msg["method"])
        if handler is None:
            return
        try:
            handler(msg.get("params", {}))
        except Exception:
            logger.exception(f"Notification handler for {msg['method']!r} raised")

    def send_request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        timeout: float = DEFAULT_REQUEST_TIMEOUT,
    ) -> Any:
        """Send a request and block until its response arrives or times out.

        Raises PackTransportError on disconnect, timeout, or a remote-side
        error response.
        """
        if not self.connected or self._process is None or self._process.stdin is None:
            raise PackTransportError("not connected")

        request_id = next(self._id_counter)
        event = threading.Event()
        with self._pending_lock:
            self._pending_events[request_id] = event

        line = pack_protocol.encode_request(request_id, method, params)
        try:
            with self._write_lock:
                self._process.stdin.write(line.encode("utf-8"))
                self._process.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            with self._pending_lock:
                self._pending_events.pop(request_id, None)
            raise PackTransportError(
                f"connection lost while sending {method!r}: {e}",
            ) from e

        if not event.wait(timeout=timeout):
            with self._pending_lock:
                self._pending_events.pop(request_id, None)
                self._pending_results.pop(request_id, None)
            raise PackTransportError(f"timed out waiting for {method!r} response")

        with self._pending_lock:
            self._pending_events.pop(request_id, None)
            is_error, value = self._pending_results.pop(
                request_id,
                (True, "no result recorded"),
            )

        if is_error:
            raise PackTransportError(str(value))
        return value

    def _teardown(self) -> None:
        if self._teardown_done:
            return
        self._teardown_done = True
        self.connected = False
        # Wake up anyone still waiting on a response that will never arrive.
        with self._pending_lock:
            for request_id, event in self._pending_events.items():
                self._pending_results[request_id] = (True, "connection closed")
                event.set()
        for handler in self._disconnect_handlers:
            try:
                handler()
            except Exception:
                logger.exception("Disconnect handler raised")

    def close(self) -> None:
        """Tear down only the local SSH subprocess/threads — never signals

        the remote daemon to stop. This is the entire mechanism by which
        closing the app leaves the remote Pack session running: the
        remote daemon has no idea this side hung up, it just stops
        getting requests and drops notifications until something
        reattaches.
        """
        if self._process is None:
            return
        try:
            if self._process.stdin:
                self._process.stdin.close()
        except OSError:
            pass
        try:
            self._process.terminate()
        except OSError:
            pass
        # _teardown() fires from the reader thread once stdout actually
        # closes as a result of the terminate() above.
