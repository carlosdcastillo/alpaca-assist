"""Tests for pack_daemon.py — the remote-side Pack tab daemon."""
from __future__ import annotations

import socket
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from pack_daemon import PackDaemonAdapter
from pack_daemon import _absolutize_mcp_config
from pack_daemon import _bind_socket
from pack_daemon import make_dispatcher


class TestAbsolutizeMcpConfig:
    def test_relative_existing_script_is_absolutized(self, tmp_path: Path) -> None:
        (tmp_path / "server.py").write_text("# a server\n")
        raw = {"mine": {"command": ["python", "server.py"], "args": []}}

        fixed = _absolutize_mcp_config(raw, tmp_path)

        assert fixed["mine"]["command"] == [sys.executable, str(tmp_path / "server.py")]

    def test_bare_python_or_python3_rewritten_to_sys_executable(self, tmp_path: Path) -> None:
        raw = {
            "a": {"command": ["python"], "args": []},
            "b": {"command": ["python3"], "args": []},
        }

        fixed = _absolutize_mcp_config(raw, tmp_path)

        assert fixed["a"]["command"] == [sys.executable]
        assert fixed["b"]["command"] == [sys.executable]

    def test_nonexistent_relative_path_left_unchanged(self, tmp_path: Path) -> None:
        raw = {"win": {"command": ["C:/tools/thing.exe"], "args": ["serve"]}}

        fixed = _absolutize_mcp_config(raw, tmp_path)

        assert fixed["win"]["command"] == ["C:/tools/thing.exe"]

    def test_already_absolute_path_left_unchanged(self, tmp_path: Path) -> None:
        script = tmp_path / "abs_server.py"
        script.write_text("# a server\n")
        raw = {"mine": {"command": [str(script)], "args": []}}

        fixed = _absolutize_mcp_config(raw, tmp_path)

        assert fixed["mine"]["command"] == [str(script)]

    def test_preserves_other_entry_fields(self, tmp_path: Path) -> None:
        raw = {"mine": {"command": ["python"], "args": ["-u"], "disabled_tools": ["x"]}}

        fixed = _absolutize_mcp_config(raw, tmp_path)

        assert fixed["mine"]["args"] == ["-u"]
        assert fixed["mine"]["disabled_tools"] == ["x"]


class TestPackDaemonAdapter:
    def test_notify_dropped_silently_when_nobody_attached(self) -> None:
        adapter = PackDaemonAdapter()

        # No connection set — must not raise.
        adapter.on_streaming_start("tab-1", 0)
        adapter.on_content_update("tab-1", MagicMock(_asdict=lambda: {"content_chunk": "hi"}))
        adapter.on_error("tab-1", "boom")

    def test_notify_forwards_to_attached_connection(self) -> None:
        adapter = PackDaemonAdapter()
        conn = MagicMock()
        adapter.set_connection(conn)

        adapter.on_streaming_start("tab-1", 2)

        conn.send_notification.assert_called_once_with(
            "on_streaming_start",
            {"tab_id": "tab-1", "answer_index": 2},
        )

    def test_on_content_update_serializes_via_asdict(self) -> None:
        adapter = PackDaemonAdapter()
        conn = MagicMock()
        adapter.set_connection(conn)
        update = MagicMock()
        update._asdict.return_value = {"content_chunk": "hello", "is_done": False}

        adapter.on_content_update("tab-1", update)

        conn.send_notification.assert_called_once_with(
            "on_content_update",
            {"tab_id": "tab-1", "update": {"content_chunk": "hello", "is_done": False}},
        )

    def test_fold_event_create_wait_set_sequence(self) -> None:
        adapter = PackDaemonAdapter()
        conn = MagicMock()
        adapter.set_connection(conn)

        adapter.inject_tool_fold("tab-1", "fold-1", "result", "body", 0)
        conn.send_notification.assert_called_once_with(
            "inject_tool_fold",
            {
                "tab_id": "tab-1",
                "fold_id": "fold-1",
                "fold_type": "result",
                "body_text": "body",
                "answer_index": 0,
            },
        )

        # Simulate the local side confirming render on a background thread,
        # then verify wait_for_fold_rendered unblocks promptly and truthily.
        def confirm() -> None:
            time.sleep(0.05)
            adapter.fold_rendered("tab-1", "fold-1", True)

        threading.Thread(target=confirm).start()
        rendered = adapter.wait_for_fold_rendered("tab-1", "fold-1", timeout=2.0)

        assert rendered is True

    def test_wait_for_fold_rendered_times_out_without_confirmation(self) -> None:
        adapter = PackDaemonAdapter()
        adapter.inject_tool_fold("tab-1", "fold-2", "call", "body", 0)

        rendered = adapter.wait_for_fold_rendered("tab-1", "fold-2", timeout=0.1)

        assert rendered is False

    def test_wait_for_fold_rendered_unknown_fold_returns_false(self) -> None:
        adapter = PackDaemonAdapter()

        assert adapter.wait_for_fold_rendered("tab-1", "never-injected", timeout=0.1) is False

    def test_on_new_qa_turn_increments_per_tab_independently(self) -> None:
        adapter = PackDaemonAdapter()

        assert adapter.on_new_qa_turn("tab-1") == 0
        assert adapter.on_new_qa_turn("tab-1") == 1
        assert adapter.on_new_qa_turn("tab-2") == 0

    def test_on_new_qa_turn_never_touches_connection(self) -> None:
        adapter = PackDaemonAdapter()
        conn = MagicMock()
        adapter.set_connection(conn)

        adapter.on_new_qa_turn("tab-1")

        conn.send_notification.assert_not_called()


