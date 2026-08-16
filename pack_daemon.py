#!/usr/bin/env python3
"""Pack tab remote daemon.

Runs on the remote host, hosting exactly one real AppCore + one real
ChatTab for a single Pack tab session. Reachable via a Unix domain socket
at ~/.alpaca_pack/<session_id>/daemon.sock, normally bridged to the local
side over SSH stdio by pack_bridge.py (which also handles detached
launch-or-attach — this file assumes it is already the sole owner of its
session directory by the time main() runs).

Usage:
    python3 pack_daemon.py <session_id> [--model MODEL_NAME]

First launch (no chat_session.json yet in the session directory) creates a
fresh ChatTab, optionally seeded with --model (the local side's current
model preference at Pack-tab-creation time; after that, remote and local
preferences evolve independently — see core/chat_tab_base.py's
`self.preferences = app_core.preferences` sharing, which is per-AppCore,
not global, so this daemon's own AppCore never touches the local
preferences.json at all). A relaunch against an existing session directory
instead resumes the previously saved tab via AppCore.load_session(),
exactly the same restore path webview_app.py already uses for local tabs.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import socket
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any
from typing import cast

from core import pack_protocol
from core.pack_files import PackFileStore
from core.projects import prepare_workspace
from core.projects import probe_workspace
from core.surface_supervisor import SURFACE_METHODS
from core.tool_output_gate import read_gated_tool_output
from video_tool_result import read_video_chunk

logger = logging.getLogger("pack_daemon")

SOCKET_BACKLOG = 4


class PackDaemonAdapter:
    """Substitutes for WebViewAPI as ChatTab's `_app_core.api`.

    Every push that would normally go to JS (on_streaming_start,
    on_content_update, on_streaming_end, on_error, update_tab_title)
    instead becomes a notification written to whichever socket connection
    is currently attached — dropped silently if nobody is attached, which
    is safe: on reattach the local side re-fetches full state via `attach`
    rather than replaying missed events (see core/pack_tab.py).

    inject_tool_fold/wait_for_fold_rendered/on_new_qa_turn mirror
    webview_api.py's real bookkeeping (webview_api.py:1007-1017,
    1089-1137) so the remote ToolHandler's existing blocking-wait behavior
    is preserved unmodified; `fold_rendered` closes that loop when the
    local side confirms real JS rendering completed.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._conn: PackConnection | None = None
        self._fold_rendered_events: dict[str, threading.Event] = {}
        self._answer_index: dict[str, int] = {}
        self._tab: Any | None = None

    def set_connection(self, conn: PackConnection | None) -> None:
        with self._lock:
            self._conn = conn

    def set_tab(self, tab: Any) -> None:
        """Give the adapter a reference to the real ChatTab so

        on_streaming_end/on_error can relay its live token-usage totals —
        session_output_tokens etc. are plain ChatTab attributes read
        directly by webview_api.get_status_info() for local tabs, but
        PackTab (the local proxy) never carries them on its own; nothing
        else ever pushes these numbers across the wire.
        """
        self._tab = tab

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        with self._lock:
            conn = self._conn
        if conn is not None:
            conn.send_notification(method, params)

    def _token_stats(self) -> dict[str, Any]:
        tab = self._tab
        if tab is None:
            return {}
        return {
            "session_output_tokens": getattr(tab, "session_output_tokens", 0),
            "session_input_tokens": getattr(tab, "session_input_tokens", 0),
            "session_cached_input_tokens": getattr(
                tab,
                "session_cached_input_tokens",
                0,
            ),
            "last_invocation_metrics": getattr(tab, "last_invocation_metrics", None),
        }

    def on_streaming_start(self, tab_id: str, answer_index: int) -> None:
        self._notify(
            "on_streaming_start",
            {"tab_id": tab_id, "answer_index": answer_index},
        )

    def on_streaming_end(self, tab_id: str, answer_index: int) -> None:
        self._notify(
            "on_streaming_end",
            {"tab_id": tab_id, "answer_index": answer_index, **self._token_stats()},
        )

    def on_content_update(self, tab_id: str, update: Any) -> None:
        self._notify(
            "on_content_update",
            {"tab_id": tab_id, "update": update._asdict()},
        )

    def on_error(self, tab_id: str, message: str, details: str = "") -> None:
        self._notify(
            "on_error",
            {
                "tab_id": tab_id,
                "message": message,
                "details": details,
                **self._token_stats(),
            },
        )

    def update_tab_title(self, tab_id: str, title: str) -> None:
        self._notify("update_tab_title", {"tab_id": tab_id, "title": title})

    def inject_tool_fold(
        self,
        tab_id: str,
        fold_id: str,
        fold_type: str,
        body_text: str,
        answer_index: int,
    ) -> None:
        event_key = f"{tab_id}:{fold_id}"
        with self._lock:
            self._fold_rendered_events[event_key] = threading.Event()
        self._notify(
            "inject_tool_fold",
            {
                "tab_id": tab_id,
                "fold_id": fold_id,
                "fold_type": fold_type,
                "body_text": body_text,
                "answer_index": answer_index,
            },
        )

    def wait_for_fold_rendered(
        self,
        tab_id: str,
        fold_id: str,
        timeout: float = 2.0,
    ) -> bool:
        event_key = f"{tab_id}:{fold_id}"
        with self._lock:
            event = self._fold_rendered_events.get(event_key)
            if event is None:
                return False
        result = event.wait(timeout=timeout)
        with self._lock:
            self._fold_rendered_events.pop(event_key, None)
        return result

    def fold_rendered(self, tab_id: str, fold_id: str, rendered: bool) -> None:
        """Called by the daemon's request dispatcher once the local side

        confirms (or times out on) real JS-side fold rendering.
        """
        event_key = f"{tab_id}:{fold_id}"
        with self._lock:
            event = self._fold_rendered_events.get(event_key)
            if event:
                event.set()

    def on_new_qa_turn(self, tab_id: str) -> int:
        """Pure local bookkeeping — never sent over the wire.

        Mirrors webview_api.py:1007-1017's dict+lock+increment exactly;
        synchronous, no UI dependency on either side, so each side keeps
        its own independent counter with no benefit to networking it.
        """
        with self._lock:
            current = self._answer_index.get(tab_id, -1)
            new_index = current + 1
            self._answer_index[tab_id] = new_index
            return new_index


