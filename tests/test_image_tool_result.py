"""Tests for image_tool_result.py's sentinel encode/decode."""
from __future__ import annotations

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
        # No literal NUL bytes — a coincidental textual mention shouldn't match.
        assert parse_image_result("ALPACA_IMAGE_RESULT is just a string here") is None


class TestParseHandlesMalformedPayload:
    def test_sentinel_with_too_few_parts_returns_none(self) -> None:
        malformed = SENTINEL + "image/png\x00onlyonefield"
        assert parse_image_result(malformed) is None

    def test_sentinel_with_no_data_after_it_returns_none(self) -> None:
        malformed = SENTINEL
        assert parse_image_result(malformed) is None

    def test_extra_nul_bytes_in_description_are_preserved(self) -> None:
        # split(..., 2) caps at 3 parts, so a NUL inside the description
        # itself (however unlikely) stays part of the description rather
        # than truncating it.
        encoded = SENTINEL + "image/png\x00QUJD\x00desc\x00withNUL"
        result = parse_image_result(encoded)
        assert result == ("image/png", "QUJD", "desc\x00withNUL")
