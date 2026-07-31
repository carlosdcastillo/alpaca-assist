"""Tests for core/pack_tab.py — the local proxy for a remote Pack tab."""
from __future__ import annotations

import time
from unittest import mock
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from core.pack_tab import PackTab
from core.pack_transport import PackTransportError


@pytest.fixture
def mock_transport_class():
    with patch("core.pack_tab.PackTransport") as cls:
        yield cls


@pytest.fixture
def app_core() -> MagicMock:
    core = MagicMock()
    core.api = MagicMock()
    core.get_active_tab_id.return_value = None
    return core


@pytest.fixture
def pack_tab(mock_transport_class: MagicMock, app_core: MagicMock) -> PackTab:
    return PackTab("tab-1", "Pack Tab", app_core, 42, "user@host", "session-abc")


GRAPH_STATE = {
    "chat_state": {
        "graph": {
            "id": "g1",
            "title": "",
            "created_at": "2026-01-01T00:00:00",
            "active_node_id": None,
            "nodes": {},
        },
    },
    "tab_id": "remote-tab-1",
    "name": "Real Title",
    "conversation_id": 42,
}

# Shape of a real `attach` RPC response (core/pack_daemon.py's dispatcher),
# as opposed to GRAPH_STATE above, which is the shape of the *inner*
# "state" value (== tab.get_serializable_data()).
ATTACH_RESPONSE = {
    "state": GRAPH_STATE,
    "title": "Real Title",
    "is_streaming": False,
    "resumed": True,
}


class TestConstruction:
    def test_skip_db_storage_is_true(self, pack_tab: PackTab) -> None:
        assert PackTab.skip_db_storage is True

    def test_starts_with_empty_graph_chat_state(self, pack_tab: PackTab) -> None:
        assert pack_tab.chat_state.to_dict().get("graph", {}).get("nodes") == {}

    def test_starts_online_and_not_streaming(self, pack_tab: PackTab) -> None:
        assert pack_tab.offline is False
        assert pack_tab.is_streaming is False


class TestHandleUserMessage:
    def test_sets_current_answer_index_before_returning(self, pack_tab: PackTab) -> None:
        transport = pack_tab._transport
        transport.connected = True
        transport.send_request.return_value = {"answer_index": 7}

        pack_tab.handle_user_message("hello", [])

        assert pack_tab._current_answer_index == 7
        transport.send_request.assert_any_call(
            "send_message",
            {"message": "hello", "images": []},
            timeout=mock.ANY,
        )

    def test_offline_send_triggers_error_push_and_sets_offline(
        self,
        pack_tab: PackTab,
        app_core: MagicMock,
    ) -> None:
        transport = pack_tab._transport
        transport.connected = False
        transport.connect.side_effect = PackTransportError("no route to host")

        pack_tab.handle_user_message("hello", [])

        assert pack_tab.offline is True
        app_core.api.on_error.assert_called_once()
        assert "Pack tab offline" in app_core.api.on_error.call_args[0][1]



class TestMutatingMethods:
    def test_compact_conversation_mutates_then_resyncs(self, pack_tab: PackTab) -> None:
        transport = pack_tab._transport
        transport.connected = True
        transport.send_request.side_effect = [
            {"compacted": True},  # the compact_conversation call
            ATTACH_RESPONSE,  # the follow-up attach/resync call
        ]

        result = pack_tab.compact_conversation()

        assert result == {"compacted": True}
        assert pack_tab.title == "Real Title"
        methods_called = [c.args[0] for c in transport.send_request.call_args_list]
        assert methods_called == ["compact_conversation", "attach"]

    def test_truncate_conversation_offline_returns_failure_dict(
        self,
        pack_tab: PackTab,
    ) -> None:
        transport = pack_tab._transport
        transport.send_request.side_effect = PackTransportError("gone")

        result = pack_tab.truncate_conversation()

        assert result == {"success": False, "reason": "offline"}

    def test_pop_conversation_mutates_then_resyncs(self, pack_tab: PackTab) -> None:
        transport = pack_tab._transport
        transport.send_request.side_effect = [
            {"popped": True, "question": "q", "images": []},
            ATTACH_RESPONSE,
        ]

        result = pack_tab.pop_conversation()

        assert result["popped"] is True
        assert transport.send_request.call_count == 2


class TestSerialization:
    def test_get_serializable_data_includes_pack_fields(self, pack_tab: PackTab) -> None:
        data = pack_tab.get_serializable_data()

        assert data["tab_type"] == "pack"
        assert data["host"] == "user@host"
        assert data["session_id"] == "session-abc"
        assert data["tab_id"] == "tab-1"
        assert data["conversation_id"] == 42

    def test_load_from_data_round_trip(self, pack_tab: PackTab) -> None:
        pack_tab.load_from_data(GRAPH_STATE | {"host": "other@host2", "session_id": "sess2"})

        assert pack_tab.title == "Real Title"
        assert pack_tab.host == "other@host2"
        assert pack_tab.session_id == "sess2"
        assert pack_tab.chat_state.to_dict()["graph"]["id"] == "g1"

    def test_load_from_data_without_host_session_keeps_existing(
        self,
        pack_tab: PackTab,
    ) -> None:
        original_host = pack_tab.host
        pack_tab.load_from_data({"chat_state": {"graph": GRAPH_STATE["chat_state"]["graph"]}})

        assert pack_tab.host == original_host