class PackConnection:
    """One accepted socket connection: frames writes, dispatches reads."""

    def __init__(
        self,
        sock: socket.socket,
        dispatch: Callable[[str, dict[str, Any]], Any],
    ) -> None:
        self._sock = sock
        self._dispatch = dispatch
        self._write_lock = threading.Lock()

    def send_notification(self, method: str, params: dict[str, Any]) -> None:
        self._send(pack_protocol.encode_notification(method, params))

    def _send(self, text: str) -> None:
        with self._write_lock:
            try:
                self._sock.sendall(text.encode("utf-8"))
            except OSError:
                pass  # connection gone; accept loop notices on next recv/read

    def serve_forever(self) -> None:
        """Read requests until the connection closes, dispatching each one."""
        with self._sock.makefile("r", encoding="utf-8") as reader:
            for line in reader:
                try:
                    msg = pack_protocol.decode_line(line)
                except pack_protocol.ProtocolError as e:
                    logger.warning(f"Bad message from client: {e}")
                    continue
                if pack_protocol.is_request(msg):
                    self._handle_request(msg)

    def _handle_request(self, msg: dict[str, Any]) -> None:
        request_id = msg["id"]
        method = msg["method"]
        params = msg.get("params", {})
        try:
            result = self._dispatch(method, params)
            self._send(pack_protocol.encode_response(request_id, result))
        except Exception as e:
            logger.exception(f"Error handling request {method!r}")
            self._send(pack_protocol.encode_error_response(request_id, str(e)))


