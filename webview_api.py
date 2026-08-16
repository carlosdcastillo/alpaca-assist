"""PyWebView API bridge for bidirectional Python/JS communication."""

from __future__ import annotations

import json
import logging
import os
import queue
import threading
import urllib.parse
import uuid
import webbrowser
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any
from typing import Optional

from core.pack_tab import PackTab
from core.projects import list_projects
from core.projects import load_project
from utils import ContentUpdate

if TYPE_CHECKING:
    from webview_app import ChatApp

logger = logging.getLogger(__name__)

# {absolute pack.json path: (mtime, parsed hosts)} — module-level rather
# than an instance attribute since WebViewAPI uses __slots__ and this data
# isn't per-instance anyway. get_status_info's status-bar poll can call
# this up to once/second while a Pack tab is streaming; keyed on
# (path, mtime) so edits made to pack.json while the app is running are
# still picked up without needing an app restart, but a steady file
# doesn't get re-read/re-parsed on every single poll. Keyed on the
# resolved absolute path, not just mtime, since PACK_FILE is cwd-relative
# — a bare mtime key would risk serving another file's stale cached
# content if cwd ever changed between calls (e.g. in tests, each using a
# different tmp dir) and the two files' mtimes happened to coincide.
_pack_hosts_cache: dict[str, tuple[float, list[dict[str, str]]]] = {}


