# unittest.mock replaces these typed methods with MagicMock instances at runtime.
# mypy: disable-error-code="attr-defined"
"""Tests for core/pack_tab.py — the local proxy for a remote Pack tab."""
from __future__ import annotations

import base64
import time
from typing import Any
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


GRAPH_STATE: dict[str, Any] = {
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
    def test_starts_with_empty_graph_chat_state(self, pack_tab: PackTab) -> None:
        assert pack_tab.chat_state.to_dict().get("graph", {}).get("nodes") == {}

    def test_starts_online_and_not_streaming(self, pack_tab: PackTab) -> None:
        assert pack_tab.offline is False
        assert pack_tab.is_streaming is False

    def test_starts_with_zeroed_token_stats(self, pack_tab: PackTab) -> None:
        """webview_api.get_status_info() reads these via getattr(tab, ...,

        0) for any tab — PackTab must carry real zero-valued attributes
        from construction, not just fall back to the getattr default,
        so a freshly created (not-yet-attached) tab shows 0 rather than
        the crude chars/4 estimate.
        """
        assert pack_tab.session_output_tokens == 0
        assert pack_tab.session_input_tokens == 0
        assert pack_tab.session_cached_input_tokens == 0
        assert pack_tab.last_invocation_metrics is None


class TestConnect:
    def test_connect_forwards_model_to_the_transport(
        self,
        pack_tab: PackTab,
    ) -> None:
        """Regression test: AppCore.create_pack_tab/PackTab.connect_async's

        model parameter used to be accepted and silently dropped — a
        freshly created Pack tab always got whatever DEFAULT_MODEL
        happened to be, regardless of what was selected locally. model
        must actually reach PackTransport.connect so pack_bridge.py can
        forward it to a freshly-spawned daemon's --model flag.
        """
        pack_tab._transport.send_request.return_value = ATTACH_RESPONSE

        pack_tab.connect_async(model="kimi-k3")
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not pack_tab._transport.connect.called:
            time.sleep(0.02)

        pack_tab._transport.connect.assert_called_once_with(model="kimi-k3")

    def test_reconnect_does_not_force_a_model(
        self,
        pack_tab: PackTab,
    ) -> None:
        """Reconnecting an already-offline tab targets a daemon that (if

        still alive) already has its own preferences — there's no local
        model context to forward here, unlike a fresh connect_async.
        """
        pack_tab.offline = True
        pack_tab._transport.send_request.return_value = ATTACH_RESPONSE

        pack_tab.reconnect_async()
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not pack_tab._transport.connect.called:
            time.sleep(0.02)

        pack_tab._transport.connect.assert_called_once_with(model=None)


class TestHandleUserMessage:
    def test_sets_current_answer_index_before_returning(
        self,
        pack_tab: PackTab,
    ) -> None:
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

        with pytest.raises(PackTransportError):
            pack_tab.handle_user_message("hello", [])

        assert pack_tab.offline is True
        app_core.api.on_error.assert_called_once()
        assert "Pack tab offline" in app_core.api.on_error.call_args[0][1]


class TestSetModel:
    def test_sends_model_to_remote_daemon(self, pack_tab: PackTab) -> None:
        transport = pack_tab._transport
        transport.connected = True
        transport.send_request.return_value = {"success": True}

        result = pack_tab.set_model("new-model")

        assert result == {"success": True}
        transport.send_request.assert_called_once_with(
            "set_model",
            {"model": "new-model"},
            timeout=mock.ANY,
        )


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

    def test_recompute_title_starts_on_remote_daemon(self, pack_tab: PackTab) -> None:
        transport = pack_tab._transport
        transport.send_request.return_value = {"started": True}

        result = pack_tab.recompute_title()

        assert result == {"started": True}
        transport.send_request.assert_called_once_with(
            "recompute_title",
            {},
            timeout=mock.ANY,
        )

    def test_pop_conversation_mutates_then_resyncs(self, pack_tab: PackTab) -> None:
        transport = pack_tab._transport
        transport.send_request.side_effect = [
            {"popped": True, "question": "q", "images": []},
            ATTACH_RESPONSE,
        ]

        result = pack_tab.pop_conversation()

        assert result["popped"] is True
        assert transport.send_request.call_count == 2


class TestTokenStats:
    def test_apply_token_stats_updates_present_fields(self, pack_tab: PackTab) -> None:
        pack_tab._apply_token_stats(
            {
                "session_output_tokens": 100,
                "session_input_tokens": 200,
                "session_cached_input_tokens": 10,
                "last_invocation_metrics": {"output_token_count": 100},
            },
        )

        assert pack_tab.session_output_tokens == 100
        assert pack_tab.session_input_tokens == 200
        assert pack_tab.session_cached_input_tokens == 10
        assert pack_tab.last_invocation_metrics == {"output_token_count": 100}

    def test_apply_token_stats_ignores_absent_fields_rather_than_resetting(
        self,
        pack_tab: PackTab,
    ) -> None:
        """Not every caller sends every field — a payload missing a key

        must leave whatever value is already there alone, not reset it
        to 0 (which would make the count visibly regress on a partial
        update instead of just staying stale until the next full one).
        """
        pack_tab.session_output_tokens = 500

        pack_tab._apply_token_stats({"session_input_tokens": 999})

        assert pack_tab.session_output_tokens == 500
        assert pack_tab.session_input_tokens == 999

    def test_resync_picks_up_token_stats_from_the_attach_response(
        self,
        pack_tab: PackTab,
    ) -> None:
        """Reattaching (app restart, revival from history, reconnect

        after an offline blip) must show the remote ChatTab's real
        cumulative usage immediately, not 0 until the next turn happens
        to complete.
        """
        pack_tab._transport.send_request.return_value = {
            **ATTACH_RESPONSE,
            "session_output_tokens": 4321,
            "session_input_tokens": 8765,
        }

        pack_tab._resync()

        assert pack_tab.session_output_tokens == 4321
        assert pack_tab.session_input_tokens == 8765


class TestSessionLostRecreate:
    """resumed=False from an attach response means the remote daemon found

    no persisted chat_session.json — either a genuinely first-ever connect
    (nothing local to lose, apply normally) or a real "the worker is
    gone" event (local content must not be silently overwritten — ask the
    user, per PackTab._apply_resync_result / resolve_session_lost).
    """

    def test_resumed_false_with_no_local_content_applies_normally(
        self,
        pack_tab: PackTab,
        app_core: MagicMock,
    ) -> None:
        result = {**ATTACH_RESPONSE, "resumed": False}

        pack_tab._apply_resync_result(result)

        assert pack_tab.title == "Real Title"
        assert pack_tab._pending_recreate_state is None
        app_core.api._safe_evaluate_js.assert_not_called()

    def test_resumed_false_with_local_content_prompts_instead_of_overwriting(
        self,
        pack_tab: PackTab,
        app_core: MagicMock,
    ) -> None:
        pack_tab.load_from_data({"chat_state": {"questions": ["Q"], "answers": ["A"]}})
        result = {**ATTACH_RESPONSE, "resumed": False}

        pack_tab._apply_resync_result(result)

        assert pack_tab.chat_state.to_dict()["questions"] == ["Q"]
        assert pack_tab.title != "Real Title"
        assert pack_tab._pending_recreate_state == result
        app_core.api._safe_evaluate_js.assert_called_once()
        js_call = app_core.api._safe_evaluate_js.call_args[0][0]
        assert "onPackSessionLost" in js_call
        assert pack_tab.tab_id in js_call

    def test_resolve_session_lost_recreate_seeds_the_remote_tab(
        self,
        pack_tab: PackTab,
    ) -> None:
        pack_tab.load_from_data({"chat_state": {"questions": ["Q"], "answers": ["A"]}})
        pack_tab._apply_resync_result({**ATTACH_RESPONSE, "resumed": False})
        transport = pack_tab._transport
        transport.send_request.reset_mock()
        transport.send_request.return_value = {"success": True}

        pack_tab.resolve_session_lost(recreate=True)

        transport.send_request.assert_called_once_with(
            "seed_state",
            {"seed": pack_tab.get_serializable_data()},
            timeout=mock.ANY,
        )
        assert pack_tab._pending_recreate_state is None
        assert pack_tab.chat_state.to_dict()["questions"] == ["Q"]

    def test_read_video_chunk_is_proxied_to_remote_daemon(
        self,
        pack_tab: PackTab,
    ) -> None:
        pack_tab._transport.send_request.return_value = {
            "data": "YWJj",
            "done": True,
        }

        result = pack_tab.read_video_chunk("locator", 12)

        assert result["done"] is True
        pack_tab._transport.send_request.assert_called_once_with(
            "read_video_chunk",
            {"locator": "locator", "offset": 12},
            timeout=mock.ANY,
        )

    def test_read_gated_tool_output_is_proxied_to_remote_daemon(
        self,
        pack_tab: PackTab,
    ) -> None:
        pack_tab._transport.send_request.return_value = {"content": "full media"}

        result = pack_tab.read_gated_tool_output("placeholder")

        assert result == "full media"
        pack_tab._transport.send_request.assert_called_once_with(
            "read_gated_tool_output",
            {"gated_text": "placeholder"},
            timeout=mock.ANY,
        )

    def test_materialize_file_downloads_chunks_and_reuses_cache(
        self,
        pack_tab: PackTab,
    ) -> None:
        transport = pack_tab._transport
        transport.send_request.side_effect = [
            {
                "locator": "opaque",
                "name": "report.txt",
                "size": 6,
                "identity": "a" * 64,
            },
            {
                "size": 6,
                "data": base64.b64encode(b"abc").decode("ascii"),
                "next_offset": 3,
                "done": False,
            },
            {
                "size": 6,
                "data": base64.b64encode(b"def").decode("ascii"),
                "next_offset": 6,
                "done": True,
            },
            {
                "locator": "opaque-2",
                "name": "report.txt",
                "size": 6,
                "identity": "a" * 64,
            },
        ]

        first = pack_tab.materialize_file("build/report.txt")
        second = pack_tab.materialize_file("build/report.txt")

        assert first == second
        assert first.read_bytes() == b"abcdef"
        assert [call.args[0] for call in transport.send_request.call_args_list] == [
            "resolve_file_reference",
            "read_file_chunk",
            "read_file_chunk",
            "resolve_file_reference",
        ]
        pack_tab.cleanup_resources()
        assert not first.exists()

    def test_materialize_file_removes_partial_download_after_failure(
        self,
        pack_tab: PackTab,
    ) -> None:
        transport = pack_tab._transport
        transport.send_request.side_effect = [
            {
                "locator": "opaque",
                "name": "report.txt",
                "size": 6,
                "identity": "b" * 64,
            },
            {
                "size": 6,
                "data": base64.b64encode(b"abc").decode("ascii"),
                "next_offset": 3,
                "done": True,
            },
        ]

        with pytest.raises(PackTransportError, match="ended early"):
            pack_tab.materialize_file("report.txt")

        assert pack_tab._file_cache_dir is not None
        assert list(pack_tab._file_cache_dir.iterdir()) == []

    def test_resolve_session_lost_recreate_failure_marks_offline(
        self,
        pack_tab: PackTab,
    ) -> None:
        pack_tab.load_from_data({"chat_state": {"questions": ["Q"], "answers": ["A"]}})
        pack_tab._apply_resync_result({**ATTACH_RESPONSE, "resumed": False})
        transport = pack_tab._transport
        transport.send_request.side_effect = PackTransportError("gone")

        pack_tab.resolve_session_lost(recreate=True)

        assert pack_tab.offline is True

    def test_resolve_session_lost_start_fresh_applies_the_empty_state(
        self,
        pack_tab: PackTab,
    ) -> None:
        pack_tab.load_from_data({"chat_state": {"questions": ["Q"], "answers": ["A"]}})
        pack_tab._apply_resync_result({**ATTACH_RESPONSE, "resumed": False})

        pack_tab.resolve_session_lost(recreate=False)

        assert pack_tab.title == "Real Title"
        assert pack_tab.chat_state.to_dict()["graph"]["id"] == "g1"
        assert pack_tab._pending_recreate_state is None

    def test_resolve_session_lost_with_nothing_pending_is_a_noop(
        self,
        pack_tab: PackTab,
    ) -> None:
        transport = pack_tab._transport
        transport.send_request.reset_mock()

        pack_tab.resolve_session_lost(recreate=True)

        transport.send_request.assert_not_called()


class TestSerialization:
    def test_get_serializable_data_includes_pack_fields(
        self,
        pack_tab: PackTab,
    ) -> None:
        data = pack_tab.get_serializable_data()

        assert data["tab_type"] == "pack"
        assert data["host"] == "user@host"
        assert data["session_id"] == "session-abc"
        assert data["tab_id"] == "tab-1"
        assert data["conversation_id"] == 42

    def test_load_from_data_round_trip(self, pack_tab: PackTab) -> None:
        pack_tab.load_from_data(
            GRAPH_STATE | {"host": "other@host2", "session_id": "sess2"},
        )

        assert pack_tab.title == "Real Title"
        assert pack_tab.host == "other@host2"
        assert pack_tab.session_id == "sess2"
        assert pack_tab.chat_state.to_dict()["graph"]["id"] == "g1"

    def test_load_from_data_without_host_session_keeps_existing(
        self,
        pack_tab: PackTab,
    ) -> None:
        original_host = pack_tab.host
        pack_tab.load_from_data(
            {"chat_state": {"graph": GRAPH_STATE["chat_state"]["graph"]}},
        )

        assert pack_tab.host == original_host


class TestCleanupNeverKillsRemote:
    def test_cleanup_resources_only_closes_local_transport(
        self,
        pack_tab: PackTab,
    ) -> None:
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
        pack_tab._on_streaming_start(
            {"tab_id": "remote-daemon-tab-99", "answer_index": 2},
        )

        assert pack_tab.is_streaming is True
        app_core.api.on_streaming_start.assert_called_once_with("tab-1", 2)

    def test_on_streaming_end_clears_local_flag_and_forwards(
        self,
        pack_tab: PackTab,
        app_core: MagicMock,
    ) -> None:
        pack_tab.is_streaming = True
        pack_tab._transport.send_request.return_value = ATTACH_RESPONSE

        pack_tab._on_streaming_end(
            {"tab_id": "remote-daemon-tab-99", "answer_index": 2},
        )

        assert pack_tab.is_streaming is False
        app_core.api.on_streaming_end.assert_called_once_with("tab-1", 2)

    def test_on_streaming_end_applies_token_stats_synchronously(
        self,
        pack_tab: PackTab,
    ) -> None:
        """Regression test: PackTab never had session_output_tokens etc.

        of its own — nothing pushed them from the remote ChatTab, so
        webview_api.get_status_info()'s getattr(tab, "session_output_tokens",
        0) always saw 0, and the status bar fell back to a crude chars/4
        estimate for every Pack tab on every turn. Applied before the
        async resync (which only refreshes chat_state) so the status bar
        is right immediately, not after an extra RPC round trip.
        """
        pack_tab._transport.send_request.return_value = ATTACH_RESPONSE

        pack_tab._on_streaming_end(
            {
                "tab_id": "remote-daemon-tab-99",
                "answer_index": 2,
                "session_output_tokens": 1234,
                "session_input_tokens": 5678,
                "session_cached_input_tokens": 90,
                "last_invocation_metrics": {"output_token_count": 1234},
            },
        )

        assert pack_tab.session_output_tokens == 1234
        assert pack_tab.session_input_tokens == 5678
        assert pack_tab.session_cached_input_tokens == 90
        assert pack_tab.last_invocation_metrics == {"output_token_count": 1234}

    def test_on_streaming_end_resyncs_the_chat_state_mirror(
        self,
        pack_tab: PackTab,
    ) -> None:
        """Regression test: get_conversation_state (used on tab switch)

        reads pack_tab.chat_state directly with no RPC round trip. A turn
        that streamed purely via live on_content_update pushes must still
        leave the mirror caught up afterward, or switching away and back
        to the tab shows nothing — exactly the bug this locks in.
        """
        assert pack_tab.chat_state.to_dict()["graph"]["nodes"] == {}
        pack_tab._transport.send_request.return_value = ATTACH_RESPONSE

        pack_tab._on_streaming_end(
            {"tab_id": "remote-daemon-tab-99", "answer_index": 0},
        )

        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and pack_tab.title != "Real Title":
            time.sleep(0.02)

        assert pack_tab.title == "Real Title"
        assert pack_tab.chat_state.to_dict()["graph"]["id"] == "g1"

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
        pack_tab._on_update_tab_title(
            {"tab_id": "remote-daemon-tab-99", "title": "New Title"},
        )

        assert pack_tab.title == "New Title"
        app_core.api.update_tab_title.assert_called_once_with("tab-1", "New Title")

    def test_on_error_clears_streaming_and_forwards(
        self,
        pack_tab: PackTab,
        app_core: MagicMock,
    ) -> None:
        pack_tab.is_streaming = True
        pack_tab._transport.send_request.return_value = ATTACH_RESPONSE

        pack_tab._on_error(
            {"tab_id": "remote-daemon-tab-99", "message": "boom", "details": "x"},
        )

        assert pack_tab.is_streaming is False
        app_core.api.on_error.assert_called_once_with("tab-1", "boom", "x")

    def test_on_error_applies_token_stats(self, pack_tab: PackTab) -> None:
        """A turn can end via error mid-generation with partial output

        already billed by the provider — that partial count must still
        reach the status bar, not just the clean streaming_end path.
        """
        pack_tab._transport.send_request.return_value = ATTACH_RESPONSE

        pack_tab._on_error(
            {
                "tab_id": "remote-daemon-tab-99",
                "message": "boom",
                "session_output_tokens": 42,
            },
        )

        assert pack_tab.session_output_tokens == 42

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


class TestSurfaces:
    """The control plane for live app surfaces.

    PackTab's job here is small but specific: call the daemon, then build an
    SSH tunnel to whatever port it reports, and never let those two get out
    of step. Pixels are not in scope — they go over the tunnel directly from
    the panel to the remote x11vnc, bypassing Python entirely.
    """

    OPEN_RESPONSE = {
        "surface_id": "srf_1a2b3c4d",
        "ws_port": 6080,
        "password": "s3cr3t8",
        "width": 1280,
        "height": 800,
        "description": "xeyes",
        "seq": 0,
    }

    @pytest.fixture
    def tunnels(self, pack_tab: PackTab) -> MagicMock:
        manager = MagicMock()
        manager.open.return_value = 51733
        pack_tab._surface_tunnels = manager
        return manager

    def test_open_tunnels_to_the_reported_port(
        self,
        pack_tab: PackTab,
        tunnels: MagicMock,
    ) -> None:
        pack_tab._transport.send_request.return_value = dict(self.OPEN_RESPONSE)

        result = pack_tab.surface_open({"profile": "xeyes"})

        tunnels.open.assert_called_once_with("srf_1a2b3c4d", 6080)
        assert result["ws_url"] == "ws://127.0.0.1:51733"

    def test_open_asks_as_a_user_not_as_the_model(
        self,
        pack_tab: PackTab,
        tunnels: MagicMock,
    ) -> None:
        """source="user" is what allows a raw argv. The MCP server passes
        "model" instead and is confined to named profiles."""
        pack_tab._transport.send_request.return_value = dict(self.OPEN_RESPONSE)

        pack_tab.surface_open({"argv": ["xterm"]}, 800, 600)

        method, params = pack_tab._transport.send_request.call_args[0][:2]
        assert method == "surface_open"
        assert params["source"] == "user"
        assert params["width"] == 800

    def test_a_failed_tunnel_closes_the_remote_surface(
        self,
        pack_tab: PackTab,
        tunnels: MagicMock,
    ) -> None:
        """Without a tunnel nothing local can reach the surface, so leaving
        it running would burn a display until the idle reaper notices."""
        pack_tab._transport.send_request.return_value = dict(self.OPEN_RESPONSE)
        tunnels.open.side_effect = RuntimeError("ssh died")

        with pytest.raises(RuntimeError, match="ssh died"):
            pack_tab.surface_open({"profile": "xeyes"})

        assert pack_tab._transport.send_request.call_args[0][0] == "surface_close"

    def test_attach_rebuilds_the_tunnel_for_a_live_surface(
        self,
        pack_tab: PackTab,
        tunnels: MagicMock,
    ) -> None:
        """The transcript stores a descriptor, never a session — reopening a
        conversation arrives here with an id and nothing else."""
        pack_tab._transport.send_request.return_value = dict(self.OPEN_RESPONSE)

        result = pack_tab.surface_attach("srf_1a2b3c4d")

        method, params = pack_tab._transport.send_request.call_args[0][:2]
        assert (method, params) == ("surface_attach", {"surface_id": "srf_1a2b3c4d"})
        assert result["ws_url"] == "ws://127.0.0.1:51733"

    def test_attach_to_a_dead_surface_raises_rather_than_recreating_it(
        self,
        pack_tab: PackTab,
        tunnels: MagicMock,
    ) -> None:
        """Surfaces are never revived. The card says "session ended" and stops."""
        pack_tab._transport.send_request.side_effect = PackTransportError(
            "surface srf_1a2b3c4d is no longer running",
        )

        with pytest.raises(PackTransportError, match="no longer running"):
            pack_tab.surface_attach("srf_1a2b3c4d")

        tunnels.open.assert_not_called()

    def test_close_drops_the_tunnel_before_asking_the_daemon(
        self,
        pack_tab: PackTab,
        tunnels: MagicMock,
    ) -> None:
        pack_tab._transport.send_request.return_value = {"ok": True}

        pack_tab.surface_close("srf_1a2b3c4d")

        tunnels.close.assert_called_once_with("srf_1a2b3c4d")

    def test_close_still_drops_the_tunnel_when_the_daemon_is_gone(
        self,
        pack_tab: PackTab,
        tunnels: MagicMock,
    ) -> None:
        pack_tab._transport.send_request.side_effect = PackTransportError("offline")

        result = pack_tab.surface_close("srf_1a2b3c4d")

        tunnels.close.assert_called_once_with("srf_1a2b3c4d")
        assert result == {"ok": False, "reason": "offline"}

    def test_disconnect_closes_every_tunnel(
        self,
        pack_tab: PackTab,
        tunnels: MagicMock,
    ) -> None:
        """Once the control channel is dead the panel cannot learn whether
        the surfaces behind these forwards still exist."""
        pack_tab._on_disconnect()

        tunnels.close_all.assert_called_once_with()

    def test_cleanup_closes_tunnels_but_never_the_remote_surfaces(
        self,
        pack_tab: PackTab,
        tunnels: MagicMock,
    ) -> None:
        """Same rule as the daemon itself: closing the app takes down the
        local side only. The idle reaper collects what is left."""
        pack_tab.cleanup_resources()

        tunnels.close_all.assert_called_once_with()
        for call in pack_tab._transport.send_request.call_args_list:
            assert call[0][0] != "surface_close"