def make_dispatcher(
    tab: Any,
    adapter: PackDaemonAdapter,
    resumed: bool,
    core: Any,
    supervisor: Any = None,
) -> Callable[[str, dict[str, Any]], Any]:
    """Build the method -> handler mapping for the daemon's one ChatTab."""

    pack_files = PackFileStore()

    def dispatch(method: str, params: dict[str, Any]) -> Any:
        nonlocal resumed
        if method == "attach":
            response = {
                "state": tab.get_serializable_data(),
                "title": tab.title,
                "is_streaming": tab.is_streaming,
                "resumed": resumed,
                **adapter._token_stats(),
            }
            # resumed answers "did this process find a persisted session
            # when it started" — true only for the very first attach.
            # Every later attach in this same process's lifetime is a
            # reconnect to a session that's been continuously live since
            # then, not a fresh/uncertain one, so it must report resumed
            # regardless of how this process itself started. Without
            # this, a daemon created fresh (the normal case for every
            # brand-new Pack tab, since no chat_session.json exists yet)
            # would report resumed=False forever, and PackTab's "session
            # lost" prompt would fire on every ordinary reconnect —
            # an app restart, a brief network blip — not just a real loss.
            resumed = True
            return response
        if method == "send_message":
            tab.handle_user_message(params.get("message", ""), params.get("images", []))
            return {"answer_index": getattr(tab, "_current_answer_index", 0)}
        if method == "configure_project":
            if tab.is_streaming:
                raise RuntimeError("Cannot configure a project while streaming")
            previous_project = getattr(tab, "project_name", None)
            previous_workspace = getattr(tab, "workspace_path", None)
            workspace_path = prepare_workspace(params)
            binding_changed = (
                previous_project != params["name"]
                or previous_workspace != workspace_path
            )
            tab.project_name = params["name"]
            tab.workspace_path = workspace_path
            tab.project_fingerprint = params.get("fingerprint")
            tab.project_runbook = params.get("runbook", "")
            tab.project_spinup = params.get("spinup", "")
            if binding_changed:
                tab.project_spinup_pending = True
            os.environ["ALPACA_WORKSPACE"] = workspace_path
            core.save_session()
            return {
                "success": True,
                "workspace_path": workspace_path,
                "workspace_status": probe_workspace(workspace_path),
            }
        if method == "workspace_status":
            status_workspace = getattr(tab, "workspace_path", None)
            if not status_workspace:
                return {"workspace_path": None, "exists": False, "is_git": False}
            return probe_workspace(status_workspace)
        if method == "resolve_file_reference":
            return pack_files.resolve(
                params["path"],
                getattr(tab, "workspace_path", None),
            )
        if method == "read_file_chunk":
            return pack_files.read_chunk(params["locator"], params.get("offset", 0))
        if method == "read_video_chunk":
            return read_video_chunk(params["locator"], params.get("offset", 0))
        if method == "read_gated_tool_output":
            return {
                "content": read_gated_tool_output(
                    params["gated_text"],
                    tab.tab_id,
                ),
            }
        if method == "stop_streaming":
            tab.stop_streaming()
            return {"success": True}
        if method == "set_model":
            tab.preferences["model"] = params["model"]
            core.save_preferences()
            return {"success": True}
        if method == "compact_conversation":
            return tab.compact_conversation()
        if method == "truncate_conversation":
            return tab.truncate_conversation()
        if method == "recompute_title":
            return tab.recompute_title()
        if method == "pop_conversation":
            return tab.pop_conversation()
        if method == "fold_rendered":
            adapter.fold_rendered(
                params.get("tab_id", tab.tab_id),
                params["fold_id"],
                params.get("rendered", False),
            )
            return {"ok": True}
        if method == "seed_state":
            # Local side recreating a session we reported resumed=False
            # for (no chat_session.json existed when we started) — load
            # its copy of the conversation into our still-empty tab and
            # persist it immediately, so a crash before the next autosave
            # doesn't lose the seed and repeat the same "session lost"
            # prompt on the next attach.
            tab.load_from_data(params["seed"])
            core.save_session()
            resumed = True
            return {"success": True}
        if method in SURFACE_METHODS:
            # Live app surfaces (Xvfb + x11vnc + one GUI app). Only the
            # control plane comes through here; pixels never do — they go
            # over a WebSocket straight from the panel to x11vnc, tunnelled
            # by the local side (see core/surface_tunnel.py).
            if supervisor is None:
                raise RuntimeError(
                    "no display available on this host: the surface "
                    "supervisor could not start (see daemon.log)",
                )
            return supervisor.dispatch(method, params)
        raise ValueError(f"Unknown method: {method!r}")

    return dispatch