class TestPackDaemonAdapterTokenStats:
    """Regression coverage: webview_api.get_status_info() reads

    session_output_tokens/session_input_tokens/session_cached_input_tokens/
    last_invocation_metrics as plain attributes on whatever tab object is
    in core.tabs. PackTab (the local proxy) never had these on its own —
    nothing pushed them across the wire — so the status bar silently fell
    back to a crude chars/4 estimate for every Pack tab, on every turn.
    """

    def _tab_with_stats(self) -> MagicMock:
        tab = MagicMock()
        tab.session_output_tokens = 1234
        tab.session_input_tokens = 5678
        tab.session_cached_input_tokens = 90
        tab.last_invocation_metrics = {"output_token_count": 1234}
        return tab

    def test_token_stats_empty_before_set_tab(self) -> None:
        adapter = PackDaemonAdapter()

        assert adapter._token_stats() == {}

    def test_token_stats_reads_the_real_tabs_live_attributes(self) -> None:
        adapter = PackDaemonAdapter()
        adapter.set_tab(self._tab_with_stats())

        stats = adapter._token_stats()

        assert stats["session_output_tokens"] == 1234
        assert stats["session_input_tokens"] == 5678
        assert stats["session_cached_input_tokens"] == 90
        assert stats["last_invocation_metrics"] == {"output_token_count": 1234}

    def test_on_streaming_end_notification_includes_token_stats(self) -> None:
        adapter = PackDaemonAdapter()
        adapter.set_tab(self._tab_with_stats())
        conn = MagicMock()
        adapter.set_connection(conn)

        adapter.on_streaming_end("tab-1", 0)

        params = conn.send_notification.call_args[0][1]
        assert params["session_output_tokens"] == 1234
        assert params["answer_index"] == 0

    def test_on_error_notification_includes_token_stats(self) -> None:
        """A turn can end via error mid-generation with partial output

        already billed — the partial count must still reach the local
        side, not just the clean streaming_end path.
        """
        adapter = PackDaemonAdapter()
        adapter.set_tab(self._tab_with_stats())
        conn = MagicMock()
        adapter.set_connection(conn)

        adapter.on_error("tab-1", "boom")

        params = conn.send_notification.call_args[0][1]
        assert params["session_output_tokens"] == 1234


