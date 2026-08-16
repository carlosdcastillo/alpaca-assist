"""Remote-side lifecycle for live app surfaces (Xvfb + x11vnc + one GUI app).

Lives in the pack_daemon.py process on the remote host. That placement is
deliberate and load-bearing:

  * The daemon is exactly per-Pack-tab, which is the granularity the panel
    wants — one supervisor per conversation, not per CLI invocation.
  * It already survives SSH drops and app restarts by design, so a surface
    survives a network blip the same way the conversation does.
  * It is on the machine with the display, so it can reap its own children
    instead of guessing about processes across a network.

The last point cuts both ways: the daemon deliberately outlives the local
app (core/pack_transport.py's close() never signals it to stop), so nothing
about closing a tab or quitting the app tears a surface down. Three
independent guards exist for that, and they only ever *kill*, never restore:

  1. `install_process_guards()` — atexit + SIGTERM, kills every child group.
  2. `reap_orphans()` — run at daemon start, destroys surfaces recorded by a
     previous daemon incarnation rather than adopting them.
  3. The idle reaper thread — a surface with no RPC activity for
     SURFACE_IDLE_TIMEOUT is torn down.

A surface is never resurrected. `surface_open` always builds a fresh display,
VNC server, and application from scratch; there is no attach-to-existing path
and no persistence of anything but pids-to-kill.

Importable on any platform (it is pure subprocess/threading plumbing, so the
unit tests run on Windows), but `open_surface` refuses to do anything useful
without the X tooling, which is checked up front and reported as a clean
error rather than a stack of failing spawns.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# A surface with no RPC activity at all for this long is torn down. This is
# the real backstop for orphans, because closing a Pack tab locally does not
# stop the remote daemon and never has. The panel heartbeats while it is
# attached, so "idle" means nobody is watching and nothing is driving.
SURFACE_IDLE_TIMEOUT = float(os.environ.get("ALPACA_SURFACE_IDLE_TIMEOUT", 30 * 60))
IDLE_REAPER_INTERVAL = 30.0

# :99 is already spoken for by the RUNBOOK's own headless-app workflow.
DISPLAY_SEARCH_START = 100
DISPLAY_SEARCH_END = 200

DEFAULT_WIDTH = 1280
DEFAULT_HEIGHT = 800
MIN_DIMENSION = 320
MAX_DIMENSION = 3840

# Classic RFB authentication uses only the first 8 bytes of the password —
# x11vnc -storepasswd truncates silently. Generating more would be theatre.
VNC_PASSWORD_BYTES = 8

STARTUP_TIMEOUT = 15.0
TERMINATE_GRACE = 3.0
APP_PAINT_TIMEOUT = 5.0
MAX_SNAPSHOT_BYTES = 8 * 1024 * 1024
SNAPSHOT_TIMEOUT = 20.0
INPUT_TIMEOUT = 10.0

LEASE_DEFAULT_TTL = 60.0
LEASE_MAX_TTL = 600.0
HOLDER_HUMAN = "human"
HOLDER_MODEL = "model"

MAX_INPUT_EVENTS = 32
MAX_TYPE_CHARS = 4096
# xdotool key specs: "Return", "ctrl+c", "shift+Tab". Deliberately narrow —
# nothing here reaches a shell (every spawn is an argv list), so this is
# about rejecting nonsense early with a useful message, not about quoting.
_KEYSPEC_RE = re.compile(r"^[A-Za-z0-9_]+(\+[A-Za-z0-9_]+)*$")
_PROFILE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


class SurfaceError(RuntimeError):
    """Raised for any surface operation that cannot be completed."""


class LeaseRefused(SurfaceError):
    """Raised when input is rejected because someone else holds control."""


def _free_port() -> int:
    """Ask the kernel for a free loopback port, then hand it to a child.

    Racy in principle. In practice the window is microseconds and every
    consumer of this either fails fast and gets retried, or (for x11vnc)
    refuses to start, which the readiness wait reports immediately.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _port_open(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


# SIGKILL does not exist on Windows. Surfaces only ever run on the Linux
# Pack host, but this module is deliberately importable everywhere so its
# tests can run here, and an AttributeError on teardown would make that a
# lie. Falling back to SIGTERM is meaningless on Windows anyway — the
# escalation only has teeth where process groups do.
_SIGKILL = getattr(signal, "SIGKILL", signal.SIGTERM)


def _kill_group(pid: int, sig: int) -> None:
    """Signal a child's whole process group, falling back to the bare pid.

    Every child is spawned with start_new_session=True precisely so this
    works: an app that forks helpers (a wrapper script, a browser's zygote)
    leaves them in the same group, and killing the group takes the lot.
    """
    try:
        if hasattr(os, "killpg"):
            # Guarded by the hasattr above; mypy checking against win32 stubs
            # cannot see that, and these only ever run on the Pack host.
            os.killpg(os.getpgid(pid), sig)
        else:  # pragma: no cover - Windows has neither; tests fake the spawn
            os.kill(pid, sig)
    except (ProcessLookupError, PermissionError, OSError):
        pass


class Surface:
    """One headless display, its VNC server, and the application on it."""

    def __init__(
        self,
        surface_id: str,
        display: int,
        width: int,
        height: int,
        description: str,
        rfb_port: int,
        ws_port: int,
        password: str,
        log_path: Path,
    ) -> None:
        self.id = surface_id
        self.display = display
        self.width = width
        self.height = height
        self.description = description
        self.rfb_port = rfb_port
        self.ws_port = ws_port
        self.password = password
        self.log_path = log_path
        self.created_at = time.time()
        self.processes: list[tuple[str, Any]] = []
        self.closed = False

        self._lock = threading.RLock()
        self._last_activity = time.time()
        # Bumped after every successful injected input. A model that clicks
        # using coordinates read from an older snapshot is rejected rather
        # than allowed to act on a screen that has since moved underneath it.
        self._seq = 0
        self._lease_holder: str | None = None
        self._lease_expires_at = 0.0

    # -- bookkeeping ----------------------------------------------------

    def touch(self) -> None:
        with self._lock:
            self._last_activity = time.time()

    @property
    def idle_seconds(self) -> float:
        with self._lock:
            return time.time() - self._last_activity

    @property
    def seq(self) -> int:
        with self._lock:
            return self._seq

    def pids(self) -> list[int]:
        return [proc.pid for _name, proc in self.processes]

    def alive(self) -> bool:
        """True while every process this surface owns is still running.

        Deliberately strict: if the app exits, or x11vnc dies, the surface is
        no longer something the panel can usefully attach to, so it should be
        reported dead and reaped rather than left as a black rectangle.
        """
        if self.closed or not self.processes:
            return False
        return all(proc.poll() is None for _name, proc in self.processes)

    def descriptor(self) -> dict[str, Any]:
        return {
            "surface_id": self.id,
            "display": f":{self.display}",
            "width": self.width,
            "height": self.height,
            "description": self.description,
            "created_at": self.created_at,
            "alive": self.alive(),
            "seq": self.seq,
            "idle_seconds": round(self.idle_seconds, 1),
            "lease": self.lease_state(),
        }

    # -- lease ----------------------------------------------------------

    def lease_state(self) -> dict[str, Any]:
        with self._lock:
            if self._lease_holder is None or time.time() >= self._lease_expires_at:
                return {"holder": None, "expires_at": 0.0}
            return {
                "holder": self._lease_holder,
                "expires_at": self._lease_expires_at,
            }

    def _human_lease_live(self) -> bool:
        with self._lock:
            return (
                self._lease_holder == HOLDER_HUMAN
                and time.time() < self._lease_expires_at
            )

    def lease_acquire(self, holder: str, ttl: float) -> dict[str, Any]:
        """Take or refresh control.

        The human always wins, unconditionally and without waiting for the
        model's lease to expire — that asymmetry is the whole point. The
        model's own input path never blocks the human either, since human
        pointer/key events go noVNC -> x11vnc and never pass through here.
        """
        if holder not in (HOLDER_HUMAN, HOLDER_MODEL):
            raise SurfaceError(f"unknown lease holder {holder!r}")
        ttl = max(1.0, min(float(ttl), LEASE_MAX_TTL))
        with self._lock:
            if holder == HOLDER_MODEL and self._human_lease_live():
                raise LeaseRefused(
                    "the human holds control of this surface until "
                    f"{time.strftime('%H:%M:%S', time.localtime(self._lease_expires_at))}",
                )
            self._lease_holder = holder
            self._lease_expires_at = time.time() + ttl
            self._last_activity = time.time()
        return self.lease_state()

    def lease_release(self, holder: str) -> dict[str, Any]:
        with self._lock:
            if self._lease_holder == holder:
                self._lease_holder = None
                self._lease_expires_at = 0.0
            self._last_activity = time.time()
        return self.lease_state()

    def check_seq(self, seq: int | None, width: int | None, height: int | None) -> None:
        """Reject input computed against a stale view of the screen."""
        if seq is not None and int(seq) != self.seq:
            raise SurfaceError(
                f"stale coordinates: they were computed against seq {seq}, "
                f"but the surface is now at seq {self.seq}. Take a fresh "
                "snapshot and recompute.",
            )
        if width is not None and int(width) != self.width:
            raise SurfaceError(
                f"coordinates were computed against width {width}, "
                f"but the surface is {self.width} wide",
            )
        if height is not None and int(height) != self.height:
            raise SurfaceError(
                f"coordinates were computed against height {height}, "
                f"but the surface is {self.height} tall",
            )

    def bump_seq(self) -> int:
        with self._lock:
            self._seq += 1
            self._last_activity = time.time()
            return self._seq


class SurfaceSupervisor:
    """Owns every surface belonging to one Pack session."""

    def __init__(self, session_dir: Path | str) -> None:
        self.session_dir = Path(session_dir)
        self.surfaces_dir = self.session_dir / "surfaces"
        self._surfaces: dict[str, Surface] = {}
        self._lock = threading.RLock()
        self._reaper: threading.Thread | None = None
        self._stop = threading.Event()
        self._profiles: dict[str, dict[str, Any]] | None = None

    # -- environment ----------------------------------------------------

    @staticmethod
    def missing_tools() -> list[str]:
        """Which required binaries are absent. Empty list means usable."""
        return [
            name for name in ("Xvfb", "x11vnc", "xdotool") if not shutil.which(name)
        ]

    @staticmethod
    def available() -> bool:
        return not SurfaceSupervisor.missing_tools()

    @staticmethod
    def _use_websockify() -> bool:
        """Whether a separate websockify hop is needed in front of x11vnc.

        x11vnc built against a libvncserver with WITH_WEBSOCKETS accepts a
        WebSocket handshake directly on its RFB port, which drops this from
        four processes per surface to three. Whether a given build does is
        not something to guess at from here, so: honour an explicit override,
        otherwise use websockify when it is installed (the predictable case)
        and assume built-in support when it is not.
        """
        override = os.environ.get("ALPACA_SURFACE_WEBSOCKIFY")
        if override is not None:
            return override.strip().lower() in ("1", "true", "yes")
        return shutil.which("websockify") is not None

    def _require_available(self) -> None:
        missing = self.missing_tools()
        if missing:
            raise SurfaceError(
                "no display available on this host: missing "
                + ", ".join(missing)
                + ". Surfaces need Xvfb, x11vnc and xdotool "
                "(see agents/SETUP.md).",
            )

    # -- profiles -------------------------------------------------------

    def profiles(self) -> dict[str, dict[str, Any]]:
        """Named launchable apps, from surface_profiles.json.

        A convenience catalog, not an access-control boundary: both the
        model and a human may also pass a raw argv to surface_open (see
        _resolve_spec). This just gives a curated, named shortcut so
        `surface_list` has something more useful to show than a blank
        catalog, and so a common app doesn't need its full command line
        retyped every time.
        """
        if self._profiles is not None:
            return self._profiles
        loaded: dict[str, dict[str, Any]] = {}
        for candidate in (
            self.session_dir / "surface_profiles.json",
            Path(__file__).resolve().parent.parent / "surface_profiles.json",
        ):
            if not candidate.is_file():
                continue
            try:
                with open(candidate, encoding="utf-8") as handle:
                    raw = json.load(handle)
            except (OSError, ValueError):
                logger.warning(
                    f"Could not read surface profiles from {candidate}",
                    exc_info=True,
                )
                continue
            if isinstance(raw, dict):
                for name, entry in raw.items():
                    if (
                        _PROFILE_NAME_RE.match(str(name))
                        and isinstance(entry, dict)
                        and isinstance(entry.get("argv"), list)
                        and entry["argv"]
                        and all(isinstance(part, str) for part in entry["argv"])
                    ):
                        loaded.setdefault(str(name), entry)
            break
        self._profiles = loaded
        return loaded

    def _resolve_spec(
        self,
        spec: dict[str, Any] | None,
        source: str,
    ) -> tuple[list[str], str]:
        """Turn a request into (argv, description).

        `source` ("user" or "model") is recorded but no longer gates argv:
        the model already has unrestricted shell execution on this host via
        internal_run_shell_command, and xterm is itself a permitted
        profile, so refusing a model-supplied argv here was not actually a
        security boundary -- it only made the model's path slightly more
        annoying than the human's for no real protection.
        """
        spec = spec or {}
        profile_name = spec.get("profile")
        if profile_name:
            profile = self.profiles().get(str(profile_name))
            if profile is None:
                known = ", ".join(sorted(self.profiles())) or "none configured"
                raise SurfaceError(
                    f"unknown surface profile {profile_name!r}; available: {known}",
                )
            argv = [str(part) for part in profile["argv"]]
            description = str(profile.get("description") or profile_name)
            return argv, description

        argv_spec = spec.get("argv")
        if not argv_spec:
            raise SurfaceError("surface_open needs either a profile or an argv")
        if not isinstance(argv_spec, list) or not all(
            isinstance(part, str) for part in argv_spec
        ):
            raise SurfaceError("argv must be a list of strings")
        argv = [str(part) for part in argv_spec]
        return argv, str(spec.get("description") or Path(argv[0]).name)

    # -- allocation -----------------------------------------------------

    @staticmethod
    def _display_taken(number: int) -> bool:
        return (
            Path(f"/tmp/.X{number}-lock").exists()
            or Path(f"/tmp/.X11-unix/X{number}").exists()
        )

    def _alloc_display(self) -> int:
        for number in range(DISPLAY_SEARCH_START, DISPLAY_SEARCH_END):
            if not self._display_taken(number):
                return number
        raise SurfaceError("no free X display number available")

    @staticmethod
    def _clamp_dimension(value: Any, default: int) -> int:
        try:
            number = int(value)
        except (TypeError, ValueError):
            return default
        return max(MIN_DIMENSION, min(number, MAX_DIMENSION))

    # -- process plumbing -----------------------------------------------

    def _spawn(
        self,
        surface: Surface,
        name: str,
        argv: list[str],
        env: dict[str, str] | None = None,
    ) -> Any:
        log_handle = open(surface.log_path, "a", encoding="utf-8", errors="replace")
        try:
            log_handle.write(f"\n--- {name}: {' '.join(argv)}\n")
            log_handle.flush()
            proc = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                env=env,
                start_new_session=True,
            )
        finally:
            log_handle.close()
        surface.processes.append((name, proc))
        return proc

    def _log_tail(self, surface: Surface, lines: int = 12) -> str:
        try:
            with open(surface.log_path, encoding="utf-8", errors="replace") as handle:
                return "\n".join(handle.read().splitlines()[-lines:])
        except OSError:
            return ""

    def _wait_for(
        self,
        surface: Surface,
        what: str,
        predicate: Any,
        timeout: float = STARTUP_TIMEOUT,
    ) -> None:
        """Poll until ready, failing early if the process we need already died."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            for name, proc in surface.processes:
                if proc.poll() is not None:
                    raise SurfaceError(
                        f"{name} exited with code {proc.returncode} while "
                        f"waiting for {what}. Log tail:\n{self._log_tail(surface)}",
                    )
            time.sleep(0.1)
        raise SurfaceError(
            f"timed out after {timeout:.0f}s waiting for {what}. "
            f"Log tail:\n{self._log_tail(surface)}",
        )

    # -- lifecycle ------------------------------------------------------

    def open_surface(
        self,
        spec: dict[str, Any] | None = None,
        width: Any = DEFAULT_WIDTH,
        height: Any = DEFAULT_HEIGHT,
        source: str = "user",
    ) -> dict[str, Any]:
        """Build a fresh display, VNC server and app. Never attaches to an
        existing one — surfaces are ephemeral by design.
        """
        self._require_available()
        argv, description = self._resolve_spec(spec, source)
        width = self._clamp_dimension(width, DEFAULT_WIDTH)
        height = self._clamp_dimension(height, DEFAULT_HEIGHT)

        self.surfaces_dir.mkdir(parents=True, exist_ok=True)
        surface_id = f"srf_{secrets.token_hex(4)}"
        display = self._alloc_display()
        rfb_port = _free_port()
        use_websockify = self._use_websockify()
        ws_port = _free_port() if use_websockify else rfb_port
        password = secrets.token_urlsafe(16)[:VNC_PASSWORD_BYTES]

        surface = Surface(
            surface_id=surface_id,
            display=display,
            width=width,
            height=height,
            description=description,
            rfb_port=rfb_port,
            ws_port=ws_port,
            password=password,
            log_path=self.surfaces_dir / f"{surface_id}.log",
        )

        try:
            self._start_processes(surface, argv, use_websockify)
        except Exception:
            self._destroy(surface)
            raise

        with self._lock:
            self._surfaces[surface_id] = surface
        self._write_state(surface)
        self._ensure_reaper()

        logger.info(
            f"Surface {surface_id} up on :{display} "
            f"({width}x{height}, ws port {ws_port}): {description}",
        )
        return {
            "surface_id": surface_id,
            "ws_port": ws_port,
            "password": password,
            "width": width,
            "height": height,
            "description": description,
            "seq": surface.seq,
        }

    def _start_processes(
        self,
        surface: Surface,
        argv: list[str],
        use_websockify: bool,
    ) -> None:
        display_arg = f":{surface.display}"

        # +extension RANDR costs nothing now and is what makes a resizable
        # panel possible later without rebuilding the display.
        self._spawn(
            surface,
            "Xvfb",
            [
                "Xvfb",
                display_arg,
                "-screen",
                "0",
                f"{surface.width}x{surface.height}x24",
                "+extension",
                "RANDR",
                "-nolisten",
                "tcp",
            ],
        )
        self._wait_for(
            surface,
            f"X display {display_arg}",
            lambda: Path(f"/tmp/.X11-unix/X{surface.display}").exists(),
        )

        password_file = self.surfaces_dir / f"{surface.id}.pw"
        self._store_password(surface, password_file)

        self._spawn(
            surface,
            "x11vnc",
            [
                "x11vnc",
                "-display",
                display_arg,
                "-localhost",
                "-rfbport",
                str(surface.rfb_port),
                "-rfbauth",
                str(password_file),
                "-forever",
                # -shared is what lets a human and an RFB-driving model watch
                # the same pixels rather than two parallel views of one app.
                "-shared",
                "-xkb",
                "-noxdamage",
                "-quiet",
            ],
        )
        self._wait_for(
            surface,
            f"x11vnc on port {surface.rfb_port}",
            lambda: _port_open(surface.rfb_port),
        )

        if use_websockify:
            self._spawn(
                surface,
                "websockify",
                [
                    "websockify",
                    f"127.0.0.1:{surface.ws_port}",
                    f"127.0.0.1:{surface.rfb_port}",
                ],
            )
            self._wait_for(
                surface,
                f"websockify on port {surface.ws_port}",
                lambda: _port_open(surface.ws_port),
            )

        env = dict(os.environ)
        env["DISPLAY"] = display_arg
        app_proc = self._spawn(surface, "app", argv, env=env)
        self._wait_for_paint(surface, app_proc)

    def _wait_for_paint(
        self,
        surface: Surface,
        app_proc: Any,
        timeout: float = APP_PAINT_TIMEOUT,
    ) -> None:
        """Give the app a moment to map a window before open_surface returns.

        Xvfb and x11vnc being up doesn't mean the app has drawn anything yet
        -- a snapshot taken the instant open_surface returns can catch a
        still-blank display. This is deliberately best-effort: it never
        raises, since a profile can legitimately be slow, or windowless
        until the user interacts with it. It just gives the common case
        (an app that paints promptly) time to settle first.
        """
        if not shutil.which("xdotool"):
            return
        env = dict(os.environ)
        env["DISPLAY"] = f":{surface.display}"
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if app_proc.poll() is not None:
                return  # app already exited -- no window is coming
            result = subprocess.run(
                ["xdotool", "search", "--onlyvisible", ""],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=env,
                timeout=2,
                check=False,
            )
            if result.returncode == 0 and result.stdout.strip():
                return
            time.sleep(0.1)

    def _store_password(self, surface: Surface, password_file: Path) -> None:
        """Write the obfuscated VNC password file at 0600.

        Never -nopw: x11vnc is bound to loopback and only reachable through
        an SSH tunnel, but a shared remote host means other local users could
        otherwise connect to the port directly.
        """
        result = subprocess.run(
            ["x11vnc", "-storepasswd", surface.password, str(password_file)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=STARTUP_TIMEOUT,
            check=False,
        )
        if result.returncode != 0 or not password_file.exists():
            output = (result.stdout or b"").decode("utf-8", errors="replace")
            raise SurfaceError(f"could not store the VNC password: {output.strip()}")
        try:
            password_file.chmod(0o600)
        except OSError:
            pass

    def close_surface(self, surface_id: str) -> dict[str, Any]:
        with self._lock:
            surface = self._surfaces.pop(surface_id, None)
        if surface is None:
            return {"ok": True, "already_closed": True}
        self._destroy(surface)
        return {"ok": True}

    def _destroy(self, surface: Surface) -> None:
        surface.closed = True
        for _name, proc in reversed(surface.processes):
            if proc.poll() is None:
                _kill_group(proc.pid, signal.SIGTERM)
        deadline = time.monotonic() + TERMINATE_GRACE
        for _name, proc in reversed(surface.processes):
            remaining = max(0.0, deadline - time.monotonic())
            try:
                proc.wait(timeout=remaining)
            except Exception:
                pass
        for _name, proc in reversed(surface.processes):
            if proc.poll() is None:
                _kill_group(proc.pid, _SIGKILL)
        for suffix in (".json", ".pw"):
            (self.surfaces_dir / f"{surface.id}{suffix}").unlink(missing_ok=True)
        logger.info(f"Surface {surface.id} torn down")

    def close_all(self) -> None:
        with self._lock:
            surfaces = list(self._surfaces.values())
            self._surfaces.clear()
        for surface in surfaces:
            self._destroy(surface)

    def shutdown(self) -> None:
        self._stop.set()
        self.close_all()

    # -- state files and orphan control ---------------------------------

    def _write_state(self, surface: Surface) -> None:
        """Record pids so a *later* daemon can kill what this one leaves."""
        path = self.surfaces_dir / f"{surface.id}.json"
        payload = {
            "surface_id": surface.id,
            "display": surface.display,
            "pids": surface.pids(),
            "daemon_pid": os.getpid(),
            "created_at": surface.created_at,
            "description": surface.description,
        }
        try:
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle)
        except OSError:
            logger.warning(
                f"Could not record surface state for {surface.id}",
                exc_info=True,
            )

    def reap_orphans(self) -> int:
        """Kill surfaces left behind by a previous daemon incarnation.

        Called at daemon start. Deliberately destructive: a surface from a
        dead daemon has no owner, no lease state, and nothing that could
        attach to it, so adopting it would only produce a live display
        nothing can reach.
        """
        if not self.surfaces_dir.is_dir():
            return 0
        reaped = 0
        for path in sorted(self.surfaces_dir.glob("srf_*.json")):
            try:
                with open(path, encoding="utf-8") as handle:
                    payload = json.load(handle)
            except (OSError, ValueError):
                path.unlink(missing_ok=True)
                continue
            if payload.get("daemon_pid") == os.getpid():
                continue
            for pid in payload.get("pids", []):
                if isinstance(pid, int) and _pid_alive(pid):
                    _kill_group(pid, signal.SIGTERM)
                    reaped += 1
            surface_id = payload.get("surface_id", path.stem)
            path.unlink(missing_ok=True)
            (self.surfaces_dir / f"{surface_id}.pw").unlink(missing_ok=True)
            logger.info(f"Reaped orphaned surface {surface_id}")
        return reaped

    def install_process_guards(self) -> None:
        """Kill our children when this process exits, however it exits."""
        import atexit

        atexit.register(self.close_all)

        def _handler(signum: int, _frame: Any) -> None:
            self.shutdown()
            # Restore default disposition and re-raise so the exit status
            # still reports the signal rather than a clean 0.
            try:
                signal.signal(signum, signal.SIG_DFL)
                os.kill(os.getpid(), signum)
            except OSError:  # pragma: no cover - best effort
                raise SystemExit(1)

        for signum in (signal.SIGTERM, signal.SIGINT):
            try:
                signal.signal(signum, _handler)
            except (ValueError, OSError):  # pragma: no cover - non-main thread
                pass

    def _ensure_reaper(self) -> None:
        if self._reaper is not None and self._reaper.is_alive():
            return
        self._reaper = threading.Thread(
            target=self._idle_reaper_loop,
            name="surface-idle-reaper",
            daemon=True,
        )
        self._reaper.start()

    def _idle_reaper_loop(self) -> None:
        while not self._stop.wait(IDLE_REAPER_INTERVAL):
            try:
                self._reap_once()
            except Exception:  # pragma: no cover - defensive
                logger.exception("Surface idle reaper raised")

    def _reap_once(self) -> None:
        with self._lock:
            candidates = list(self._surfaces.items())
        for surface_id, surface in candidates:
            if not surface.alive():
                logger.info(f"Surface {surface_id} exited on its own; cleaning up")
                self.close_surface(surface_id)
            elif surface.idle_seconds > SURFACE_IDLE_TIMEOUT:
                logger.info(
                    f"Surface {surface_id} idle for {surface.idle_seconds:.0f}s; "
                    "tearing it down",
                )
                self.close_surface(surface_id)

    # -- accessors ------------------------------------------------------

    def get(self, surface_id: str) -> Surface:
        with self._lock:
            surface = self._surfaces.get(surface_id)
        if surface is None:
            raise SurfaceError(f"unknown surface {surface_id!r}")
        return surface

    def list_surfaces(self) -> list[dict[str, Any]]:
        with self._lock:
            surfaces = list(self._surfaces.values())
        return [surface.descriptor() for surface in surfaces]

    # -- observation and input ------------------------------------------

    def snapshot(self, surface_id: str) -> dict[str, Any]:
        """Capture the framebuffer as a PNG.

        Uses `import -window root`, which is already the documented capture
        method for this host (agents/RUNBOOK.md) and is already installed
        wherever the Pack workflow runs.
        """
        surface = self.get(surface_id)
        surface.touch()
        if not shutil.which("import"):
            raise SurfaceError(
                "cannot take a snapshot: ImageMagick's `import` is not installed",
            )
        env = dict(os.environ)
        env["DISPLAY"] = f":{surface.display}"
        result = subprocess.run(
            [
                "import",
                "-display",
                f":{surface.display}",
                "-window",
                "root",
                "-silent",
                "png:-",
            ],
            capture_output=True,
            env=env,
            timeout=SNAPSHOT_TIMEOUT,
            check=False,
        )
        if result.returncode != 0 or not result.stdout:
            detail = (result.stderr or b"").decode("utf-8", errors="replace").strip()
            raise SurfaceError(f"snapshot failed: {detail or 'no image produced'}")
        if len(result.stdout) > MAX_SNAPSHOT_BYTES:
            raise SurfaceError(
                f"snapshot is too large ({len(result.stdout)} bytes); "
                f"maximum is {MAX_SNAPSHOT_BYTES}",
            )
        return {
            "surface_id": surface_id,
            "mime_type": "image/png",
            "data": base64.b64encode(result.stdout).decode("ascii"),
            "width": surface.width,
            "height": surface.height,
            "seq": surface.seq,
            "description": surface.description,
        }

    def _event_argv(self, surface: Surface, event: dict[str, Any]) -> list[str]:
        """Translate one input event into an xdotool invocation."""
        kind = str(event.get("type", "")).lower()

        def _coords() -> list[str]:
            try:
                x, y = int(event["x"]), int(event["y"])
            except (KeyError, TypeError, ValueError) as exc:
                raise SurfaceError(f"{kind} event needs integer x and y") from exc
            if not (0 <= x < surface.width and 0 <= y < surface.height):
                raise SurfaceError(
                    f"({x}, {y}) is outside the "
                    f"{surface.width}x{surface.height} surface",
                )
            return [str(x), str(y)]

        def _button() -> str:
            button = int(event.get("button", 1))
            if button not in (1, 2, 3, 4, 5):
                raise SurfaceError(f"unsupported mouse button {button}")
            return str(button)

        if kind == "move":
            return ["mousemove", "--sync", *_coords()]
        if kind == "click":
            return ["mousemove", "--sync", *_coords(), "click", _button()]
        if kind == "doubleclick":
            return [
                "mousemove",
                "--sync",
                *_coords(),
                "click",
                "--repeat",
                "2",
                _button(),
            ]
        if kind == "mousedown":
            return ["mousemove", "--sync", *_coords(), "mousedown", _button()]
        if kind == "mouseup":
            return ["mousemove", "--sync", *_coords(), "mouseup", _button()]
        if kind == "type":
            text = event.get("text", "")
            if not isinstance(text, str) or not text:
                raise SurfaceError("type event needs a non-empty text string")
            if len(text) > MAX_TYPE_CHARS:
                raise SurfaceError(
                    f"type event text is too long ({len(text)} > {MAX_TYPE_CHARS})",
                )
            return ["type", "--clearmodifiers", "--delay", "12", text]
        if kind == "key":
            keys = event.get("keys", "")
            if not isinstance(keys, str) or _KEYSPEC_RE.match(keys) is None:
                raise SurfaceError(
                    f"key event needs an xdotool key spec like 'ctrl+Return', got {keys!r}",
                )
            return ["key", "--clearmodifiers", keys]
        raise SurfaceError(f"unknown input event type {kind!r}")

    def inject_input(
        self,
        surface_id: str,
        events: list[dict[str, Any]],
        holder: str = HOLDER_MODEL,
        seq: int | None = None,
        width: int | None = None,
        height: int | None = None,
    ) -> dict[str, Any]:
        """Inject events with xdotool, subject to the control lease.

        Note this is only the *model's* path. Human input travels
        noVNC -> x11vnc and never reaches this code, which is exactly why
        the human can never be blocked by a stalled lease holder.
        """
        surface = self.get(surface_id)
        if not isinstance(events, list) or not events:
            raise SurfaceError("inject_input needs a non-empty list of events")
        if len(events) > MAX_INPUT_EVENTS:
            raise SurfaceError(
                f"too many events in one call ({len(events)} > {MAX_INPUT_EVENTS})",
            )
        if not shutil.which("xdotool"):
            raise SurfaceError("cannot inject input: xdotool is not installed")

        if holder == HOLDER_MODEL:
            # Raises LeaseRefused, naming the expiry, if the human has it.
            surface.lease_acquire(HOLDER_MODEL, LEASE_DEFAULT_TTL)
        surface.check_seq(seq, width, height)

        # Translate everything before running anything, so a malformed event
        # halfway down the list can't leave the surface half-driven.
        argvs = [self._event_argv(surface, event) for event in events]

        env = dict(os.environ)
        env["DISPLAY"] = f":{surface.display}"
        for argv in argvs:
            result = subprocess.run(
                ["xdotool", *argv],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=env,
                timeout=INPUT_TIMEOUT,
                check=False,
            )
            if result.returncode != 0:
                detail = (
                    (result.stdout or b"").decode("utf-8", errors="replace").strip()
                )
                raise SurfaceError(f"xdotool {argv[0]} failed: {detail}")
        return {"ok": True, "seq": surface.bump_seq()}

    # -- one dispatch table, shared by every caller ----------------------

    def dispatch(self, method: str, params: dict[str, Any]) -> Any:
        """Handle one `surface_*` call.

        Both entry points route through here — the Pack daemon's JSON-RPC
        dispatcher (the panel's path) and the Unix-socket control server (the
        MCP server's path) — so the two can never drift apart on semantics.
        """
        if method == "surface_open":
            return self.open_surface(
                spec=params.get("spec"),
                width=params.get("width", DEFAULT_WIDTH),
                height=params.get("height", DEFAULT_HEIGHT),
                source=params.get("source", "user"),
            )
        if method == "surface_attach":
            # Connection details for a surface that is already up — the
            # "Show panel" path, where the local side has a surface id from
            # the transcript but no port or password (the app may have been
            # restarted since, or the panel closed and reopened).
            # Deliberately not exposed by the MCP server: the model drives
            # through surface_input and never needs the credential.
            surface = self.get(params["surface_id"])
            if not surface.alive():
                raise SurfaceError(f"surface {surface.id} is no longer running")
            surface.touch()
            return {
                "surface_id": surface.id,
                "ws_port": surface.ws_port,
                "password": surface.password,
                "width": surface.width,
                "height": surface.height,
                "description": surface.description,
                "seq": surface.seq,
            }
        if method == "surface_close":
            return self.close_surface(params["surface_id"])
        if method == "surface_list":
            return {
                "surfaces": self.list_surfaces(),
                "profiles": sorted(self.profiles()),
            }
        if method == "surface_touch":
            # The panel's heartbeat while it is attached, which is what keeps
            # the idle reaper off a surface somebody is watching but not
            # driving. Returns the descriptor so one call also refreshes the
            # lease banner rather than needing a second round trip.
            surface = self.get(params["surface_id"])
            surface.touch()
            return surface.descriptor()
        if method == "surface_snapshot":
            return self.snapshot(params["surface_id"])
        if method == "surface_input":
            return self.inject_input(
                params["surface_id"],
                params.get("events", []),
                holder=params.get("holder", HOLDER_MODEL),
                seq=params.get("seq"),
                width=params.get("width"),
                height=params.get("height"),
            )
        if method == "surface_lease_acquire":
            return self.get(params["surface_id"]).lease_acquire(
                params.get("holder", HOLDER_HUMAN),
                params.get("ttl", LEASE_DEFAULT_TTL),
            )
        if method == "surface_lease_touch":
            return self.get(params["surface_id"]).lease_acquire(
                params.get("holder", HOLDER_HUMAN),
                params.get("ttl", LEASE_DEFAULT_TTL),
            )
        if method == "surface_lease_release":
            return self.get(params["surface_id"]).lease_release(
                params.get("holder", HOLDER_HUMAN),
            )
        raise SurfaceError(f"unknown surface method {method!r}")


SURFACE_METHODS = (
    "surface_open",
    "surface_attach",
    "surface_close",
    "surface_list",
    "surface_touch",
    "surface_snapshot",
    "surface_input",
    "surface_lease_acquire",
    "surface_lease_touch",
    "surface_lease_release",
)
