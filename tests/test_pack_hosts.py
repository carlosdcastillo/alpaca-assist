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
        (tmp_path / "pack.json").write_text("not valid json {{")

        result = api_in.get_pack_hosts()

        assert result == {"success": True, "hosts": []}


class TestLookupPackDisplayName:
    """The status-bar badge uses _lookup_pack_display_name to resolve a
    pack host's IP/hostname into the human-friendly label from pack.json.

    Callers must be able to use the return value directly as a label
    without their own fallback — the only case with nothing to show is no
    hostname at all.
    """

    def test_returns_display_name_for_known_host(
        self,
        api_in: WebViewAPI,
        tmp_path,
    ) -> None:
        (tmp_path / "pack.json").write_text(
            json.dumps(
                [{"hostname": "192.168.0.58", "display_name": "Deimos"}],
            ),
        )

        assert api_in._lookup_pack_display_name("192.168.0.58") == "Deimos"

    def test_returns_hostname_itself_for_unknown_host(
        self,
        api_in: WebViewAPI,
        tmp_path,
    ) -> None:
        (tmp_path / "pack.json").write_text(
            json.dumps(
                [{"hostname": "192.168.0.58", "display_name": "Deimos"}],
            ),
        )

        assert api_in._lookup_pack_display_name("10.0.0.99") == "10.0.0.99"

    def test_returns_hostname_itself_when_file_missing(
        self,
        api_in: WebViewAPI,
    ) -> None:
        assert api_in._lookup_pack_display_name("192.168.0.58") == "192.168.0.58"

    def test_returns_none_for_none_input(self, api_in: WebViewAPI) -> None:
        assert api_in._lookup_pack_display_name(None) is None


class TestReadPackHostsCaching:
    """_read_pack_hosts caches by (path, mtime) so the frequent status-bar
    poll doesn't re-read pack.json on every call, but still picks up edits
    made while the app is running.
    """

    def test_repeated_reads_without_changes_return_equal_data(
        self,
        api_in: WebViewAPI,
        tmp_path,
    ) -> None:
        (tmp_path / "pack.json").write_text(
            json.dumps([{"hostname": "192.168.0.58", "display_name": "Deimos"}]),
        )

        first = api_in.get_pack_hosts()
        second = api_in.get_pack_hosts()

        assert first == second

    def test_edit_after_read_is_picked_up_on_next_call(
        self,
        api_in: WebViewAPI,
        tmp_path,
    ) -> None:
        pack_file = tmp_path / "pack.json"
        pack_file.write_text(
            json.dumps([{"hostname": "192.168.0.58", "display_name": "Deimos"}]),
        )
        assert api_in._lookup_pack_display_name("192.168.0.58") == "Deimos"

        pack_file.write_text(
            json.dumps([{"hostname": "192.168.0.58", "display_name": "Renamed"}]),
        )
        # Force a distinct mtime — some filesystems have coarse (1s)
        # mtime resolution, and two writes in quick succession could
        # otherwise land on the same timestamp and defeat this test.
        new_mtime = os.path.getmtime(pack_file) + 5
        os.utime(pack_file, (new_mtime, new_mtime))

        assert api_in._lookup_pack_display_name("192.168.0.58") == "Renamed"