class TestMakeDispatcher:
    def _tab(self) -> MagicMock:
        tab = MagicMock()
        tab.tab_id = "tab-1"
        tab.title = "Pack Tab"
        tab.is_streaming = False
        tab._current_answer_index = 3
        tab.get_serializable_data.return_value = {"chat_state": {}, "name": "Pack Tab"}
        tab.compact_conversation.return_value = {"compacted": True}
        tab.truncate_conversation.return_value = {"truncated": True}
        tab.pop_conversation.return_value = {"popped": False, "reason": "empty"}
        return tab

    def _core(self) -> MagicMock:
        return MagicMock()

    def test_attach_reports_resumed_flag(self) -> None:
        tab = self._tab()
        adapter = PackDaemonAdapter()
        dispatch = make_dispatcher(tab, adapter, resumed=True, core=self._core())

        result = dispatch("attach", {})

        assert result["resumed"] is True
        assert result["title"] == "Pack Tab"
        assert result["is_streaming"] is False
        assert result["state"] == {"chat_state": {}, "name": "Pack Tab"}

    def test_attach_includes_token_stats_once_the_adapter_has_a_tab(self) -> None:
        """A reattach (or history revival) must show the real cumulative

        usage the remote ChatTab already has, not zero until the next
        turn happens to complete.
        """
        tab = self._tab()
        tab.session_output_tokens = 4321
        adapter = PackDaemonAdapter()
        adapter.set_tab(tab)
        dispatch = make_dispatcher(tab, adapter, resumed=True, core=self._core())

        result = dispatch("attach", {})

        assert result["session_output_tokens"] == 4321

    def test_only_the_first_attach_reports_resumed_false(self) -> None:
        """resumed=False means "this process found nothing persisted when

        it started" — true only for the very first attach. A daemon that
        started fresh (every brand-new Pack tab) must not keep reporting
        resumed=False on every later attach in its own lifetime, or a
        plain reconnect (app restart, brief network blip) to a perfectly
        healthy, continuously-running session would wrongly look like a
        lost one.
        """
        tab = self._tab()
        dispatch = make_dispatcher(tab, PackDaemonAdapter(), resumed=False, core=self._core())

        first = dispatch("attach", {})
        second = dispatch("attach", {})
        third = dispatch("attach", {})

        assert first["resumed"] is False
        assert second["resumed"] is True
        assert third["resumed"] is True

    def test_send_message_returns_answer_index_set_before_return(self) -> None:
        tab = self._tab()
        dispatch = make_dispatcher(tab, PackDaemonAdapter(), resumed=False, core=self._core())

        result = dispatch("send_message", {"message": "hi", "images": []})

        tab.handle_user_message.assert_called_once_with("hi", [])
        assert result == {"answer_index": 3}

    def test_stop_streaming_dispatches(self) -> None:
        tab = self._tab()
        dispatch = make_dispatcher(tab, PackDaemonAdapter(), resumed=False, core=self._core())

        result = dispatch("stop_streaming", {})

        tab.stop_streaming.assert_called_once()
        assert result == {"success": True}

    def test_set_model_updates_tab_preferences_and_persists(self) -> None:
        tab = self._tab()
        tab.preferences = {"model": "old-model"}
        core = self._core()
        dispatch = make_dispatcher(tab, PackDaemonAdapter(), resumed=False, core=core)

        result = dispatch("set_model", {"model": "new-model"})

        assert result == {"success": True}
        assert tab.preferences["model"] == "new-model"
        core.save_preferences.assert_called_once_with()

    def test_gated_tool_output_dispatches_with_remote_tab_id(self) -> None:
        tab = self._tab()
        dispatch = make_dispatcher(tab, PackDaemonAdapter(), resumed=False, core=self._core())

        with pytest.MonkeyPatch.context() as monkeypatch:
            reader = MagicMock(return_value="full media")
            monkeypatch.setattr("pack_daemon.read_gated_tool_output", reader)
            result = dispatch("read_gated_tool_output", {"gated_text": "placeholder"})

        assert result == {"content": "full media"}
        reader.assert_called_once_with("placeholder", "tab-1")

    def test_mutating_methods_dispatch_to_real_tab_methods(self) -> None:
        tab = self._tab()
        dispatch = make_dispatcher(tab, PackDaemonAdapter(), resumed=False, core=self._core())

        assert dispatch("compact_conversation", {}) == {"compacted": True}
        assert dispatch("truncate_conversation", {}) == {"truncated": True}
        assert dispatch("pop_conversation", {}) == {"popped": False, "reason": "empty"}

    def test_fold_rendered_sets_the_adapter_event(self) -> None:
        tab = self._tab()
        adapter = PackDaemonAdapter()
        adapter.inject_tool_fold("tab-1", "fold-1", "result", "body", 0)
        dispatch = make_dispatcher(tab, adapter, resumed=False, core=self._core())

        dispatch("fold_rendered", {"tab_id": "tab-1", "fold_id": "fold-1", "rendered": True})

        assert adapter.wait_for_fold_rendered("tab-1", "fold-1", timeout=0.1) is True

    def test_unknown_method_raises(self) -> None:
        tab = self._tab()
        dispatch = make_dispatcher(tab, PackDaemonAdapter(), resumed=False, core=self._core())

        with pytest.raises(ValueError):
            dispatch("not_a_real_method", {})

    def test_seed_state_loads_data_persists_and_flips_resumed(self) -> None:
        """The local side calls this to recreate a session on a daemon that

        reported resumed=False. Must load the seed into the real tab,
        save immediately (so a crash before the next autosave doesn't
        lose it and repeat the same prompt), and report resumed=True to
        any later attach in this same daemon lifetime.
        """
        tab = self._tab()
        core = self._core()
        dispatch = make_dispatcher(tab, PackDaemonAdapter(), resumed=False, core=core)
        seed = {"chat_state": {"questions": ["Q"], "answers": ["A"]}, "name": "Revived"}

        result = dispatch("seed_state", {"seed": seed})

        tab.load_from_data.assert_called_once_with(seed)
        core.save_session.assert_called_once()
        assert result == {"success": True}
        assert dispatch("attach", {})["resumed"] is True


class TestBindSocket:
    def test_binds_fresh_socket(self, tmp_path: Path) -> None:
        sock_path = tmp_path / "daemon.sock"

        listener = _bind_socket(sock_path)
        try:
            assert sock_path.exists()
        finally:
            listener.close()

    def test_clears_stale_socket_file(self, tmp_path: Path) -> None:
        sock_path = tmp_path / "daemon.sock"
        # Create a bound-but-unlistened socket file to simulate a crashed
        # daemon that left its socket special file behind.
        stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        stale.bind(str(sock_path))
        stale.close()  # closed without listen()/accept() — nothing answers it

        listener = _bind_socket(sock_path)
        try:
            assert sock_path.exists()
        finally:
            listener.close()

    def test_refuses_to_bind_over_a_live_listener(self, tmp_path: Path) -> None:
        sock_path = tmp_path / "daemon.sock"
        live = _bind_socket(sock_path)
        try:
            with pytest.raises(RuntimeError):
                _bind_socket(sock_path)
        finally:
            live.close()
