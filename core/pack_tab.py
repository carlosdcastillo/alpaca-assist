"""PackTab — a tab whose backend runs on a remote host over SSH.

Does NOT subclass ChatTabBase (that class assumes a chat_state
continuously mutated in-process by a local StreamProcessor, which is the
wrong model here — the real ChatTab lives in a remote pack_daemon.py
process). Instead, PackTab independently implements exactly the surface
every webview_api.py call site actually needs from a tab object, verified
against the real code rather than assumed:

    handle_user_message, stop_streaming, is_streaming, chat_state,
    title, conversation_id, get_serializable_data, load_from_data,
    cleanup_resources, compact_conversation, truncate_conversation,
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

import json
import logging
import threading
from typing import Any
from typing import TYPE_CHECKING

from chat_state import ChatState
from conversation_graph import ConversationGraph
from core.pack_transport import PackTransport
from core.pack_transport import PackTransportError
from utils import ContentUpdate

if TYPE_CHECKING:
    from core.app_core import AppCore

logger = logging.getLogger(__name__)

ATTACH_TIMEOUT = 15.0
MUTATE_TIMEOUT = 15.0
SEND_MESSAGE_TIMEOUT = 30.0
STOP_STREAMING_TIMEOUT = 10.0
FOLD_RENDER_TIMEOUT = 2.0


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
    ) -> None:
        self.tab_id = tab_id
        self.title = title
        self._app_core = app_core
        self.conversation_id = conversation_id
        self.host = host
        self.session_id = session_id

        self.chat_state: ChatState | ConversationGraph = ConversationGraph()
        self.is_streaming = False
        self.offline = False
        self._current_answer_index = -1
        self._connect_lock = threading.Lock()

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
        threading.Thread(target=self._connect, args=(None,), daemon=True).start()

    def _connect(self, model: str | None) -> None:
        with self._connect_lock:
            try:
                self._transport.connect()
                self._resync(timeout=ATTACH_TIMEOUT)
                self.offline = False
                self._notify_if_active()
            except PackTransportError as e:
                logger.warning(f"Pack tab {self.tab_id} failed to connect: {e}")
                self.offline = True
                api = self._app_core.api
                if api is not None:
                    api.on_error(self.tab_id, f"Pack tab offline: {e}")

    def _ensure_connected(self, timeout: float) -> None:
        """Used by handle_user_message: if offline, try one synchronous

        reconnect before giving up, so a user who's already looking at
        the tab and just types doesn't need to click away and back.
        """
        if not self.offline and self._transport.connected:
            return
        self._transport = self._build_transport()
        self._transport.connect()
        self._resync(timeout=timeout)
        self.offline = False
        self._notify_if_active()

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
        result = self._transport.send_request("attach", {}, timeout=timeout)
        self._load_state(result.get("state", {}))
        self.title = result.get("title", self.title)
        self.is_streaming = result.get("is_streaming", False)
        return result

    def _resync_async(self) -> None:
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
            except PackTransportError as e:
                logger.warning(f"Pack tab {self.tab_id} post-turn resync failed: {e}")

        threading.Thread(target=run, daemon=True).start()

    def _load_state(self, state: dict[str, Any]) -> None:
        chat_state_data = state.get("chat_state", {})
        if "graph" in chat_state_data:
            self.chat_state = ConversationGraph.from_dict(chat_state_data)
        else:
            self.chat_state = ChatState.from_dict(chat_state_data)

    def _on_disconnect(self) -> None:
        self.offline = True
        self.is_streaming = False

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
        api = self._app_core.api
        if api is not None:
            api.on_streaming_end(self.tab_id, params["answer_index"])
        # The turn's content only ever reached the display via live
        # on_content_update pushes — catch the local mirror up now so a
        # later get_conversation_state (tab switch) doesn't read stale
        # (often still-empty) state. See _resync_async's docstring.
        self._resync_async()

    def _on_content_update(self, params: dict[str, Any]) -> None:
        api = self._app_core.api
        if api is None:
            return
        update = ContentUpdate(**params["update"])
        api.on_content_update(self.tab_id, update)

    def _on_error(self, params: dict[str, Any]) -> None:
        self.is_streaming = False
        api = self._app_core.api
        if api is not None:
            api.on_error(self.tab_id, params["message"], params.get("details", ""))
        # A turn can end via error instead of a clean streaming_end —
        # resync here too so a partial turn isn't lost on tab switch.
        self._resync_async()

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
            self._current_answer_index = result.get("answer_index", self._current_answer_index)
        except PackTransportError as e:
            logger.warning(f"Pack tab {self.tab_id} send_message failed: {e}")
            self.offline = True
            api = self._app_core.api
            if api is not None:
                api.on_error(self.tab_id, f"Pack tab offline: {e}")

    def stop_streaming(self) -> None:
        try:
            self._transport.send_request("stop_streaming", {}, timeout=STOP_STREAMING_TIMEOUT)
        except PackTransportError as e:
            logger.warning(f"Pack tab {self.tab_id} stop_streaming failed: {e}")

    def compact_conversation(self) -> dict[str, Any]:
        return self._mutate("compact_conversation")

    def truncate_conversation(self) -> dict[str, Any]:
        return self._mutate("truncate_conversation")

    def pop_conversation(self) -> dict[str, Any]:
        return self._mutate("pop_conversation")

    def _mutate(self, method: str) -> dict[str, Any]:
        try:
            result = self._transport.send_request(method, {}, timeout=MUTATE_TIMEOUT)
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

    def cleanup_resources(self) -> None:
        """Tear down only the local SSH subprocess/threads. Never signals

        the remote daemon to stop — this is the entire mechanism by which
        closing the app leaves the remote Pack session running.
        """
        self._transport.close()
