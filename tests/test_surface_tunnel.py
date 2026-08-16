"""Tests for core/surface_tunnel.py — the local end of a surface's pixels.

Every test fakes the ssh subprocess. What is worth pinning down here is not
that ssh works but that the manager's bookkeeping does: one tunnel per
surface, reuse when the target hasn't moved, rebuild when it has, and a
teardown that leaves nothing behind — because a leaked `ssh -N` is invisible
and immortal in a way a leaked thread is not.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from core.surface_tunnel import SurfaceTunnel
from core.surface_tunnel import SurfaceTunnelError
from core.surface_tunnel import SurfaceTunnelManager


class FakeProcess:
    """Stand-in for the `ssh -N -L` subprocess."""

    def __init__(self, exit_code: int | None = None, stderr: bytes = b"") -> None:
        self._exit_code = exit_code
        self.terminated = False
        self.killed = False
        self.stdout = None
        self.stderr = FakeStream(stderr)

    def poll(self) -> int | None:
        return self._exit_code

    def terminate(self) -> None:
        self.terminated = True
        self._exit_code = -15

    def kill(self) -> None:
        self.killed = True
        self._exit_code = -9

    def wait(self, timeout: float | None = None) -> int:
        return self._exit_code or 0


class FakeStream:
    def __init__(self, data: bytes) -> None:
        self._data = data
        self.closed = False

    def read(self) -> bytes:
        return self._data

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def spawned() -> Any:
    """Patch Popen and make the forwarded port look immediately live."""
    calls: list[list[str]] = []

    def fake_popen(argv: list[str], **_kwargs: Any) -> FakeProcess:
        calls.append(argv)
        return FakeProcess()

    with (
        patch("core.surface_tunnel.subprocess.Popen", side_effect=fake_popen),
        patch("core.surface_tunnel._port_accepting", return_value=True),
    ):
        yield calls


class TestSurfaceTunnel:
    def test_forwards_loopback_to_loopback(self, spawned: list[list[str]]) -> None:
        """Neither end may be reachable from another machine or another user."""
        tunnel = SurfaceTunnel("user@host", 6080, local_port=51733)

        tunnel.open()

        assert spawned[0] == [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=8",
            "-o",
            "ExitOnForwardFailure=yes",
            "-o",
            "ServerAliveInterval=15",
            "-o",
            "ServerAliveCountMax=3",
            "-N",
            "-L",
            "127.0.0.1:51733:127.0.0.1:6080",
            "user@host",
        ]

    def test_batch_mode_keeps_ssh_from_prompting(
        self,
        spawned: list[list[str]],
    ) -> None:
        """A tunnel opening behind the UI must never block on a passphrase."""
        SurfaceTunnel("user@host", 6080).open()

        assert "BatchMode=yes" in spawned[0]

    def test_alive_tracks_the_subprocess(self, spawned: list[list[str]]) -> None:
        tunnel = SurfaceTunnel("user@host", 6080)
        assert tunnel.alive is False

        tunnel.open()
        assert tunnel.alive is True

        tunnel.close()
        assert tunnel.alive is False

    def test_immediate_ssh_exit_reports_its_own_error(self) -> None:
        process = FakeProcess(exit_code=255, stderr=b"Permission denied (publickey).")
        with (
            patch("core.surface_tunnel.subprocess.Popen", return_value=process),
            patch("core.surface_tunnel._port_accepting", return_value=False),
        ):
            with pytest.raises(SurfaceTunnelError, match="Permission denied"):
                SurfaceTunnel("user@host", 6080).open()

    def test_port_that_never_opens_times_out_and_cleans_up(self) -> None:
        process = FakeProcess()
        with (
            patch("core.surface_tunnel.subprocess.Popen", return_value=process),
            patch("core.surface_tunnel._port_accepting", return_value=False),
        ):
            with pytest.raises(SurfaceTunnelError, match="timed out"):
                SurfaceTunnel("user@host", 6080).open(timeout=0.3)

        assert process.terminated is True

    def test_missing_ssh_binary_is_reported_not_raised_raw(self) -> None:
        with patch(
            "core.surface_tunnel.subprocess.Popen",
            side_effect=OSError("no ssh"),
        ):
            with pytest.raises(SurfaceTunnelError, match="could not start ssh"):
                SurfaceTunnel("user@host", 6080).open()

    def test_close_is_idempotent(self, spawned: list[list[str]]) -> None:
        tunnel = SurfaceTunnel("user@host", 6080)
        tunnel.open()

        tunnel.close()
        tunnel.close()

        assert tunnel.alive is False


class TestSurfaceTunnelManager:
    def test_one_ssh_per_surface(self, spawned: list[list[str]]) -> None:
        manager = SurfaceTunnelManager("user@host")

        manager.open("srf_aaaaaaaa", 6080)
        manager.open("srf_bbbbbbbb", 6081)

        assert len(spawned) == 2

    def test_reopening_the_same_target_reuses_the_connection(
        self,
        spawned: list[list[str]],
    ) -> None:
        """Windows OpenSSH has no ControlMaster, so each tunnel costs a full
        authentication. Reuse is the only thing keeping that off the hot path."""
        manager = SurfaceTunnelManager("user@host")

        first = manager.open("srf_aaaaaaaa", 6080)
        second = manager.open("srf_aaaaaaaa", 6080)

        assert first == second
        assert len(spawned) == 1

    def test_a_moved_remote_port_rebuilds_the_tunnel(
        self,
        spawned: list[list[str]],
    ) -> None:
        """A surface reopened on the far side lands on a new port; forwarding
        to the old one would connect to nothing, or worse, to something else."""
        manager = SurfaceTunnelManager("user@host")

        manager.open("srf_aaaaaaaa", 6080)
        manager.open("srf_aaaaaaaa", 6099)

        assert len(spawned) == 2
        assert "127.0.0.1:6099" in spawned[1][-2]

    def test_close_all_leaves_nothing_behind(self, spawned: list[list[str]]) -> None:
        manager = SurfaceTunnelManager("user@host")
        manager.open("srf_aaaaaaaa", 6080)
        manager.open("srf_bbbbbbbb", 6081)

        manager.close_all()

        assert manager.local_port("srf_aaaaaaaa") is None
        assert manager.local_port("srf_bbbbbbbb") is None

    def test_local_port_is_none_once_ssh_dies(self, spawned: list[list[str]]) -> None:
        """The panel uses this to decide whether it still has a route."""
        manager = SurfaceTunnelManager("user@host")
        manager.open("srf_aaaaaaaa", 6080)

        process = manager._tunnels["srf_aaaaaaaa"]._process
        assert process is not None
        setattr(process, "_exit_code", 255)

        assert manager.local_port("srf_aaaaaaaa") is None

    def test_a_failing_tunnel_is_retried_once_then_reported(self) -> None:
        """_free_local_port closes the socket before handing the number to
        ssh, so losing the race is possible — but not worth retrying forever."""
        attempts: list[list[str]] = []

        def fake_popen(argv: list[str], **_kwargs: Any) -> FakeProcess:
            attempts.append(argv)
            return FakeProcess(exit_code=255, stderr=b"bind: Address already in use")

        with (
            patch("core.surface_tunnel.subprocess.Popen", side_effect=fake_popen),
            patch("core.surface_tunnel._port_accepting", return_value=False),
        ):
            manager = SurfaceTunnelManager("user@host")
            with pytest.raises(SurfaceTunnelError, match="Address already in use"):
                manager.open("srf_aaaaaaaa", 6080)

        assert len(attempts) == 2
