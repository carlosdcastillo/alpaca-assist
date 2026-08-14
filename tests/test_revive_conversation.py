"""Tests for webview_api.revive_conversation's tab_type routing.

Closing a Pack tab now stores it in history exactly like a regular tab
(core/app_core.py's delete_tab no longer special-cases skip_db_storage).
revive_conversation must route a stored "pack" conversation back through
create_pack_tab_and_notify_js (which reconnects to the still-running
remote daemon) rather than create_tab_and_notify_js (which would create
a disconnected local ChatTab with no ability to resume the remote
session).
"""
from __future__ import annotations

from unittest.mock import Mock
from unittest.mock import patch

from webview_api import WebViewAPI


def _api_with_conversation(tab_data: dict) -> tuple[WebViewAPI, Mock]:
    mock_app = Mock()
    mock_app.core.tabs = {}
    mock_app.core.db.get_conversation.return_value = tab_data
    return WebViewAPI(mock_app), mock_app


class TestReviveConversationRoutesPackTabs:
    def test_pack_conversation_routes_to_create_pack_tab_and_notify_js(self) -> None:
        tab_data = {
            "tab_type": "pack",
            "host": "user@host",
            "session_id": "sess-1",
            "name": "My Pack",
            "chat_state": {"graph": {"nodes": {}}},
        }
        api, mock_app = _api_with_conversation(tab_data)
        with patch.object(
            WebViewAPI,
            "create_pack_tab_and_notify_js",
            return_value={"success": True, "tab_id": "new-1"},
        ) as mock_create_pack, patch.object(
            WebViewAPI,
            "create_tab_and_notify_js",
        ) as mock_create_regular:
            mock_app.core.tabs = {"new-1": Mock()}
            result = api.revive_conversation(42)

        mock_create_pack.assert_called_once_with(
            "user@host",
            "sess-1",
            "My Pack",
            auto_switch=True,
            conversation_id=42,
        )
        mock_create_regular.assert_not_called()
        assert result == {"success": True, "tab_id": "new-1"}

    def test_regular_conversation_routes_to_create_tab_and_notify_js(self) -> None:
        tab_data = {"name": "Regular", "chat_state": {"questions": [], "answers": []}}
        api, mock_app = _api_with_conversation(tab_data)
        with patch.object(
            WebViewAPI,
            "create_tab_and_notify_js",
            return_value={"success": True, "tab_id": "new-2"},
        ) as mock_create_regular, patch.object(
            WebViewAPI,
            "create_pack_tab_and_notify_js",
        ) as mock_create_pack:
            mock_app.core.tabs = {"new-2": Mock()}
            result = api.revive_conversation(7)

        mock_create_regular.assert_called_once_with(
            "Regular",
            auto_switch=True,
            conversation_id=7,
        )
        mock_create_pack.assert_not_called()
        assert result == {"success": True, "tab_id": "new-2"}

    def test_revived_pack_tab_loads_saved_state(self) -> None:
        tab_data = {
            "tab_type": "pack",
            "host": "user@host",
            "session_id": "sess-1",
            "name": "My Pack",
            "chat_state": {"graph": {"nodes": {}}},
        }
        api, mock_app = _api_with_conversation(tab_data)
        revived_tab = Mock()
        with patch.object(
            WebViewAPI,
            "create_pack_tab_and_notify_js",
            return_value={"success": True, "tab_id": "new-1"},
        ):
            mock_app.core.tabs = {"new-1": revived_tab}
            api.revive_conversation(42)

        revived_tab.load_from_data.assert_called_once_with(tab_data)


class TestGetHistoryReportsTabType:
    """History rows must carry tab_type so the UI can badge Pack

    conversations, matching the chain-link glyph already used for open
    Pack tabs in the tab bar.
    """

    def test_pack_and_regular_rows_both_report_their_tab_type(self) -> None:
        mock_app = Mock()
        mock_app.core.db.get_history_records.return_value = [
            {"id": 1, "title": "Remote Chat", "tab_type": "pack"},
            {"id": 2, "title": "Local Chat", "tab_type": None},
        ]
        mock_app.core.db.get_history_facets.return_value = {"folders": []}
        api = WebViewAPI(mock_app)

        result = api.get_history()

        by_title = {c["title"]: c["tab_type"] for c in result["conversations"]}
        assert by_title["Remote Chat"] == "pack"
        assert by_title["Local Chat"] is None
