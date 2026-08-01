"""Tests for webview_api.get_pack_hosts — the pack.json quick-pick

list surfaced by the "New Pack Tab..." UI.
"""
from __future__ import annotations

import json
import os

import pytest

from webview_api import WebViewAPI


@pytest.fixture
def api_in(tmp_path, monkeypatch):
    """A WebViewAPI whose cwd is a fresh tmp dir, matching how

    core/config.PACK_FILE is read as a cwd-relative path.
    """
    original = os.getcwd()
    os.chdir(tmp_path)
    yield WebViewAPI(object())
    os.chdir(original)


class TestGetPackHosts:
    def test_missing_file_returns_empty_list(self, api_in: WebViewAPI) -> None:
        result = api_in.get_pack_hosts()

        assert result == {"success": True, "hosts": []}

    def test_reads_hosts_from_the_file(self, api_in: WebViewAPI, tmp_path) -> None:
        (tmp_path / "pack.json").write_text(
            json.dumps(
                [
                    {"hostname": "192.168.0.58", "display_name": "Deimos"},
                    {"hostname": "user@otherhost", "display_name": "Other Box"},
                ],
            ),
        )

        result = api_in.get_pack_hosts()

        assert result == {
            "success": True,
            "hosts": [
                {"hostname": "192.168.0.58", "display_name": "Deimos"},
                {"hostname": "user@otherhost", "display_name": "Other Box"},
            ],
        }

    def test_missing_display_name_falls_back_to_hostname(
        self,
        api_in: WebViewAPI,
        tmp_path,
    ) -> None:
        (tmp_path / "pack.json").write_text(
            json.dumps([{"hostname": "192.168.0.58"}]),
        )

        result = api_in.get_pack_hosts()

        assert result["hosts"] == [
            {"hostname": "192.168.0.58", "display_name": "192.168.0.58"},
        ]

    def test_entry_without_hostname_is_skipped(
        self,
        api_in: WebViewAPI,
        tmp_path,
    ) -> None:
        (tmp_path / "pack.json").write_text(
            json.dumps(
                [
                    {"display_name": "No hostname here"},
                    {"hostname": "192.168.0.58", "display_name": "Deimos"},
                ],
            ),
        )

        result = api_in.get_pack_hosts()

        assert result["hosts"] == [
            {"hostname": "192.168.0.58", "display_name": "Deimos"},
        ]

    def test_non_dict_entries_are_skipped(self, api_in: WebViewAPI, tmp_path) -> None:
        (tmp_path / "pack.json").write_text(
            json.dumps(["192.168.0.58", {"hostname": "otherhost"}]),
        )

        result = api_in.get_pack_hosts()

        assert result["hosts"] == [
            {"hostname": "otherhost", "display_name": "otherhost"},
        ]

    def test_non_list_content_returns_empty_list(
        self,
        api_in: WebViewAPI,
        tmp_path,
    ) -> None:
        (tmp_path / "pack.json").write_text(json.dumps({"not": "a list"}))

        result = api_in.get_pack_hosts()

        assert result == {"success": True, "hosts": []}

    def test_malformed_json_returns_empty_list_not_an_error(
        self,
        api_in: WebViewAPI,
        tmp_path,
    ) -> None:
        (tmp_path / "pack.json").write_text("not valid json {{[")

        result = api_in.get_pack_hosts()

        assert result == {"success": True, "hosts": []}
