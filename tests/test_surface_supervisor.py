"""Tests for core/surface_supervisor.py — remote-side surface lifecycle.

Every process spawn is faked, so these run on Windows even though the real
thing only ever runs on the Linux Pack host. What is actually being asserted
is the plumbing that has teeth: that the argv handed to x11vnc is never
password-free, that a failed startup leaves nothing behind, that the model
cannot spell "give me a shell" as an argv, that the human always wins the
lease, and that orphan reaping kills rather than adopts.

Readiness polling is stubbed out for the lifecycle tests (it wants real
/tmp/.X11-unix entries and real listening ports) and exercised directly
against `_wait_for` instead.
"""
from __future__ import annotations

import base64
import json
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

from core.surface_supervisor import _SIGKILL
from core.surface_supervisor import HOLDER_HUMAN
from core.surface_supervisor import HOLDER_MODEL
from core.surface_supervisor import LeaseRefused
from core.surface_supervisor import MAX_INPUT_EVENTS
from core.surface_supervisor import MAX_SNAPSHOT_BYTES
from core.surface_supervisor import Surface
from core.surface_supervisor import SurfaceError
from core.surface_supervisor import SurfaceSupervisor


class FakePopen:
    """Stands in for a spawned child. Tracks its own liveness."""

    _next_pid = 4000
    spawned: list[FakePopen] = []

    def __init__(self, argv: list[str], **kwargs: Any) -> None:
        FakePopen._next_pid += 1
        self.pid = FakePopen._next_pid
        self.argv = list(argv)
        self.kwargs = kwargs
        self.returncode: int | None = None
        self.killed_with: list[int] = []
        FakePopen.spawned.append(self)

    @property
    def name(self) -> str:
        return self.argv[0]

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        if self.returncode is None:
            self.returncode = 0
        return self.returncode

    def exit(self, code: int = 1) -> None:
        self.returncode = code


