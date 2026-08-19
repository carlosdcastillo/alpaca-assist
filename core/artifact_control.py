"""Unix-socket channel from the artifact MCP server to its Pack daemon."""

from __future__ import annotations

import os
import socket
import threading
from pathlib import Path
from typing import Any

from core import pack_protocol

SOCKET_NAME = "control.sock"


class ArtifactControlServer:
    def __init__(self, store: Any) -> None:
        self._store = store
        self.socket_path = store.artifacts_dir / SOCKET_NAME
        self._listener: socket.socket | None = None

    def start(self) -> None:
        self.socket_path.unlink(missing_ok=True)
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(self.socket_path))
        listener.listen(4)
        self.socket_path.chmod(0o600)
        self._listener = listener
        threading.Thread(
            target=self._accept_loop,
            daemon=True,
            name="artifact-control",
        ).start()

    def _accept_loop(self) -> None:
        assert self._listener is not None
        while True:
            try:
                conn, _ = self._listener.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn: socket.socket) -> None:
        with conn, conn.makefile("r", encoding="utf-8") as reader:
            line = reader.readline()
            try:
                msg = pack_protocol.decode_line(line)
                result = self._store.dispatch(msg["method"], msg.get("params", {}))
                reply = pack_protocol.encode_response(msg["id"], result)
            except Exception as exc:
                request_id = msg.get("id", 0) if "msg" in locals() else 0
                reply = pack_protocol.encode_error_response(request_id, str(exc))
            conn.sendall(reply.encode("utf-8"))


class ArtifactControlClient:
    def __init__(self, socket_path: Path | str) -> None:
        self.socket_path = Path(socket_path)

    @classmethod
    def discover(cls) -> ArtifactControlClient | None:
        override = os.environ.get("ALPACA_ARTIFACT_SOCKET")
        if override:
            return cls(override)
        local = Path.cwd() / "artifacts" / SOCKET_NAME
        if local.exists():
            return cls(local)
        candidates = list(
            (Path.home() / ".alpaca_pack").glob(f"*/artifacts/{SOCKET_NAME}"),
        )
        return cls(candidates[0]) if len(candidates) == 1 else None

    def call(self, method: str, params: dict[str, Any]) -> Any:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(45)
        try:
            sock.connect(str(self.socket_path))
            sock.sendall(
                pack_protocol.encode_request(1, method, params).encode("utf-8"),
            )
            with sock.makefile("r", encoding="utf-8") as reader:
                msg = pack_protocol.decode_line(reader.readline())
            if "error" in msg:
                raise RuntimeError(msg["error"]["message"])
            return msg["result"]
        finally:
            sock.close()