_BARE_PYTHON_NAMES = {"python", "python3"}


def _absolutize_mcp_config(
    raw_config: dict[str, Any],
    repo_root: Path,
) -> dict[str, Any]:
    """Rewrite repo-relative script paths and bare interpreter names in

    mcp_servers.json entries to absolute paths.

    mcp_servers.json entries like {"command": ["python", "simple_mcp_server.py"]}
    assume a cwd of the repo root and a PATH where "python" resolves to an
    interpreter with this repo's dependencies installed — both true for the
    normal app, but this daemon's cwd is its isolated session directory
    (required so AppCore's own cwd-relative persistence doesn't collide
    across sessions), and its PATH comes from a non-interactive SSH
    session, which may not resolve "python"/"python3" to the same
    interpreter this daemon itself is running under (e.g. a system Python
    lacking the `mcp` package, even though .venv-sys has it). Left
    unrewritten, mcp_manager.py's subprocess spawn would silently pick up
    the wrong script location, the wrong interpreter, or both.
    """
    result: dict[str, Any] = {}
    for name, entry in raw_config.items():
        new_entry = dict(entry)
        fixed = []
        for part in entry.get("command", []):
            if part in _BARE_PYTHON_NAMES:
                fixed.append(sys.executable)
                continue
            candidate = repo_root / part
            if not os.path.isabs(part) and candidate.is_file():
                fixed.append(str(candidate))
            else:
                fixed.append(part)
        new_entry["command"] = fixed
        result[name] = new_entry
    return result


def _prepare_mcp_config(session_dir: Path, repo_root: Path) -> None:
    """Snapshot the repo's mcp_servers.json into the session dir with

    absolute-ified script paths (see _absolutize_mcp_config). A snapshot,
    not a live symlink — matches the v1 scope (no UI to reconfigure a
    Pack tab's MCP servers; edit the source file and restart the daemon
    to pick up changes).
    """
    source = repo_root / "mcp_servers.json"
    if not source.exists():
        return
    try:
        with open(source, encoding="utf-8") as f:
            raw_config = json.load(f)
        fixed_config = _absolutize_mcp_config(raw_config, repo_root)
        with open(session_dir / "mcp_servers.json", "w", encoding="utf-8") as f:
            json.dump(fixed_config, f, indent=2)
    except Exception:
        logger.warning(
            "Could not prepare mcp_servers.json for this session; "
            "MCP servers may be unavailable",
            exc_info=True,
        )


def _start_surface_supervisor(session_dir: Path) -> Any:
    """Bring up the live-app-surface supervisor, or return None if this host
    can't host one.

    Never fatal. A machine with no Xvfb/x11vnc is a perfectly good Pack host
    for everything else, and a surface request against it should fail with a
    clear "no display available" at call time rather than preventing the
    daemon from starting at all.

    Reaping happens *before* anything else: surfaces recorded by a previous
    incarnation of this daemon are killed, never adopted. Nothing could
    attach to them anyway — the tunnel, the lease and the panel's handle all
    died with the process that owned them.
    """
    try:
        from core.surface_control import SurfaceControlServer
        from core.surface_supervisor import SurfaceSupervisor

        supervisor = SurfaceSupervisor(session_dir)
        reaped = supervisor.reap_orphans()
        if reaped:
            logger.info(f"Reaped {reaped} orphaned surface process(es) at startup")
        missing = supervisor.missing_tools()
        if missing:
            logger.info(
                "Surfaces unavailable on this host (missing: "
                + ", ".join(missing)
                + ")",
            )
            return supervisor
        supervisor.install_process_guards()
        SurfaceControlServer(supervisor, supervisor.surfaces_dir).start()
        return supervisor
    except Exception:
        logger.warning("Could not start the surface supervisor", exc_info=True)
        return None