class FakeRun:
    """Dispatching stand-in for subprocess.run, keyed on the binary name."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.results: dict[str, subprocess.CompletedProcess] = {}

    def set(
        self,
        binary: str,
        returncode: int,
        stdout: bytes = b"",
        stderr: bytes = b"",
    ) -> None:
        self.results[binary] = subprocess.CompletedProcess(
            args=[binary],
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
        )

    def __call__(self, argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        self.calls.append(list(argv))
        if argv[0] == "x11vnc" and "-storepasswd" in argv:
            Path(argv[-1]).write_bytes(b"fake-vnc-password-file")
        return self.results.get(
            argv[0],
            subprocess.CompletedProcess(
                args=argv,
                returncode=0,
                stdout=b"",
                stderr=b"",
            ),
        )

    def argv_for(self, binary: str) -> list[list[str]]:
        return [call for call in self.calls if call and call[0] == binary]


@pytest.fixture
def fake_run(monkeypatch: pytest.MonkeyPatch) -> FakeRun:
    runner = FakeRun()
    monkeypatch.setattr("core.surface_supervisor.subprocess.run", runner)
    return runner


@pytest.fixture
def killed(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, int]]:
    """Record kills and mark the corresponding fake process dead."""
    record: list[tuple[int, int]] = []

    def _kill(pid: int, sig: int) -> None:
        record.append((pid, sig))
        for proc in FakePopen.spawned:
            if proc.pid == pid:
                proc.exit(-sig)

    monkeypatch.setattr("core.surface_supervisor._kill_group", _kill)
    return record


@pytest.fixture
def supervisor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_run: FakeRun,
) -> Any:
    """A supervisor whose every external dependency is faked."""
    FakePopen.spawned = []
    monkeypatch.setattr("core.surface_supervisor.subprocess.Popen", FakePopen)
    monkeypatch.setattr(
        "core.surface_supervisor.shutil.which",
        lambda name: f"/usr/bin/{name}" if name != "websockify" else None,
    )
    monkeypatch.setattr(
        SurfaceSupervisor,
        "_display_taken",
        staticmethod(lambda n: False),
    )
    # Readiness polling wants real X sockets and real listeners; covered
    # separately against _wait_for.
    monkeypatch.setattr(SurfaceSupervisor, "_wait_for", lambda *a, **k: None)

    sup = SurfaceSupervisor(tmp_path)
    yield sup
    sup.shutdown()


def make_surface(tmp_path: Path, width: int = 1280, height: int = 800) -> Surface:
    return Surface(
        surface_id="srf_1a2b3c4d",
        display=100,
        width=width,
        height=height,
        description="test app",
        rfb_port=5900,
        ws_port=5900,
        password="secret42",
        log_path=tmp_path / "srf_1a2b3c4d.log",
    )


class TestAvailability:
    def test_missing_tools_named_in_the_error(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("core.surface_supervisor.shutil.which", lambda name: None)
        sup = SurfaceSupervisor(tmp_path)

        assert sup.missing_tools() == ["Xvfb", "x11vnc", "xdotool"]
        assert sup.available() is False
        with pytest.raises(SurfaceError, match="no display available"):
            sup.open_surface({"argv": ["xeyes"]})

    def test_available_when_every_tool_is_present(self, supervisor: Any) -> None:
        assert supervisor.missing_tools() == []
        assert supervisor.available() is True


class TestOpenSurface:
    def test_spawns_the_three_process_stack(self, supervisor: Any) -> None:
        result = supervisor.open_surface({"argv": ["xeyes"], "description": "eyes"})

        assert [proc.name for proc in FakePopen.spawned] == ["Xvfb", "x11vnc", "xeyes"]
        assert result["surface_id"].startswith("srf_")
        assert result["width"] == 1280
        assert result["height"] == 800
        assert result["description"] == "eyes"

    def test_websockify_added_only_when_installed(
        self,
        supervisor: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "core.surface_supervisor.shutil.which",
            lambda name: f"/usr/bin/{name}",
        )

        result = supervisor.open_surface({"argv": ["xeyes"]})

        names = [proc.name for proc in FakePopen.spawned]
        assert names == ["Xvfb", "x11vnc", "websockify", "xeyes"]
        surface = supervisor.get(result["surface_id"])
        assert result["ws_port"] != surface.rfb_port

    def test_websockify_skipped_means_ws_port_is_the_rfb_port(
        self,
        supervisor: Any,
    ) -> None:
        result = supervisor.open_surface({"argv": ["xeyes"]})

        surface = supervisor.get(result["surface_id"])
        assert result["ws_port"] == surface.rfb_port

    def test_websockify_choice_can_be_forced_by_env(
        self,
        supervisor: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("ALPACA_SURFACE_WEBSOCKIFY", "1")

        supervisor.open_surface({"argv": ["xeyes"]})

        assert "websockify" in [proc.name for proc in FakePopen.spawned]

    def test_x11vnc_is_loopback_shared_and_password_protected(
        self,
        supervisor: Any,
    ) -> None:
        supervisor.open_surface({"argv": ["xeyes"]})

        argv = next(p.argv for p in FakePopen.spawned if p.name == "x11vnc")
        assert "-localhost" in argv
        assert "-shared" in argv
        assert "-rfbauth" in argv
        assert "-nopw" not in argv

    def test_password_file_written_via_storepasswd(
        self,
        supervisor: Any,
        fake_run: FakeRun,
        tmp_path: Path,
    ) -> None:
        result = supervisor.open_surface({"argv": ["xeyes"]})

        stored = fake_run.argv_for("x11vnc")
        assert stored and "-storepasswd" in stored[0]
        assert stored[0][2] == result["password"]
        assert (tmp_path / "surfaces" / f"{result['surface_id']}.pw").exists()

    def test_xvfb_gets_randr_and_no_tcp_listener(self, supervisor: Any) -> None:
        supervisor.open_surface({"argv": ["xeyes"]}, width=800, height=600)

        argv = next(p.argv for p in FakePopen.spawned if p.name == "Xvfb")
        assert "800x600x24" in argv
        assert argv[argv.index("+extension") + 1] == "RANDR"
        assert argv[argv.index("-nolisten") + 1] == "tcp"

    def test_app_runs_against_the_allocated_display(self, supervisor: Any) -> None:
        result = supervisor.open_surface({"argv": ["xeyes"]})

        app = next(p for p in FakePopen.spawned if p.name == "xeyes")
        surface = supervisor.get(result["surface_id"])
        assert app.kwargs["env"]["DISPLAY"] == f":{surface.display}"

    def test_children_get_their_own_session_for_group_kills(
        self,
        supervisor: Any,
    ) -> None:
        supervisor.open_surface({"argv": ["xeyes"]})

        assert all(proc.kwargs.get("start_new_session") for proc in FakePopen.spawned)

    def test_display_search_skips_taken_numbers(
        self,
        supervisor: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            SurfaceSupervisor,
            "_display_taken",
            staticmethod(lambda n: n < 103),
        )

        result = supervisor.open_surface({"argv": ["xeyes"]})

        assert supervisor.get(result["surface_id"]).display == 103

    def test_dimensions_are_clamped(self, supervisor: Any) -> None:
        small = supervisor.open_surface({"argv": ["xeyes"]}, width=1, height=1)
        huge = supervisor.open_surface({"argv": ["xeyes"]}, width=99999, height=99999)
        junk = supervisor.open_surface({"argv": ["xeyes"]}, width="wide", height=None)

        assert (small["width"], small["height"]) == (320, 320)
        assert (huge["width"], huge["height"]) == (3840, 3840)
        assert (junk["width"], junk["height"]) == (1280, 800)

    def test_state_file_records_pids_for_a_later_daemon_to_kill(
        self,
        supervisor: Any,
        tmp_path: Path,
    ) -> None:
        result = supervisor.open_surface({"argv": ["xeyes"]})

        state = json.loads(
            (tmp_path / "surfaces" / f"{result['surface_id']}.json").read_text(),
        )
        assert state["pids"] == [proc.pid for proc in FakePopen.spawned]
        assert state["display"] == supervisor.get(result["surface_id"]).display

    def test_failed_startup_leaves_nothing_registered_or_running(
        self,
        supervisor: Any,
        monkeypatch: pytest.MonkeyPatch,
        killed: list[tuple[int, int]],
    ) -> None:
        def _boom(*_args: Any, **_kwargs: Any) -> None:
            raise SurfaceError("x11vnc exited with code 1")

        monkeypatch.setattr(SurfaceSupervisor, "_wait_for", _boom)

        with pytest.raises(SurfaceError, match="x11vnc exited"):
            supervisor.open_surface({"argv": ["xeyes"]})

        assert supervisor.list_surfaces() == []
        assert {pid for pid, _sig in killed} == {FakePopen.spawned[0].pid}


class TestProfiles:
    def _write_profiles(self, tmp_path: Path) -> None:
        (tmp_path / "surface_profiles.json").write_text(
            json.dumps(
                {
                    "editor": {"argv": ["gedit"], "description": "a text editor"},
                    "bad": {"argv": "not-a-list"},
                },
            ),
            encoding="utf-8",
        )

    def test_profile_resolves_to_argv_and_description(
        self,
        supervisor: Any,
        tmp_path: Path,
    ) -> None:
        self._write_profiles(tmp_path)

        result = supervisor.open_surface({"profile": "editor"}, source="model")

        assert result["description"] == "a text editor"
        assert FakePopen.spawned[-1].argv == ["gedit"]

    def test_malformed_profile_entries_are_dropped(
        self,
        supervisor: Any,
        tmp_path: Path,
    ) -> None:
        self._write_profiles(tmp_path)

        assert sorted(supervisor.profiles()) == ["editor"]

    def test_model_cannot_pass_raw_argv(self, supervisor: Any) -> None:
        with pytest.raises(SurfaceError, match="must name a profile"):
            supervisor.open_surface(
                {"argv": ["bash", "-c", "curl evil"]},
                source="model",
            )

    def test_user_may_pass_raw_argv(self, supervisor: Any) -> None:
        result = supervisor.open_surface({"argv": ["xeyes"]}, source="user")

        assert result["description"] == "xeyes"

    def test_unknown_profile_lists_what_is_available(
        self,
        supervisor: Any,
        tmp_path: Path,
    ) -> None:
        self._write_profiles(tmp_path)

        with pytest.raises(SurfaceError, match="available: editor"):
            supervisor.open_surface({"profile": "nope"}, source="model")

    def test_missing_profiles_file_is_not_an_error(self, supervisor: Any) -> None:
        assert supervisor.profiles() == {}

    def test_empty_spec_is_rejected(self, supervisor: Any) -> None:
        with pytest.raises(SurfaceError, match="needs either a profile or an argv"):
            supervisor.open_surface({})


class TestCloseAndLiveness:
    def test_close_kills_every_process_and_clears_state_files(
        self,
        supervisor: Any,
        tmp_path: Path,
        killed: list[tuple[int, int]],
    ) -> None:
        result = supervisor.open_surface({"argv": ["xeyes"]})
        surface_id = result["surface_id"]

        assert supervisor.close_surface(surface_id) == {"ok": True}

        assert {pid for pid, _sig in killed} == {p.pid for p in FakePopen.spawned}
        assert all(sig == signal.SIGTERM for _pid, sig in killed)
        assert not (tmp_path / "surfaces" / f"{surface_id}.json").exists()
        assert not (tmp_path / "surfaces" / f"{surface_id}.pw").exists()
        assert supervisor.list_surfaces() == []

    def test_close_is_idempotent(self, supervisor: Any, killed: list[Any]) -> None:
        result = supervisor.open_surface({"argv": ["xeyes"]})
        supervisor.close_surface(result["surface_id"])

        assert supervisor.close_surface(result["surface_id"]) == {
            "ok": True,
            "already_closed": True,
        }

    def test_survivors_are_escalated_to_sigkill_after_the_grace_period(
        self,
        supervisor: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        supervisor.open_surface({"argv": ["xeyes"]})
        record: list[tuple[int, int]] = []
        monkeypatch.setattr(
            "core.surface_supervisor._kill_group",
            lambda pid, sig: record.append((pid, sig)),
        )
        # Nothing marks the fakes dead, so every one of them "survives" the
        # grace period and must get the second, harder signal.
        monkeypatch.setattr(FakePopen, "wait", lambda self, timeout=None: 0)

        supervisor.close_all()

        pids = {proc.pid for proc in FakePopen.spawned}
        assert {(pid, signal.SIGTERM) for pid in pids} <= set(record)
        assert {(pid, _SIGKILL) for pid in pids} <= set(record)

    def test_close_all_takes_down_every_surface(
        self,
        supervisor: Any,
        killed: list[Any],
    ) -> None:
        supervisor.open_surface({"argv": ["xeyes"]})
        supervisor.open_surface({"argv": ["xclock"]})

        supervisor.close_all()

        assert supervisor.list_surfaces() == []

    def test_surface_is_dead_when_any_process_exits(
        self,
        supervisor: Any,
    ) -> None:
        result = supervisor.open_surface({"argv": ["xeyes"]})
        surface = supervisor.get(result["surface_id"])
        assert surface.alive() is True

        next(p for p in FakePopen.spawned if p.name == "xeyes").exit(0)

        assert surface.alive() is False

    def test_get_unknown_surface_raises(self, supervisor: Any) -> None:
        with pytest.raises(SurfaceError, match="unknown surface"):
            supervisor.get("srf_ffffffff")

    def test_descriptor_reports_geometry_and_lease(self, supervisor: Any) -> None:
        result = supervisor.open_surface({"argv": ["xeyes"], "description": "eyes"})

        descriptor = supervisor.list_surfaces()[0]

        assert descriptor["surface_id"] == result["surface_id"]
        assert descriptor["description"] == "eyes"
        assert descriptor["alive"] is True
        assert descriptor["lease"] == {"holder": None, "expires_at": 0.0}


class TestReadinessWait:
    """The `supervisor` fixture stubs `_wait_for` out, so these build their
    own un-stubbed instance to exercise the real polling loop.
    """

    def test_returns_as_soon_as_the_predicate_passes(self, tmp_path: Path) -> None:
        sup = SurfaceSupervisor(tmp_path)
        surface = make_surface(tmp_path)
        calls = {"n": 0}

        def _ready() -> bool:
            calls["n"] += 1
            return calls["n"] >= 2

        sup._wait_for(surface, "a thing", _ready, timeout=2.0)

        assert calls["n"] == 2

    def test_fails_early_when_a_needed_process_died(self, tmp_path: Path) -> None:
        sup = SurfaceSupervisor(tmp_path)
        surface = make_surface(tmp_path)
        surface.log_path.write_text("x11vnc: could not bind port\n", encoding="utf-8")
        proc = FakePopen(["x11vnc"])
        proc.exit(1)
        surface.processes.append(("x11vnc", proc))

        started = time.monotonic()
        with pytest.raises(SurfaceError, match="could not bind port"):
            sup._wait_for(surface, "x11vnc", lambda: False, timeout=30.0)

        # The point of the early exit: it must not sit out the full timeout.
        assert time.monotonic() - started < 5.0

    def test_times_out_with_the_log_tail(self, tmp_path: Path) -> None:
        sup = SurfaceSupervisor(tmp_path)
        surface = make_surface(tmp_path)
        surface.log_path.write_text("nothing useful happened\n", encoding="utf-8")

        with pytest.raises(
            SurfaceError,
            match="(?s)timed out.*nothing useful happened",
        ):
            sup._wait_for(surface, "the display", lambda: False, timeout=0.3)


class TestLease:
    def test_human_lease_blocks_the_model(self, tmp_path: Path) -> None:
        surface = make_surface(tmp_path)
        surface.lease_acquire(HOLDER_HUMAN, 60)

        with pytest.raises(LeaseRefused, match="the human holds control"):
            surface.lease_acquire(HOLDER_MODEL, 60)

    def test_human_preempts_a_live_model_lease_immediately(
        self,
        tmp_path: Path,
    ) -> None:
        surface = make_surface(tmp_path)
        surface.lease_acquire(HOLDER_MODEL, 600)

        state = surface.lease_acquire(HOLDER_HUMAN, 60)

        assert state["holder"] == HOLDER_HUMAN

    def test_expired_human_lease_lets_the_model_back_in(
        self,
        tmp_path: Path,
    ) -> None:
        surface = make_surface(tmp_path)
        surface.lease_acquire(HOLDER_HUMAN, 1)
        surface._lease_expires_at = time.time() - 0.01

        assert surface.lease_acquire(HOLDER_MODEL, 60)["holder"] == HOLDER_MODEL

    def test_expired_lease_reports_no_holder(self, tmp_path: Path) -> None:
        surface = make_surface(tmp_path)
        surface.lease_acquire(HOLDER_HUMAN, 1)
        surface._lease_expires_at = time.time() - 0.01

        assert surface.lease_state() == {"holder": None, "expires_at": 0.0}

    def test_ttl_is_capped(self, tmp_path: Path) -> None:
        surface = make_surface(tmp_path)

        state = surface.lease_acquire(HOLDER_HUMAN, 10_000)

        assert state["expires_at"] - time.time() <= 601

    def test_release_only_affects_your_own_lease(self, tmp_path: Path) -> None:
        surface = make_surface(tmp_path)
        surface.lease_acquire(HOLDER_HUMAN, 60)

        surface.lease_release(HOLDER_MODEL)
        assert surface.lease_state()["holder"] == HOLDER_HUMAN

        surface.lease_release(HOLDER_HUMAN)
        assert surface.lease_state()["holder"] is None

    def test_unknown_holder_rejected(self, tmp_path: Path) -> None:
        surface = make_surface(tmp_path)

        with pytest.raises(SurfaceError, match="unknown lease holder"):
            surface.lease_acquire("someone-else", 60)


class TestEventTranslation:
    def test_click_moves_then_clicks(self, supervisor: Any, tmp_path: Path) -> None:
        surface = make_surface(tmp_path)

        argv = supervisor._event_argv(surface, {"type": "click", "x": 10, "y": 20})

        assert argv == ["mousemove", "--sync", "10", "20", "click", "1"]

    def test_every_event_type_translates(self, supervisor: Any, tmp_path: Path) -> None:
        surface = make_surface(tmp_path)
        cases = [
            ({"type": "move", "x": 1, "y": 2}, "mousemove"),
            ({"type": "doubleclick", "x": 1, "y": 2}, "mousemove"),
            ({"type": "mousedown", "x": 1, "y": 2}, "mousemove"),
            ({"type": "mouseup", "x": 1, "y": 2}, "mousemove"),
            ({"type": "type", "text": "hello"}, "type"),
            ({"type": "key", "keys": "ctrl+Return"}, "key"),
        ]

        for event, expected in cases:
            assert supervisor._event_argv(surface, event)[0] == expected

    def test_coordinates_outside_the_surface_are_rejected(
        self,
        supervisor: Any,
        tmp_path: Path,
    ) -> None:
        surface = make_surface(tmp_path, width=100, height=100)

        with pytest.raises(SurfaceError, match="outside the 100x100 surface"):
            supervisor._event_argv(surface, {"type": "click", "x": 100, "y": 50})

    def test_missing_coordinates_are_rejected(
        self,
        supervisor: Any,
        tmp_path: Path,
    ) -> None:
        with pytest.raises(SurfaceError, match="integer x and y"):
            supervisor._event_argv(make_surface(tmp_path), {"type": "click"})

    def test_unsupported_button_rejected(self, supervisor: Any, tmp_path: Path) -> None:
        with pytest.raises(SurfaceError, match="unsupported mouse button"):
            supervisor._event_argv(
                make_surface(tmp_path),
                {"type": "click", "x": 1, "y": 1, "button": 9},
            )

    def test_nonsense_key_spec_rejected(self, supervisor: Any, tmp_path: Path) -> None:
        with pytest.raises(SurfaceError, match="xdotool key spec"):
            supervisor._event_argv(
                make_surface(tmp_path),
                {"type": "key", "keys": "ctrl+c; rm -rf /"},
            )

    def test_overlong_type_text_rejected(self, supervisor: Any, tmp_path: Path) -> None:
        with pytest.raises(SurfaceError, match="too long"):
            supervisor._event_argv(
                make_surface(tmp_path),
                {"type": "type", "text": "x" * 5000},
            )

    def test_unknown_event_type_rejected(self, supervisor: Any, tmp_path: Path) -> None:
        with pytest.raises(SurfaceError, match="unknown input event type"):
            supervisor._event_argv(make_surface(tmp_path), {"type": "teleport"})


class TestInjectInput:
    def _open(self, supervisor: Any) -> str:
        return str(supervisor.open_surface({"argv": ["xeyes"]})["surface_id"])

    def test_events_run_through_xdotool_and_bump_seq(
        self,
        supervisor: Any,
        fake_run: FakeRun,
    ) -> None:
        surface_id = self._open(supervisor)

        result = supervisor.inject_input(
            surface_id,
            [{"type": "click", "x": 5, "y": 6}],
            holder=HOLDER_HUMAN,
        )

        assert result == {"ok": True, "seq": 1}
        assert fake_run.argv_for("xdotool")[0][1:] == [
            "mousemove",
            "--sync",
            "5",
            "6",
            "click",
            "1",
        ]

    def test_input_targets_the_surface_display(
        self,
        supervisor: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        surface_id = self._open(supervisor)
        seen: dict[str, Any] = {}

        def _run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
            seen["env"] = kwargs["env"]
            return subprocess.CompletedProcess(argv, 0, b"", b"")

        monkeypatch.setattr("core.surface_supervisor.subprocess.run", _run)
        supervisor.inject_input(
            surface_id,
            [{"type": "key", "keys": "Return"}],
            holder=HOLDER_HUMAN,
        )

        assert seen["env"]["DISPLAY"] == f":{supervisor.get(surface_id).display}"

    def test_model_input_refused_while_the_human_holds_control(
        self,
        supervisor: Any,
        fake_run: FakeRun,
    ) -> None:
        surface_id = self._open(supervisor)
        supervisor.get(surface_id).lease_acquire(HOLDER_HUMAN, 60)
        before = len(fake_run.argv_for("xdotool"))

        with pytest.raises(LeaseRefused):
            supervisor.inject_input(surface_id, [{"type": "click", "x": 1, "y": 1}])

        assert len(fake_run.argv_for("xdotool")) == before

    def test_model_input_takes_the_lease_when_it_is_free(
        self,
        supervisor: Any,
    ) -> None:
        surface_id = self._open(supervisor)

        supervisor.inject_input(surface_id, [{"type": "click", "x": 1, "y": 1}])

        assert supervisor.get(surface_id).lease_state()["holder"] == HOLDER_MODEL

    def test_stale_seq_is_rejected(self, supervisor: Any) -> None:
        surface_id = self._open(supervisor)
        supervisor.inject_input(surface_id, [{"type": "click", "x": 1, "y": 1}])

        with pytest.raises(SurfaceError, match="stale coordinates"):
            supervisor.inject_input(
                surface_id,
                [{"type": "click", "x": 1, "y": 1}],
                seq=0,
            )

    def test_matching_seq_is_accepted(self, supervisor: Any) -> None:
        surface_id = self._open(supervisor)

        result = supervisor.inject_input(
            surface_id,
            [{"type": "click", "x": 1, "y": 1}],
            seq=0,
        )

        assert result["seq"] == 1

    def test_mismatched_resolution_is_rejected(self, supervisor: Any) -> None:
        surface_id = self._open(supervisor)

        with pytest.raises(SurfaceError, match="computed against width 640"):
            supervisor.inject_input(
                surface_id,
                [{"type": "click", "x": 1, "y": 1}],
                width=640,
            )

    def test_a_malformed_event_prevents_the_whole_batch(
        self,
        supervisor: Any,
        fake_run: FakeRun,
    ) -> None:
        """Translate everything before running anything, so a bad event
        halfway down cannot leave the surface half-driven.
        """
        surface_id = self._open(supervisor)
        before = len(fake_run.argv_for("xdotool"))

        with pytest.raises(SurfaceError):
            supervisor.inject_input(
                surface_id,
                [
                    {"type": "click", "x": 1, "y": 1},
                    {"type": "nonsense"},
                ],
                holder=HOLDER_HUMAN,
            )

        assert len(fake_run.argv_for("xdotool")) == before

    def test_empty_event_list_rejected(self, supervisor: Any) -> None:
        with pytest.raises(SurfaceError, match="non-empty list"):
            supervisor.inject_input(self._open(supervisor), [])

    def test_too_many_events_rejected(self, supervisor: Any) -> None:
        events = [{"type": "click", "x": 1, "y": 1}] * (MAX_INPUT_EVENTS + 1)

        with pytest.raises(SurfaceError, match="too many events"):
            supervisor.inject_input(self._open(supervisor), events)

    def test_xdotool_failure_surfaces_its_output(
        self,
        supervisor: Any,
        fake_run: FakeRun,
    ) -> None:
        surface_id = self._open(supervisor)
        fake_run.set("xdotool", 1, stdout=b"no such display")

        with pytest.raises(SurfaceError, match="no such display"):
            supervisor.inject_input(
                surface_id,
                [{"type": "click", "x": 1, "y": 1}],
                holder=HOLDER_HUMAN,
            )


class TestSnapshot:
    def test_returns_base64_png_with_the_current_seq(
        self,
        supervisor: Any,
        fake_run: FakeRun,
    ) -> None:
        surface_id = supervisor.open_surface({"argv": ["xeyes"]})["surface_id"]
        fake_run.set("import", 0, stdout=b"\x89PNG fake bytes")

        result = supervisor.snapshot(surface_id)

        assert result["mime_type"] == "image/png"
        assert base64.b64decode(result["data"]) == b"\x89PNG fake bytes"
        assert result["seq"] == 0
        assert (result["width"], result["height"]) == (1280, 800)

    def test_failure_reports_stderr(self, supervisor: Any, fake_run: FakeRun) -> None:
        surface_id = supervisor.open_surface({"argv": ["xeyes"]})["surface_id"]
        fake_run.set("import", 1, stderr=b"unable to open display")

        with pytest.raises(SurfaceError, match="unable to open display"):
            supervisor.snapshot(surface_id)

    def test_oversized_snapshot_rejected(
        self,
        supervisor: Any,
        fake_run: FakeRun,
    ) -> None:
        surface_id = supervisor.open_surface({"argv": ["xeyes"]})["surface_id"]
        fake_run.set("import", 0, stdout=b"x" * (MAX_SNAPSHOT_BYTES + 1))

        with pytest.raises(SurfaceError, match="too large"):
            supervisor.snapshot(surface_id)

    def test_snapshot_counts_as_activity(
        self,
        supervisor: Any,
        fake_run: FakeRun,
    ) -> None:
        surface_id = supervisor.open_surface({"argv": ["xeyes"]})["surface_id"]
        fake_run.set("import", 0, stdout=b"png")
        surface = supervisor.get(surface_id)
        surface._last_activity = time.time() - 500

        supervisor.snapshot(surface_id)

        assert surface.idle_seconds < 5


class TestReaping:
    def test_orphans_from_a_previous_daemon_are_killed_not_adopted(
        self,
        supervisor: Any,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        surfaces_dir = tmp_path / "surfaces"
        surfaces_dir.mkdir(parents=True, exist_ok=True)
        (surfaces_dir / "srf_deadbeef.json").write_text(
            json.dumps(
                {
                    "surface_id": "srf_deadbeef",
                    "pids": [1234, 1235],
                    "daemon_pid": 999_999,
                },
            ),
        )
        (surfaces_dir / "srf_deadbeef.pw").write_text("x")
        record: list[tuple[int, int]] = []
        monkeypatch.setattr("core.surface_supervisor._pid_alive", lambda pid: True)
        monkeypatch.setattr(
            "core.surface_supervisor._kill_group",
            lambda pid, sig: record.append((pid, sig)),
        )

        assert supervisor.reap_orphans() == 2

        assert [pid for pid, _sig in record] == [1234, 1235]
        assert not (surfaces_dir / "srf_deadbeef.json").exists()
        assert not (surfaces_dir / "srf_deadbeef.pw").exists()
        assert supervisor.list_surfaces() == []

    def test_our_own_surfaces_are_left_alone(
        self,
        supervisor: Any,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        result = supervisor.open_surface({"argv": ["xeyes"]})
        record: list[Any] = []
        monkeypatch.setattr(
            "core.surface_supervisor._kill_group",
            lambda pid, sig: record.append(pid),
        )

        assert supervisor.reap_orphans() == 0

        assert record == []
        assert (tmp_path / "surfaces" / f"{result['surface_id']}.json").exists()

    def test_dead_pids_are_not_counted(
        self,
        supervisor: Any,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        surfaces_dir = tmp_path / "surfaces"
        surfaces_dir.mkdir(parents=True, exist_ok=True)
        (surfaces_dir / "srf_deadbeef.json").write_text(
            json.dumps(
                {"surface_id": "srf_deadbeef", "pids": [1], "daemon_pid": 999_999},
            ),
        )
        monkeypatch.setattr("core.surface_supervisor._pid_alive", lambda pid: False)

        assert supervisor.reap_orphans() == 0

    def test_unreadable_state_file_is_discarded(
        self,
        supervisor: Any,
        tmp_path: Path,
    ) -> None:
        surfaces_dir = tmp_path / "surfaces"
        surfaces_dir.mkdir(parents=True, exist_ok=True)
        (surfaces_dir / "srf_garbage.json").write_text("{not json")

        assert supervisor.reap_orphans() == 0
        assert not (surfaces_dir / "srf_garbage.json").exists()

    def test_missing_directory_is_not_an_error(self, tmp_path: Path) -> None:
        assert SurfaceSupervisor(tmp_path / "nope").reap_orphans() == 0

    def test_idle_surface_is_torn_down(
        self,
        supervisor: Any,
        killed: list[Any],
    ) -> None:
        surface_id = supervisor.open_surface({"argv": ["xeyes"]})["surface_id"]
        supervisor.get(surface_id)._last_activity = time.time() - 10_000

        supervisor._reap_once()

        assert supervisor.list_surfaces() == []

    def test_active_surface_is_left_running(
        self,
        supervisor: Any,
        killed: list[Any],
    ) -> None:
        supervisor.open_surface({"argv": ["xeyes"]})

        supervisor._reap_once()

        assert len(supervisor.list_surfaces()) == 1

    def test_self_exited_surface_is_cleaned_up(
        self,
        supervisor: Any,
        killed: list[Any],
    ) -> None:
        supervisor.open_surface({"argv": ["xeyes"]})
        next(p for p in FakePopen.spawned if p.name == "xeyes").exit(0)

        supervisor._reap_once()

        assert supervisor.list_surfaces() == []


class TestDispatch:
    def test_routes_the_documented_methods(
        self,
        supervisor: Any,
        fake_run: FakeRun,
    ) -> None:
        opened = supervisor.dispatch("surface_open", {"spec": {"argv": ["xeyes"]}})
        surface_id = opened["surface_id"]
        fake_run.set("import", 0, stdout=b"png")

        listed = supervisor.dispatch("surface_list", {})
        assert [s["surface_id"] for s in listed["surfaces"]] == [surface_id]
        assert listed["profiles"] == []

        touched = supervisor.dispatch("surface_touch", {"surface_id": surface_id})
        assert touched["surface_id"] == surface_id
        assert touched["lease"] == {"holder": None, "expires_at": 0.0}

        assert (
            supervisor.dispatch(
                "surface_snapshot",
                {"surface_id": surface_id},
            )["mime_type"]
            == "image/png"
        )
        assert supervisor.dispatch(
            "surface_input",
            {
                "surface_id": surface_id,
                "events": [{"type": "click", "x": 1, "y": 1}],
                "holder": HOLDER_HUMAN,
            },
        ) == {"ok": True, "seq": 1}
        assert (
            supervisor.dispatch(
                "surface_lease_acquire",
                {"surface_id": surface_id, "holder": HOLDER_HUMAN},
            )["holder"]
            == HOLDER_HUMAN
        )
        assert (
            supervisor.dispatch(
                "surface_lease_touch",
                {"surface_id": surface_id, "holder": HOLDER_HUMAN},
            )["holder"]
            == HOLDER_HUMAN
        )
        assert (
            supervisor.dispatch(
                "surface_lease_release",
                {"surface_id": surface_id, "holder": HOLDER_HUMAN},
            )["holder"]
            is None
        )
        assert supervisor.dispatch("surface_close", {"surface_id": surface_id}) == {
            "ok": True,
        }

    def test_touch_keeps_the_reaper_off_and_reports_the_lease(
        self,
        supervisor: Any,
    ) -> None:
        """The panel heartbeats through this while it is attached, so one
        call has to do both jobs: prove somebody is watching, and hand back
        enough to redraw the lease banner without a second round trip.
        """
        surface_id = supervisor.open_surface({"argv": ["xeyes"]})["surface_id"]
        surface = supervisor.get(surface_id)
        surface._last_activity = time.time() - 10_000
        surface.lease_acquire(HOLDER_HUMAN, 60)

        result = supervisor.dispatch("surface_touch", {"surface_id": surface_id})

        assert result["lease"]["holder"] == HOLDER_HUMAN
        supervisor._reap_once()
        assert len(supervisor.list_surfaces()) == 1

    def test_open_defaults_to_the_user_source(self, supervisor: Any) -> None:
        """The panel's path may pass argv; only the MCP server marks itself
        as the model.
        """
        assert supervisor.dispatch("surface_open", {"spec": {"argv": ["xeyes"]}})

    def test_open_honours_an_explicit_model_source(self, supervisor: Any) -> None:
        with pytest.raises(SurfaceError, match="must name a profile"):
            supervisor.dispatch(
                "surface_open",
                {"spec": {"argv": ["xeyes"]}, "source": "model"},
            )

    def test_unknown_method_raises(self, supervisor: Any) -> None:
        with pytest.raises(SurfaceError, match="unknown surface method"):
            supervisor.dispatch("surface_teleport", {})
