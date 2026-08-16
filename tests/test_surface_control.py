"""Tests for core/surface_control.py — supervisor ↔ MCP server plumbing.

The socket round-trip needs AF_UNIX and so only runs on the Pack host's
platform. Discovery is pure path logic and runs everywhere, which is the part
worth pinning down anyway: the MCP server is launched by mcp_manager with a
filtered environment and no explicit cwd, so *how* it finds the supervisor is
load-bearing and easy to break silently.
"""

from __future__ import annotations

import socket
import threading
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from core.surface_control import SOCKET_NAME
from core.surface_control import SurfaceControlClient
from core.surface_control import SurfaceControlServer

HAS_AF_UNIX = hasattr(socket, "AF_UNIX")
unix_only = pytest.mark.skipif(
    not HAS_AF_UNIX,
    reason="Unix domain sockets are unavailable on this platform",
)


class TestDiscovery:
    def test_explicit_override_wins(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("ALPACA_SURFACE_SOCKET", str(tmp_path / "explicit.sock"))

        client = SurfaceControlClient.discover()

        assert client is not None
        assert client.socket_path == tmp_path / "explicit.sock"

    def test_falls_back_to_the_daemons_session_directory(
        self,
        tmp_path: Path,
        monkeypatch,
    ) -> None:
        """pack_daemon.py chdirs into the session directory before spawning
        anything, so the MCP server's own cwd is where the socket lives."""
        monkeypatch.delenv("ALPACA_SURFACE_SOCKET", raising=False)
        (tmp_path / "surfaces").mkdir()
        (tmp_path / "surfaces" / SOCKET_NAME).write_text("")
        monkeypatch.chdir(tmp_path)

        client = SurfaceControlClient.discover()

        assert client is not None
        assert client.socket_path == tmp_path / "surfaces" / SOCKET_NAME

    def test_no_socket_anywhere_means_no_display(
        self,
        tmp_path: Path,
        monkeypatch,
    ) -> None:
        """A local Windows tab has no supervisor. The MCP server must report
        that rather than fail to start."""
        monkeypatch.delenv("ALPACA_SURFACE_SOCKET", raising=False)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))

        assert SurfaceControlClient.discover() is None

    def test_several_live_sessions_are_ambiguous_rather_than_guessed(
        self,
        tmp_path: Path,
        monkeypatch,
    ) -> None:
        """Picking one arbitrarily would drive an app in somebody else's
        conversation."""
        monkeypatch.delenv("ALPACA_SURFACE_SOCKET", raising=False)
        monkeypatch.chdir(tmp_path)
        home = tmp_path / "home"
        for name in ("session-a", "session-b"):
            surfaces = home / ".alpaca_pack" / name / "surfaces"
            surfaces.mkdir(parents=True)
            (surfaces / SOCKET_NAME).write_text("")
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

        assert SurfaceControlClient.discover() is None


@unix_only
class TestRoundTrip:
    @pytest.fixture
    def served(self, tmp_path: Path) -> Any:
        supervisor = MagicMock()
        supervisor.dispatch.return_value = {"surfaces": [], "profiles": ["xeyes"]}
        server = SurfaceControlServer(supervisor, tmp_path / "surfaces")
        server.start()
        yield server, supervisor
        server.stop()

    def test_a_call_reaches_the_supervisor_and_comes_back(self, served: Any) -> None:
        server, supervisor = served

        result = SurfaceControlClient(server.socket_path).call("surface_list", {})

        supervisor.dispatch.assert_called_once_with("surface_list", {})
        assert result["profiles"] == ["xeyes"]

    def test_a_supervisor_error_becomes_a_client_error(self, served: Any) -> None:
        """A refused lease or a stale seq has to reach the model as a message
        it can act on, not as a dropped connection."""
        server, supervisor = served
        supervisor.dispatch.side_effect = RuntimeError("the human holds control")

        with pytest.raises(RuntimeError, match="the human holds control"):
            SurfaceControlClient(server.socket_path).call("surface_input", {})

    def test_concurrent_callers_do_not_cross_wires(self, served: Any) -> None:
        server, supervisor = served
        supervisor.dispatch.side_effect = lambda method, params: {"echo": params}
        results: list[Any] = []

        def call(index: int) -> None:
            results.append(
                SurfaceControlClient(server.socket_path).call(
                    "surface_touch",
                    {"surface_id": f"srf_{index:08x}"},
                ),
            )

        threads = [threading.Thread(target=call, args=(i,)) for i in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)

        assert sorted(r["echo"]["surface_id"] for r in results) == [
            f"srf_{i:08x}" for i in range(4)
        ]

    def test_socket_is_not_world_readable(self, served: Any) -> None:
        """A shared Pack host means other local users must not be able to
        drive an app in this conversation."""
        server, _supervisor = served

        assert server.socket_path.stat().st_mode & 0o077 == 0

    def test_a_missing_supervisor_is_a_clean_failure(self, tmp_path: Path) -> None:
        with pytest.raises(OSError):
            SurfaceControlClient(tmp_path / "absent.sock").call("surface_list", {})
