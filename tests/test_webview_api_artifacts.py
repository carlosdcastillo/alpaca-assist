from __future__ import annotations

from unittest.mock import Mock

import pytest


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from core.app_core import AppCore
    from webview_api import WebViewAPI

    core = AppCore(api=Mock())
    core.skill_manager = Mock(skills={})
    app = Mock(core=core)
    return WebViewAPI(app), core


def test_artifact_attach_forwards_only_the_opaque_id(api) -> None:
    bridge, core = api
    tab = Mock()
    tab.artifact_attach.return_value = {
        "manifest": {"artifact_id": "art_12345678"},
        "html": "<canvas></canvas>",
    }
    core.tabs["pack-1"] = tab

    result = bridge.artifact_attach("pack-1", "art_12345678")

    tab.artifact_attach.assert_called_once_with("art_12345678")
    assert result["success"] is True
    assert result["html"] == "<canvas></canvas>"


def test_artifact_attach_rejects_a_local_tab(api) -> None:
    bridge, core = api
    core.tabs["local-1"] = Mock(spec=["tab_id", "chat_state"])

    result = bridge.artifact_attach("local-1", "art_12345678")

    assert result["success"] is False
    assert "Pack tab" in result["error"]
