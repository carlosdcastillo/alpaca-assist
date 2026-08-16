"""Tests for core/pack_protocol.py — the Pack tab wire format."""

from __future__ import annotations

import pytest

from core.pack_protocol import ProtocolError
from core.pack_protocol import decode_line
from core.pack_protocol import encode_error_response
from core.pack_protocol import encode_notification
from core.pack_protocol import encode_request
from core.pack_protocol import encode_response
from core.pack_protocol import is_notification
from core.pack_protocol import is_request
from core.pack_protocol import is_response


class TestEncodeDecodeRoundTrip:
    def test_request_round_trip(self) -> None:
        line = encode_request(1, "send_message", {"message": "hi", "images": []})

        assert line.endswith("\n")
        msg = decode_line(line)

        assert msg == {
            "id": 1,
            "method": "send_message",
            "params": {"message": "hi", "images": []},
        }

    def test_request_default_empty_params(self) -> None:
        msg = decode_line(encode_request(2, "stop_streaming"))

        assert msg == {"id": 2, "method": "stop_streaming", "params": {}}

    def test_response_round_trip(self) -> None:
        msg = decode_line(encode_response(5, {"answer_index": 3}))

        assert msg == {"id": 5, "result": {"answer_index": 3}}

    def test_error_response_round_trip(self) -> None:
        msg = decode_line(encode_error_response(5, "boom"))

        assert msg == {"id": 5, "error": {"message": "boom"}}

    def test_notification_round_trip(self) -> None:
        msg = decode_line(
            encode_notification("on_content_update", {"content_chunk": "hi"}),
        )

        assert msg == {"method": "on_content_update", "params": {"content_chunk": "hi"}}

    def test_notification_default_empty_params(self) -> None:
        msg = decode_line(encode_notification("on_streaming_end"))

        assert msg == {"method": "on_streaming_end", "params": {}}


class TestDecodeMalformedInput:
    def test_empty_line_raises(self) -> None:
        with pytest.raises(ProtocolError):
            decode_line("")

    def test_whitespace_only_line_raises(self) -> None:
        with pytest.raises(ProtocolError):
            decode_line("   \n")

    def test_invalid_json_raises(self) -> None:
        with pytest.raises(ProtocolError):
            decode_line("{not json")

    def test_json_array_raises(self) -> None:
        with pytest.raises(ProtocolError):
            decode_line("[1, 2, 3]")

    def test_json_scalar_raises(self) -> None:
        with pytest.raises(ProtocolError):
            decode_line("42")


class TestMessageDiscrimination:
    def test_request_is_request_only(self) -> None:
        msg = decode_line(encode_request(1, "attach"))

        assert is_request(msg) is True
        assert is_response(msg) is False
        assert is_notification(msg) is False

    def test_response_is_response_only(self) -> None:
        msg = decode_line(encode_response(1, {"ok": True}))

        assert is_request(msg) is False
        assert is_response(msg) is True
        assert is_notification(msg) is False

    def test_error_response_is_response_only(self) -> None:
        msg = decode_line(encode_error_response(1, "nope"))

        assert is_request(msg) is False
        assert is_response(msg) is True
        assert is_notification(msg) is False

    def test_notification_is_notification_only(self) -> None:
        msg = decode_line(encode_notification("update_tab_title", {"title": "x"}))

        assert is_request(msg) is False
        assert is_response(msg) is False
        assert is_notification(msg) is True
