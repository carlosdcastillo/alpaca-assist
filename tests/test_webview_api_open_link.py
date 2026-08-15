"""Tests for opening Markdown links outside the app WebView."""
from pathlib import Path
from unittest.mock import Mock
from unittest.mock import patch

import pytest

from webview_api import WebViewAPI


@pytest.fixture
def api() -> WebViewAPI:
    app = Mock()
    app.core.tabs = {}
    return WebViewAPI(app)


@pytest.mark.parametrize(
    "href",
    [
        "http://example.com/docs",
        "https://example.com/docs",
        "file:///tmp/README.md",
    ],
)
def test_open_link_opens_url_targets_unchanged(api: WebViewAPI, href: str) -> None:
    with patch("webview_api.webbrowser.open", return_value=True) as open_browser:
        result = api.open_link("tab-1", href)

    assert result == {"success": True}
    open_browser.assert_called_once_with(href)


def test_open_link_resolves_and_opens_a_local_file(
    api: WebViewAPI,
    tmp_path,
    monkeypatch,
) -> None:
    local_file = tmp_path / "README.md"
    local_file.write_text("# Read me", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    with patch("webview_api.webbrowser.open", return_value=True) as open_browser:
        result = api.open_link("tab-1", "README.md")

    assert result == {"success": True}
    open_browser.assert_called_once_with(local_file.as_uri())


def test_open_link_rejects_unsupported_schemes(api: WebViewAPI) -> None:
    with patch("webview_api.webbrowser.open") as open_browser:
        result = api.open_link("tab-1", "javascript:alert(1)")

    assert result == {
        "success": False,
        "error": "Unsupported link scheme: javascript",
    }
    open_browser.assert_not_called()


def test_pack_file_is_materialized_by_owning_tab_and_opened_locally(
    api: WebViewAPI,
    tmp_path: Path,
) -> None:
    from core.pack_tab import PackTab

    with patch("core.pack_tab.PackTransport"):
        tab = PackTab("pack-1", "Pack", api._app.core, 1, "worker", "session")
    local_copy = tmp_path / "cached-report.txt"
    local_copy.write_text("worker data", encoding="utf-8")
    api._app.core.tabs["pack-1"] = tab

    with patch.object(tab, "materialize_file", return_value=local_copy) as materialize:
        with patch("webview_api.webbrowser.open", return_value=True) as open_browser:
            result = api.open_link("pack-1", "reports/final.txt#L20")

    materialize.assert_called_once_with("reports/final.txt")
    open_browser.assert_called_once_with(f"{local_copy.as_uri()}#L20")
    assert result == {"success": True, "remote": True, "filename": "report.txt"}


def test_pack_file_uri_is_resolved_remotely_not_opened_as_a_local_path(
    api: WebViewAPI,
    tmp_path: Path,
) -> None:
    from core.pack_tab import PackTab

    with patch("core.pack_tab.PackTransport"):
        tab = PackTab("pack-1", "Pack", api._app.core, 1, "worker", "session")
    local_copy = tmp_path / "cached.txt"
    local_copy.write_text("worker data", encoding="utf-8")
    api._app.core.tabs["pack-1"] = tab

    with patch.object(tab, "materialize_file", return_value=local_copy) as materialize:
        with patch("webview_api.webbrowser.open", return_value=True) as open_browser:
            result = api.open_link("pack-1", "file:///srv/build/output.txt")

    materialize.assert_called_once_with("/srv/build/output.txt")
    open_browser.assert_called_once_with(local_copy.as_uri())
    assert result["remote"] is True
