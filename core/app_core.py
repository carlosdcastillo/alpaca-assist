"""Core app logic, UI-agnostic."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import threading
from datetime import datetime
from pathlib import Path
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import TYPE_CHECKING

import pyperclip

from agent_skills import SkillManager
from conversation_graph import ConversationGraph
from core.config import _DEFAULT_SKILLS_DIR
from core.config import DEFAULT_PREFERENCES
from core.config import MCP_SERVERS_FILE
from core.text_parsing import export_and_open
from core.text_parsing import parse_code_blocks
from core.tool_output_gate import sweep_orphaned_output_dirs
from database import ConversationDatabase
from mcp_manager import MCPManager
from utils import is_macos

if TYPE_CHECKING:
    from core.chat_tab import ChatTab
    from core.pack_tab import PackTab
    from webview_api import WebViewAPI

logger = logging.getLogger(__name__)

SESSION_FILE: str = "chat_session.json"
PREFERENCES_FILE: str = "preferences.json"

# Autosave interval in milliseconds (30 seconds)
_AUTOSAVE_INTERVAL_MS = 30_000


class AppCore:
    """UI-agnostic core functionality for the ChatApp."""

    def __init__(self, api: WebViewAPI | None = None) -> None:
        self.api = api  # WebViewAPI reference for UI updates
        sweep_orphaned_output_dirs()
        self.db = ConversationDatabase()
        self.preferences = DEFAULT_PREFERENCES.copy()
        self.load_preferences()
        self.tabs: dict[str, ChatTab | PackTab] = {}
        self.mcp_manager = MCPManager()
        self.event_loop: asyncio.AbstractEventLoop | None = None
        self.mcp_thread: threading.Thread | None = None
        self._loop_ready = threading.Event()
        self._tab_counter = 0
        self.skill_manager = SkillManager()
        self._autosave_timer: threading.Timer | None = None
        self._shutdown_event = threading.Event()
        self.start_mcp_event_loop()
        self.load_mcp_servers()
        self.discover_skills()

    def start_mcp_event_loop(self) -> None:
        """Start the asyncio event loop in a separate thread for MCP operations."""

        def run_event_loop() -> None:
            self.event_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.event_loop)
            self._loop_ready.set()
            self.event_loop.run_forever()

        self.mcp_thread = threading.Thread(target=run_event_loop, daemon=True)
        self.mcp_thread.start()

    def load_mcp_servers(self) -> None:
        """Load and connect to configured MCP servers."""
        try:
            if not os.path.exists(MCP_SERVERS_FILE):
                return
            if not self._loop_ready.wait(timeout=5.0):
                logger.error("MCP event loop did not become ready in time")
                return
            with open(MCP_SERVERS_FILE) as f:
                configs = json.load(f)
            self.mcp_manager.server_configs = configs
            for name, config in configs.items():
                disabled = set(config.get("disabled_tools", []))
                if disabled:
                    self.mcp_manager.disabled_tools[name] = disabled
                if self.event_loop is not None:
                    asyncio.run_coroutine_threadsafe(
                        self.mcp_manager.add_server(
                            name,
                            config["command"],
                            config.get("args", []),
                        ),
                        self.event_loop,
                    )
        except Exception as e:
            logger.error(f"Error loading MCP servers: {e}")

    def save_mcp_config(self) -> None:
        """Write server configs (including disabled_tools) to mcp_servers.json."""
        try:
            configs: dict[str, Any] = {}
            for name, config in self.mcp_manager.server_configs.items():
                entry: dict[str, Any] = {
                    "command": config.get("command", []),
                    "args": config.get("args", []),
                }
                disabled = self.mcp_manager.disabled_tools.get(name, set())
                if disabled:
                    entry["disabled_tools"] = sorted(disabled)
                configs[name] = entry
            with open(MCP_SERVERS_FILE, "w") as f:
                json.dump(configs, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving MCP config: {e}")

    def discover_skills(self) -> None:
        """Run skill discovery and update the UI status bar."""
        skills_cfg = self.preferences.get("agent_skills", {})
        enabled = bool(skills_cfg.get("enabled", True))
        extra_dirs = list(skills_cfg.get("directories", []))
        directories = [_DEFAULT_SKILLS_DIR] + extra_dirs
        warnings = self.skill_manager.discover(directories, enabled=enabled)
        n = len(self.skill_manager.skills)
        if enabled:
            logger.info("Skill discovery complete: %d skill(s) found", n)
        for w in warnings:
            logger.warning("Skill discovery warning: %s", w)

    def get_skills_xml(self) -> str:
        """Return the <available_skills> XML block for system prompt injection."""
        return self.skill_manager.to_prompt_xml()

    def reload_mcp_servers(self) -> None:
        """Tear down all MCP connections and reload config from disk."""
        if not self.event_loop:
            return
        try:
            future = asyncio.run_coroutine_threadsafe(
                self.mcp_manager.shutdown(),
                self.event_loop,
            )
            future.result(timeout=10.0)
        except Exception as e:
            logger.warning(f"Error during MCP shutdown for reload: {e}")
        self.mcp_manager.servers.clear()
        self.mcp_manager.available_tools.clear()
        self.mcp_manager.server_configs.clear()
        self.mcp_manager.disabled_tools.clear()
        self.mcp_manager._server_locks.clear()
        self.load_mcp_servers()

    def get_available_mcp_tools(self) -> list[dict[str, Any]]:
        """Get all available MCP tools in Ollama-compatible format."""
        import internal_tools

        available_tools: list[dict[str, Any]] = list(internal_tools.TOOL_SCHEMAS)
        mcp_tools = self.mcp_manager.get_available_tools()
        logger.debug(f"Debug: MCP Manager has {len(mcp_tools)} servers")
        logger.debug(f"Debug: Server names: {list(mcp_tools.keys())}")
        for server_name, tools in mcp_tools.items():
            for tool in tools:
                available_tools.append(
                    {
                        "type": "function",
                        "function": {
                            "name": f"{server_name}_{tool['name']}",
                            "description": tool.get("description", ""),
                            "parameters": tool.get("inputSchema", {}),
                        },
                    },
                )
        logger.debug(f"Debug: Final tool count: {len(available_tools)}")
        return available_tools

    def check_mcp_status(self) -> None:
        """Debug method to check MCP server status."""
        logger.debug("=== MCP Status Check ===")
        logger.debug(f"MCP Manager exists: {self.mcp_manager is not None}")
        logger.debug(f"Event loop exists: {self.event_loop is not None}")
        logger.debug(
            f"Server configs: {getattr(self.mcp_manager, 'server_configs', {})}",
        )
        if hasattr(self.mcp_manager, "servers"):
            logger.debug(f"Connected servers: {list(self.mcp_manager.servers.keys())}")
        tools = self.get_available_mcp_tools()
        logger.debug(f"Available tools: {len(tools)}")
        logger.debug("========================")

    def call_mcp_tool(
        self,
        server_name: str,
        tool_name: str,
        arguments: dict[str, Any],
        callback: Any | None = None,
    ) -> None:
        """Call an MCP tool and optionally execute a callback with the result."""
        if not self.event_loop:
            logger.error(
                f"[MCP] Cannot call tool '{tool_name}': event loop not running "
                f"— MCP server '{server_name}' may have failed to connect.",
            )
            if callback:
                callback({"error": f"MCP server '{server_name}' is not connected."})
            return

        async def execute_and_callback() -> None:
            result = await self.mcp_manager.call_tool(server_name, tool_name, arguments)
            if callback:
                callback(result)

        asyncio.run_coroutine_threadsafe(execute_and_callback(), self.event_loop)

    def load_preferences(self) -> None:
        """Load preferences from file."""
        if os.path.exists(PREFERENCES_FILE):
            try:
                with open(PREFERENCES_FILE) as f:
                    saved_prefs = json.load(f)
                    self.preferences.update(saved_prefs)
            except Exception as e:
                logger.error(f"Error loading preferences: {e}")

    def save_preferences(self) -> None:
        """Save preferences to file."""
        try:
            with open(PREFERENCES_FILE, "w") as f:
                json.dump(self.preferences, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving preferences: {e}")

    def save_session(self) -> None:
        """Save all tabs and their contents to disk.

        Uses an atomic write (serialize → .tmp → rename) so a crash or
        kill signal during the write never corrupts the previous good file.
        The previous good file is also preserved as SESSION_FILE + ".bak"
        so there is always one recovery option.
        """
        current_tab_id = self.get_active_tab_id()
        session_data: dict[str, Any] = {
            "tabs": [],
            "selected_tab_id": current_tab_id,
            "version": "2.0",
        }
        # Take a snapshot to avoid race condition with concurrent tab mutations
        tabs_snapshot = dict(self.tabs)
        for tab_id, tab in tabs_snapshot.items():
            tab_data: dict[str, Any] = {
                "tab_id": tab_id,
                **tab.get_serializable_data(),
            }
            logger.debug(
                f"Saving tab {tab_id}: title='{tab.title}', data keys={list(tab_data.keys())}",
            )
            session_data["tabs"].append(tab_data)
        tmp_file = SESSION_FILE + ".tmp"
        bak_file = SESSION_FILE + ".bak"
        try:
            # Write fully to a temp file first — the live file is never touched
            # until the new content is completely on disk.
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(session_data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            # Rotate: move the current good file to .bak, then atomically
            # promote the new .tmp to the live file.
            if os.path.exists(SESSION_FILE):
                os.replace(SESSION_FILE, bak_file)
            os.replace(tmp_file, SESSION_FILE)
            logger.info(
                f"Session saved successfully with {len(session_data['tabs'])} tabs",
            )
        except Exception as e:
            logger.error(f"Error saving session: {e}")

    def load_session(self) -> tuple[list[dict[str, Any]], str | None]:
        """Load saved tabs and their contents from disk.

        Tries the main session file first; falls back to the .bak copy if
        the main file is missing or corrupt.

        Returns tuple of (tabs_data, selected_tab_id) for the UI to restore.
        """
        for path, label in [(SESSION_FILE, "main"), (SESSION_FILE + ".bak", "backup")]:
            if not os.path.exists(path):
                continue
            try:
                with open(path, encoding="utf-8") as f:
                    session_data: dict[str, Any] = json.load(f)
                tabs = session_data.get("tabs", [])
                selected_id = session_data.get("selected_tab_id")
                if label == "backup":
                    logger.warning(
                        "Loaded session from backup file (main file was missing or corrupt)",
                    )
                return tabs, selected_id
            except Exception as e:
                logger.error(f"Error loading session from {label} file ({path}): {e}")
        return [], None

    def get_active_tab_id(self) -> str | None:
        """Get the currently active tab ID."""
        # To be set by UI layer
        return getattr(self, "_active_tab_id", None)

    def set_active_tab_id(self, tab_id: str | None) -> None:
        """Set the currently active tab ID."""
        self._active_tab_id = tab_id

    def _alloc_tab_id(self) -> str:
        """Allocate the next unique tab ID."""
        self._tab_counter += 1
        import uuid

        return f"tab-{self._tab_counter}-{uuid.uuid4().hex[:8]}"

    def create_tab(
        self,
        title: str | None = None,
        conversation_id: int | None = None,
    ) -> tuple[str, ChatTab]:
        """Create a new tab.

        Returns (tab_id, tab_instance).
        conversation_id is pre-allocated from the sequences table when not supplied
        (e.g. revive reuses the existing DB id).
        """
        from core.chat_tab import ChatTab

        tab_id = self._alloc_tab_id()
        if title is None:
            title = f"Chat {len(self.tabs) + 1}"
        if conversation_id is None:
            conversation_id = self.db.allocate_conversation_id()

        tab = ChatTab(tab_id, title, self, conversation_id)
        self.tabs[tab_id] = tab
        return tab_id, tab

    def create_pack_tab(
        self,
        host: str,
        session_id: str,
        title: str | None = None,
        model: str | None = None,
        conversation_id: int | None = None,
    ) -> tuple[str, PackTab]:
        """Create a new Pack tab — a tab whose backend runs on a remote

        host over SSH, reached via core/pack_transport.py.

        Sibling to create_tab, same allocation pattern. Returns
        immediately; the actual SSH connect + attach happens on a
        background thread (see PackTab.connect_async), so the tab button
        appears right away exactly like a local tab.
        """
        from core.pack_tab import PackTab

        tab_id = self._alloc_tab_id()
        if title is None:
            title = "Pack Tab"
        if conversation_id is None:
            conversation_id = self.db.allocate_conversation_id()

        tab = PackTab(tab_id, title, self, conversation_id, host, session_id)
        self.tabs[tab_id] = tab
        tab.connect_async(model=model)
        return tab_id, tab

    def delete_tab(self, tab_id: str) -> None:
        """Delete a tab and store in database if it has content.

        Pack tabs are stored the same as ordinary tabs — their
        get_serializable_data() includes tab_type/host/session_id, and
        webview_api.revive_conversation uses that to reconnect to the
        still-running remote daemon instead of creating a plain local
        ChatTab. Closing a Pack tab only detaches locally; the remote
        daemon keeps running either way.
        """
        if tab_id not in self.tabs:
            return
        tab = self.tabs[tab_id]
        self.store_tab_in_database(tab)
        tab.cleanup_resources()
        del self.tabs[tab_id]

    def store_tab_in_database(self, tab: ChatTab) -> None:
        """Store a tab's conversation in the database before closing it."""
        tab_data = tab.get_serializable_data()
        chat_state = tab_data.get("chat_state", {})
        has_content = bool(
            chat_state.get("graph", {}).get("nodes")
            or (chat_state.get("questions") and chat_state.get("answers")),
        )
        if not has_content:
            return

        tab_title = tab.title
        if "created_date" not in tab_data:
            tab_data["created_date"] = datetime.now().isoformat()
        try:
            conv_id = self.db.store_conversation(
                tab.conversation_id,
                tab_title,
                tab_data,
            )
            logger.info(f"Stored conversation '{tab_title}' with ID {conv_id}")
        except Exception as e:
            logger.error(f"Error storing conversation: {e}")

    def fetch_models_from_api(self) -> list[str]:
        """Fetch available models from the API."""
        import requests

        api_url = self.preferences.get("api_url", "http://localhost:11434")
        # Normalize URL - remove trailing /api/chat if present
        if api_url.endswith("/api/chat"):
            api_url = api_url[:-9]  # Remove "/api/chat"
        elif api_url.endswith("/api/chat/"):
            api_url = api_url[:-10]  # Remove "/api/chat/"

        response = requests.get(f"{api_url}/api/tags", timeout=10)
        response.raise_for_status()
        data = response.json()
        models = [m["name"] for m in data.get("models", [])]
        return sorted(models)

    def export_tab_to_html(self, tab: ChatTab) -> None:
        """Export a tab's conversation to HTML."""
        try:
            content, title = self._get_export_content_and_title(tab)
            export_and_open(content, title)
        except Exception as e:
            logger.error(f"Error exporting to HTML: {e}")

    def copy_to_clipboard(self, text: str) -> bool:
        """Copy text to the system clipboard.

        Backs the code-block Copy button. Goes through GTK directly rather
        than the browser's navigator.clipboard API — WebKit2GTK's clipboard
        permission handling isn't implemented by pywebview, so the browser
        API silently rejects there. GTK talks to the display server (X11 or
        XWayland) directly and needs no external clipboard binary (xclip,
        wl-copy, ...). Falls back to pyperclip for non-GTK platforms.
        """
        try:
            import gi

            gi.require_version("Gtk", "3.0")
            from gi.repository import Gdk
            from gi.repository import Gtk

            clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
            clipboard.set_text(text, -1)
            clipboard.store()
            return True
        except Exception as e:
            logger.warning(f"GTK clipboard copy failed, trying pyperclip: {e}")
            try:
                pyperclip.copy(text)
                return True
            except Exception as e2:
                logger.error(f"Clipboard copy failed: {e2}")
                return False

    @staticmethod
    def _mime_from_b64(b64: str) -> str:
        """Detect image MIME type from leading base64 characters (magic bytes)."""
        import base64 as _base64

        try:
            raw = _base64.b64decode(b64[:16] + "==")
        except Exception:
            return "image/jpeg"
        if raw[:3] == b"\xff\xd8\xff":
            return "image/jpeg"
        if raw[:8] == b"\x89PNG\r\n\x1a\n":
            return "image/png"
        if raw[:6] in (b"GIF87a", b"GIF89a"):
            return "image/gif"
        if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
            return "image/webp"
        return "image/jpeg"

    def _get_export_content_and_title(self, tab: ChatTab) -> tuple[str, str]:
        """Return (markdown_text, title) for the tab's conversation.

        export_and_open() expects markdown — it runs it through a markdown
        processor to produce the final HTML.  Do NOT return HTML here.
        """
        from conversation_graph import ConversationGraph
        from chat_state import ChatState

        chat_state = tab.chat_state
        title = tab.title
        # Note: The title is added by export_to_html() in the HTML template,
        # so we don't include it in the markdown content to avoid duplication.
        parts: list[str] = []

        if isinstance(chat_state, ConversationGraph):
            questions = chat_state.questions
            answers = chat_state.answers
        elif isinstance(chat_state, ChatState):
            questions = chat_state.questions
            answers = chat_state.answers
        else:
            questions = []
            answers = []

        all_images: list[list[str]] = list(
            getattr(chat_state, "question_images", []) or [],
        )

        for i, question in enumerate(questions):
            parts.append(f"## You\n\n{question}\n")
            imgs = all_images[i] if i < len(all_images) else []
            if imgs:
                img_tags = "".join(
                    f'<img src="data:{self._mime_from_b64(b64)};base64,{b64}"'
                    f' style="max-width:100%;max-height:525px;object-fit:contain;'
                    f'border-radius:4px;display:block;margin:8px auto;">'
                    for b64 in imgs
                )
                parts.append(f'<div style="text-align:center">{img_tags}</div>\n')
            if i < len(answers):
                answer = answers[i]
                if hasattr(answer, "get_text_only_content"):
                    answer_text = answer.get_text_only_content()
                elif isinstance(answer, str):
                    answer_text = answer
                else:
                    answer_text = str(answer)
                parts.append(f"## Assistant\n\n{answer_text}\n")

        return "\n---\n\n".join(parts), title

    def start_autosave(self) -> None:
        """Start the periodic autosave timer.

        Saves session every 30 seconds to minimize data loss.
        Also performs an immediate save on startup.
        """
        self._shutdown_event.clear()
        logger.info("Starting autosave (interval: %d ms)", _AUTOSAVE_INTERVAL_MS)
        logger.info(f"Number of tabs: {len(self.tabs)}")

        # Save immediately on startup
        logger.info("Performing immediate save on startup...")
        self.save_session()

        # Then schedule periodic saves
        self._schedule_next_autosave()
        logger.info("Autosave timer scheduled")

    def _schedule_next_autosave(self) -> None:
        """Schedule the next autosave iteration."""
        if self._shutdown_event.is_set():
            logger.debug("Autosave: shutdown event set, not scheduling")
            return

        def _periodic_save() -> None:
            if self._shutdown_event.is_set():
                logger.debug("Autosave: shutdown event set, skipping save")
                return
            try:
                logger.debug("Autosave: saving session...")
                self.save_session()
                logger.debug("Autosave: session saved successfully")
            except Exception as e:
                logger.error(f"Error during autosave: {e}")
            finally:
                # Schedule next save only if not shutting down
                if not self._shutdown_event.is_set():
                    self._schedule_next_autosave()
                else:
                    logger.debug("Autosave: not rescheduling due to shutdown")

        # Use threading.Timer for background execution
        interval_sec = _AUTOSAVE_INTERVAL_MS / 1000.0
        logger.debug(f"Autosave: scheduling next save in {interval_sec}s")
        self._autosave_timer = threading.Timer(interval_sec, _periodic_save)
        self._autosave_timer.daemon = True
        self._autosave_timer.start()

    def stop_autosave(self) -> None:
        """Stop the autosave timer."""
        self._shutdown_event.set()
        if self._autosave_timer is not None:
            self._autosave_timer.cancel()
            self._autosave_timer = None
        logger.info("Autosave stopped")

    def shutdown(self) -> None:
        """Clean shutdown of the application."""
        self.stop_autosave()
        self.save_preferences()
        logger.info("Saving session on exit...")
        self.save_session()
        if self.event_loop and self.mcp_manager:
            asyncio.run_coroutine_threadsafe(
                self.mcp_manager.shutdown(),
                self.event_loop,
            )
            self.event_loop.call_soon_threadsafe(self.event_loop.stop)
            if self.mcp_thread is not None:
                self.mcp_thread.join(timeout=2)
