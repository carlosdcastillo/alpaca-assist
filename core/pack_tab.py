"""PackTab — a tab whose backend runs on a remote host over SSH.

Does NOT subclass ChatTabBase (that class assumes a chat_state
continuously mutated in-process by a local StreamProcessor, which is the
wrong model here — the real ChatTab lives in a remote pack_daemon.py
process). Instead, PackTab independently implements exactly the surface
every webview_api.py call site actually needs from a tab object, verified
against the real code rather than assumed:

    handle_user_message, stop_streaming, set_model, is_streaming, chat_state,
    title, conversation_id, get_serializable_data, load_from_data,
    cleanup_resources, compact_conversation, truncate_conversation, recompute_title,
    pop_conversation, _current_answer_index (raw attribute, read
    immediately after handle_user_message returns — see
    webview_api.py:169).

AppCore.tabs is a plain dict[str, ChatTab] accessed everywhere via duck
typing (no isinstance checks anywhere in webview_api.py), so a PackTab
instance slots in next to ordinary ChatTabs with no special-casing.
Closing one stores its conversation in the local database exactly like a
regular tab; webview_api.revive_conversation checks the stored
`tab_type` and, for "pack", reconnects to the still-running remote
daemon instead of creating a disconnected local ChatTab.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any

from chat_state import ChatState
from conversation_graph import ConversationGraph
from core.pack_files import MAX_PACK_FILE_BYTES
from core.pack_transport import PackTransport
from core.pack_transport import PackTransportError
from core.surface_tunnel import SurfaceTunnelManager
from utils import ContentUpdate

if TYPE_CHECKING:
    from core.app_core import AppCore

logger = logging.getLogger(__name__)

ATTACH_TIMEOUT = 15.0
PROJECT_SETUP_TIMEOUT = 620.0
MUTATE_TIMEOUT = 15.0
SEND_MESSAGE_TIMEOUT = 30.0
VIDEO_CHUNK_TIMEOUT = 30.0
FILE_CHUNK_TIMEOUT = 30.0
GATED_OUTPUT_TIMEOUT = 30.0
STOP_STREAMING_TIMEOUT = 10.0
FOLD_RENDER_TIMEOUT = 2.0
# Building a surface means spawning Xvfb, x11vnc and the app itself and
# waiting for each to become reachable, so it is meaningfully slower than
# the other RPCs here.
SURFACE_OPEN_TIMEOUT = 60.0
SURFACE_TIMEOUT = 30.0


class PackTab:
    """A tab proxying to a real ChatTab running in a remote pack_daemon.py."""

    def __init__(
        self,
        tab_id: str,
        title: str,
        app_core: AppCore,
        conversation_id: int,
        host: str,
        session_id: str,
        project_payload: dict[str, Any] | None = None,
    ) -> None:
        self.tab_id = tab_id
        self.title = title
        self._app_core = app_core
        self.conversation_id = conversation_id
        self.host = host
        self.session_id = session_id
        self._project_payload = project_payload
        self.project_name = project_payload.get("name") if project_payload else None
        self.workspace_path = (
            project_payload.get("workspace_path") if project_payload else None
        )
        self.project_fingerprint = (
            project_payload.get("fingerprint") if project_payload else None
        )
        self.project_setup_state = "setting_up" if project_payload else None
        self.project_setup_error: str | None = None
        self._project_ready = threading.Event()
        if project_payload is None:
            self._project_ready.set()
        self._workspace_status: dict[str, Any] | None = None
        self._workspace_status_at = 0.0
        self._file_cache: tempfile.TemporaryDirectory[str] | None = None
        self._file_cache_dir: Path | None = None
        self._file_cache_lock = threading.Lock()
        self._surface_tunnels = SurfaceTunnelManager(host)

        self.chat_state: ChatState | ConversationGraph = ConversationGraph()
        self.is_streaming = False
        self.offline = False
        self._current_answer_index = -1
        self._connect_lock = threading.Lock()
        # Set by _apply_resync_result when the daemon reports resumed=False
        # while we still hold real local content — see resolve_session_lost.
        self._pending_recreate_state: dict[str, Any] | None = None
        # Mirrors the real remote ChatTab's own attributes of the same
        # name — webview_api.get_status_info() reads these via plain
        # getattr(tab, ...) for both tab kinds, so PackTab must carry
        # them locally. Updated from the attach response (see
        # _apply_resync_result) and from PackDaemonAdapter's
        # on_streaming_end/on_error notifications, which is the only
        # place these numbers actually change on the remote side.
        self.session_output_tokens = 0
        self.session_input_tokens = 0
        self.session_cached_input_tokens = 0
        self.last_invocation_metrics: dict[str, Any] | None = None

        self._transport = self._build_transport()

    # -------------------------------------------------------------------
    # Transport lifecycle
    # -------------------------------------------------------------------

    def _build_transport(self) -> PackTransport:
        transport = PackTransport(self.host, self.session_id)
        transport.on_notification("on_streaming_start", self._on_streaming_start)
        transport.on_notification("on_streaming_end", self._on_streaming_end)
        transport.on_notification("on_content_update", self._on_content_update)
        transport.on_notification("on_error", self._on_error)
        transport.on_notification("update_tab_title", self._on_update_tab_title)
        transport.on_notification("inject_tool_fold", self._on_inject_tool_fold)
        transport.on_disconnect(self._on_disconnect)
        return transport

    def connect_async(self, model: str | None = None) -> None:
        """Kick off SSH-connect + attach on a background thread (non-blocking

        — the tab button appears immediately, exactly like a local tab).
        """
        threading.Thread(target=self._connect, args=(model,), daemon=True).start()

    def reconnect_async(self) -> None:
        """Retry a connection for an offline tab (e.g. on tab-click, see

        webview_api.py's switch_tab). Rebuilds the transport since the
        previous one's ssh subprocess has already exited.
        """
        with self._connect_lock:
            if not self.offline:
                return
            self._transport = self._build_transport()
            self.project_setup_error = None
            if self._project_payload:
                self.project_setup_state = "setting_up"
                self._project_ready.clear()
        threading.Thread(target=self._connect, args=(None,), daemon=True).start()

    def _connect(self, model: str | None) -> None:
        with self._connect_lock:
            try:
                self._transport.connect(model=model)
                self._resync(timeout=ATTACH_TIMEOUT)
                self._configure_project()
                self.offline = False
                self._notify_if_active()
            except PackTransportError as e:
                logger.warning(f"Pack tab {self.tab_id} failed to connect: {e}")
                self.offline = True
                if self._project_payload:
                    self.project_setup_state = "failed"
                self.project_setup_error = str(e)
                self._project_ready.set()
                api = self._app_core.api
                if api is not None:
                    api.on_error(self.tab_id, f"Pack tab offline: {e}")
            except Exception as e:
                logger.warning(f"Pack tab {self.tab_id} project setup failed: {e}")
                self.offline = True
                self.project_setup_state = "failed"
                self.project_setup_error = str(e)
                self._project_ready.set()
                api = self._app_core.api
                if api is not None:
                    api.on_error(self.tab_id, f"Project setup failed: {e}")

    def _configure_project(self) -> None:
        if not self._project_payload:
            self._project_ready.set()
            return
        try:
            result = self._transport.send_request(
                "configure_project",
                self._project_payload,
                timeout=PROJECT_SETUP_TIMEOUT,
            )
            self.workspace_path = result["workspace_path"]
            self.project_name = self._project_payload["name"]
            self.project_fingerprint = self._project_payload.get("fingerprint")
            self.project_setup_state = "ready"
            self._workspace_status = result.get("workspace_status")
            self._workspace_status_at = time.monotonic()
        except Exception as e:
            self.project_setup_error = str(e)
            raise
        finally:
            self._project_ready.set()

    def _ensure_connected(self, timeout: float) -> None:
        """Used by handle_user_message: if offline, try one synchronous

        reconnect before giving up, so a user who's already looking at
        the tab and just types doesn't need to click away and back.
        """
        if self.offline or not self._transport.connected:
            self._transport = self._build_transport()
            self.project_setup_error = None
            if self._project_payload:
                self.project_setup_state = "setting_up"
                self._project_ready.clear()
            self._transport.connect()
            self._resync(timeout=timeout)
            self._configure_project()
            self.offline = False
            self._notify_if_active()
        if not self._project_ready.wait(timeout=PROJECT_SETUP_TIMEOUT):
            raise PackTransportError("timed out waiting for project setup")
        if self.project_setup_error:
            raise PackTransportError(
                f"project setup failed: {self.project_setup_error}",
            )

    def _notify_if_active(self) -> None:
        """Tell JS to re-fetch and repaint this tab's conversation if it's

        the one currently on screen — a (re)connect can bring in content
        that streamed on the remote host while nobody was attached, which
        the frontend otherwise has no reason to know to re-fetch.
        """
        api = self._app_core.api
        if api is None:
            return
        if self._app_core.get_active_tab_id() == self.tab_id:
            api._safe_evaluate_js(f"app.onPackStateSynced({json.dumps(self.tab_id)});")

    def _resync(self, timeout: float = ATTACH_TIMEOUT) -> dict[str, Any]:
        """Fetch full current state from the remote daemon and rebuild the

        local chat_state mirror from it. Reuses the same pattern already
        used for backgrounded local tabs: re-fetch wholesale rather than
        replay a live event log across any gap.
        """
        result: dict[str, Any] = self._transport.send_request(
            "attach",
            {},
            timeout=timeout,
        )
        self._apply_resync_result(result)
        return result

    def _apply_resync_result(self, result: dict[str, Any]) -> None:
        """Adopt an attach response — unless it reports the remote session

        was lost (resumed=False) while we're still holding real local
        content, in which case silently overwriting it would repeat the
        exact "goes blank" bug this class has already been fixed for
        once, just triggered by a dead remote worker instead of a stale
        mirror. Stash the response and ask the user whether to recreate
        the remote session from the local copy or accept a fresh one —
        see resolve_session_lost.
        """
        if not result.get("resumed", True) and self._has_local_content():
            self._pending_recreate_state = result
            api = self._app_core.api
            if api is not None:
                api._safe_evaluate_js(
                    f"app.onPackSessionLost({json.dumps(self.tab_id)});",
                )
            return
        self._load_state(result.get("state", {}))
        self.title = result.get("title", self.title)
        self.is_streaming = result.get("is_streaming", False)
        self._apply_token_stats(result)

    def _apply_token_stats(self, payload: dict[str, Any]) -> None:
        """Adopt the remote ChatTab's live token-usage totals, if present

        (sent by the attach response and by on_streaming_end/on_error —
        see PackDaemonAdapter._token_stats). Absent/partial payloads
        leave whatever we already have untouched rather than resetting
        to 0, since not every caller sends every field.
        """
        if "session_output_tokens" in payload:
            self.session_output_tokens = payload["session_output_tokens"]
        if "session_input_tokens" in payload:
            self.session_input_tokens = payload["session_input_tokens"]
        if "session_cached_input_tokens" in payload:
            self.session_cached_input_tokens = payload["session_cached_input_tokens"]
        if "last_invocation_metrics" in payload:
            self.last_invocation_metrics = payload["last_invocation_metrics"]

    def _has_local_content(self) -> bool:
        data = self.chat_state.to_dict()
        return bool(
            data.get("graph", {}).get("nodes")
            or (data.get("questions") and data.get("answers")),
        )

    def resolve_session_lost(self, recreate: bool) -> None:
        """Called after the user answers the onPackSessionLost prompt.

        recreate=True pushes our local copy to the (empty) remote tab via
        the seed_state RPC, continuing the same conversation on a fresh
        remote session. recreate=False accepts the empty state the daemon
        already reported — the same outcome as attaching to a genuinely
        new Pack tab.
        """
        result = self._pending_recreate_state
        self._pending_recreate_state = None
        if result is None:
            return
        if recreate:
            try:
                self._transport.send_request(
                    "seed_state",
                    {"seed": self.get_serializable_data()},
                    timeout=ATTACH_TIMEOUT,
                )
            except PackTransportError as e:
                logger.warning(f"Pack tab {self.tab_id} recreate failed: {e}")
                self.offline = True
                return
        else:
            self._load_state(result.get("state", {}))
            self.title = result.get("title", self.title)
            self.is_streaming = result.get("is_streaming", False)
        self._notify_if_active()

    def _resync_async(self, *, notify_if_active: bool = False) -> None:
        """Fire-and-forget resync on a background thread.

        Must never be called directly from a notification handler: those
        run on the transport's own reader thread, and _resync's blocking
        send_request("attach", ...) waits for a response that only that
        same reader thread can read — calling it inline would deadlock.

        This is the fix for a real bug: get_conversation_state (used when
        switching tabs) reads self.chat_state directly, with no RPC round
        trip — it was never refreshed after a turn completed, only at
        connect time and after compact/truncate/pop. A turn streamed via
        live on_content_update pushes rendered fine while the tab stayed
        active, but the mirror itself was still whatever it was at the
        last resync (usually empty), so switching away and back showed
        nothing at all.
        """

        def run() -> None:
            try:
                self._resync(timeout=ATTACH_TIMEOUT)
                if notify_if_active:
                    self._notify_if_active()
            except PackTransportError as e:
                logger.warning(f"Pack tab {self.tab_id} post-turn resync failed: {e}")

        threading.Thread(target=run, daemon=True).start()

    def refresh_async(self) -> None:
        """Refresh the remote mirror and repaint this tab if it is still active.

        Used when activating a Pack tab so switching away and back during a
        remote turn shows the daemon's current state, then resumes receiving
        the ordinary live content notifications.
        """
        self._resync_async(notify_if_active=True)

    def _load_state(self, state: dict[str, Any]) -> None:
        chat_state_data = state.get("chat_state", {})
        if "graph" in chat_state_data:
            self.chat_state = ConversationGraph.from_dict(chat_state_data)
        else:
            self.chat_state = ChatState.from_dict(chat_state_data)

    def _on_disconnect(self) -> None:
        self.offline = True
        self.is_streaming = False
        # The forwards are dead weight the moment the control channel is:
        # their own ssh processes may well still be alive, but the panel has
        # no way to learn whether the surfaces behind them still exist. A
        # reconnect re-tunnels through surface_attach.
        self._surface_tunnels.close_all()

    # -------------------------------------------------------------------
    # Notification handlers — forward to the real, unmodified webview_api.py
    # push methods so the frontend renders a Pack tab exactly like a local
    # one, with zero JS changes.
    # -------------------------------------------------------------------

    # NOTE: every handler below uses self.tab_id (this PackTab's own id in
    # the local AppCore.tabs registry), never params["tab_id"] — that field
    # is the *remote* daemon's internal ChatTab id, allocated by its own
    # independent AppCore._alloc_tab_id() and meaningless locally. Forwarding
    # it instead of self.tab_id would silently never match any tab the
    # frontend knows about.

    def _on_streaming_start(self, params: dict[str, Any]) -> None:
        self.is_streaming = True
        api = self._app_core.api
        if api is not None:
            api.on_streaming_start(self.tab_id, params["answer_index"])

    def _on_streaming_end(self, params: dict[str, Any]) -> None:
        self.is_streaming = False
        # Applied synchronously (unlike chat_state, which only catches up
        # via the async resync below) so the status bar reflects real
        # usage the moment a turn ends, not after an extra RPC round trip.
        self._apply_token_stats(params)
        api = self._app_core.api
        if api is not None:
            api.on_streaming_end(self.tab_id, params["answer_index"])
        # The turn's content only ever reached the display via live
        # on_content_update pushes, and some persisted components (notably
        # CLI media/screenshots) are only reconstructed by a full render.
        # Catch the mirror up and repaint the active tab automatically so
        # those components do not require a tab switch to appear.
        self._resync_async(notify_if_active=True)

    def _on_content_update(self, params: dict[str, Any]) -> None:
        api = self._app_core.api
        if api is None:
            return
        update = ContentUpdate(**params["update"])
        api.on_content_update(self.tab_id, update)

    def _on_error(self, params: dict[str, Any]) -> None:
        self.is_streaming = False
        self._apply_token_stats(params)
        api = self._app_core.api
        if api is not None:
            api.on_error(self.tab_id, params["message"], params.get("details", ""))
        # A turn can end via error instead of a clean streaming_end —
        # resync and repaint here too so persisted partial results appear.
        self._resync_async(notify_if_active=True)

    def _on_update_tab_title(self, params: dict[str, Any]) -> None:
        self.title = params["title"]
        api = self._app_core.api
        if api is not None:
            api.update_tab_title(self.tab_id, params["title"])

    def _on_inject_tool_fold(self, params: dict[str, Any]) -> None:
        api = self._app_core.api
        if api is None:
            return
        fold_id = params["fold_id"]
        api.inject_tool_fold(
            self.tab_id,
            fold_id,
            params["fold_type"],
            params["body_text"],
            params["answer_index"],
        )

        def confirm() -> None:
            # mypy can't carry the `api is None` narrowing above into this
            # closure (it could theoretically run after `api` is rebound),
            # even though in practice it's the same object captured at
            # definition time.
            assert api is not None
            rendered = api.wait_for_fold_rendered(
                self.tab_id,
                fold_id,
                timeout=FOLD_RENDER_TIMEOUT,
            )
            try:
                # No "tab_id" here — the daemon's dispatcher defaults to its
                # own (single) tab when omitted (see pack_daemon.py's
                # make_dispatcher), which is correct: the local tab_id used
                # above for the real webview_api call is meaningless on the
                # remote side, which allocated its own independent tab_id.
                self._transport.send_request(
                    "fold_rendered",
                    {"fold_id": fold_id, "rendered": rendered},
                    timeout=FOLD_RENDER_TIMEOUT + 2.0,
                )
            except PackTransportError:
                pass  # connection may already be gone; nothing more to do

        threading.Thread(target=confirm, daemon=True).start()

    # -------------------------------------------------------------------
    # Duck-typed ChatTab surface
    # -------------------------------------------------------------------

    def handle_user_message(self, message: str, images: list[str]) -> None:
        try:
            self._ensure_connected(timeout=ATTACH_TIMEOUT)
            result = self._transport.send_request(
                "send_message",
                {"message": message, "images": images},
                timeout=SEND_MESSAGE_TIMEOUT,
            )
            self._current_answer_index = result.get(
                "answer_index",
                self._current_answer_index,
            )
        except PackTransportError as e:
            logger.warning(f"Pack tab {self.tab_id} send_message failed: {e}")
            self.offline = True
            api = self._app_core.api
            if api is not None:
                api.on_error(self.tab_id, f"Pack tab offline: {e}")
            # Re-raise so webview_api.send_message reports failure to the
            # frontend instead of silently swallowing it — app.js's own
            # send_message catch block (restores the typed text, offers a
            # retry dialog) never ran because this returned normally on
            # failure, so a failed send looked identical to a successful
            # one in the UI.
            raise

    def stop_streaming(self) -> None:
        try:
            self._transport.send_request(
                "stop_streaming",
                {},
                timeout=STOP_STREAMING_TIMEOUT,
            )
        except PackTransportError as e:
            logger.warning(f"Pack tab {self.tab_id} stop_streaming failed: {e}")

    def set_model(self, model: str) -> dict[str, Any]:
        """Change and persist the model used by the remote ChatTab."""
        self._ensure_connected(timeout=ATTACH_TIMEOUT)
        result: dict[str, Any] = self._transport.send_request(
            "set_model",
            {"model": model},
            timeout=MUTATE_TIMEOUT,
        )
        return result

    def get_workspace_status(self, max_age: float = 5.0) -> dict[str, Any] | None:
        if not self.project_name or not self.workspace_path or self.offline:
            return self._workspace_status
        if time.monotonic() - self._workspace_status_at < max_age:
            return self._workspace_status
        try:
            self._workspace_status = self._transport.send_request(
                "workspace_status",
                {},
                timeout=ATTACH_TIMEOUT,
            )
            self._workspace_status_at = time.monotonic()
        except PackTransportError:
            pass
        return self._workspace_status

    def read_video_chunk(self, locator: str, offset: int) -> dict[str, Any]:
        """Fetch a bounded video chunk from the remote Pack daemon."""
        self._ensure_connected(timeout=ATTACH_TIMEOUT)
        result: dict[str, Any] = self._transport.send_request(
            "read_video_chunk",
            {"locator": locator, "offset": offset},
            timeout=VIDEO_CHUNK_TIMEOUT,
        )
        return result

    def materialize_file(self, remote_path: str) -> Path:
        """Download a worker-local file to this Pack tab's temporary cache."""
        self._ensure_connected(timeout=ATTACH_TIMEOUT)
        metadata: dict[str, Any] = self._transport.send_request(
            "resolve_file_reference",
            {"path": remote_path},
            timeout=ATTACH_TIMEOUT,
        )
        size = int(metadata["size"])
        identity = str(metadata["identity"])
        locator = metadata.get("locator")
        if size < 0 or size > MAX_PACK_FILE_BYTES:
            raise PackTransportError("Pack worker returned an invalid file size")
        if re.fullmatch(r"[0-9a-f]{64}", identity) is None:
            raise PackTransportError("Pack worker returned an invalid file identity")
        if not isinstance(locator, str) or not locator:
            raise PackTransportError("Pack worker returned an invalid file locator")
        name = self._safe_cache_name(str(metadata["name"]))

        with self._file_cache_lock:
            if self._file_cache_dir is None:
                self._file_cache = tempfile.TemporaryDirectory(
                    prefix=f"alpaca-pack-{self.tab_id}-",
                )
                self._file_cache_dir = Path(self._file_cache.name)
            target = self._file_cache_dir / f"{identity[:16]}-{name}"
            if target.is_file() and target.stat().st_size == size:
                return target

            partial = target.with_name(f".{target.name}.{uuid.uuid4().hex}.part")
            offset = 0
            finished = size == 0
            try:
                with partial.open("wb") as output:
                    while offset < size:
                        chunk: dict[str, Any] = self._transport.send_request(
                            "read_file_chunk",
                            {"locator": locator, "offset": offset},
                            timeout=FILE_CHUNK_TIMEOUT,
                        )
                        if int(chunk["size"]) != size:
                            raise PackTransportError(
                                "Pack file changed while downloading",
                            )
                        try:
                            data = base64.b64decode(chunk["data"], validate=True)
                        except (ValueError, TypeError) as exc:
                            raise PackTransportError(
                                "Pack worker returned invalid file data",
                            ) from exc
                        next_offset = int(chunk["next_offset"])
                        if next_offset != offset + len(data) or next_offset <= offset:
                            raise PackTransportError(
                                "Pack worker returned an invalid file offset",
                            )
                        output.write(data)
                        offset = next_offset
                        finished = bool(chunk["done"])
                        if finished:
                            break
                if offset != size or not finished:
                    raise PackTransportError(
                        f"Pack file download ended early ({offset:,} of {size:,} bytes)",
                    )
                partial.replace(target)
            finally:
                partial.unlink(missing_ok=True)
            return target

    @staticmethod
    def _safe_cache_name(name: str) -> str:
        """Keep a useful extension while avoiding host-specific filename hazards."""
        leaf = Path(name).name
        cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", leaf).strip(" .")
        return cleaned[:160] or "pack-file"

    # -------------------------------------------------------------------
    # Live app surfaces
    #
    # The control plane only. Framebuffer traffic never touches Python: the
    # panel opens a WebSocket to the local end of the tunnel these methods
    # set up, and noVNC talks RFB to x11vnc through it.
    # -------------------------------------------------------------------

    def surface_open(
        self,
        spec: dict[str, Any] | None = None,
        width: int = 1280,
        height: int = 800,
    ) -> dict[str, Any]:
        """Start a surface on the remote host and tunnel to its WebSocket."""
        self._ensure_connected(timeout=ATTACH_TIMEOUT)
        result: dict[str, Any] = self._transport.send_request(
            "surface_open",
            {"spec": spec, "width": width, "height": height, "source": "user"},
            timeout=SURFACE_OPEN_TIMEOUT,
        )
        return self._tunnel_to(result)

    def surface_attach(self, surface_id: str) -> dict[str, Any]:
        """Re-tunnel to a surface that is still running.

        The transcript stores a descriptor, never a session, so reopening a
        conversation and clicking "Show panel" arrives here with an id and
        nothing else. If the surface is gone this raises, and the card says
        so — there is deliberately no path that recreates it.
        """
        self._ensure_connected(timeout=ATTACH_TIMEOUT)
        result: dict[str, Any] = self._transport.send_request(
            "surface_attach",
            {"surface_id": surface_id},
            timeout=SURFACE_TIMEOUT,
        )
        return self._tunnel_to(result)

    def _tunnel_to(self, result: dict[str, Any]) -> dict[str, Any]:
        """Add a local WebSocket URL to a supervisor connection descriptor.

        On failure the remote surface is closed rather than left orphaned:
        without a tunnel nothing local can reach it, so keeping it alive
        would only burn a display until the idle reaper notices.
        """
        surface_id = result["surface_id"]
        try:
            local_port = self._surface_tunnels.open(surface_id, int(result["ws_port"]))
        except Exception as e:
            logger.warning(f"Pack tab {self.tab_id} surface tunnel failed: {e}")
            try:
                self.surface_close(surface_id)
            except Exception:
                pass
            raise
        return {
            **result,
            "ws_url": f"ws://127.0.0.1:{local_port}",
            "local_port": local_port,
        }

    def surface_close(self, surface_id: str) -> dict[str, Any]:
        self._surface_tunnels.close(surface_id)
        try:
            result: dict[str, Any] = self._transport.send_request(
                "surface_close",
                {"surface_id": surface_id},
                timeout=SURFACE_TIMEOUT,
            )
            return result
        except PackTransportError as e:
            logger.warning(f"Pack tab {self.tab_id} surface_close failed: {e}")
            return {"ok": False, "reason": "offline"}

    def surface_call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Proxy any other `surface_*` control call to the remote supervisor."""
        self._ensure_connected(timeout=ATTACH_TIMEOUT)
        result: dict[str, Any] = self._transport.send_request(
            method,
            params,
            timeout=SURFACE_TIMEOUT,
        )
        return result

    def read_gated_tool_output(self, gated_text: str) -> str:
        """Fetch a gated result from the remote Pack daemon's temp file."""
        self._ensure_connected(timeout=ATTACH_TIMEOUT)
        result: dict[str, Any] = self._transport.send_request(
            "read_gated_tool_output",
            {"gated_text": gated_text},
            timeout=GATED_OUTPUT_TIMEOUT,
        )
        return str(result["content"])

    def compact_conversation(self) -> dict[str, Any]:
        return self._mutate("compact_conversation")

    def truncate_conversation(self) -> dict[str, Any]:
        return self._mutate("truncate_conversation")

    def recompute_title(self) -> dict[str, Any]:
        try:
            result: dict[str, Any] = self._transport.send_request(
                "recompute_title",
                {},
                timeout=MUTATE_TIMEOUT,
            )
            return result
        except PackTransportError as e:
            logger.warning(f"Pack tab {self.tab_id} recompute_title failed: {e}")
            return {"success": False, "reason": "offline"}

    def pop_conversation(self) -> dict[str, Any]:
        return self._mutate("pop_conversation")

    def _mutate(self, method: str) -> dict[str, Any]:
        try:
            result: dict[str, Any] = self._transport.send_request(
                method,
                {},
                timeout=MUTATE_TIMEOUT,
            )
            self._resync(timeout=MUTATE_TIMEOUT)
            return result
        except PackTransportError as e:
            logger.warning(f"Pack tab {self.tab_id} {method} failed: {e}")
            return {"success": False, "reason": "offline"}

    def get_serializable_data(self) -> dict[str, Any]:
        return {
            "tab_type": "pack",
            "host": self.host,
            "session_id": self.session_id,
            "chat_state": self.chat_state.to_dict(),
            "tab_id": self.tab_id,
            "name": self.title,
            "conversation_id": self.conversation_id,
            "project": self.project_name,
            "workspace_path": self.workspace_path,
            "project_fingerprint": self.project_fingerprint,
        }

    def load_from_data(self, data: dict[str, Any]) -> None:
        """Seed the local mirror from a possibly-stale saved blob so the UI

        paints something immediately; connect_async's subsequent _resync
        overwrites this with the authoritative remote state.
        """
        chat_state_data = data.get("chat_state", {})
        if "graph" in chat_state_data:
            self.chat_state = ConversationGraph.from_dict(chat_state_data)
        else:
            self.chat_state = ChatState.from_dict(chat_state_data)
        self.title = data.get("name", data.get("title", self.title))
        if data.get("host"):
            self.host = data["host"]
        if data.get("session_id"):
            self.session_id = data["session_id"]
        self.project_name = data.get("project") or self.project_name
        self.workspace_path = data.get("workspace_path") or self.workspace_path
        self.project_fingerprint = (
            data.get("project_fingerprint") or self.project_fingerprint
        )

    def cleanup_resources(self) -> None:
        """Tear down only the local SSH subprocess/threads. Never signals

        the remote daemon to stop — this is the entire mechanism by which
        closing the app leaves the remote Pack session running.

        Surfaces follow the same rule: the tunnels go, the Xvfb/x11vnc/app
        processes on the far side do not. The supervisor's idle reaper is
        what collects those (30 minutes by default), which is the deliberate
        cost of a surface surviving a network blip mid-session.
        """
        self._surface_tunnels.close_all()
        self._transport.close()
        if self._file_cache is not None:
            self._file_cache.cleanup()
            self._file_cache = None
            self._file_cache_dir = None
