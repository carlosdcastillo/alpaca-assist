"""Tests for opening Markdown links outside the app WebView."""
from unittest.mock import Mock
from unittest.mock import patch

import pytest

from webview_api import WebViewAPI


@pytest.fixture
def api() -> WebViewAPI:
    return WebViewAPI(Mock())


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
        result = api.open_link(href)

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
        result = api.open_link("README.md")

    assert result == {"success": True}
    open_browser.assert_called_once_with(local_file.as_uri())


def test_open_link_rejects_unsupported_schemes(api: WebViewAPI) -> None:
    with patch("webview_api.webbrowser.open") as open_browser:
        result = api.open_link("javascript:alert(1)")

    assert result == {
        "success": False,
        "error": "Unsupported link scheme: javascript",
    }
    open_browser.assert_not_called()
