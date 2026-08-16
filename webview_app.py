"""Main PyWebView application window."""

import logging
import os
import sys
import urllib.parse
from pathlib import Path
from typing import Any
from typing import Optional

import webview

from core.app_core import AppCore
from logging_config import configure_logging
from webview_api import WebViewAPI

logger = logging.getLogger(__name__)


def get_web_dir() -> Path:
    """Get path to web directory, works in dev and PyInstaller modes."""
    if getattr(sys, "frozen", False):
        # PyInstaller: files are in sys._MEIPASS
        base_path = Path(sys._MEIPASS)  # type: ignore[attr-defined]
    else:
        # Development: files are relative to this file
        base_path = Path(__file__).parent

    return base_path / "web"


class ChatApp:
    """PyWebView-based Chat Application."""

    # Use __slots__ to prevent pywebview introspection issues
    __slots__ = ("core", "api", "window", "_active_tab_id", "_web_dir")

    def __init__(self) -> None:
        configure_logging(
            level=logging.DEBUG if os.getenv("DEBUG") else logging.WARNING,
        )

        # Create API bridge first (AppCore needs it)
        self.api = WebViewAPI(self)

        # Initialize core business logic with API reference
        self.core = AppCore(api=self.api)

        # Set up window
        self._setup_window()

        # Track active tab
        self._active_tab_id: str | None = None

    def _setup_window(self) -> None:
        """Create the PyWebView window."""
        self._web_dir = get_web_dir()
        index_html = self._web_dir / "index.html"

        # Ensure web directory exists
        if not self._web_dir.exists():
            logger.error(f"Web directory not found: {self._web_dir}")
            raise RuntimeError(f"Web directory not found: {self._web_dir}")

        # Pass the saved theme via the URL query string rather than writing
        # it to a generated file — index.html reads it before first paint to
        # avoid a dark/light flash, and this works the same whether the app
        # directory is writable or not (e.g. a read-only PyInstaller bundle).
        theme = self.core.preferences.get("theme", "dark")
        index_url = f"{index_html}?theme={urllib.parse.quote(theme)}"

        self.window = webview.create_window(
            "Alpaca Assist",
            index_url,
            js_api=self.api,
            width=1400,
            height=900,
            min_size=(800, 600),
            text_select=True,
        )
        self.api.set_window(self.window)

    def _get_icon_path(self) -> Path | None:
        """Get the path to the application icon."""
        if getattr(sys, "frozen", False):
            base_path = Path(sys._MEIPASS)  # type: ignore[attr-defined]
        else:
            base_path = Path(__file__).parent

        icon_path = base_path / "assets" / "alpaca-assist.png"
        return icon_path if icon_path.exists() else None

    def run(self) -> None:
        """Start the application."""
        # Set the active tab getter on core
        self.core.get_active_tab_id = lambda: self._active_tab_id  # type: ignore[method-assign]

        # Start webview — blocks until the window is closed
        if self.window is None:
            raise RuntimeError("Window not initialized")
        icon_path = self._get_icon_path()
        webview.start(
            self._on_start,
            self.window,  # type: ignore[arg-type]
            debug=os.getenv("DEBUG") is not None,
            icon=str(icon_path) if icon_path else None,
            # Required for the live-surface panel's noVNC bundle: Chromium
            # treats a file:// page as an opaque origin and blocks ES module
            # loads (static or dynamic import()) on CORS grounds. pywebview
            # serves local files through an internal HTTP server instead
            # when this is set -- no other change needed, since a local
            # window URL is already routed through that server once it's
            # running (see webview/__init__.py's has_local_urls check).
            http_server=True,
        )

        # Window has been closed. Run final save + MCP cleanup now.
        # This is the guaranteed shutdown path regardless of how the user closed.
        logger.info("Window closed — running final shutdown save")
        self.core.shutdown()

    def _on_start(self, window: Any) -> None:
        """Called when webview is ready."""
        logger.info("WebView started successfully")
        logger.info(f"Working directory: {os.getcwd()}")
        logger.info(f"Session file path: {os.path.abspath('chat_session.json')}")

        # Safety-net save on OS close (× button / Alt+F4).  The Exit menu
        # action calls save_and_close() which handles its own feedback; this
        # covers the native-close path where JS isn't involved.
        def _on_closing() -> None:
            try:
                self.api._safe_evaluate_js(
                    "document.getElementById('status-text').textContent"
                    " = 'Saving session…';",
                )
                self.core.stop_autosave()
                self.core.save_session()
            except Exception as e:
                logger.error(f"Error saving on close: {e}")

        if self.window is not None:
            self.window.events.closing += _on_closing

        # Load session and restore tabs
        self._restore_session()

        # Start autosave after session is restored
        logger.info("Starting autosave...")
        self.core.start_autosave()
        logger.info(f"Autosave timer: {self.core._autosave_timer}")

    def _restore_session(self) -> None:
        """Restore tabs from saved session."""
        try:
            tabs_data, selected_tab_id = self.core.load_session()
            logger.info(
                f"Loaded {len(tabs_data)} tabs from session, selected_tab_id={selected_tab_id}",
            )

            if not tabs_data:
                # No saved session — let JS create the default tab via onSessionRestoreComplete
                logger.info(
                    "No saved session data, signalling JS to create default tab",
                )
                self.api._safe_evaluate_js("app.onSessionRestoreComplete();")
                return

            # Restore each tab - don't auto-switch during bulk creation.
            # Build old_id -> new_id mapping so we can restore the correct
            # active tab (old IDs are never reused across sessions).
            first_tab_id = None
            old_to_new: dict[str, str] = {}
            for i, tab_data in enumerate(tabs_data):
                title = tab_data.get("name", tab_data.get("title", "Chat"))
                old_id = tab_data.get("tab_id", "")
                logger.info(
                    f"Restoring tab {i}: title='{title}' from data keys {list(tab_data.keys())}",
                )

                saved_conv_id_raw = tab_data.get("conversation_id")
                saved_conv_id = (
                    int(saved_conv_id_raw) if saved_conv_id_raw is not None else None
                )
                if tab_data.get("tab_type") == "pack":
                    project_kwargs = {}
                    if tab_data.get("project"):
                        project_kwargs = {
                            "project": tab_data["project"],
                            "workspace_path": tab_data.get("workspace_path"),
                        }
                    result = self.api.create_pack_tab_and_notify_js(
                        tab_data.get("host", ""),
                        tab_data.get("session_id", ""),
                        title,
                        auto_switch=False,
                        conversation_id=saved_conv_id,
                        **project_kwargs,
                    )
                else:
                    result = self.api.create_tab_and_notify_js(
                        title,
                        auto_switch=False,
                        conversation_id=saved_conv_id,
                    )

                logger.info(f"create_tab_and_notify_js result: {result}")
                if result["success"]:
                    new_id = result["tab_id"]
                    if first_tab_id is None:
                        first_tab_id = new_id
                    if old_id:
                        old_to_new[old_id] = new_id
                    tab = self.core.tabs.get(new_id)
                    if tab:
                        logger.info(f"Before load_from_data: tab.title='{tab.title}'")
                        tab.load_from_data(tab_data)
                        logger.info(
                            f"After load_from_data: tab.title='{tab.title}', chat_state has {len(tab.chat_state.questions)} questions",
                        )
                    else:
                        logger.error(
                            f"Tab {new_id} not found in core.tabs after creation",
                        )
                else:
                    logger.error(
                        f"Failed to create tab: {result.get('error', 'unknown error')}",
                    )

            # Resolve the previously active tab using the old->new ID map.
            resolved_selected = (
                old_to_new.get(selected_tab_id) if selected_tab_id else None
            )
            tab_to_select = resolved_selected or first_tab_id
            logger.info(f"Selecting tab: {tab_to_select}")
            if tab_to_select:
                self.set_active_tab(tab_to_select)
                # Notify the UI to select this tab (this will trigger content loading)
                self.api.select_tab(tab_to_select)

            # Signal JS that restore is complete — JS creates a default tab only if
            # nothing arrived (i.e. empty session). Must fire after all createTabUI calls.
            self.api._safe_evaluate_js("app.onSessionRestoreComplete();")

        except Exception as e:
            logger.error(f"Error restoring session: {e}", exc_info=True)
            # On error, signal JS so it still creates a default tab
            self.api._safe_evaluate_js("app.onSessionRestoreComplete();")

    def set_active_tab(self, tab_id: str) -> None:
        """Set the currently active tab."""
        self._active_tab_id = tab_id
        tab = self.core.tabs.get(tab_id)
        if tab and self.window is not None:
            self.window.title = f"Alpaca Assist - {tab.title}"

    def get_active_tab_id(self) -> str | None:
        """Get the currently active tab ID."""
        return self._active_tab_id


def main() -> None:
    """Application entry point."""
    app = ChatApp()
    app.run()


if __name__ == "__main__":
    main()
