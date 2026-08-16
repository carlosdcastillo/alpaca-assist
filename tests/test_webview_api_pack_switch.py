"""Regression tests for refreshing remote Pack tabs on activation."""

from unittest.mock import MagicMock

from core.pack_tab import PackTab
from webview_api import WebViewAPI


def test_switching_to_online_pack_tab_refreshes_remote_state() -> None:
    app = MagicMock()
    tab = MagicMock(spec=PackTab)
    tab.offline = False
    app.core.tabs = {"pack-1": tab}
    api = WebViewAPI(app)

    result = api.switch_tab("pack-1")

    assert result == {"success": True}
    app.set_active_tab.assert_called_once_with("pack-1")
    tab.refresh_async.assert_called_once_with()
    tab.reconnect_async.assert_not_called()


def test_switching_to_offline_pack_tab_reconnects_instead_of_refreshing() -> None:
    app = MagicMock()
    tab = MagicMock(spec=PackTab)
    tab.offline = True
    app.core.tabs = {"pack-1": tab}
    api = WebViewAPI(app)

    result = api.switch_tab("pack-1")

    assert result == {"success": True}
    tab.reconnect_async.assert_called_once_with()
    tab.refresh_async.assert_not_called()
