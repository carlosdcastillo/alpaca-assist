"""Local-side SSH port forwards carrying surface WebSocket traffic.

A surface's pixels never pass through Python. The panel opens a WebSocket
straight to x11vnc and noVNC decodes RFB in the page — pushing framebuffer
updates through webview_api's 50 ms `get_pending_js()` queue would be a
category error, that queue exists for chat tokens.

But x11vnc is bound to loopback on the remote host, deliberately (see
core/surface_supervisor.py), so something has to bridge the two loopbacks.
That is this module: one `ssh -N -L 127.0.0.1:<local>:127.0.0.1:<remote>`
per surface, both ends loopback-only, so no port is ever exposed on a
routable interface at either end and no other user on either machine can
reach it.

This is necessarily a *second* SSH connection, separate from the one
PackTransport already holds open: that one's stdio is fully consumed by the
JSON-RPC bridge relay, so it cannot also carry a port forward.

    Gotcha: the usual fix for the extra connection cost
    (-o ControlMaster=auto -o ControlPath=...) is not available. Windows
    OpenSSH does not implement connection multiplexing. Each surface
    therefore pays one extra SSH authentication at open time. Auth is
    key-based and non-interactive (BatchMode=yes is already the convention
    here), so this is a startup-latency cost, not a prompt.
"""
from __future__ import annotations

import logging
import socket
import subprocess
import threading
import time

logger = logging.getLogger(__name__)

SSH_OPTIONS = [
    "-o",
    "BatchMode=yes",
    "-o",
    "ConnectTimeout=8",
    # Turns a lost race for the local port into an immediate clean failure
    # instead of an ssh that sits there connected but forwarding nothing.
    "-o",
    "ExitOnForwardFailure=yes",
    "-o",
    "ServerAliveInterval=15",
    "-o",
    "ServerAliveCountMax=3",
]

TUNNEL_READY_TIMEOUT = 20.0
TUNNEL_CLOSE_GRACE = 2.0


class SurfaceTunnelError(RuntimeError):
    """Raised when a tunnel cannot be established."""


def _free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _port_accepting(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


class SurfaceTunnel:
    """One `ssh -N -L` subprocess forwarding a single surface's WebSocket."""

    def __init__(self, host: str, remote_port: int, local_port: int | None = None) -> None:
        self.host = host
        self.remote_port = remote_port
        self.local_port = local_port or _free_local_port()
        self._process: subprocess.Popen[bytes] | None = None

    @property
    def alive(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def open(self, timeout: float = TUNNEL_READY_TIMEOUT) -> None:
        argv = [
            "ssh",
            *SSH_OPTIONS,
            "-N",
            "-L",
            f"127.0.0.1:{self.local_port}:127.0.0.1:{self.remote_port}",
            self.host,
        ]
        try:
            self._process = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
        except OSError as exc:
            raise SurfaceTunnelError(f"could not start ssh: {exc}") from exc

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if _port_accepting(self.local_port):
                logger.info(
                    f"Surface tunnel up: 127.0.0.1:{self.local_port} -> "
                    f"{self.host}:{self.remote_port}",
                )
                return
            if self._process.poll() is not None:
                raise SurfaceTunnelError(
                    f"ssh exited immediately: {self._stderr_tail()}",
                )
            time.sleep(0.1)
        self.close()
        raise SurfaceTunnelError(
            f"timed out after {timeout:.0f}s waiting for the tunnel to 127.0.0.1:"
            f"{self.remote_port} on {self.host}",
        )

    def _stderr_tail(self) -> str:
        if self._process is None or self._process.stderr is None:
            return "no output"
        try:
            data = self._process.stderr.read() or b""
        except OSError:
            return "no output"
        text = data.decode("utf-8", errors="replace").strip()
        return text.splitlines()[-1] if text else "no output"

    def close(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        try:
            process.terminate()
        except OSError:
            pass
        try:
            process.wait(timeout=TUNNEL_CLOSE_GRACE)
        except Exception:
            try:
                process.kill()
            except OSError:
                pass
        for stream in (process.stderr, process.stdout):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass


class SurfaceTunnelManager:
    """Every tunnel belonging to one Pack tab, keyed by surface id.

    Owned by PackTab, which closes the lot in `cleanup_resources()` and in
    `_on_disconnect()`. Note the asymmetry that follows from the daemon
    deliberately outliving the local app: closing these tunnels does not stop
    the remote surfaces, only our route to them. The supervisor's idle reaper
    is what eventually collects those.
    """

    def __init__(self, host: str) -> None:
        self.host = host
        self._tunnels: dict[str, SurfaceTunnel] = {}
        self._lock = threading.RLock()

    def open(self, surface_id: str, remote_port: int) -> int:
        """Return a local port forwarding to `remote_port`, reusing if possible."""
        with self._lock:
            existing = self._tunnels.get(surface_id)
            if existing is not None:
                if existing.alive and existing.remote_port == remote_port:
                    return existing.local_port
                existing.close()
                del self._tunnels[surface_id]

        last_error: Exception | None = None
        # One retry: _free_local_port closes the socket before handing the
        # number to ssh, so another process can in principle take it first.
        for _attempt in range(2):
            tunnel = SurfaceTunnel(self.host, remote_port)
            try:
                tunnel.open()
            except SurfaceTunnelError as exc:
                last_error = exc
                tunnel.close()
                continue
            with self._lock:
                self._tunnels[surface_id] = tunnel
            return tunnel.local_port
        raise SurfaceTunnelError(str(last_error))

    def close(self, surface_id: str) -> None:
        with self._lock:
            tunnel = self._tunnels.pop(surface_id, None)
        if tunnel is not None:
            tunnel.close()

    def close_all(self) -> None:
        with self._lock:
            tunnels = list(self._tunnels.values())
            self._tunnels.clear()
        for tunnel in tunnels:
            tunnel.close()

    def local_port(self, surface_id: str) -> int | None:
        with self._lock:
            tunnel = self._tunnels.get(surface_id)
        return tunnel.local_port if tunnel is not None and tunnel.alive else None