class WebViewAPI:
    """Bidirectional bridge using JS polling for UI updates.

    This class provides a thread-safe mechanism for Python to push updates
    to the web UI via JavaScript polling (Pattern C from the spec).
    """

    # Use __slots__ to prevent pywebview from introspecting problematic attributes
    __slots__ = (
        "_app",
        "_window",
        "_pending_updates",
        "_tab_counter",
        "_current_answer_index",
        "_lock",
        "_fold_rendered_events",
    )

    def __init__(self, app: ChatApp) -> None:
        self._app = app
        self._window: Any | None = None
        self._pending_updates: queue.Queue[str] = queue.Queue()
        self._tab_counter = 0
        self._current_answer_index: dict[str, int] = {}
        self._lock = threading.RLock()
        # Track fold rendering completion events
        self._fold_rendered_events: dict[str, threading.Event] = {}

    def set_window(self, window: Any) -> None:
        """Set the webview window reference."""
        self._window = window

    def get_pending_js(self) -> list[str]:
        """Called from JavaScript via polling to get pending UI updates.

        This method is called by the JS side every ~50ms to retrieve
        any Python-side UI updates. Returns a list of JavaScript code
        strings to execute.
        """
        updates = []
        with self._lock:
            while not self._pending_updates.empty():
                try:
                    updates.append(self._pending_updates.get_nowait())
                except queue.Empty:
                    break
        return updates

    def _safe_evaluate_js(self, js_code: str) -> None:
        """Queue JS for pickup by JavaScript polling.

        Safe to call from any thread. The JS side will pick this up
        on its next poll and execute via eval() or Function().
        """
        with self._lock:
            self._pending_updates.put(js_code)

    # =======================================================================
    # JavaScript-callable API methods
    # =======================================================================

    def create_tab(self, title: str = "New Chat") -> dict[str, Any]:
        """Create new tab — Python generates the ID."""
        try:
            tab_id, tab = self._app.core.create_tab(title)
            self._current_answer_index[tab_id] = -1

            return {
                "success": True,
                "tab_id": tab_id,
                "title": title,
                "conversation_id": tab.conversation_id,
            }
        except Exception as e:
            logger.error(f"Error creating tab: {e}")
            return {"success": False, "error": str(e)}

    def get_pack_hosts(self) -> dict[str, Any]:
        """Return the quick-pick host list from pack.json, if any.

        A JSON array of {"hostname": <ssh target>, "display_name": <label>}
        entries; display_name falls back to hostname when omitted, and
        entries without a hostname are skipped. Missing file or malformed
        content just yields an empty list — "New Pack Tab..." always
        falls back to letting the user type a host by hand.
        """
        try:
            return {"success": True, "hosts": self._read_pack_hosts()}
        except Exception as e:
            logger.error(f"Error reading pack file: {e}")
            return {"success": True, "hosts": []}

    def get_projects(self) -> dict[str, Any]:
        """Return configured projects from the user-owned ~/packs directory."""
        try:
            return {
                "success": True,
                "projects": [project.to_info() for project in list_projects()],
            }
        except Exception as e:
            logger.error(f"Error reading projects: {e}")
            return {"success": False, "error": str(e), "projects": []}

    @staticmethod
    def _read_pack_hosts() -> list[dict[str, str]]:
        """Parse pack.json into a list of {hostname, display_name} dicts.

        Cached by (path, mtime) — get_status_info's status-bar poll can
        call this (via _lookup_pack_display_name) up to once/second while a
        Pack tab is streaming, and re-parsing a steady file on every single
        poll is wasted work. Keying on mtime rather than caching forever
        means edits to pack.json while the app is running are still picked
        up on the next call after they land, not just after a restart.
        """
        import os

        from core.config import PACK_FILE

        if not os.path.exists(PACK_FILE):
            return []

        abspath = os.path.abspath(PACK_FILE)
        mtime = os.path.getmtime(PACK_FILE)
        cached = _pack_hosts_cache.get(abspath)
        if cached is not None and cached[0] == mtime:
            return cached[1]

        with open(PACK_FILE) as f:
            raw = json.load(f)
        hosts: list[dict[str, str]] = []
        if isinstance(raw, list):
            for entry in raw:
                if not isinstance(entry, dict):
                    continue
                hostname = entry.get("hostname")
                if not hostname:
                    continue
                hosts.append(
                    {
                        "hostname": str(hostname),
                        "display_name": str(entry.get("display_name") or hostname),
                    },
                )
        _pack_hosts_cache[abspath] = (mtime, hosts)
        return hosts

    def _lookup_pack_display_name(self, hostname: str | None) -> str | None:
        """Return the best label to show for *hostname*: its display_name
        from pack.json if listed there, otherwise the hostname itself, or
        None if no hostname was given at all. Callers can always use the
        return value directly as a label without needing their own
        fallback — the only case with nothing to show is no input.
        """
        if not hostname:
            return None
        try:
            for entry in self._read_pack_hosts():
                if entry["hostname"] == hostname:
                    return entry["display_name"]
        except Exception:
            logger.warning(
                f"Could not read pack.json to resolve display name for {hostname!r}",
            )
        return hostname

    def create_pack_tab(
        self,
        host: str,
        title: str = "Pack Tab",
        project: str = "",
    ) -> dict[str, Any]:
        """Create a new Pack tab — a tab whose backend runs on `host` over SSH.

        Seeds the remote daemon's first-launch preferences with the local
        current model choice; after that, remote and local preferences
        evolve independently (ChatTabBase.preferences is a reference to
        the single shared AppCore.preferences dict, so the remote
        daemon's own AppCore must never be pointed at this machine's
        preferences.json directly).
        """
        try:
            session_id = uuid.uuid4().hex
            model = self._app.core.preferences.get("model")
            project_payload = self._project_payload(project, host, session_id)
            tab_id, tab = self._app.core.create_pack_tab(
                host,
                session_id,
                title,
                model=model,
                project_payload=project_payload,
            )
            self._current_answer_index[tab_id] = -1

            return {
                "success": True,
                "tab_id": tab_id,
                "title": title,
                "conversation_id": tab.conversation_id,
                "host": host,
                "session_id": session_id,
                "project": project_payload.get("name") if project_payload else None,
            }
        except Exception as e:
            logger.error(f"Error creating pack tab: {e}")
            return {"success": False, "error": str(e)}

    @staticmethod
    def _project_payload(
        project: str | None,
        host: str,
        session_id: str,
        workspace_path: str | None = None,
    ) -> dict[str, Any] | None:
        project_name = (project or "").strip()
        if not project_name:
            return None
        try:
            payload: dict[str, Any] = load_project(
                project_name,
                host=host,
            ).to_payload(session_id)
            return payload
        except (OSError, ValueError):
            if not workspace_path:
                raise
            # A removed project definition must not make an existing remote
            # workspace unreachable. Reattach without runbook/spinup and let
            # the status UI surface the missing local definition.
            return {
                "name": project_name,
                "repo_url": "",
                "branch": None,
                "workspace_path": workspace_path,
                "runbook": "",
                "spinup": "",
                "fingerprint": None,
                "definition_missing": True,
            }

    def copy_to_clipboard(self, text: str) -> dict[str, Any]:
        """Copy text to the system clipboard — used by code-block Copy buttons."""
        try:
            success = self._app.core.copy_to_clipboard(text)
            return {"success": success}
        except Exception as e:
            logger.error(f"Error copying to clipboard: {e}")
            return {"success": False, "error": str(e)}

    def save_and_close(self) -> dict[str, Any]:
        """Save the session then close the window.

        Called by the Exit menu action so the user sees feedback before the
        window disappears. On success the window is destroyed; on failure the
        caller shows a toast and the window stays open so the user can retry.
        """
        try:
            self._safe_evaluate_js(
                "document.getElementById('status-text').textContent"
                " = 'Saving session…';",
            )
            self._app.core.stop_autosave()  # avoid a concurrent autosave race
            self._app.core.save_session()
            if self._window is not None:
                self._window.destroy()
            return {"success": True}
        except Exception as e:
            logger.error(f"Error during save_and_close: {e}")
            return {"success": False, "error": str(e)}

    def close_tab(self, tab_id: str) -> dict[str, Any]:
        """Close a tab."""
        try:
            self._app.core.delete_tab(tab_id)
            if tab_id in self._current_answer_index:
                del self._current_answer_index[tab_id]
            return {"success": True}
        except Exception as e:
            logger.error(f"Error closing tab: {e}")
            return {"success": False, "error": str(e)}

    def get_tabs(self) -> list[dict[str, Any]]:
        """Get list of all tabs."""
        return [
            {"tab_id": tab_id, "title": tab.title}
            for tab_id, tab in self._app.core.tabs.items()
        ]

    def switch_tab(self, tab_id: str) -> dict[str, Any]:
        """Switch to a different tab."""
        try:
            self._app.set_active_tab(tab_id)
            tab = self._app.core.tabs.get(tab_id)
            if isinstance(tab, PackTab) and tab.offline:
                # Clicking into an offline Pack tab is as good a signal as
                # any to retry — reuses the tab-click path that's already
                # wired, no new UI surface needed.
                tab.reconnect_async()
            elif isinstance(tab, PackTab):
                # Live content only updates the visible DOM; the PackTab's
                # local chat_state mirror can lag behind the remote daemon
                # during a turn. Refresh in the background on activation and
                # repaint when it arrives so tab switching never has to wait
                # for streaming to finish.
                tab.refresh_async()
            return {"success": True}
        except Exception as e:
            logger.error(f"Error switching tab: {e}")
            return {"success": False, "error": str(e)}

    def send_message(
        self,
        tab_id: str,
        message: str,
        images: list[str] | None = None,
    ) -> dict[str, Any]:
        """Handle user message submission from web UI."""
        try:
            tab = self._app.core.tabs.get(tab_id)
            if not tab:
                return {"success": False, "error": "Tab not found"}

            # Handle the message (this starts streaming and calls on_new_qa_turn internally)
            tab.handle_user_message(message, images or [])

            answer_index: int = getattr(tab, "_current_answer_index", 0)
            return {
                "success": True,
                "answer_index": answer_index,
            }
        except Exception as e:
            logger.error(f"Error sending message: {e}")
            return {"success": False, "error": str(e)}

    def stop_streaming(self, tab_id: str) -> dict[str, Any]:
        """Stop streaming for a tab."""
        try:
            tab = self._app.core.tabs.get(tab_id)
            if not tab:
                return {"success": False, "error": "Tab not found"}

            tab.stop_streaming()
            return {"success": True}
        except Exception as e:
            logger.error(f"Error stopping streaming: {e}")
            return {"success": False, "error": str(e)}

    def get_conversation_state(self, tab_id: str) -> dict[str, Any]:
        """Return serialized conversation state for web UI."""
        try:
            tab = self._app.core.tabs.get(tab_id)
            if not tab:
                return {"success": False, "error": "Tab not found"}

            # Get base serializable data
            state = tab.get_serializable_data()

            # Ensure chat_state is properly serialized
            chat_state = state.get("chat_state", {})

            # If it's a ConversationGraph, ensure it's fully serialized
            if hasattr(tab.chat_state, "to_dict"):
                chat_state = tab.chat_state.to_dict()
                state["chat_state"] = chat_state

            # DEBUG: Log the actual structure
            logger.debug(f"[DEBUG] chat_state type: {type(tab.chat_state)}")
            logger.debug(
                f"[DEBUG] chat_state keys: {chat_state.keys() if isinstance(chat_state, dict) else 'N/A'}",
            )
            if isinstance(chat_state, dict):
                if "questions" in chat_state:
                    logger.debug(
                        f"[DEBUG] questions count: {len(chat_state['questions'])}",
                    )
                if "answers" in chat_state:
                    logger.debug(f"[DEBUG] answers count: {len(chat_state['answers'])}")
                    for i, ans in enumerate(chat_state["answers"]):
                        logger.debug(
                            f"[DEBUG] answer[{i}] type: {type(ans)}, value: {ans}",
                        )
                if "graph" in chat_state:
                    logger.debug(
                        f"[DEBUG] graph nodes count: {len(chat_state['graph'].get('nodes', {}))}",
                    )

            # Add metadata for the frontend
            state["_metadata"] = {
                "has_graph": "graph" in chat_state,
                "has_legacy": "questions" in chat_state and "answers" in chat_state,
                "tab_id": tab_id,
                "title": tab.title,
            }

            return {
                "success": True,
                "state": state,
            }
        except Exception as e:
            logger.error(f"Error getting conversation state: {e}")
            return {"success": False, "error": str(e)}

    def get_preferences(self) -> dict[str, Any]:
        """Load settings."""
        return {
            "success": True,
            "preferences": self._app.core.preferences,
        }

    def save_preferences(self, preferences: dict[str, Any]) -> dict[str, Any]:
        """Persist settings."""
        try:
            self._app.core.preferences.update(preferences)
            self._app.core.save_preferences()
            # Theme on next launch is read from preferences.json directly
            # (see ChatApp._setup_window) — nothing else to sync here.
            return {"success": True}
        except Exception as e:
            logger.error(f"Error saving preferences: {e}")
            return {"success": False, "error": str(e)}

    def get_status_info(self, tab_id: str) -> dict[str, Any]:
        """Return status bar data for a tab: text stats, token metrics, skill count."""
        import math

        try:
            tab = self._app.core.tabs.get(tab_id)
            if not tab:
                return {"success": False, "error": "Tab not found"}

            text = tab.chat_state.get_display_text()
            char_count = len(text.encode("utf-8"))
            line_count = text.count("\n") + 1 if text else 0
            token_estimate = math.ceil(char_count / 4.0)

            metrics = getattr(tab, "last_invocation_metrics", None)
            session_output_tokens = getattr(tab, "session_output_tokens", 0)
            session_input_tokens = getattr(tab, "session_input_tokens", 0)
            session_cached_input_tokens = getattr(
                tab,
                "session_cached_input_tokens",
                0,
            )
            latency_ms = metrics.get("invocation_latency_ms") if metrics else None

            skill_count = len(self._app.core.skill_manager.skills)

            # Local vs. Pack context for the status bar badge. PackTab
            # instances carry host/offline/session_id; plain ChatTabs don't.
            is_pack = hasattr(tab, "host")
            pack_info: dict[str, Any] = {"is_pack": is_pack}
            if is_pack:
                pack_info["host"] = getattr(tab, "host", None)
                pack_info["connected"] = not getattr(tab, "offline", True)
                pack_info["session_id"] = getattr(tab, "session_id", None)
                # Resolve a human-friendly display name from pack.json so
                # the badge shows "Pack: Deimos" instead of the raw IP.
                pack_info["display_name"] = self._lookup_pack_display_name(
                    pack_info["host"],
                )
                pack_info["project"] = getattr(tab, "project_name", None)
                pack_info["workspace_path"] = getattr(tab, "workspace_path", None)
                pack_info["project_setup_error"] = getattr(
                    tab,
                    "project_setup_error",
                    None,
                )
                pack_info["project_setup_state"] = getattr(
                    tab,
                    "project_setup_state",
                    None,
                )
                if isinstance(tab, PackTab):
                    pack_info["workspace_status"] = tab.get_workspace_status()

            return {
                "success": True,
                "char_count": char_count,
                "line_count": line_count,
                "token_estimate": token_estimate,
                "session_input_tokens": session_input_tokens,
                "session_cached_input_tokens": session_cached_input_tokens,
                "session_output_tokens": session_output_tokens,
                "latency_ms": latency_ms,
                "skill_count": skill_count,
                **pack_info,
            }
        except Exception as e:
            logger.error(f"Error getting status info: {e}")
            return {"success": False, "error": str(e)}

    def get_history(
        self,
        search_term: str = "",
        folder: str | None = None,
        archived: bool = False,
    ) -> dict[str, Any]:
        """Return searchable, organized closed-conversation history."""
        try:
            conversations = self._app.core.db.get_history_records(
                search_term,
                folder,
                archived,
            )
            facets = self._app.core.db.get_history_facets()
            return {"success": True, "conversations": conversations, **facets}
        except Exception as e:
            logger.error(f"Error getting history: {e}")
            return {"success": False, "error": str(e)}

    def update_history_entry(
        self,
        conv_id: int,
        changes: dict[str, Any],
    ) -> dict[str, Any]:
        """Rename, pin, folder, tag, or archive one history entry."""
        try:
            if "tags" in changes:
                changes["tags"] = [
                    str(tag).strip() for tag in changes["tags"] if str(tag).strip()
                ]
            updated = self._app.core.db.update_history_metadata(int(conv_id), **changes)
            return {"success": updated}
        except Exception as e:
            logger.error(f"Error updating history entry: {e}")
            return {"success": False, "error": str(e)}

    def delete_history_entries(self, conv_ids: list[int]) -> dict[str, Any]:
        """Permanently delete multiple conversations from history."""
        try:
            ids = [int(conv_id) for conv_id in conv_ids]
            deleted = self._app.core.db.delete_conversations(ids)
            return {"success": True, "deleted": deleted}
        except Exception as e:
            logger.error(f"Error deleting history entries: {e}")
            return {"success": False, "error": str(e)}

    def export_history_backup(self, conv_ids: list[int]) -> dict[str, Any]:
        """Save selected conversations, or all history when none are selected, as JSON."""
        try:
            import webview as _wv

            if self._window is None:
                return {"success": False, "error": "Window not initialized"}
            result = self._window.create_file_dialog(
                _wv.SAVE_DIALOG,
                save_filename="alpaca-assist-history.json",
                file_types=("JSON files (*.json)", "All files (*.*)"),
            )
            if not result:
                return {"success": False, "cancelled": True}
            path = result[0] if isinstance(result, (list, tuple)) else result
            backup = self._app.core.db.export_history(
                [int(i) for i in conv_ids] or None,
            )
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(backup, handle, ensure_ascii=False, indent=2)
            return {
                "success": True,
                "count": len(backup["conversations"]),
                "path": str(path),
            }
        except Exception as e:
            logger.error(f"Error exporting history backup: {e}")
            return {"success": False, "error": str(e)}

    def import_history_backup(self) -> dict[str, Any]:
        """Import conversations from an Alpaca Assist JSON backup."""
        try:
            import webview as _wv

            if self._window is None:
                return {"success": False, "error": "Window not initialized"}
            result = self._window.create_file_dialog(
                _wv.OPEN_DIALOG,
                file_types=("JSON files (*.json)", "All files (*.*)"),
            )
            if not result:
                return {"success": False, "cancelled": True}
            path = result[0] if isinstance(result, (list, tuple)) else result
            with open(path, encoding="utf-8") as handle:
                backup = json.load(handle)
            imported = self._app.core.db.import_history(backup)
            return {"success": True, "imported": imported}
        except Exception as e:
            logger.error(f"Error importing history backup: {e}")
            return {"success": False, "error": str(e)}

    def revive_conversation(self, conv_id: int) -> dict[str, Any]:
        """Restore a closed conversation as a new tab."""
        try:
            conv_id = int(conv_id)

            # Refuse to open a conversation that is already open in a tab
            for tab in self._app.core.tabs.values():
                if tab.conversation_id == conv_id:
                    return {"success": False, "error": "already_open"}

            tab_data = self._app.core.db.get_conversation(conv_id)
            if not tab_data:
                return {"success": False, "error": "Conversation not found"}
            title = tab_data.get("name") or tab_data.get("title") or "Chat"
            # Reuse the permanent conversation_id — don't allocate a new one.
            if tab_data.get("tab_type") == "pack":
                project_kwargs = {}
                if tab_data.get("project"):
                    project_kwargs = {
                        "project": tab_data["project"],
                        "workspace_path": tab_data.get("workspace_path"),
                    }
                result = self.create_pack_tab_and_notify_js(
                    tab_data.get("host", ""),
                    tab_data.get("session_id", ""),
                    title,
                    auto_switch=True,
                    conversation_id=conv_id,
                    **project_kwargs,
                )
            else:
                result = self.create_tab_and_notify_js(
                    title,
                    auto_switch=True,
                    conversation_id=conv_id,
                )
            if not result["success"]:
                return result
            tab_id = result["tab_id"]
            new_tab = self._app.core.tabs.get(tab_id)
            if new_tab:
                new_tab.load_from_data(tab_data)
            logger.info(f"Revived conversation {conv_id} as tab {tab_id}")
            return {"success": True, "tab_id": tab_id}
        except Exception as e:
            logger.error(f"Error reviving conversation: {e}")
            return {"success": False, "error": str(e)}

    def navigate_to_tab(self, tab_id: str) -> dict[str, Any]:
        """Switch to an open tab, or restore it from history if closed.

        Returns:
            {success, action: "switch"|"restore_queued", tab_id} on success.
            For "switch": JS should call tabManager.switchToTab(tab_id) directly.
            For "restore_queued": tab creation is queued via the poll channel;
                JS just waits for the poll to fire createTabUI.
        """
        try:
            logger.info(
                f"navigate_to_tab: tab_id={repr(tab_id)}, open_tabs={list(self._app.core.tabs.keys())}",
            )
            # Already open — tell JS to switch synchronously (no poll needed)
            if tab_id in self._app.core.tabs:
                return {"success": True, "action": "switch", "tab_id": tab_id}

            # Closed — search history by the tab_id stored in the conversation blob
            conv_id = self._app.core.db.find_conversation_by_tab_id(tab_id)
            logger.info(
                f"navigate_to_tab: find_conversation_by_tab_id({repr(tab_id)}) -> {conv_id}",
            )
            if conv_id is None:
                return {"success": False, "error": "not_found"}

            result = self.revive_conversation(conv_id)
            if result.get("success"):
                # revive_conversation queues createTabUI via _safe_evaluate_js
                return {"success": True, "action": "restore_queued"}
            return {"success": False, "error": result.get("error", "restore_failed")}
        except Exception as e:
            logger.error(f"Error navigating to tab {tab_id}: {e}")
            return {"success": False, "error": str(e)}

    def navigate_to_conv(self, conv_id: int) -> dict[str, Any]:
        """Switch to a tab by its stable DB conv_id, opening from history if closed."""
        try:
            conv_id = int(conv_id)
            # Check if already open in any tab
            for tab_id, tab in self._app.core.tabs.items():
                if tab.conversation_id == conv_id:
                    return {"success": True, "action": "switch", "tab_id": tab_id}
            # Not open — restore from DB
            result = self.revive_conversation(conv_id)
            if result.get("success"):
                return {"success": True, "action": "restore_queued"}
            return {"success": False, "error": result.get("error", "restore_failed")}
        except Exception as e:
            logger.error(f"Error navigating to conv {conv_id}: {e}")
            return {"success": False, "error": str(e)}

    def open_link(self, tab_id: str, href: str) -> dict[str, Any]:
        """Open a URL or a file belonging to the tab that rendered its link."""
        try:
            parsed = urllib.parse.urlsplit(href)
            is_windows_path = (
                os.name == "nt"
                and len(href) >= 3
                and href[1] == ":"
                and href[2] in ("/", "\\")
            )
            if parsed.scheme in ("http", "https"):
                target = href
            elif parsed.scheme in ("", "file") or is_windows_path:
                path_text = (
                    href if is_windows_path else urllib.parse.unquote(parsed.path)
                )
                tab = self._app.core.tabs.get(tab_id)
                if isinstance(tab, PackTab):
                    if parsed.scheme == "file" and parsed.netloc not in (
                        "",
                        "localhost",
                    ):
                        return {
                            "success": False,
                            "error": "Pack file links to another host are not supported",
                        }
                    local_copy = tab.materialize_file(path_text)
                    target = local_copy.as_uri()
                    if parsed.fragment:
                        target += f"#{parsed.fragment}"
                    if not webbrowser.open(target):
                        return {
                            "success": False,
                            "error": "No application could open the Pack file",
                        }
                    return {
                        "success": True,
                        "remote": True,
                        "filename": local_copy.name.split("-", 1)[-1],
                    }

                if parsed.scheme == "file":
                    target = href
                    if not webbrowser.open(target):
                        return {
                            "success": False,
                            "error": "No application could open the link",
                        }
                    return {"success": True}

                path = Path(path_text).expanduser()
                if not path.is_absolute():
                    path = Path.cwd() / path
                path = path.resolve()
                if not path.exists():
                    return {"success": False, "error": "Local file not found"}
                target = path.as_uri()
                if parsed.fragment:
                    target += f"#{parsed.fragment}"
            else:
                return {
                    "success": False,
                    "error": f"Unsupported link scheme: {parsed.scheme}",
                }

            if not webbrowser.open(target):
                return {"success": False, "error": "No application could open the link"}
            return {"success": True}
        except Exception as e:
            logger.error(f"Error opening link {href}: {e}")
            return {"success": False, "error": str(e)}

    def delete_history_entry(self, conv_id: int) -> dict[str, Any]:
        """Permanently delete a conversation from history."""
        try:
            deleted = self._app.core.db.delete_conversation(int(conv_id))
            return {"success": deleted}
        except Exception as e:
            logger.error(f"Error deleting history entry: {e}")
            return {"success": False, "error": str(e)}

    def get_models(self) -> dict[str, Any]:
        """Fetch available models from API.

        Returns connected:True/False so callers can update the health
        indicator independently of whether models were found.
        """
        try:
            models = self._app.core.fetch_models_from_api()
            return {"success": True, "connected": True, "models": models}
        except Exception as e:
            logger.error(f"Error fetching models: {e}")
            return {"success": False, "connected": False, "models": [], "error": str(e)}

    def set_model(self, model: str) -> dict[str, Any]:
        """Set the selected model."""
        try:
            active_tab_id = self._app.get_active_tab_id()
            active_tab = (
                self._app.core.tabs.get(active_tab_id) if active_tab_id else None
            )
            if isinstance(active_tab, PackTab):
                return active_tab.set_model(model)
            self._app.core.preferences["model"] = model
            self._app.core.save_preferences()
            return {"success": True}
        except Exception as e:
            logger.error(f"Error setting model: {e}")
            return {"success": False, "error": str(e)}

    def get_mcp_tools(self) -> dict[str, Any]:
        """Get available MCP tools."""
        try:
            tools = self._app.core.get_available_mcp_tools()
            return {"success": True, "tools": tools}
        except Exception as e:
            logger.error(f"Error getting MCP tools: {e}")
            return {"success": False, "error": str(e)}

    def get_mcp_config(self) -> dict[str, Any]:
        """Return MCP server configs with their available tools and enabled states."""
        try:
            mcp = self._app.core.mcp_manager
            servers = {}
            for name, config in mcp.server_configs.items():
                available = mcp.available_tools.get(name, [])
                disabled = mcp.disabled_tools.get(name, set())
                servers[name] = {
                    "command": config.get("command", []),
                    "args": config.get("args", []),
                    "available_tools": [
                        {
                            "name": t.get("name", ""),
                            "description": t.get("description", ""),
                            "enabled": t.get("name", "") not in disabled,
                        }
                        for t in available
                    ],
                }
            return {"success": True, "servers": servers}
        except Exception as e:
            logger.error(f"Error getting MCP config: {e}")
            return {"success": False, "error": str(e)}

    def add_mcp_server(
        self,
        name: str,
        command_str: str,
        args_str: str,
    ) -> dict[str, Any]:
        """Add a new MCP server and start connecting asynchronously."""
        try:
            import shlex

            mcp = self._app.core.mcp_manager
            if name in mcp.server_configs:
                return {"success": False, "error": f"Server '{name}' already exists"}
            command = shlex.split(command_str) if command_str else []
            args = shlex.split(args_str) if args_str else []
            if not command:
                return {"success": False, "error": "Command is required"}
            mcp.server_configs[name] = {"command": command, "args": args}
            self._app.core.save_mcp_config()
            if self._app.core.event_loop:
                import asyncio as _asyncio

                _asyncio.run_coroutine_threadsafe(
                    mcp.add_server(name, command, args),
                    self._app.core.event_loop,
                )
            return {"success": True}
        except Exception as e:
            logger.error(f"Error adding MCP server: {e}")
            return {"success": False, "error": str(e)}

    def update_mcp_server(
        self,
        old_name: str,
        new_name: str,
        command_str: str,
        args_str: str,
    ) -> dict[str, Any]:
        """Update an existing MCP server config (does not reconnect; use reload)."""
        try:
            import shlex

            mcp = self._app.core.mcp_manager
            command = shlex.split(command_str) if command_str else []
            args = shlex.split(args_str) if args_str else []
            if not new_name or not command:
                return {"success": False, "error": "Name and Command are required"}
            if new_name != old_name and new_name in mcp.server_configs:
                return {
                    "success": False,
                    "error": f"Server '{new_name}' already exists",
                }
            disabled = mcp.disabled_tools.pop(old_name, set())
            if old_name in mcp.server_configs:
                del mcp.server_configs[old_name]
            mcp.server_configs[new_name] = {"command": command, "args": args}
            if disabled:
                mcp.disabled_tools[new_name] = disabled
            self._app.core.save_mcp_config()
            return {"success": True}
        except Exception as e:
            logger.error(f"Error updating MCP server: {e}")
            return {"success": False, "error": str(e)}

    def remove_mcp_server(self, name: str) -> dict[str, Any]:
        """Remove an MCP server and disconnect it."""
        try:
            mcp = self._app.core.mcp_manager
            if name in mcp.server_configs:
                del mcp.server_configs[name]
            mcp.disabled_tools.pop(name, None)
            if self._app.core.event_loop and name in mcp.servers:
                import asyncio as _asyncio

                _asyncio.run_coroutine_threadsafe(
                    mcp.disconnect_server(name),
                    self._app.core.event_loop,
                )
            else:
                mcp.servers.pop(name, None)
                mcp.available_tools.pop(name, None)
            self._app.core.save_mcp_config()
            return {"success": True}
        except Exception as e:
            logger.error(f"Error removing MCP server: {e}")
            return {"success": False, "error": str(e)}

    def toggle_mcp_tool(
        self,
        server_name: str,
        tool_name: str,
        enabled: bool,
    ) -> dict[str, Any]:
        """Enable or disable a specific MCP tool."""
        try:
            self._app.core.mcp_manager.set_tool_enabled(server_name, tool_name, enabled)
            self._app.core.save_mcp_config()
            return {"success": True}
        except Exception as e:
            logger.error(f"Error toggling MCP tool: {e}")
            return {"success": False, "error": str(e)}

    def test_mcp_connection(self, command_str: str, args_str: str) -> dict[str, Any]:
        """Test an MCP server connection in a temporary event loop."""
        try:
            import asyncio as _asyncio
            import shlex

            command = shlex.split(command_str) if command_str else []
            args = shlex.split(args_str) if args_str else []
            if not command:
                return {"success": False, "error": "No command specified"}

            async def _test() -> dict[str, Any]:
                from mcp_manager import MCPManager

                tmp = MCPManager()
                try:
                    ok = await tmp.add_server("_test_", command, args)
                    if not ok:
                        return {"success": False, "error": "Failed to connect"}
                    tool_count = len(tmp.available_tools.get("_test_", []))
                    await tmp.disconnect_server("_test_")
                    return {"success": True, "tool_count": tool_count}
                except Exception as exc:
                    return {"success": False, "error": str(exc)}

            loop = _asyncio.new_event_loop()
            try:
                return loop.run_until_complete(_asyncio.wait_for(_test(), timeout=15.0))
            except _asyncio.TimeoutError:
                return {"success": False, "error": "Connection timed out"}
            finally:
                loop.close()
        except Exception as e:
            logger.error(f"Error testing MCP connection: {e}")
            return {"success": False, "error": str(e)}

    def reload_mcp_config(self) -> dict[str, Any]:
        """Reload all MCP servers from disk."""
        try:
            self._app.core.reload_mcp_servers()
            return {"success": True}
        except Exception as e:
            logger.error(f"Error reloading MCP config: {e}")
            return {"success": False, "error": str(e)}

    def call_mcp_tool_direct(
        self,
        server_name: str,
        tool_name: str,
        arguments_json: str,
    ) -> dict[str, Any]:
        """Call an MCP tool directly and return the result synchronously."""
        try:
            arguments = json.loads(arguments_json) if arguments_json.strip() else {}
        except Exception:
            return {"success": False, "error": "Invalid JSON arguments"}
        try:
            result_holder: dict[str, Any] = {"result": None}
            done_event = threading.Event()

            def callback(result: Any) -> None:
                result_holder["result"] = result
                done_event.set()

            self._app.core.call_mcp_tool(
                server_name,
                tool_name,
                arguments,
                callback=callback,
            )
            if done_event.wait(timeout=30.0):
                return {"success": True, "result": str(result_holder["result"])}
            return {"success": False, "error": "Timeout waiting for tool result"}
        except Exception as e:
            logger.error(f"Error calling MCP tool: {e}")
            return {"success": False, "error": str(e)}

    def open_file_dialog_mcp(self) -> dict[str, Any]:
        """Open a native file dialog for selecting an MCP server executable."""
        try:
            import webview as _wv

            if self._window is None:
                return {"success": False, "error": "Window not initialized"}
            result = self._window.create_file_dialog(
                _wv.OPEN_DIALOG,
                file_types=(
                    "Python Files (*.py)",
                    "Executable Files (*.exe)",
                    "All Files (*.*)",
                ),
            )
            if result and len(result) > 0:
                return {"success": True, "path": result[0]}
            return {"success": False}
        except Exception as e:
            logger.error(f"Error opening file dialog: {e}")
            return {"success": False, "error": str(e)}

    def _detect_image_mime(self, data: bytes) -> str | None:
        """Return MIME type from image magic bytes, or None if unrecognized."""
        if data[:3] == b"\xff\xd8\xff":
            return "image/jpeg"
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            return "image/png"
        if data[:6] in (b"GIF87a", b"GIF89a"):
            return "image/gif"
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            return "image/webp"
        return None

    def attach_image(self) -> dict[str, Any]:
        """Open a native file dialog for selecting an image and return base64 data.

        Returns:
            {"success": True, "data": base64_string, "mime_type": mime_type, "filename": name}
            or {"success": False, "error": message, "cancelled": True} if user cancelled
        """
        try:
            import base64
            import os

            import webview as _wv

            if self._window is None:
                return {"success": False, "error": "Window not initialized"}

            result = self._window.create_file_dialog(
                _wv.OPEN_DIALOG,
                file_types=(
                    "Image files (*.jpg;*.jpeg;*.png;*.gif;*.webp)",
                    "JPEG (*.jpg;*.jpeg)",
                    "PNG (*.png)",
                    "GIF (*.gif)",
                    "WebP (*.webp)",
                    "All files (*.*)",
                ),
            )

            # User cancelled
            if not result or len(result) == 0:
                return {"success": False, "cancelled": True}

            file_path = result[0]
            filename = os.path.basename(file_path)

            # Validate extension
            ext = file_path.rsplit(".", 1)[-1].lower() if "." in file_path else ""
            if ext not in ("jpg", "jpeg", "png", "gif", "webp"):
                return {"success": False, "error": f"Unsupported image format: {ext}"}

            # Read and validate image
            try:
                with open(file_path, "rb") as fh:
                    image_bytes = fh.read()
            except OSError as exc:
                return {"success": False, "error": f"Error reading file: {exc}"}

            # Detect MIME type from magic bytes
            mime_type = self._detect_image_mime(image_bytes)
            if mime_type is None:
                return {
                    "success": False,
                    "error": "Unsupported image format (invalid image data)",
                }

            # Encode to base64
            b64_data = base64.b64encode(image_bytes).decode("utf-8")

            return {
                "success": True,
                "data": b64_data,
                "mime_type": mime_type,
                "filename": filename,
            }

        except Exception as e:
            logger.error(f"Error attaching image: {e}")
            return {"success": False, "error": str(e)}

    def get_video_chunk(
        self,
        tab_id: str,
        locator: str,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Return one bounded video chunk without storing bytes in chat state."""
        try:
            tab = self._app.core.tabs.get(tab_id)
            if tab is None:
                return {"success": False, "error": "Tab not found"}
            if hasattr(tab, "read_video_chunk"):
                chunk = tab.read_video_chunk(locator, offset)
            else:
                from video_tool_result import read_video_chunk

                chunk = read_video_chunk(locator, offset)
            return {"success": True, **chunk}
        except Exception as e:
            logger.warning(f"Could not load video chunk for tab {tab_id}: {e}")
            return {"success": False, "error": str(e)}

    # =======================================================================
    # Live app surfaces
    #
    # Control plane only, and deliberately thin. Framebuffer traffic never
    # appears here: the panel opens a WebSocket to the tunnel's local end and
    # noVNC speaks RFB through it. Routing pixels through _safe_evaluate_js
    # and the 50 ms get_pending_js() poller would be a category error — that
    # queue exists for chat tokens.
    #
    # Every method duck-types on the tab the same way get_video_chunk does,
    # so a local (Windows) tab, which has no remote display, fails with a
    # clear message instead of an AttributeError.
    # =======================================================================

    def _surface_tab(self, tab_id: str) -> Any:
        tab = self._app.core.tabs.get(tab_id)
        if tab is None:
            raise RuntimeError("Tab not found")
        if not hasattr(tab, "surface_open"):
            raise RuntimeError(
                "Live app surfaces need a Pack tab — this tab runs locally, "
                "and Windows has no X display to share.",
            )
        return tab

    def surface_open(
        self,
        tab_id: str,
        spec: dict[str, Any] | None = None,
        width: int = 1280,
        height: int = 800,
    ) -> dict[str, Any]:
        """Start a remote app surface and return a local ws:// URL for it."""
        try:
            result = self._surface_tab(tab_id).surface_open(spec, width, height)
            return {"success": True, **result}
        except Exception as e:
            logger.warning(f"Could not open a surface for tab {tab_id}: {e}")
            return {"success": False, "error": str(e)}

    def surface_attach(self, tab_id: str, surface_id: str) -> dict[str, Any]:
        """Re-tunnel to a still-running surface, e.g. from a transcript card."""
        try:
            result = self._surface_tab(tab_id).surface_attach(surface_id)
            return {"success": True, **result}
        except Exception as e:
            logger.info(f"Could not attach to surface {surface_id}: {e}")
            return {"success": False, "error": str(e)}

    def surface_close(self, tab_id: str, surface_id: str) -> dict[str, Any]:
        try:
            result = self._surface_tab(tab_id).surface_close(surface_id)
            return {"success": True, **result}
        except Exception as e:
            logger.warning(f"Could not close surface {surface_id}: {e}")
            return {"success": False, "error": str(e)}

    def surface_control(
        self,
        tab_id: str,
        method: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Proxy one non-connection surface call (list, touch, lease, snapshot).

        The method name is checked against the supervisor's own table rather
        than passed through: page JS must not be able to reach an arbitrary
        Pack daemon RPC by naming it here.
        """
        try:
            from core.surface_supervisor import SURFACE_METHODS

            if method not in SURFACE_METHODS or method in (
                "surface_open",
                "surface_attach",
                "surface_close",
            ):
                raise RuntimeError(f"Unsupported surface method: {method}")
            result = self._surface_tab(tab_id).surface_call(method, params or {})
            return {"success": True, **result}
        except Exception as e:
            logger.info(f"Surface call {method} failed for tab {tab_id}: {e}")
            return {"success": False, "error": str(e)}

    def get_gated_tool_output(self, tab_id: str, gated_text: str) -> dict[str, Any]:
        """Load a gated result on demand, proxying reads for Pack tabs."""
        try:
            tab = self._app.core.tabs.get(tab_id)
            if tab is None:
                return {"success": False, "error": "Tab not found"}
            if hasattr(tab, "read_gated_tool_output"):
                content = tab.read_gated_tool_output(gated_text)
            else:
                from core.tool_output_gate import read_gated_tool_output

                content = read_gated_tool_output(gated_text, tab_id)
            return {"success": True, "content": content}
        except Exception as e:
            logger.warning(f"Could not load gated tool output for tab {tab_id}: {e}")
            return {"success": False, "error": str(e)}

    def get_agent_skills_config(self) -> dict[str, Any]:
        """Return agent skills configuration and discovered skills list."""
        try:
            from core.config import _DEFAULT_SKILLS_DIR

            skills_cfg = self._app.core.preferences.get("agent_skills", {})
            skills = [
                {
                    "name": s.name,
                    "description": s.description,
                    "location": s.location,
                    "license": s.license,
                }
                for s in self._app.core.skill_manager.skills
            ]
            return {
                "success": True,
                "enabled": bool(skills_cfg.get("enabled", True)),
                "directories": list(skills_cfg.get("directories", [])),
                "default_dir": _DEFAULT_SKILLS_DIR,
                "skills": skills,
            }
        except Exception as e:
            logger.error(f"Error getting agent skills config: {e}")
            return {"success": False, "error": str(e)}

    def save_agent_skills_config(self, config: dict[str, Any]) -> dict[str, Any]:
        """Save agent skills config and trigger rediscovery."""
        try:
            self._app.core.preferences["agent_skills"] = {
                "enabled": bool(config.get("enabled", True)),
                "directories": list(config.get("directories", [])),
            }
            self._app.core.save_preferences()
            self._app.core.discover_skills()
            return {
                "success": True,
                "skill_count": len(self._app.core.skill_manager.skills),
            }
        except Exception as e:
            logger.error(f"Error saving agent skills config: {e}")
            return {"success": False, "error": str(e)}

    def refresh_skills(self) -> dict[str, Any]:
        """Re-run skill discovery and return the updated list."""
        try:
            self._app.core.discover_skills()
            skills = [
                {
                    "name": s.name,
                    "description": s.description,
                    "location": s.location,
                    "license": s.license,
                }
                for s in self._app.core.skill_manager.skills
            ]
            return {"success": True, "skills": skills, "skill_count": len(skills)}
        except Exception as e:
            logger.error(f"Error refreshing skills: {e}")
            return {"success": False, "error": str(e)}

    def export_conversation(self, tab_id: str) -> dict[str, Any]:
        """Export a conversation to HTML."""
        try:
            tab = self._app.core.tabs.get(tab_id)
            if not tab:
                return {"success": False, "error": "Tab not found"}

            self._app.core.export_tab_to_html(tab)
            return {"success": True}
        except Exception as e:
            logger.error(f"Error exporting conversation: {e}")
            return {"success": False, "error": str(e)}

    def compact_conversation(self, tab_id: str) -> dict[str, Any]:
        """Compact a conversation by removing tool call/result components."""
        try:
            tab = self._app.core.tabs.get(tab_id)
            if not tab:
                return {"success": False, "error": "Tab not found"}
            result = tab.compact_conversation()
            return {"success": True, **result}
        except Exception as e:
            logger.error(f"Error compacting conversation: {e}")
            return {"success": False, "error": str(e)}

    def truncate_conversation(self, tab_id: str) -> dict[str, Any]:
        """Truncate a conversation to last Q/A pair."""
        try:
            tab = self._app.core.tabs.get(tab_id)
            if not tab:
                return {"success": False, "error": "Tab not found"}
            result = tab.truncate_conversation()
            return {"success": True, **result}
        except Exception as e:
            logger.error(f"Error truncating conversation: {e}")
            return {"success": False, "error": str(e)}

    def recompute_title(self, tab_id: str) -> dict[str, Any]:
        """Generate a fresh title for an existing conversation."""
        try:
            tab = self._app.core.tabs.get(tab_id)
            if not tab:
                return {"success": False, "error": "Tab not found"}
            if tab.is_streaming:
                return {"success": False, "error": "Cannot recompute while streaming"}
            result = tab.recompute_title()
            return {"success": True, **result}
        except Exception as e:
            logger.error(f"Error recomputing conversation title: {e}")
            return {"success": False, "error": str(e)}

    def pop_conversation(self, tab_id: str) -> dict[str, Any]:
        """Remove the last Q/A pair and return the popped question."""
        try:
            tab = self._app.core.tabs.get(tab_id)
            if not tab:
                return {"success": False, "error": "Tab not found"}
            result = tab.pop_conversation()
            return {"success": True, **result}
        except Exception as e:
            logger.error(f"Error popping conversation: {e}")
            return {"success": False, "error": str(e)}

    def clone_conversation(self, tab_id: str) -> dict[str, Any]:
        """Duplicate the current conversation into a new tab."""
        try:
            tab = self._app.core.tabs.get(tab_id)
            if not tab:
                return {"success": False, "error": "Tab not found"}
            if tab.is_streaming:
                return {"success": False, "error": "Cannot clone while streaming"}

            clone_title = f"Clone: {tab.title}"
            result = self.create_tab_and_notify_js(clone_title, auto_switch=True)
            if not result["success"]:
                return result

            clone_conv_id = result["conversation_id"]
            new_tab = self._app.core.tabs.get(result["tab_id"])
            # create_tab_and_notify_js only ever creates a plain local
            # ChatTab, never a PackTab — the isinstance check below is for
            # mypy's benefit (tabs.get()'s return type is the wider
            # ChatTab | PackTab union), not a runtime possibility.
            if new_tab is not None and not isinstance(new_tab, PackTab):
                # load_from_data restores the *original* title and conversation_id
                # from the serialized payload — reassert our own values.
                new_tab.load_from_data(tab.get_serializable_data())
                new_tab.conversation_id = clone_conv_id
                new_tab.title = clone_title
                new_tab._summary_handler._generated = True
                self.update_tab_title(result["tab_id"], clone_title)

            logger.info(f"Cloned tab {tab_id} as {result['tab_id']}")
            return {"success": True, "tab_id": result["tab_id"]}
        except Exception as e:
            logger.error(f"Error cloning conversation: {e}")
            return {"success": False, "error": str(e)}

    _HANDOFF_PROMPT = (
        "Please summarize this conversation as a handoff, covering: "
        "what has been accomplished, what is currently being worked on, "
        "what are the important files involved, and exactly where we left off."
    )

    def perform_handoff(self, original_tab_id: str) -> dict[str, Any]:
        """Clone the current conversation into a new tab and generate a handoff summary.

        Creates a new tab, clones and compacts the conversation, then submits a
        summarization prompt.  After streaming completes a callback injects a
        back-reference, truncates to the single handoff Q/A, and renames the tab.
        """
        try:
            original_tab = self._app.core.tabs.get(original_tab_id)
            if not original_tab:
                return {"success": False, "error": "Tab not found"}
            if original_tab.is_streaming:
                return {"success": False, "error": "Cannot handoff while streaming"}
            if not original_tab.chat_state.questions:
                return {"success": False, "error": "Conversation is empty"}

            original_title = original_tab.title

            # --- Create the new tab (Python + JS side) -------------------------
            new_tab_id, new_tab = self._app.core.create_tab("📋 Handoff")
            handoff_conv_id = new_tab.conversation_id
            self._current_answer_index[new_tab_id] = -1
            # Notify JS to create the tab UI and switch to it
            self._safe_evaluate_js(
                f"app.tabManager.createTabUI("
                f"{json.dumps(new_tab_id)}, {json.dumps('📋 Handoff')}, true);",
            )
            self._safe_evaluate_js(
                f"app.tabManager.setConversationId({json.dumps(new_tab_id)}, {handoff_conv_id});",
            )

            # --- Load clone and compact ----------------------------------------
            new_tab.load_from_data(original_tab.get_serializable_data())
            # load_from_data copies the original's conversation_id; restore our own.
            new_tab.conversation_id = handoff_conv_id
            new_tab.compact_conversation()  # strip tool call/result components

            # Suppress automatic summary generation — we control the title ourselves
            new_tab._summary_handler._generated = True

            # conversation_id is permanent and allocated at tab-creation time,
            # so the back-link is always available without a force-save.
            orig_conv_id = original_tab.conversation_id

            # --- Register post-streaming callback ------------------------------
            api_ref = self  # capture for closure

            def _on_handoff_complete() -> None:
                try:
                    # Prepend a clickable back-link at the top of the answer.
                    # Uses alpaca://conv/{conv_id} — stable across restarts.
                    if new_tab.chat_state.answers:
                        back_link = (
                            f"[↩ Back to: {original_title}]"
                            f"(alpaca://conv/{orig_conv_id})\n\n"
                        )
                        last_answer = new_tab.chat_state.answers[-1]
                        with last_answer._lock:
                            last_answer.components.insert(0, back_link)

                    # Keep only the handoff Q/A pair
                    new_tab.chat_state.truncate_to_last()

                    # Rename the tab
                    handoff_title = f"📋 {original_title}"
                    new_tab.title = handoff_title
                    api_ref.update_tab_title(new_tab_id, handoff_title)

                    # Tell JS to reload the display with the clean truncated state
                    api_ref._safe_evaluate_js(
                        f"app.onHandoffComplete({json.dumps(new_tab_id)});",
                    )
                except Exception as cb_err:
                    logger.error(f"[HANDOFF] Post-streaming callback error: {cb_err}")
                    # Still reload so the tab is not left in a broken state
                    api_ref._safe_evaluate_js(
                        f"app.onHandoffComplete({json.dumps(new_tab_id)});",
                    )

            new_tab._on_streaming_complete_callback = _on_handoff_complete

            # --- Add question to JS display BEFORE streaming starts ------------
            self._safe_evaluate_js(
                f"app.chatDisplay.addQuestion({json.dumps(self._HANDOFF_PROMPT)}, []);",
            )

            # --- Submit the handoff prompt (starts streaming asynchronously) --
            new_tab.handle_user_message(self._HANDOFF_PROMPT, [])

            logger.info(
                f"[HANDOFF] Started for tab {original_tab_id} → new tab {new_tab_id}",
            )
            return {"success": True}

        except Exception as e:
            logger.error(f"[HANDOFF] Error: {e}")
            return {"success": False, "error": str(e)}

    # =======================================================================
    # Python-side methods for pushing updates to web UI
    # =======================================================================

    def on_new_qa_turn(self, tab_id: str) -> int:
        """Called when a new Q/A pair starts (user sends message).

        Increments the answer index for the tab and returns the new index.
        The streaming layer calls this before starting streaming.
        """
        with self._lock:
            current = self._current_answer_index.get(tab_id, -1)
            new_index = current + 1
            self._current_answer_index[tab_id] = new_index
            return new_index

    def on_content_update(self, tab_id: str, update: ContentUpdate) -> None:
        """Push streaming content update to web UI."""
        update_type = "content"
        if update.is_tool_call:
            update_type = "tool_call"
        elif update.is_tool_result:
            update_type = "tool_result"
        elif update.is_done:
            update_type = "done"
        elif update.is_error:
            update_type = "error"

        # Use the update's answer_index if provided, otherwise fall back to tracked index
        answer_index = (
            update.answer_index
            if update.answer_index is not None
            else self._current_answer_index.get(tab_id, 0)
        )

        payload = {
            "type": update_type,
            "content": update.content_chunk,
            "answer_index": answer_index,
            "is_tool_call": update.is_tool_call,
            "is_tool_result": update.is_tool_result,
            "is_done": update.is_done,
            "is_error": update.is_error,
            "tool_id": update.tool_id,
            "metrics": update.metrics,
        }

        logger.debug(
            f"[UI DEBUG] Sending content update: type={update_type}, answer_index={answer_index}",
        )

        self._safe_evaluate_js(
            f"app.onContentUpdate({json.dumps(tab_id)}, {json.dumps(payload)});",
        )

    def on_streaming_start(self, tab_id: str, answer_index: int) -> None:
        """Notify web UI that streaming has started."""
        self._safe_evaluate_js(
            f"app.onStreamingStart({json.dumps(tab_id)}, {answer_index});",
        )

    def on_streaming_end(self, tab_id: str, answer_index: int) -> None:
        """Notify web UI that streaming has ended."""
        self._safe_evaluate_js(
            f"app.onStreamingEnd({json.dumps(tab_id)}, {answer_index});",
        )

    def on_turn_timing(
        self,
        tab_id: str,
        answer_index: int,
        timing: dict[str, Any],
    ) -> None:
        """Push a finished turn's timing to the web UI.

        This is a separate signal from on_streaming_end on purpose: that one
        fires once per LLM invocation, so a turn with four tool calls emits it
        five times. This fires exactly once, when the whole tool loop is done,
        and is what stops the live turn stopwatch.
        """
        self._safe_evaluate_js(
            f"app.onTurnTiming({json.dumps(tab_id)}, {answer_index}, "
            f"{json.dumps(timing)});",
        )

    def on_error(self, tab_id: str, message: str, details: str = "") -> None:
        """Push error notification to web UI."""
        payload = {
            "message": message,
            "details": details,
        }
        self._safe_evaluate_js(
            f"app.onError({json.dumps(tab_id)}, {json.dumps(payload)});",
        )

    def update_tab_title(self, tab_id: str, title: str) -> None:
        """Update the tab title in the web UI."""
        self._safe_evaluate_js(
            f"app.updateTabTitle({json.dumps(tab_id)}, {json.dumps(title)});",
        )
        # Also update the native window title if this is the active tab
        if self._app.get_active_tab_id() == tab_id and self._app.window:
            self._app.window.title = f"Alpaca Assist - {title}"

    def inject_tool_fold(
        self,
        tab_id: str,
        fold_id: str,
        fold_type: str,
        body_text: str,
        answer_index: int,
        duration_ms: int | None = None,
    ) -> None:
        """Inject a tool fold widget into the web UI and wait for confirmation."""
        # Create an event to track when the fold is rendered
        event_key = f"{tab_id}:{fold_id}"
        with self._lock:
            self._fold_rendered_events[event_key] = threading.Event()

        payload = {
            "fold_id": fold_id,
            "type": fold_type,  # 'call' or 'result'
            "body": body_text,
            "answer_index": answer_index,
            "duration_ms": duration_ms,
        }
        self._safe_evaluate_js(
            f"app.injectToolFold({json.dumps(tab_id)}, {json.dumps(payload)});",
        )

    def wait_for_fold_rendered(
        self,
        tab_id: str,
        fold_id: str,
        timeout: float = 2.0,
    ) -> bool:
        """Wait for JavaScript to confirm the fold widget is rendered.

        Returns True if fold was rendered, False on timeout.
        """
        event_key = f"{tab_id}:{fold_id}"
        with self._lock:
            event = self._fold_rendered_events.get(event_key)
            if event is None:
                # Fold wasn't injected yet or already consumed
                return False

        # Wait for JavaScript to signal fold is rendered
        result = event.wait(timeout=timeout)

        # Clean up
        with self._lock:
            self._fold_rendered_events.pop(event_key, None)

        return result

    def on_fold_rendered(self, tab_id: str, fold_id: str) -> dict[str, Any]:
        """Called by JavaScript when a fold widget is fully rendered."""
        event_key = f"{tab_id}:{fold_id}"
        with self._lock:
            event = self._fold_rendered_events.get(event_key)
            if event:
                event.set()
        return {"success": True}

    def select_tab(self, tab_id: str) -> None:
        """Tell the web UI to select a specific tab.

        Called from Python side during session restoration.
        """
        self._safe_evaluate_js(
            f"app.selectTab({json.dumps(tab_id)});",
        )

    def create_tab_and_notify_js(
        self,
        title: str = "New Chat",
        auto_switch: bool = True,
        conversation_id: int | None = None,
    ) -> dict[str, Any]:
        """Create a new tab and notify JavaScript to create the UI.

        Used during session restoration, revive, and handoff.
        Pass conversation_id to reuse an existing permanent ID (revive / restore);
        omit it to allocate a fresh one.
        """
        try:
            tab_id, tab = self._app.core.create_tab(
                title,
                conversation_id=conversation_id,
            )
            self._current_answer_index[tab_id] = -1

            self._safe_evaluate_js(
                f"app.tabManager.createTabUI({json.dumps(tab_id)}, {json.dumps(title)}, {json.dumps(auto_switch)});",
            )
            # Push the permanent conversation ID so JS can show/copy it.
            self._safe_evaluate_js(
                f"app.tabManager.setConversationId({json.dumps(tab_id)}, {tab.conversation_id});",
            )

            logger.info(
                f"Created tab {tab_id} conv={tab.conversation_id} title='{title}' auto_switch={auto_switch}",
            )

            return {
                "success": True,
                "tab_id": tab_id,
                "title": title,
                "conversation_id": tab.conversation_id,
            }
        except Exception as e:
            logger.error(f"Error creating tab: {e}")
            return {"success": False, "error": str(e)}

    def create_pack_tab_and_notify_js(
        self,
        host: str,
        session_id: str,
        title: str = "Pack Tab",
        auto_switch: bool = True,
        conversation_id: int | None = None,
        project: str | None = None,
        workspace_path: str | None = None,
    ) -> dict[str, Any]:
        """Create a new Pack tab and notify JavaScript to create the UI.

        Used during session restoration to reattach a previously saved
        Pack tab. Unlike create_tab_and_notify_js, session_id is always
        supplied by the caller (never generated here) since restore must
        reuse the exact session id the remote daemon is keyed on.
        """
        try:
            project_payload = self._project_payload(
                project,
                host,
                session_id,
                workspace_path=workspace_path,
            )
            tab_id, tab = self._app.core.create_pack_tab(
                host,
                session_id,
                title,
                conversation_id=conversation_id,
                project_payload=project_payload,
            )
            self._current_answer_index[tab_id] = -1

            self._safe_evaluate_js(
                f"app.tabManager.createTabUI({json.dumps(tab_id)}, {json.dumps(title)}, "
                f"{json.dumps(auto_switch)}, true, "
                f"{json.dumps(project_payload.get('name') if project_payload else None)});",
            )
            self._safe_evaluate_js(
                f"app.tabManager.setConversationId({json.dumps(tab_id)}, {tab.conversation_id});",
            )

            logger.info(
                f"Created pack tab {tab_id} conv={tab.conversation_id} "
                f"host={host} session={session_id} title='{title}' auto_switch={auto_switch}",
            )

            return {
                "success": True,
                "tab_id": tab_id,
                "title": title,
                "conversation_id": tab.conversation_id,
                "host": host,
                "session_id": session_id,
                "project": project_payload.get("name") if project_payload else None,
            }
        except Exception as e:
            logger.error(f"Error creating pack tab: {e}")
            return {"success": False, "error": str(e)}

    def resolve_pack_session_lost(self, tab_id: str, recreate: bool) -> dict[str, Any]:
        """Called after the user answers the "Pack session lost" prompt

        (see core/pack_tab.py's PackTab._apply_resync_result, which fires
        app.onPackSessionLost instead of silently discarding local content
        when the remote daemon reports resumed=False).
        """
        try:
            tab = self._app.core.tabs.get(tab_id)
            if tab is None:
                return {"success": False, "error": "tab_not_found"}
            resolver = getattr(tab, "resolve_session_lost", None)
            if resolver is None:
                return {"success": False, "error": "not_a_pack_tab"}
            resolver(recreate)
            return {"success": True}
        except Exception as e:
            logger.error(f"Error resolving pack session lost for {tab_id}: {e}")
            return {"success": False, "error": str(e)}