def _bind_socket(sock_path: Path) -> socket.socket:
    """Bind the daemon's listening socket, clearing a stale one if needed.

    Defense in depth alongside pack_bridge.py's flock-guarded launch
    decision: if this daemon is ever invoked directly (not only through
    the bridge), it still refuses to double-bind against a socket that
    something else is actually listening on.
    """
    af_unix = getattr(socket, "AF_UNIX")
    if sock_path.exists():
        probe = socket.socket(af_unix, socket.SOCK_STREAM)
        probe.settimeout(1.0)
        alive = True
        try:
            probe.connect(str(sock_path))
        except OSError:
            alive = False
        finally:
            probe.close()
        if alive:
            raise RuntimeError(f"a daemon is already listening on {sock_path}")
        sock_path.unlink(missing_ok=True)

    sock = socket.socket(af_unix, socket.SOCK_STREAM)
    sock.bind(str(sock_path))
    sock.listen(SOCKET_BACKLOG)
    return sock


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session_id")
    parser.add_argument("--model", default=None)
    args = parser.parse_args()

    session_dir = Path.home() / ".alpaca_pack" / args.session_id
    session_dir.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.FileHandler(session_dir / "daemon.log")],
    )

    repo_root = Path(__file__).resolve().parent
    _prepare_mcp_config(session_dir, repo_root)

    resumed = (session_dir / "chat_session.json").exists()

    # Isolate this daemon's persistence (chat_session.json, preferences.json,
    # conversations.db are all cwd-relative in AppCore/ConversationDatabase)
    # before importing/constructing AppCore.
    os.chdir(session_dir)
    sys.path.insert(0, str(repo_root))

    from core.app_core import AppCore

    core = AppCore(api=None)
    adapter = PackDaemonAdapter()
    # Deliberately duck-typed, not a WebViewAPI subclass — see
    # PackDaemonAdapter's own docstring above.
    core.api = cast(Any, adapter)

    tab = None
    if resumed:
        tabs_data, _selected = core.load_session()
        if tabs_data:
            tab_data = tabs_data[0]
            saved_conv_id = tab_data.get("conversation_id")
            _tab_id, tab = core.create_tab(
                tab_data.get("name", tab_data.get("title", "Pack Tab")),
                conversation_id=int(saved_conv_id)
                if saved_conv_id is not None
                else None,
            )
            tab.load_from_data(tab_data)
        else:
            resumed = False

    if tab is None:
        if args.model:
            core.preferences["model"] = args.model
            core.save_preferences()
        _tab_id, tab = core.create_tab("Pack Tab")

    core.start_autosave()
    adapter.set_tab(tab)

    supervisor = _start_surface_supervisor(session_dir)

    dispatch = make_dispatcher(tab, adapter, resumed, core, supervisor)

    sock_path = session_dir / "daemon.sock"
    listener = _bind_socket(sock_path)
    logger.info(f"Pack daemon listening on {sock_path} (resumed={resumed})")

    try:
        while True:
            conn_sock, _addr = listener.accept()
            logger.info("Client attached")
            conn = PackConnection(conn_sock, dispatch)
            adapter.set_connection(conn)
            try:
                conn.serve_forever()
            finally:
                adapter.set_connection(None)
                conn_sock.close()
                logger.info("Client detached")
    finally:
        listener.close()


if __name__ == "__main__":
    main()
