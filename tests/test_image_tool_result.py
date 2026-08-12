"""Tests for image_tool_result.py's sentinel encode/decode."""
from __future__ import annotations

import json

from image_tool_result import encode_image_result
from image_tool_result import parse_image_result
from image_tool_result import SENTINEL


class TestEncodeDecodeRoundTrip:
    def test_round_trip(self) -> None:
        encoded = encode_image_result("image/png", "QUJD", "a small test image")
        result = parse_image_result(encoded)
        assert result == ("image/png", "QUJD", "a small test image")

    def test_round_trip_with_empty_description(self) -> None:
        encoded = encode_image_result("image/jpeg", "Zm9v", "")
        assert parse_image_result(encoded) == ("image/jpeg", "Zm9v", "")


class TestParseFindsSentinelAnywhere:
    """Callers prepend their own text (e.g. 'Tool execution result:\\n')
    before the stored content, so parsing must not assume the sentinel is
    at position 0.
    """

    def test_sentinel_with_prefix_text(self) -> None:
        encoded = encode_image_result("image/png", "QUJD", "desc")
        prefixed = f"Tool execution result:\n{encoded}"
        assert parse_image_result(prefixed) == ("image/png", "QUJD", "desc")

    def test_sentinel_with_lots_of_prefix_text(self) -> None:
        encoded = encode_image_result("image/png", "QUJD", "desc")
        prefixed = ("x" * 5000) + encoded
        assert parse_image_result(prefixed) == ("image/png", "QUJD", "desc")


class TestParseReturnsNoneForOrdinaryText:
    def test_plain_text_returns_none(self) -> None:
        assert parse_image_result("just some ordinary tool output") is None

    def test_empty_string_returns_none(self) -> None:
        assert parse_image_result("") is None

    def test_text_mentioning_sentinel_word_without_actual_sentinel(self) -> None:
        # "ALPACA_IMAGE_RESULT" alone, without the surrounding "@@" marks
        # that make up the real SENTINEL, shouldn't match.
        assert parse_image_result("ALPACA_IMAGE_RESULT is just a string here") is None


class TestParseHandlesMalformedPayload:
    def test_sentinel_with_too_few_parts_returns_none(self) -> None:
        malformed = SENTINEL + "image/png@@ALPACA_FIELD@@onlyonefield"
        assert parse_image_result(malformed) is None

    def test_sentinel_with_no_data_after_it_returns_none(self) -> None:
        malformed = SENTINEL
        assert parse_image_result(malformed) is None

    def test_extra_field_separators_in_description_are_preserved(self) -> None:
        # split(..., 2) caps at 3 parts, so the field separator appearing
        # again inside the description itself (however unlikely) stays
        # part of the description rather than truncating it.
        encoded = SENTINEL + "image/png@@ALPACA_FIELD@@QUJD@@ALPACA_FIELD@@desc@@ALPACA_FIELD@@more"
        result = parse_image_result(encoded)
        assert result == ("image/png", "QUJD", "desc@@ALPACA_FIELD@@more")


class TestSurvivesJsonRoundTrip:
    """The actual bug this delimiter choice fixes: every tool result is
    json.dumps()'d before storage (core/chat_tab_tools.py's _execute_tool),
    and JSON string encoding escapes control characters like the old
    NUL-byte delimiter into literal \\uXXXX sequences — silently breaking
    a raw-byte search on the other side. Confirmed live: the model
    receiving the broken output correctly reported seeing raw base64 text
    with "ALPACA_IMAGE_RESULT" leaking through as a literal substring,
    not an image. Printable ASCII survives json.dumps() unchanged, so
    this is the actual regression guard for that failure.
    """

    def test_sentinel_survives_json_dumps(self) -> None:
        encoded = encode_image_result("image/jpeg", "QUJDRA==", "a screenshot")
        # Exactly what _execute_tool does: wrap in the tool-result envelope,
        # then json.dumps() it before it's ever stored.
        wrapped = {"content": [{"type": "text", "text": encoded}], "isError": False}
        stored = json.dumps(wrapped, indent=2)

        assert parse_image_result(stored) is not None
        mime_type, base64_data, description = parse_image_result(stored)
        assert mime_type == "image/jpeg"
        assert base64_data == "QUJDRA=="
        assert description == "a screenshot"

    def test_sentinel_survives_json_dumps_plus_replay_prefix(self) -> None:
        """The full real path: json.dumps() at storage time, then
        "Tool execution result:\\n" prepended at replay time
        (prepare_continuation_messages) — both must survive together.
        """
        encoded = encode_image_result("image/png", "QUJD", "desc")
        wrapped = {"content": [{"type": "text", "text": encoded}], "isError": False}
        stored = json.dumps(wrapped, indent=2)
        replayed = f"Tool execution result:\n{stored}"

        assert parse_image_result(replayed) == ("image/png", "QUJD", "desc")
