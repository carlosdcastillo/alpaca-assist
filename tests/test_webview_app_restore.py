"""Tests for webview_app.py's ChatApp._restore_session Pack-tab branch."""

from __future__ import annotations

import sys
from typing import Any
from unittest.mock import MagicMock

import pytest

# webview_app.py does `import webview` at module level for pywebview's GTK
# bridge, only available in the app's own .venv-sys (system-site-packages)
# environment, not the plain venv this test suite otherwise runs under.
# _restore_session itself never touches the real package (only
# create_window/run do), so a stub is sufficient here.
sys.modules.setdefault("webview", MagicMock())

from webview_app import ChatApp  # noqa: E402


@pytest.fixture
def app() -> Any:
    """A ChatApp instance with __init__ skipped — _restore_session only

    touches self.core / self.api (mocked) and self.set_active_tab (real —
    ChatApp uses __slots__, so a method can't be shadowed per-instance;
    the real implementation is safe here since it only touches self.core
    (mocked) and self.window, set to None below).
    """
    instance = object.__new__(ChatApp)
    instance.core = MagicMock()
    instance.api = MagicMock()
    instance.window = None
    return instance


class TestRestoreSessionRoutesPackTabs:
    def test_pack_tab_data_routes_to_create_pack_tab_and_notify_js(
        self,
        app: Any,
    ) -> None:
        app.core.load_session.return_value = (
            [
                {
                    "tab_id": "old-1",
                    "name": "My Pack",
                    "tab_type": "pack",
                    "host": "user@host",
                    "session_id": "sess-1",
                    "conversation_id": 5,
                    "chat_state": {"graph": {"nodes": {}}},
                },
            ],
            None,
        )
        app.api.create_pack_tab_and_notify_js.return_value = {
            "success": True,
            "tab_id": "new-1",
        }
        app.core.tabs = {"new-1": MagicMock()}

        app._restore_session()

        app.api.create_pack_tab_and_notify_js.assert_called_once_with(
            "user@host",
            "sess-1",
            "My Pack",
            auto_switch=False,
            conversation_id=5,
            initial_data=app.core.load_session.return_value[0][0],
        )
        app.api.create_tab_and_notify_js.assert_not_called()

    def test_regular_tab_data_routes_to_create_tab_and_notify_js(
        self,
        app: Any,
    ) -> None:
        app.core.load_session.return_value = (
            [
                {
                    "tab_id": "old-1",
                    "name": "Regular",
                    "conversation_id": 3,
                    "chat_state": {"questions": [], "answers": []},
                },
            ],
            None,
        )
        app.api.create_tab_and_notify_js.return_value = {
            "success": True,
            "tab_id": "new-1",
        }
        app.core.tabs = {"new-1": MagicMock()}

        app._restore_session()

        app.api.create_tab_and_notify_js.assert_called_once_with(
            "Regular",
            auto_switch=False,
            conversation_id=3,
        )
        app.api.create_pack_tab_and_notify_js.assert_not_called()

    def test_mixed_tabs_route_independently(self, app: Any) -> None:
        app.core.load_session.return_value = (
            [
                {"tab_id": "old-1", "name": "Regular", "chat_state": {}},
                {
                    "tab_id": "old-2",
                    "name": "Pack",
                    "tab_type": "pack",
                    "host": "h",
                    "session_id": "s",
                    "chat_state": {},
                },
            ],
            None,
        )
        app.api.create_tab_and_notify_js.return_value = {
            "success": True,
            "tab_id": "new-1",
        }
        app.api.create_pack_tab_and_notify_js.return_value = {
            "success": True,
            "tab_id": "new-2",
        }
        app.core.tabs = {"new-1": MagicMock(), "new-2": MagicMock()}

        app._restore_session()

        assert app.api.create_tab_and_notify_js.call_count == 1
        assert app.api.create_pack_tab_and_notify_js.call_count == 1

    def test_no_saved_tabs_signals_js_and_creates_nothing(self, app: Any) -> None:
        app.core.load_session.return_value = ([], None)

        app._restore_session()

        app.api.create_tab_and_notify_js.assert_not_called()
        app.api.create_pack_tab_and_notify_js.assert_not_called()
        app.api._safe_evaluate_js.assert_called_with("app.onSessionRestoreComplete();")
