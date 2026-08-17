"""Tests for WebViewAPI.get_status_info pack/local context fields.

Covers the status-bar improvement: the API must report whether a tab is a
local chat or a Pack tab, and for Pack tabs expose the host and whether the
connection is currently up.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest


@pytest.fixture
def core(tmp_path, monkeypatch):
    """Real AppCore with a temp preferences path, plus a stub skill manager."""
    monkeypatch.chdir(tmp_path)
    from core.app_core import AppCore

    instance = AppCore(api=Mock())
    instance.skill_manager = Mock()
    instance.skill_manager.skills = {}
    return instance


@pytest.fixture
def api(core):
    from webview_api import WebViewAPI

    mock_app = Mock()
    mock_app.core = core
    return WebViewAPI(mock_app)


class _FakeChatState:
    def __init__(self, text: str = ""):
        self._text = text

    def get_display_text(self) -> str:
        return self._text


class TestGetStatusInfoLocal:
    def test_local_tab_reports_is_pack_false(self, api, core) -> None:
        tab = Mock()
        tab.chat_state = _FakeChatState("hello")
        tab.last_invocation_metrics = None
        tab.session_output_tokens = 0
        tab.session_input_tokens = 0
        tab.session_cached_input_tokens = 0
        # No host attribute -> local tab
        del tab.host
        core.tabs["tab-1"] = tab

        result = api.get_status_info("tab-1")

        assert result["success"] is True
        assert result["is_pack"] is False
        assert "host" not in result
        assert "connected" not in result

    def test_local_tab_includes_chat_stats(self, api, core) -> None:
        tab = Mock()
        tab.chat_state = _FakeChatState("hello\nworld")
        tab.last_invocation_metrics = None
        tab.session_output_tokens = 0
        tab.session_input_tokens = 0
        tab.session_cached_input_tokens = 0
        del tab.host
        core.tabs["tab-1"] = tab

        result = api.get_status_info("tab-1")

        assert result["char_count"] == 11
        assert result["line_count"] == 2
        assert result["token_estimate"] == 3


class TestGetStatusInfoPack:
    def test_pack_tab_reports_host_and_connected(self, api, core) -> None:
        tab = Mock()
        tab.chat_state = _FakeChatState("")
        tab.last_invocation_metrics = None
        tab.session_output_tokens = 0
        tab.session_input_tokens = 0
        tab.session_cached_input_tokens = 0
        tab.host = "build-server-01"
        tab.offline = False
        tab.session_id = "sess-123"
        core.tabs["tab-pack"] = tab

        result = api.get_status_info("tab-pack")

        assert result["success"] is True
        assert result["is_pack"] is True
        assert result["host"] == "build-server-01"
        assert result["connected"] is True
        assert result["session_id"] == "sess-123"
        # No pack.json in this tab's (isolated, empty) cwd — display_name
        # must still fall back to the raw host, not be missing or None.
        assert result["display_name"] == "build-server-01"

    def test_pack_tab_reports_display_name_from_pack_json(
        self,
        api,
        core,
        tmp_path,
    ) -> None:
        import json

        (tmp_path / "pack.json").write_text(
            json.dumps([{"hostname": "192.168.0.58", "display_name": "Deimos"}]),
        )
        tab = Mock()
        tab.chat_state = _FakeChatState("")
        tab.last_invocation_metrics = None
        tab.session_output_tokens = 0
        tab.session_input_tokens = 0
        tab.session_cached_input_tokens = 0
        tab.host = "192.168.0.58"
        tab.offline = False
        tab.session_id = "sess-123"
        core.tabs["tab-pack"] = tab

        result = api.get_status_info("tab-pack")

        assert result["display_name"] == "Deimos"

    def test_pack_tab_reports_disconnected_when_offline(self, api, core) -> None:
        tab = Mock()
        tab.chat_state = _FakeChatState("")
        tab.last_invocation_metrics = None
        tab.session_output_tokens = 0
        tab.session_input_tokens = 0
        tab.session_cached_input_tokens = 0
        tab.host = "build-server-01"
        tab.offline = True
        tab.session_id = None
        core.tabs["tab-pack"] = tab

        result = api.get_status_info("tab-pack")

        assert result["is_pack"] is True
        assert result["connected"] is False

    def test_missing_tab_returns_error(self, api) -> None:
        result = api.get_status_info("no-such-tab")

        assert result["success"] is False
        assert "error" in result


class TestGetWorkspaceChanges:
    def test_pack_tab_returns_status_and_diffs(self, api, core) -> None:
        from core.pack_tab import PackTab

        tab = Mock(spec=PackTab)
        tab.get_workspace_changes.return_value = {
            "is_git": True,
            "branch": "main",
            "entries": [{"path": "core/app.py", "diff": "@@\n+added\n"}],
        }
        core.tabs["tab-pack"] = tab

        result = api.get_workspace_changes("tab-pack")

        assert result["success"] is True
        assert result["branch"] == "main"
        assert result["entries"][0]["path"] == "core/app.py"

    def test_local_tab_is_refused(self, api, core) -> None:
        core.tabs["tab-1"] = Mock()

        result = api.get_workspace_changes("tab-1")

        assert result["success"] is False
        assert "Pack" in result["error"]

    def test_missing_tab_returns_error(self, api) -> None:
        result = api.get_workspace_changes("no-such-tab")

        assert result["success"] is False
        assert result["error"] == "Tab not found"
