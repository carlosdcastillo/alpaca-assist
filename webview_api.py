"""PyWebView API bridge for bidirectional Python/JS communication."""
from __future__ import annotations

import json
import logging
import queue
import threading
from typing import Any
from typing import Optional
from typing import TYPE_CHECKING

from utils import ContentUpdate

if TYPE_CHECKING:
    from webview_app import ChatApp

logger = logging.getLogger(__name__)


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
            result: dict[str, Any] = {
                "success": True,
                "answer_index": answer_index,
            }

            # Return graph node info so JS can attach QA bars immediately
            from conversation_graph import ConversationGraph

            if isinstance(tab.chat_state, ConversationGraph):
                try:
                    pairs = tab.chat_state._get_active_pairs()
                    if 0 <= answer_index < len(pairs):
                        user_node, _ = pairs[answer_index]
                        user_siblings = tab.chat_state.get_siblings(user_node.id)
                        result["user_node_id"] = user_node.id
                        result["user_sibling_count"] = len(user_siblings)
                        result["user_position"] = (
                            user_siblings.index(user_node.id)
                            if user_node.id in user_siblings
                            else 0
                        )
                except Exception:
                    pass

            return result
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
            }
        except Exception as e:
            logger.error(f"Error getting status info: {e}")
            return {"success": False, "error": str(e)}

    def get_history(self, search_term: str = "") -> dict[str, Any]:
        """Return closed conversation history from the database."""
        try:
            if search_term:
                rows = self._app.core.db.search_conversations(search_term)
            else:
                rows = self._app.core.db.get_conversations()
            conversations = [
                {
                    "id": row[0],
                    "title": row[1],
                    "created_date": row[2],
                    "closed_date": row[3],
                }
                for row in rows
            ]
            return {"success": True, "conversations": conversations}
        except Exception as e:
            logger.error(f"Error getting history: {e}")
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
            if new_tab:
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

    def navigate_qa(self, tab_id: str, direction: str) -> dict[str, Any]:
        """Navigate to next/previous Q/A pair."""
        try:
            tab = self._app.core.tabs.get(tab_id)
            if not tab:
                return {"success": False, "error": "Tab not found"}

            if direction == "next":
                tab.go_to_next_qa()
            else:
                tab.go_to_previous_qa()

            return {"success": True}
        except Exception as e:
            logger.error(f"Error navigating Q/A: {e}")
            return {"success": False, "error": str(e)}

    def fork_conversation(
        self,
        tab_id: str,
        node_id: str,
        new_question: str = "",
    ) -> dict[str, Any]:
        """Fork conversation from a node with a new question.

        The node_id can be either a user or assistant node.  When the fork
        button is on an answer bar, node_id is the assistant node; we
        resolve it to its parent user node so fork_from_node() works
        correctly (it expects a user node ID).
        """
        try:
            tab = self._app.core.tabs.get(tab_id)
            if not tab:
                return {"success": False, "error": "Tab not found"}

            # If node_id is an assistant node, resolve to its parent user node
            from conversation_graph import ConversationGraph

            if isinstance(tab.chat_state, ConversationGraph):
                node = tab.chat_state.nodes.get(node_id)
                if node and node.role == "assistant":
                    # Assistant node's parent is the user node
                    node_id = node.parent_id or node_id

            result = tab.fork_from_node(node_id, new_question)
            if result.get("success"):
                answer_index = result["answer_index"]
                with self._lock:
                    self._current_answer_index[tab_id] = answer_index
                self._safe_evaluate_js(
                    f"app.onGraphBranchCreated({json.dumps(tab_id)}, {answer_index});",
                )
            return result
        except Exception as e:
            logger.error(f"Error forking conversation: {e}")
            return {"success": False, "error": str(e)}

    def regenerate_answer(self, tab_id: str, node_id: str) -> dict[str, Any]:
        """Regenerate the answer for the pair containing node_id.

        node_id can be either a user or assistant node.  When the regenerate
        button is on an answer bar, node_id is the assistant node; the
        ChatTab layer already handles both via _find_pair_index_for_node.
        """
        try:
            tab = self._app.core.tabs.get(tab_id)
            if not tab:
                return {"success": False, "error": "Tab not found"}
            result = tab.regenerate_answer(node_id)
            if result.get("success"):
                answer_index = result["answer_index"]
                with self._lock:
                    self._current_answer_index[tab_id] = answer_index
                self._safe_evaluate_js(
                    f"app.onGraphBranchCreated({json.dumps(tab_id)}, {answer_index});",
                )
            return result
        except Exception as e:
            logger.error(f"Error regenerating answer: {e}")
            return {"success": False, "error": str(e)}

    def edit_question(self, tab_id: str, node_id: str, new_text: str) -> dict[str, Any]:
        """Edit a question and stream a new answer.

        node_id should be a user (question) node since the edit button
        is on the question bar.
        """
        try:
            tab = self._app.core.tabs.get(tab_id)
            if not tab:
                return {"success": False, "error": "Tab not found"}
            result = tab.edit_question(node_id, new_text)
            if result.get("success"):
                answer_index = result["answer_index"]
                with self._lock:
                    self._current_answer_index[tab_id] = answer_index
                self._safe_evaluate_js(
                    f"app.onGraphBranchCreated({json.dumps(tab_id)}, {answer_index});",
                )
            return result
        except Exception as e:
            logger.error(f"Error editing question: {e}")
            return {"success": False, "error": str(e)}

    def navigate_sibling(
        self,
        tab_id: str,
        node_id: str,
        direction: str,
    ) -> dict[str, Any]:
        """Navigate to the previous or next sibling of node_id."""
        try:
            from conversation_graph import ConversationGraph

            tab = self._app.core.tabs.get(tab_id)
            if not tab:
                return {"success": False, "error": "Tab not found"}
            if not isinstance(tab.chat_state, ConversationGraph):
                return {"success": False, "error": "Not a graph conversation"}
            if direction == "prev":
                new_id = tab.chat_state.navigate_to_prev_sibling(node_id)
            else:
                new_id = tab.chat_state.navigate_to_next_sibling(node_id)
            if new_id is None:
                return {"success": False, "error": "No sibling in that direction"}
            return {"success": True, "new_active_node_id": new_id}
        except Exception as e:
            logger.error(f"Error navigating sibling: {e}")
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
        # Push QA bar info for graph conversations so the answer bar appears after streaming
        try:
            from conversation_graph import ConversationGraph

            tab = self._app.core.tabs.get(tab_id)
            if tab and isinstance(tab.chat_state, ConversationGraph):
                pairs = tab.chat_state._get_active_pairs()
                if 0 <= answer_index < len(pairs):
                    user_node, asst_node = pairs[answer_index]
                    if asst_node:
                        asst_siblings = tab.chat_state.get_siblings(asst_node.id)
                        asst_pos = (
                            asst_siblings.index(asst_node.id)
                            if asst_node.id in asst_siblings
                            else 0
                        )
                        qa_info = {
                            "nodeId": asst_node.id,
                            "siblingCount": len(asst_siblings),
                            "position": asst_pos,
                        }
                        self._safe_evaluate_js(
                            f"app.chatDisplay.finalizeAnswerBar"
                            f"({answer_index}, {json.dumps(qa_info)});",
                        )
        except Exception as e:
            logger.warning(f"Could not push answer QA bar: {e}")

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
