"""Unix-socket control channel between the surface supervisor and its MCP server.

The supervisor lives in the pack_daemon.py process. The model-facing MCP
server (surface_mcp_server.py) is a *different* process — an ordinary stdio
MCP server that mcp_manager.py spawns, which on a Pack tab means it is
spawned by the daemon, on the remote host, in the daemon's session
directory. Both are therefore on the machine with the display, and a Unix
socket in the session directory is all that is needed to join them. No new
transport, no ssh in an mcp_servers.json command line, no change to
mcp_manager.py.

Framing is core/pack_protocol.py, unchanged, so there is exactly one wire
format in this codebase rather than two that can drift.
"""

from __future__ import annotations

import logging
import os
import socket
import threading
from pathlib import Path
from typing import Any

from core import pack_protocol

logger = logging.getLogger(__name__)

SOCKET_NAME = "control.sock"
SOCKET_BACKLOG = 4
CLIENT_TIMEOUT = 45.0


def _af_unix() -> int:
    family = getattr(socket, "AF_UNIX", None)
    if family is None:  # pragma: no cover - Windows
        raise RuntimeError("Unix domain sockets are not available on this platform")
    return int(family)


class SurfaceControlServer:
    """Serves `surface_*` calls from the MCP server to the supervisor."""

    def __init__(self, supervisor: Any, surfaces_dir: Path | str) -> None:
        self._supervisor = supervisor
        self.surfaces_dir = Path(surfaces_dir)
        self.socket_path = self.surfaces_dir / SOCKET_NAME
        self._listener: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        """Bind and serve on a background thread. Never raises to the caller.

        A surface control socket that fails to bind must not take the whole
        Pack daemon down with it — the conversation is the primary function
        here and surfaces are an addition to it.
        """
        try:
            self.surfaces_dir.mkdir(parents=True, exist_ok=True)
            # A leftover socket file from a previous daemon is stale by
            # definition: reap_orphans has already killed everything that
            # incarnation owned.
            self.socket_path.unlink(missing_ok=True)
            listener = socket.socket(_af_unix(), socket.SOCK_STREAM)
            listener.bind(str(self.socket_path))
            listener.listen(SOCKET_BACKLOG)
            try:
                self.socket_path.chmod(0o600)
            except OSError:
                pass
        except (OSError, RuntimeError) as exc:
            logger.warning(f"Surface control socket unavailable: {exc}")
            return
        self._listener = listener
        self._thread = threading.Thread(
            target=self._accept_loop,
            name="surface-control",
            daemon=True,
        )
        self._thread.start()
        logger.info(f"Surface control socket listening on {self.socket_path}")

    def stop(self) -> None:
        self._stop.set()
        if self._listener is not None:
            try:
                self._listener.close()
            except OSError:
                pass
            self._listener = None
        self.socket_path.unlink(missing_ok=True)

    def _accept_loop(self) -> None:
        assert self._listener is not None
        while not self._stop.is_set():
            try:
                conn, _addr = self._listener.accept()
            except OSError:
                return
            threading.Thread(
                target=self._serve_connection,
                args=(conn,),
                daemon=True,
            ).start()

    def _serve_connection(self, conn: socket.socket) -> None:
        try:
            with conn, conn.makefile("r", encoding="utf-8") as reader:
                for line in reader:
                    try:
                        msg = pack_protocol.decode_line(line)
                    except pack_protocol.ProtocolError as exc:
                        logger.warning(f"Bad surface control message: {exc}")
                        continue
                    if not pack_protocol.is_request(msg):
                        continue
                    request_id = msg["id"]
                    try:
                        result = self._supervisor.dispatch(
                            msg["method"],
                            msg.get("params", {}),
                        )
                        reply = pack_protocol.encode_response(request_id, result)
                    except Exception as exc:
                        reply = pack_protocol.encode_error_response(
                            request_id,
                            str(exc),
                        )
                    conn.sendall(reply.encode("utf-8"))
        except OSError:
            pass


class SurfaceControlClient:
    """Short-lived client used by surface_mcp_server.py, one call per connect.

    Connection-per-call rather than a persistent channel: MCP tool calls are
    infrequent and independent, and a dead socket (daemon restarted, session
    directory gone) should surface as a clean error on the next call instead
    of a stale handle that has to be detected and rebuilt.
    """

    def __init__(self, socket_path: Path | str) -> None:
        self.socket_path = Path(socket_path)

    @classmethod
    def discover(cls) -> SurfaceControlClient | None:
        """Find the supervisor's socket, or None if this host has no surfaces.

        Ordered by how much the answer is trusted: an explicit override, then
        the daemon's own session directory (which is this process's cwd,
        because pack_daemon.py chdirs there before spawning anything), then a
        single unambiguous session under ~/.alpaca_pack.
        """
        override = os.environ.get("ALPACA_SURFACE_SOCKET")
        if override:
            return cls(override)

        local = Path.cwd() / "surfaces" / SOCKET_NAME
        if local.exists():
            return cls(local)

        candidates = sorted(
            (Path.home() / ".alpaca_pack").glob(f"*/surfaces/{SOCKET_NAME}"),
        )
        if len(candidates) == 1:
            return cls(candidates[0])
        return None

    def call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        sock = socket.socket(_af_unix(), socket.SOCK_STREAM)
        sock.settimeout(CLIENT_TIMEOUT)
        try:
            sock.connect(str(self.socket_path))
            sock.sendall(
                pack_protocol.encode_request(1, method, params).encode("utf-8"),
            )
            with sock.makefile("r", encoding="utf-8") as reader:
                line = reader.readline()
            if not line:
                raise RuntimeError("surface supervisor closed the connection")
            msg = pack_protocol.decode_line(line)
            if "error" in msg:
                raise RuntimeError(msg["error"].get("message", "unknown error"))
            return msg.get("result")
        finally:
            sock.close()
