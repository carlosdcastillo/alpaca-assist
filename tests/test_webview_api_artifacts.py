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


def test_artifact_attach_reads_the_local_store_for_a_non_pack_tab(
    api,
    tmp_path,
) -> None:
    """A local tab has no daemon to proxy through, but the artifact bytes
    are on this machine either way — nothing about publishing or attaching
    was ever remote."""
    from core.artifact_store import ArtifactStore

    bridge, core = api
    source = tmp_path / "demo.html"
    source.write_text("<canvas id='sim'></canvas>", encoding="utf-8")
    manifest = ArtifactStore(tmp_path).publish_html(str(source), "Demo")["manifest"]
    core.tabs["local-1"] = Mock(spec=["tab_id", "chat_state"])

    result = bridge.artifact_attach("local-1", manifest["artifact_id"])

    assert result["success"] is True
    assert "<canvas id='sim'></canvas>" in result["html"]
    # The store, not the caller, is what makes this safe to serve.
    assert "Content-Security-Policy" in result["html"]


def test_artifact_attach_still_reports_an_unknown_id(api) -> None:
    bridge, core = api
    core.tabs["local-1"] = Mock(spec=["tab_id", "chat_state"])

    result = bridge.artifact_attach("local-1", "art_12345678")

    assert result["success"] is False


def test_artifact_attach_refuses_a_traversal_id(api) -> None:
    bridge, core = api
    core.tabs["local-1"] = Mock(spec=["tab_id", "chat_state"])

    result = bridge.artifact_attach("local-1", "../../etc")

    assert result["success"] is False
    assert "invalid artifact id" in result["error"]
