"""Tests for core/surface_protocol.py's sentinel encode/parse.

The surface sentinel carries a *descriptor* and never bytes, so the thing
worth testing hard is the parser's willingness to say "this isn't a surface"
— a malformed or truncated result must render as ordinary text rather than
produce a card pointing at a session that never existed.
"""
from __future__ import annotations

from core.surface_protocol import encode_surface_result
from core.surface_protocol import parse_surface_result
from core.surface_protocol import SENTINEL


class TestEncodeDecodeRoundTrip:
    def test_round_trip(self) -> None:
        encoded = encode_surface_result("srf_1a2b3c4d", 1280, 800, "gedit")
        assert parse_surface_result(encoded) == ("srf_1a2b3c4d", 1280, 800, "gedit")

    def test_round_trip_with_empty_description(self) -> None:
        encoded = encode_surface_result("srf_00000000", 640, 480, "")
        assert parse_surface_result(encoded) == ("srf_00000000", 640, 480, "")

    def test_description_may_contain_spaces_and_punctuation(self) -> None:
        encoded = encode_surface_result("srf_deadbeef", 800, 600, "GIMP 2.10 (test)")
        assert parse_surface_result(encoded) == (
            "srf_deadbeef",
            800,
            600,
            "GIMP 2.10 (test)",
        )


class TestParseFindsSentinelAnywhere:
    """Callers prepend their own text before the stored content, so parsing
    must not assume the sentinel sits at position 0.
    """

    def test_sentinel_with_prefix_text(self) -> None:
        encoded = encode_surface_result("srf_1a2b3c4d", 1280, 800, "gedit")
        prefixed = f"Tool execution result:\n{encoded}"
        assert parse_surface_result(prefixed) == ("srf_1a2b3c4d", 1280, 800, "gedit")


class TestStorageEnvelopeQuoteTrimming:
    """The last field has no closing delimiter, so when the result arrives
    wrapped in the {"content": [...]} storage envelope the envelope's own
    quote leaks into the description and has to be cut.
    """

    def test_trailing_envelope_quote_removed(self) -> None:
        encoded = encode_surface_result("srf_1a2b3c4d", 1280, 800, "gedit")
        wrapped = f'{{"content": [{{"text": "{encoded}"}}]}}'
        assert parse_surface_result(wrapped) == ("srf_1a2b3c4d", 1280, 800, "gedit")

    def test_escaped_quote_backslash_removed_with_the_quote(self) -> None:
        encoded = encode_surface_result("srf_1a2b3c4d", 1280, 800, "gedit\\")
        assert parse_surface_result(f'{encoded}"') == (
            "srf_1a2b3c4d",
            1280,
            800,
            "gedit",
        )


class TestParseRejectsMalformed:
    def test_plain_text_returns_none(self) -> None:
        assert parse_surface_result("just an ordinary tool result") is None

    def test_missing_fields_returns_none(self) -> None:
        assert parse_surface_result(f"{SENTINEL}srf_1a2b3c4d") is None

    def test_bad_surface_id_returns_none(self) -> None:
        assert (
            parse_surface_result(
                encode_surface_result("not-a-surface", 800, 600, "x"),
            )
            is None
        )

    def test_uppercase_hex_in_id_returns_none(self) -> None:
        assert (
            parse_surface_result(
                encode_surface_result("srf_1A2B3C4D", 800, 600, "x"),
            )
            is None
        )

    def test_bad_geometry_returns_none(self) -> None:
        assert (
            parse_surface_result(
                f"{SENTINEL}srf_1a2b3c4d@@ALPACA_FIELD@@bigxsmall@@ALPACA_FIELD@@x",
            )
            is None
        )

    def test_zero_dimension_returns_none(self) -> None:
        assert (
            parse_surface_result(
                encode_surface_result("srf_1a2b3c4d", 0, 600, "x"),
            )
            is None
        )

    def test_truncated_mid_sentinel_returns_none(self) -> None:
        encoded = encode_surface_result("srf_1a2b3c4d", 1280, 800, "gedit")
        assert parse_surface_result(encoded[: len(SENTINEL) - 4]) is None
