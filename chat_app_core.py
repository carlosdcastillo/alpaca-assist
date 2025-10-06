import asyncio
import json
import os
import platform
import threading
import tkinter as tk
from datetime import datetime
from tkinter import filedialog
from tkinter import messagebox
from tkinter import ttk
from typing import Any
from typing import cast
from typing import Dict
from typing import List
from typing import Optional

import pyperclip

from chat_tab import ChatTab
from conversation_history import ConversationHistoryWindow
from database import ConversationDatabase
from find_dialog import FindDialog
from mcp_config import MCPConfigWindow
from mcp_manager import MCPManager
from preferences import DEFAULT_PREFERENCES
from preferences import PreferencesWindow
from syntax_text import SyntaxHighlightedText
from text_utils import export_and_open
from text_utils import parse_code_blocks
from tooltip import ToolTip
from utils import is_macos


class ChatAppCore:
    """Core functionality for the ChatApp including initialization, preferences, and session management."""

    def __init__(self, master: tk.Tk) -> None:
        self.master = master
        master.title("Alpaca Assist")
        self.db = ConversationDatabase()
        self.preferences = DEFAULT_PREFERENCES.copy()
        self.load_preferences()
        master.geometry(str(self.preferences["window_geometry"]))
        self.style = ttk.Style()
        self.style.configure("Custom.TButton", padding=(10, 10), width=15)
        self.style.configure("TNotebook.Tab", padding=(4, 4))
        self.file_completions: list[str] = []
        self.last_focused_widget: SyntaxHighlightedText | None = None
        self.tabs: list[ChatTab] = []
        self.load_file_completions()
        self.mcp_manager = MCPManager()
        self.event_loop = None
        self.mcp_thread = None
        self.start_mcp_event_loop()
        self.load_mcp_servers()

    def start_mcp_event_loop(self):
        """Start the asyncio event loop in a separate thread for MCP operations."""

        def run_event_loop():
            self.event_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.event_loop)
            self.event_loop.run_forever()

        self.mcp_thread = threading.Thread(target=run_event_loop, daemon=True)
        self.mcp_thread.start()

    def load_mcp_servers(self):
        """Load and connect to configured MCP servers."""
        try:
            if os.path.exists("mcp_servers.json"):
                with open("mcp_servers.json") as f:
                    configs = json.load(f)
                    self.mcp_manager.server_configs = configs
                    for name, config in configs.items():
                        if self.event_loop:
                            asyncio.run_coroutine_threadsafe(
                                self.mcp_manager.add_server(
                                    name,
                                    config["command"],
                                    config.get("args", []),
                                ),
                                self.event_loop,
                            )
        except Exception as e:
            print(f"Error loading MCP servers: {e}")

    def get_available_mcp_tools(self) -> list[dict[str, Any]]:
        """Get all available MCP tools in Ollama-compatible format."""
        if not self.mcp_manager.get_available_tools():
            print("No MCP servers connected, waiting briefly...")
            import time

            time.sleep(1.0)
            if not self.mcp_manager.get_available_tools():
                print("Attempting to reconnect MCP servers...")
                self.load_mcp_servers()
                time.sleep(1.0)
        available_tools = []
        mcp_tools = self.mcp_manager.get_available_tools()
        print(f"Debug: MCP Manager has {len(mcp_tools)} servers")
        print(f"Debug: Server names: {list(mcp_tools.keys())}")
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
        print(f"Debug: Final tool count: {len(available_tools)}")
        return available_tools

    def check_mcp_status(self):
        """Debug method to check MCP server status."""
        print("=== MCP Status Check ===")
        print(f"MCP Manager exists: {self.mcp_manager is not None}")
        print(f"Event loop exists: {self.event_loop is not None}")
        print(f"Server configs: {getattr(self.mcp_manager, 'server_configs', {})}")
        if hasattr(self.mcp_manager, "servers"):
            print(f"Connected servers: {list(self.mcp_manager.servers.keys())}")
        tools = self.get_available_mcp_tools()
        print(f"Available tools: {len(tools)}")
        print("========================")

    def call_mcp_tool(
        self,
        server_name: str,
        tool_name: str,
        arguments: dict[str, Any],
        callback=None,
    ):
        """Call an MCP tool and optionally execute a callback with the result."""
        if not self.event_loop:
            return

        async def execute_and_callback():
            result = await self.mcp_manager.call_tool(server_name, tool_name, arguments)
            if callback:
                self.master.after(0, lambda: callback(result))

        asyncio.run_coroutine_threadsafe(execute_and_callback(), self.event_loop)

    def on_closing(self) -> None:
        """Handle application closing by saving session and quitting."""
        self.preferences["window_geometry"] = self.master.geometry()
        self.save_preferences()
        if self.preferences["auto_save"]:
            self.save_session()
        if self.event_loop and self.mcp_manager:
            asyncio.run_coroutine_threadsafe(
                self.mcp_manager.shutdown(),
                self.event_loop,
            )
            self.event_loop.call_soon_threadsafe(self.event_loop.stop)
        self.master.destroy()

    def load_preferences(self) -> None:
        """Load preferences from file."""
        if os.path.exists("preferences.json"):
            try:
                with open("preferences.json") as f:
                    saved_prefs = json.load(f)
                    self.preferences.update(saved_prefs)
            except Exception as e:
                print(f"Error loading preferences: {e}")

    def save_preferences(self) -> None:
        """Save preferences to file."""
        try:
            with open("preferences.json", "w") as f:
                json.dump(self.preferences, f, indent=2)
        except Exception as e:
            print(f"Error saving preferences: {e}")

    def apply_preferences(self) -> None:
        """Apply all preferences to the application."""
        self.apply_appearance_preferences(self.preferences)

    def apply_appearance_preferences(self, prefs: dict[str, Any]) -> None:
        """Apply appearance-related preferences."""
        for i, tab in enumerate(self.tabs):
            tab.chat_display.update_font(prefs["font_family"], prefs["font_size"])
            tab.input_field.update_font(prefs["font_family"], prefs["font_size"])
            tab.chat_display.update_background_color(prefs["background_color"])
            tab.input_field.update_background_color(prefs["background_color"])
            tab.chat_display.update_theme(prefs["theme"])
            tab.input_field.update_theme(prefs["theme"])
            tab.chat_display.config(maxundo=prefs["max_undo_levels"])
            tab.input_field.config(maxundo=prefs["max_undo_levels"])

    def save_session(self) -> None:
        """Save all tabs and their contents to disk."""
        current_tab_index = (
            self.notebook.index(self.notebook.select()) if self.tabs else 0
        )
        session_data: dict[str, Any] = {
            "tabs": [],
            "window": {"geometry": self.master.geometry()},
            "selected_tab_index": current_tab_index,
            "version": "1.1",
        }
        for tab in self.tabs:
            tab_data: dict[str, Any] = {
                "name": self.notebook.tab(self.tabs.index(tab), "text"),
                **tab.get_serializable_data(),
            }
            session_data["tabs"].append(tab_data)
        try:
            with open("chat_session.json", "w") as f:
                json.dump(session_data, f, indent=2)
            print("Session saved successfully")
        except Exception as e:
            print(f"Error saving session: {e}")

    def load_session(self) -> None:
        """Load saved tabs and their contents from disk."""
        if not os.path.exists("chat_session.json"):
            self.create_tab()
            return
        try:
            with open("chat_session.json") as f:
                session_data: dict[str, Any] = json.load(f)
            if "window" in session_data and "geometry" in session_data["window"]:
                self.master.geometry(
                    cast(dict[str, str], session_data["window"])["geometry"],
                )
            if self.tabs:
                for tab in self.tabs:
                    self.notebook.forget(tab.frame)
                self.tabs = []
            for tab_data in cast(list[dict[str, Any]], session_data.get("tabs", [])):
                new_tab: ChatTab = ChatTab(self, self.notebook, self.file_completions)
                new_tab.load_from_data(tab_data)
                self.tabs.append(new_tab)
                new_tab.rebuild_display_from_state()
                tab_name: str = tab_data.get("name", f"Tab {len(self.tabs)}")
                self.notebook.tab(self.tabs.index(new_tab), text=tab_name)
            if not self.tabs:
                self.create_tab()
            selected_tab_index = session_data.get("selected_tab_index", 0)
            if 0 <= selected_tab_index < len(self.tabs):
                self.notebook.select(selected_tab_index)
                tab_name = self.notebook.tab(selected_tab_index, "text")
                self.master.title(f"Alpaca Assist - {tab_name}")
            print("Session loaded successfully")
        except Exception as e:
            print(f"Error loading session: {e}")
            self.create_tab()

    def save_file_completions(self) -> None:
        with open("file_completions.json", "w") as f:
            json.dump(self.file_completions, f, indent=2)

    def load_file_completions(self) -> None:
        if os.path.exists("file_completions.json"):
            with open("file_completions.json") as f:
                self.file_completions = json.load(f)

    def update_tabs_file_completions(self) -> None:
        for tab in self.tabs:
            tab.update_file_completions(self.file_completions)
        self.save_file_completions()

    def handle_ctrl_return(self, tab) -> str:
        """Handle Ctrl+Return event for a specific tab."""
        tab.submit_message()
        return "break"

    def store_tab_in_database(self, tab: ChatTab) -> None:
        """Store a tab's conversation in the database before closing it."""
        tab_data = tab.get_serializable_data()
        if not tab_data.get("chat_state", {}).get("questions") or not tab_data.get(
            "chat_state",
            {},
        ).get("answers"):
            return
        tab_index = self.tabs.index(tab)
        tab_title = self.notebook.tab(tab_index, "text")
        if "created_date" not in tab_data:
            tab_data["created_date"] = datetime.now().isoformat()
        try:
            conv_id = self.db.store_conversation(tab_title, tab_data)
            if tab_data.get("original_conversation_id"):
                print(f"Updated conversation '{tab_title}' with ID {conv_id}")
            else:
                print(f"Stored new conversation '{tab_title}' with ID {conv_id}")
        except Exception as e:
            print(f"Error storing conversation: {e}")
            messagebox.showerror("Database Error", f"Failed to store conversation: {e}")

    def update_tab_name(self, tab: ChatTab, summary: str) -> None:
        tab_index = self.tabs.index(tab)
        self.notebook.tab(tab_index, text=summary)
        current_tab_index = self.notebook.index(self.notebook.select())
        if tab_index == current_tab_index:
            self.master.title(f"Alpaca Assist - {summary}")

    def update_last_focused(self, event: tk.Event) -> None:
        self.last_focused_widget = cast(SyntaxHighlightedText, event.widget)
        for tab in self.tabs:
            if event.widget == tab.chat_display:
                self.master.after_idle(tab.update_status_bar)
                break

    def create_tab(self, tab_name: str | None = None) -> None:
        """Create a new tab."""
        tab = ChatTab(self, self.notebook, self.file_completions, self.preferences)
        self.tabs.append(tab)
        if tab_name is None:
            tab_name = f"Chat {len(self.tabs)}"
        self.notebook.add(tab.frame, text=tab_name)
        self.notebook.select(len(self.tabs) - 1)
        self.master.title(f"Alpaca Assist - {tab_name}")
        if hasattr(self, "bind_tab_shortcuts"):
            modifier = "Command" if is_macos() else "Control"
            self.bind_tab_shortcuts(tab, modifier)

    def delete_tab(self) -> None:
        """Delete tab and automatically store in database if it has content."""
        if len(self.tabs) <= 1:
            return
        current_tab = self.notebook.select()
        tab_index = self.notebook.index(current_tab)
        tab = self.tabs[tab_index]
        tab_data = tab.get_serializable_data()
        has_questions = tab_data.get("chat_state", {}).get("questions") and any(
            q.strip() for q in tab_data["chat_state"]["questions"]
        )
        has_answers = tab_data.get("chat_state", {}).get("answers") and any(
            a.strip() for a in tab_data["chat_state"]["answers"]
        )
        has_unsaved_input = False
        if hasattr(tab, "input_field"):
            input_text = tab.input_field.get("1.0", tk.END).strip()
            has_unsaved_input = bool(input_text)
        has_content = has_questions or has_answers or has_unsaved_input
        if has_content:
            self.store_tab_in_database(tab)
        tab.cleanup_resources()
        self.notebook.forget(current_tab)
        del self.tabs[tab_index]
