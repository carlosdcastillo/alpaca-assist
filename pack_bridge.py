#!/usr/bin/env python3
"""Pack tab bridge — the argv `ssh` runs on the remote host.

Idempotently launches (if needed) a detached pack_daemon.py for the given
session id, then relays this process's own stdio to the daemon's Unix
domain socket. The local side runs this via:

    ssh -o BatchMode=yes -o ConnectTimeout=8 <host> \
        "python3 ~/pywebview_demo/pack_bridge.py <session_id>"

so from the local side's point of view, the ssh subprocess's stdin/stdout
*is* the persistent JSON-RPC channel to the remote ChatTab, regardless of
whether this invocation started a fresh daemon or reattached to one that
was already running (e.g. because a previous SSH link dropped without the
daemon dying — the whole point of Pack tabs).

Usage:
    python3 pack_bridge.py <session_id>

Race safety: two near-simultaneous invocations for the same session id
serialize on a flock() around the launch decision only (not the relay
itself), so the slower one always finds the socket already live and never
double-spawns a daemon. pack_daemon.py additionally refuses to bind a
socket that something is already listening on, as defense in depth.
"""
from __future__ import annotations

import fcntl
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

CONNECT_PROBE_TIMEOUT = 1.0
DAEMON_START_TIMEOUT = 5.0
DAEMON_POLL_INTERVAL = 0.1
RELAY_CHUNK = 65536


def _probe_connect(sock_path: Path) -> socket.socket | None:
    """Return a connected socket if a daemon is already listening, else None."""
    if not sock_path.exists():
        return None
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(CONNECT_PROBE_TIMEOUT)
    try:
        sock.connect(str(sock_path))
        sock.settimeout(None)
        return sock
    except OSError:
        sock.close()
        return None


def _launch_or_attach(session_id: str) -> socket.socket:
    """Idempotently ensure a daemon is running for `session_id`, return a

    fresh connected socket to it.
    """
    session_dir = Path.home() / ".alpaca_pack" / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    sock_path = session_dir / "daemon.sock"
    lock_path = session_dir / "daemon.lock"

    lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)

        existing = _probe_connect(sock_path)
        if existing is not None:
            return existing

        # Stale socket file (daemon crashed without cleanup) — clear it so
        # the daemon's own bind() doesn't refuse to start.
        sock_path.unlink(missing_ok=True)

        repo_root = Path(__file__).resolve().parent
        log_path = session_dir / "daemon.log"
        with open(log_path, "ab") as log_file:
            subprocess.Popen(
                [sys.executable, str(repo_root / "pack_daemon.py"), session_id],
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                cwd=str(session_dir),
                start_new_session=True,  # detach from this ssh session/pgrp
            )

        deadline = time.monotonic() + DAEMON_START_TIMEOUT
        while time.monotonic() < deadline:
            connected = _probe_connect(sock_path)
            if connected is not None:
                return connected
            time.sleep(DAEMON_POLL_INTERVAL)

        raise RuntimeError(
            f"pack_daemon.py did not start listening within {DAEMON_START_TIMEOUT}s "
            f"(session={session_id}); check {log_path}",
        )
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def _relay(sock: socket.socket) -> None:
    """Bidirectionally pipe this process's stdio to/from `sock` until either

    side closes.
    """

    def stdin_to_socket() -> None:
        try:
            while True:
                chunk = os.read(0, RELAY_CHUNK)
                if not chunk:
                    break
                sock.sendall(chunk)
        except OSError:
            pass
        finally:
            try:
                sock.shutdown(socket.SHUT_WR)
            except OSError:
                pass

    def socket_to_stdout() -> None:
        try:
            while True:
                chunk = sock.recv(RELAY_CHUNK)
                if not chunk:
                    break
                os.write(1, chunk)
        except OSError:
            pass

    t1 = threading.Thread(target=stdin_to_socket, daemon=True)
    t2 = threading.Thread(target=socket_to_stdout, daemon=True)
    t1.start()
    t2.start()
    # Exit once the daemon->local direction ends (daemon closed or crashed);
    # the stdin->socket thread is daemonized so it won't block process exit.
    t2.join()


def main() -> None:
    if len(sys.argv) != 2:
        print("usage: pack_bridge.py <session_id>", file=sys.stderr)
        sys.exit(2)
    session_id = sys.argv[1]

    try:
        sock = _launch_or_attach(session_id)
    except Exception as e:
        print(f"pack_bridge: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        _relay(sock)
    finally:
        sock.close()


if __name__ == "__main__":
    main()
