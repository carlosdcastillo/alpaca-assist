"""Tests for WebViewAPI's live-surface bridge methods.

Two things matter at this seam. First, a local (Windows) tab has no remote
display, and must say so rather than raise an AttributeError at the JS
boundary — the same duck-typing get_video_chunk already does. Second,
surface_control is reachable from page JS with a caller-supplied method name,
so it must not become a way to invoke arbitrary Pack daemon RPCs.
"""
from __future__ import annotations

from unittest.mock import Mock

import pytest


@pytest.fixture
def core(tmp_path, monkeypatch):
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


@pytest.fixture
def pack_tab(core):
    """A tab with the surface surface — i.e. one that looks like a PackTab."""
    tab = Mock()
    tab.surface_open.return_value = {
        "surface_id": "srf_1a2b3c4d",
        "ws_url": "ws://127.0.0.1:51733",
        "password": "s3cr3t8",
        "width": 1280,
        "height": 800,
        "description": "xeyes",
    }
    tab.surface_attach.return_value = dict(tab.surface_open.return_value)
    tab.surface_close.return_value = {"ok": True}
    tab.surface_call.return_value = {"surfaces": [], "profiles": ["xeyes"]}
    core.tabs["pack-1"] = tab
    return tab


@pytest.fixture
def local_tab(core):
    """A local ChatTab has no surface methods at all."""
    tab = Mock(spec=["tab_id", "title", "chat_state"])
    core.tabs["local-1"] = tab
    return tab


class TestSurfaceOpen:
    def test_returns_the_tunnelled_url_to_js(self, api, pack_tab) -> None:
        result = api.surface_open("pack-1", {"profile": "xeyes"}, 1280, 800)

        assert result["success"] is True
        assert result["ws_url"] == "ws://127.0.0.1:51733"

    def test_forwards_geometry_unchanged(self, api, pack_tab) -> None:
        api.surface_open("pack-1", {"profile": "xeyes"}, 800, 600)

        pack_tab.surface_open.assert_called_once_with({"profile": "xeyes"}, 800, 600)

    def test_local_tab_gets_an_explanation_not_a_crash(self, api, local_tab) -> None:
        result = api.surface_open("local-1", {"profile": "xeyes"})

        assert result["success"] is False
        assert "Pack tab" in result["error"]

    def test_unknown_tab_reports_cleanly(self, api) -> None:
        result = api.surface_open("nope", {"profile": "xeyes"})

        assert result == {"success": False, "error": "Tab not found"}

    def test_backend_failure_is_reported_not_raised(self, api, pack_tab) -> None:
        pack_tab.surface_open.side_effect = RuntimeError("no free X display number")

        result = api.surface_open("pack-1", {"profile": "xeyes"})

        assert result == {"success": False, "error": "no free X display number"}


class TestSurfaceAttach:
    def test_attaches_by_id(self, api, pack_tab) -> None:
        result = api.surface_attach("pack-1", "srf_1a2b3c4d")

        pack_tab.surface_attach.assert_called_once_with("srf_1a2b3c4d")
        assert result["success"] is True

    def test_a_dead_surface_is_a_clean_failure(self, api, pack_tab) -> None:
        """This is the "Show panel" path on a reopened conversation. Surfaces
        are never revived, so the only correct answer is "it's gone"."""
        pack_tab.surface_attach.side_effect = RuntimeError(
            "surface srf_1a2b3c4d is no longer running",
        )

        result = api.surface_attach("pack-1", "srf_1a2b3c4d")

        assert result["success"] is False
        assert "no longer running" in result["error"]


class TestSurfaceControl:
    def test_forwards_a_known_method(self, api, pack_tab) -> None:
        result = api.surface_control("pack-1", "surface_list", {})

        pack_tab.surface_call.assert_called_once_with("surface_list", {})
        assert result["profiles"] == ["xeyes"]

    @pytest.mark.parametrize(
        "method",
        ["send_message", "seed_state", "configure_project", "read_file_chunk"],
    )
    def test_refuses_to_proxy_arbitrary_daemon_rpcs(
        self,
        api,
        pack_tab,
        method: str,
    ) -> None:
        """The method name comes from page JS. Passing it through would make
        this a general-purpose remote-call primitive."""
        result = api.surface_control("pack-1", method, {})

        assert result["success"] is False
        assert "Unsupported surface method" in result["error"]
        pack_tab.surface_call.assert_not_called()

    @pytest.mark.parametrize(
        "method",
        ["surface_open", "surface_attach", "surface_close"],
    )
    def test_refuses_the_connection_methods(self, api, pack_tab, method: str) -> None:
        """These need a tunnel built alongside them, which only the dedicated
        methods above do. Routing them here would return a remote port with
        no local route to it."""
        result = api.surface_control("pack-1", method, {})

        assert result["success"] is False
        pack_tab.surface_call.assert_not_called()

    def test_local_tab_is_refused_before_any_call(self, api, local_tab) -> None:
        result = api.surface_control("local-1", "surface_list", {})

        assert result["success"] is False
        assert "Pack tab" in result["error"]


class TestSurfaceClose:
    def test_closes_by_id(self, api, pack_tab) -> None:
        result = api.surface_close("pack-1", "srf_1a2b3c4d")

        pack_tab.surface_close.assert_called_once_with("srf_1a2b3c4d")
        assert result["success"] is True