class TestCleanupNeverKillsRemote:
    def test_cleanup_resources_only_closes_local_transport(self, pack_tab: PackTab) -> None:
        transport = pack_tab._transport

        pack_tab.cleanup_resources()

        transport.close.assert_called_once()
        sent_methods = [c.args[0] for c in transport.send_request.call_args_list]
        assert "kill" not in " ".join(sent_methods).lower()
        assert "stop" not in " ".join(sent_methods).lower() or not sent_methods


class TestNotificationHandlers:
    def test_on_streaming_start_sets_local_flag_and_forwards(
        self,
        pack_tab: PackTab,
        app_core: MagicMock,
    ) -> None:
        pack_tab._on_streaming_start({"tab_id": "remote-daemon-tab-99","answer_index": 2})

        assert pack_tab.is_streaming is True
        app_core.api.on_streaming_start.assert_called_once_with("tab-1", 2)

    def test_on_streaming_end_clears_local_flag_and_forwards(
        self,
        pack_tab: PackTab,
        app_core: MagicMock,
    ) -> None:
        pack_tab.is_streaming = True

        pack_tab._on_streaming_end({"tab_id": "remote-daemon-tab-99","answer_index": 2})

        assert pack_tab.is_streaming is False
        app_core.api.on_streaming_end.assert_called_once_with("tab-1", 2)

    def test_on_content_update_reconstructs_real_content_update(
        self,
        pack_tab: PackTab,
        app_core: MagicMock,
    ) -> None:
        pack_tab._on_content_update(
            {
                "tab_id": "tab-1",
                "update": {"answer_index": 0, "content_chunk": "hi", "is_done": False},
            },
        )

        app_core.api.on_content_update.assert_called_once()
        tab_id, update = app_core.api.on_content_update.call_args[0]
        assert tab_id == "tab-1"
        assert update.content_chunk == "hi"
        assert update.is_done is False

    def test_on_update_tab_title_updates_local_title_and_forwards(
        self,
        pack_tab: PackTab,
        app_core: MagicMock,
    ) -> None:
        pack_tab._on_update_tab_title({"tab_id": "remote-daemon-tab-99","title": "New Title"})

        assert pack_tab.title == "New Title"
        app_core.api.update_tab_title.assert_called_once_with("tab-1", "New Title")

    def test_on_error_clears_streaming_and_forwards(
        self,
        pack_tab: PackTab,
        app_core: MagicMock,
    ) -> None:
        pack_tab.is_streaming = True

        pack_tab._on_error({"tab_id": "remote-daemon-tab-99","message": "boom", "details": "x"})

        assert pack_tab.is_streaming is False
        app_core.api.on_error.assert_called_once_with("tab-1", "boom", "x")

    def test_on_inject_tool_fold_uses_local_tab_id_for_the_real_push(
        self,
        pack_tab: PackTab,
        app_core: MagicMock,
    ) -> None:
        transport = pack_tab._transport
        app_core.api.wait_for_fold_rendered.return_value = True

        pack_tab._on_inject_tool_fold(
            {
                "tab_id": "remote-daemon-tab-99",
                "fold_id": "fold-1",
                "fold_type": "result",
                "body_text": "body",
                "answer_index": 0,
            },
        )

        app_core.api.inject_tool_fold.assert_called_once_with(
            "tab-1",  # local tab id, not the remote one from params
            "fold-1",
            "result",
            "body",
            0,
        )

    def test_on_inject_tool_fold_echoes_render_confirmation_without_remote_tab_id(
        self,
        pack_tab: PackTab,
        app_core: MagicMock,
    ) -> None:
        """The fold_rendered request must NOT carry the local tab_id — the

        daemon's dispatcher keys its wait on its own (single) tab when the
        field is omitted; sending the local id would silently never match.
        """
        transport = pack_tab._transport
        app_core.api.wait_for_fold_rendered.return_value = True

        pack_tab._on_inject_tool_fold(
            {
                "tab_id": "remote-daemon-tab-99",
                "fold_id": "fold-1",
                "fold_type": "result",
                "body_text": "body",
                "answer_index": 0,
            },
        )

        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and transport.send_request.call_count < 1:
            time.sleep(0.02)

        transport.send_request.assert_called_once_with(
            "fold_rendered",
            {"fold_id": "fold-1", "rendered": True},
            timeout=mock.ANY,
        )

    def test_on_disconnect_marks_offline(self, pack_tab: PackTab) -> None:
        pack_tab.is_streaming = True

        pack_tab._on_disconnect()

        assert pack_tab.offline is True
        assert pack_tab.is_streaming is False
