"""Unit tests for ToolCallDetector."""
import pytest

from tool_call_detector import PatternMatch
from tool_call_detector import ToolCallDetector


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def detector():
    return ToolCallDetector()


# ---------------------------------------------------------------------------
# find_in_chunk - single-chunk detection
# ---------------------------------------------------------------------------


class TestFindInChunkSingleChunk:
    def test_detects_internal_format(self, detector):
        chunk = 'some text {"tool_call": {"name": "foo"}}'
        match = detector.find_in_chunk(chunk)
        assert match.start_idx == chunk.index('{"tool_call"')
        assert not match.is_cross_chunk

    def test_detects_internal_format_with_space(self, detector):
        chunk = '{ "tool_call": {"name": "bar"}}'
        match = detector.find_in_chunk(chunk)
        assert match.start_idx == 0
        assert not match.is_cross_chunk

    def test_detects_openai_format(self, detector):
        chunk = '{"tool_calls": [{"function": {"name": "x"}}]}'
        match = detector.find_in_chunk(chunk)
        assert match.start_idx == 0
        assert not match.is_cross_chunk

    def test_returns_earliest_match(self, detector):
        # Both patterns present - should return the one with the smaller index
        chunk = '{"tool_calls": []} {"tool_call": {}}'
        match = detector.find_in_chunk(chunk)
        assert match.start_idx == 0  # tool_calls comes first

    def test_no_pattern(self, detector):
        match = detector.find_in_chunk("hello world, no tool here")
        assert match.start_idx == -1
        assert not match.is_cross_chunk

    def test_empty_chunk(self, detector):
        match = detector.find_in_chunk("")
        assert match.start_idx == -1


# ---------------------------------------------------------------------------
# find_in_chunk - cross-chunk detection
# ---------------------------------------------------------------------------


class TestFindInChunkCrossChunk:
    def test_detects_pattern_spanning_boundary(self, detector):
        # '{"tool_call"' split across two chunks
        first = 'some content {"tool'
        second = '_call": {"name": "foo"}}'
        detector.advance(first)
        match = detector.find_in_chunk(second)
        # Pattern starts in buffer (cross-chunk)
        assert match.start_idx == 0
        assert match.is_cross_chunk

    def test_no_false_positive_without_open_brace(self, detector):
        # advance without any '{' - cross-chunk path should not fire
        detector.advance("no brace here")
        match = detector.find_in_chunk(
            '_call": {}',
        )  # looks like a suffix but no prior brace
        assert match.start_idx == -1

    def test_pattern_starting_in_current_chunk_after_brace(self, detector):
        # Buffer has '{', pattern fully in current chunk
        detector.advance("prefix {")
        chunk = '"tool_call": {}}'
        # The full pattern '{"tool_call"' starts at buffer[-1] + chunk[0],
        # so cross-chunk. start_idx should be 0 with is_cross_chunk=True.
        match = detector.find_in_chunk(chunk)
        assert match.start_idx == 0
        assert match.is_cross_chunk

    def test_reset_clears_cross_chunk_state(self, detector):
        detector.advance('some {"tool')
        detector.reset()
        match = detector.find_in_chunk('_call": {}}')
        # After reset the buffer is gone - no cross-chunk detection
        assert match.start_idx == -1


# ---------------------------------------------------------------------------
# is_complete_json
# ---------------------------------------------------------------------------


SIMPLE_TOOL_JSON = '{"tool_call": {"name": "my_tool", "arguments": {"x": 1}}}'


class TestIsCompleteJson:
    def test_complete_json(self, detector):
        assert detector.is_complete_json(SIMPLE_TOOL_JSON)

    def test_incomplete_json(self, detector):
        assert not detector.is_complete_json('{"tool_call": {"name": "foo"')

    def test_empty_string(self, detector):
        assert not detector.is_complete_json("")

    def test_no_tool_call_key(self, detector):
        assert not detector.is_complete_json('{"other": "value"}')

    def test_strips_tool_tags(self, detector):
        # Wrap with security tags - should still parse correctly
        tagged = f"<tool:abc123>{SIMPLE_TOOL_JSON}</tool:abc123>"
        assert detector.is_complete_json(tagged)

    def test_strips_partial_tool_tag(self, detector):
        # Opening tag only (e.g. proxy injected but didn't close yet)
        tagged = f"<tool:deadbeef-1234-5678-abcd-000000000000>{SIMPLE_TOOL_JSON}"
        assert detector.is_complete_json(tagged)

    def test_newline_indented_format(self, detector):
        indented = (
            '{\n  "tool_call": {\n    "name": "foo",\n    "arguments": {}\n  }\n}'
        )
        assert detector.is_complete_json(indented)

    def test_spaced_format(self, detector):
        spaced = '{ "tool_call": { "name": "bar", "arguments": {} } }'
        assert detector.is_complete_json(spaced)


# ---------------------------------------------------------------------------
# advance and reset
# ---------------------------------------------------------------------------


class TestAdvanceAndReset:
    def test_advance_fills_buffer(self, detector):
        detector.advance("hello")
        assert detector._partial_pattern_buffer == "hello"

    def test_advance_trims_to_buffer_size(self, detector):
        long_content = "x" * 200
        detector.advance(long_content)
        assert (
            len(detector._partial_pattern_buffer)
            == detector.PARTIAL_PATTERN_BUFFER_SIZE
        )

    def test_advance_accumulates_short_chunks(self, detector):
        detector.advance("abc")
        detector.advance("def")
        assert detector._partial_pattern_buffer == "abcdef"

    def test_reset_clears_buffer_and_brace_flag(self, detector):
        # _saw_open_brace is set by find_in_chunk (not advance)
        detector.find_in_chunk('{"tool_call')
        assert detector._saw_open_brace
        detector.reset()
        assert detector._partial_pattern_buffer == ""
        assert not detector._saw_open_brace

    def test_initial_state(self, detector):
        assert detector._partial_pattern_buffer == ""
        assert not detector._saw_open_brace
