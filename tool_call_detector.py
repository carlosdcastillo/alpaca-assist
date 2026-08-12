"""Streaming tool-call detection with cross-chunk pattern support."""
import json
import re
from dataclasses import dataclass
from typing import Any

# Pattern for security wrapper tags: <tool:UUID> / </tool:UUID>
TOOL_TAG_PATTERN = re.compile(r"</?tool(?::[a-f0-9-]+)?>")


@dataclass
class PatternMatch:
    """Result of ToolCallDetector.find_in_chunk()."""

    start_idx: int = -1  # position in current chunk; -1 means not found
    is_cross_chunk: bool = False  # True when pattern started in a previous chunk


class ToolCallDetector:
    """Detect tool-call JSON patterns in streaming text with cross-chunk support.

    Usage pattern inside a streaming loop::

        detector = ToolCallDetector()
        for chunk in stream:
            match = detector.find_in_chunk(chunk)
            if match.start_idx != -1 and not in_tool_call:
                # start buffering
                detector.reset()
            elif in_tool_call and detector.is_complete_json(buffer):
                # handle complete tool call
                detector.reset()
            else:
                # normal display chunk
                detector.advance(chunk)
    """

    # Patterns that can start a tool call (internal and OpenAI formats)
    TOOL_CALL_START_PATTERNS: list[str] = [
        '{"tool_call"',
        '{ "tool_call"',
        '{\n  "tool_call"',
        '{"tool_calls"',
        '{ "tool_calls"',
    ]

    # Subset used for JSON completeness: internal format only
    _JSON_START_PATTERNS: list[str] = [
        '{"tool_call"',
        '{ "tool_call"',
        '{\n  "tool_call"',
    ]

    # How many characters of the previous chunk to keep for cross-chunk detection
    PARTIAL_PATTERN_BUFFER_SIZE: int = 50

    # ------------------------------------------------------------------ #
    # Lifecycle                                                            #
    # ------------------------------------------------------------------ #

    def __init__(self) -> None:
        self._partial_pattern_buffer: str = ""
        self._saw_open_brace: bool = False

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def find_in_chunk(self, chunk: str) -> PatternMatch:
        """Find a tool-call start pattern in *chunk*, including cross-chunk boundaries.

        Updates internal brace-tracking state as a side-effect.  Call
        ``advance()`` (not this method) to advance the buffer for display chunks.
        """
        if "{" in chunk:
            self._saw_open_brace = True

        # Fast path: look for pattern in the current chunk alone
        best_idx = -1
        for pattern in self.TOOL_CALL_START_PATTERNS:
            idx = chunk.find(pattern)
            if idx != -1 and (best_idx == -1 or idx < best_idx):
                best_idx = idx

        if best_idx != -1:
            return PatternMatch(start_idx=best_idx, is_cross_chunk=False)

        # Cross-chunk path: only bother when we have buffered context
        if self._saw_open_brace and self._partial_pattern_buffer:
            combined = self._partial_pattern_buffer + chunk
            buf_len = len(self._partial_pattern_buffer)
            for pattern in self.TOOL_CALL_START_PATTERNS:
                combined_idx = combined.find(pattern)
                if combined_idx != -1:
                    if combined_idx < buf_len:
                        # Pattern started in the previous chunk (already displayed).
                        # Signal the caller to start buffering from position 0.
                        return PatternMatch(start_idx=0, is_cross_chunk=True)
                    else:
                        return PatternMatch(
                            start_idx=combined_idx - buf_len,
                            is_cross_chunk=False,
                        )

        return PatternMatch()

    def is_complete_json(self, buffer: str) -> bool:
        """Return ``True`` if *buffer* contains a complete, valid internal-format tool-call JSON object.

        Strips ``<tool:UUID>`` security wrapper tags before parsing so they do
        not invalidate the JSON.
        """
        if not buffer:
            return False
        clean = TOOL_TAG_PATTERN.sub("", buffer)
        for pattern in self._JSON_START_PATTERNS:
            start_idx = clean.find(pattern)
            if start_idx != -1:
                ok, _ = self._parse_json_at(clean, start_idx)
                if ok:
                    return True
        return False

    def advance(self, chunk: str) -> None:
        """Slide the cross-chunk buffer forward with the tail of a displayed chunk.

        Call this for every chunk that is sent directly to the UI (i.e., *not*
        buffered as part of a potential tool call).
        """
        if len(chunk) >= self.PARTIAL_PATTERN_BUFFER_SIZE:
            self._partial_pattern_buffer = chunk[-self.PARTIAL_PATTERN_BUFFER_SIZE :]
        else:
            combined = self._partial_pattern_buffer + chunk
            self._partial_pattern_buffer = combined[-self.PARTIAL_PATTERN_BUFFER_SIZE :]

    def reset(self) -> None:
        """Reset all cross-chunk state.

        Call after a tool call is detected/handled or when the buffer is flushed.
        """
        self._partial_pattern_buffer = ""
        self._saw_open_brace = False

    # ------------------------------------------------------------------ #
    # Batch scanning (complete accumulated text)                          #
    # ------------------------------------------------------------------ #

    def find_all_complete(self, text: str) -> tuple[list[tuple[int, int]], str]:
        """Find all complete internal-format tool calls in a fully-accumulated string.

        Unlike ``find_in_chunk()`` (which is stateful and designed for streaming),
        this method scans a complete text in one pass and is stateless.

        Returns:
            ``(positions, text)`` where *positions* is a sorted list of
            ``(start, end)`` tuples that index into the original *text*, and
            *text* is returned unchanged so callers can slice it directly.
        """
        positions: list[tuple[int, int]] = []
        seen_starts: set[int] = set()
        decoder = json.JSONDecoder()

        for pattern in self._JSON_START_PATTERNS:
            search_from = 0
            while True:
                start_idx = text.find(pattern, search_from)
                if start_idx == -1:
                    break
                if start_idx in seen_starts:
                    search_from = start_idx + 1
                    continue
                try:
                    parsed: dict[str, Any] | list[Any] | str | int | float | bool | None
                    parsed, end_offset = decoder.raw_decode(text, start_idx)
                    if isinstance(parsed, dict) and "tool_call" in parsed:
                        seen_starts.add(start_idx)
                        positions.append((start_idx, end_offset))
                        search_from = end_offset
                    else:
                        search_from = start_idx + len(pattern)
                except json.JSONDecodeError:
                    search_from = start_idx + len(pattern)

        positions.sort()
        return positions, text

    # ------------------------------------------------------------------ #
    # Private helpers                                                      #
    # ------------------------------------------------------------------ #

    def _parse_json_at(
        self,
        text: str,
        start_idx: int,
    ) -> tuple[bool, dict[str, Any] | None]:
        """Walk braces from *start_idx* and attempt to parse the enclosed JSON.

        Returns ``(True, parsed_dict)`` on success or ``(False, None)`` on
        failure / incompleteness.
        """
        fragment = text[start_idx:]
        depth = 0
        in_string = False
        escape_next = False

        for i, ch in enumerate(fragment):
            if escape_next:
                escape_next = False
                continue
            if ch == "\\" and in_string:
                escape_next = True
                continue
            if ch == '"' and not escape_next:
                in_string = not in_string
                continue
            if not in_string:
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            parsed_obj: dict[str, Any] | list[
                                Any
                            ] | str | int | float | bool | None = json.loads(
                                fragment[: i + 1],
                            )
                            if (
                                isinstance(parsed_obj, dict)
                                and "tool_call" in parsed_obj
                            ):
                                return True, parsed_obj
                        except json.JSONDecodeError:
                            pass
                        return False, None

        return False, None
