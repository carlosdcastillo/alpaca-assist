"""Time awareness primitives — turn timing, gap detection, human formatting.

The app records three independent kinds of time, and they are not
interchangeable:

*Wall time* is how long a turn took from the user pressing send to the last
token landing.  *LLM time* is how much of that the model was actually
generating (summed ``invocation_latency_ms`` across the initial call and every
tool-loop continuation).  *Tool time* is how much was spent inside tool
execution.  A three-minute turn is usually 20 seconds of model and 160 seconds
of a shell command, and only the decomposition explains where it went.

Durations are measured with ``time.monotonic`` so a clock adjustment mid-turn
cannot produce a negative elapsed.  Wall-clock stamps use ``time.time``
because they have to survive serialisation and be shown to a human.  The two
are kept in separate fields on purpose; never subtract one from the other.

Threading
---------
``TurnTiming`` is mutated from three threads at once — the stream thread marks
first token, each tool thread adds its own duration, and the completion
callback finalises — so every mutator takes ``_lock``.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from typing import Any

# A pause longer than this between one turn finishing and the next question
# is treated as the user having stepped away rather than kept typing.  Ten
# minutes is deliberately well above "read the answer and reply" and well
# below "came back after lunch", so the marker means something when it shows.
GAP_THRESHOLD_SECONDS = 600.0


@dataclass
class TurnTiming:
    """Timing record for one assistant turn, including its whole tool loop.

    A "turn" spans from the user's message to the final token of the answer,
    across however many LLM invocations the tool loop needed.
    """

    started_at: float | None = None
    first_token_at: float | None = None
    completed_at: float | None = None
    llm_ms: int = 0
    tool_ms: int = 0
    invocations: int = 0
    tool_count: int = 0
    _mono_start: float | None = field(default=None, repr=False, compare=False)
    _restored_wall_ms: int | None = field(default=None, repr=False, compare=False)
    # Annotated Any because threading.RLock is a factory function, not a type.
    _lock: Any = field(default_factory=threading.RLock, repr=False, compare=False)

    @classmethod
    def start(cls) -> TurnTiming:
        """Begin timing a turn as of now."""
        return cls(started_at=time.time(), _mono_start=time.monotonic())

    def mark_first_token(self) -> None:
        """Record time-to-first-token, ignoring all but the first call.

        Continuations after a tool call also produce a "first" token; only the
        original one measures the latency a user perceives as the wait.
        """
        with self._lock:
            if self.first_token_at is None:
                self.first_token_at = time.time()

    def add_invocation(self, latency_ms: int | None = None) -> None:
        """Count one LLM call, adding its server-reported latency if present.

        ``latency_ms`` comes from ``invocation_metrics`` and is absent when a
        stream fails before the terminal ``message_delta``.  The call is still
        counted — an invocation that produced no metrics happened, and hiding
        it would make the tool-loop count silently wrong.
        """
        with self._lock:
            self.invocations += 1
            if latency_ms:
                self.llm_ms += int(latency_ms)

    def add_tool(self, duration_ms: int) -> None:
        """Record one completed tool execution."""
        with self._lock:
            self.tool_count += 1
            self.tool_ms += max(0, int(duration_ms))

    def finish(self) -> None:
        """Close the turn.  Idempotent — the first finish wins.

        The tool loop can reach completion through several paths (clean done,
        user stop, stream error), and more than one may fire for a single
        turn.  Later calls must not stretch the recorded duration.
        """
        with self._lock:
            if self.completed_at is None:
                self.completed_at = time.time()
                # Freeze the elapsed span so wall_ms stops advancing.
                if self._mono_start is not None:
                    self._restored_wall_ms = int(
                        (time.monotonic() - self._mono_start) * 1000,
                    )

    @property
    def wall_ms(self) -> int:
        """Elapsed monotonic time, live while the turn is still running."""
        with self._lock:
            if self._restored_wall_ms is not None:
                return self._restored_wall_ms
            if self._mono_start is None:
                return 0
            return int((time.monotonic() - self._mono_start) * 1000)

    @property
    def ttft_ms(self) -> int | None:
        """Time from send to the first visible token, or None if none arrived."""
        with self._lock:
            if self.first_token_at is None or self.started_at is None:
                return None
            return max(0, int((self.first_token_at - self.started_at) * 1000))

    @property
    def overhead_ms(self) -> int:
        """Wall time attributable to neither the model nor tools.

        Network, queueing, fold-render waits, and the UpdatePoller's 50 ms
        cadence all land here.  Clamped at zero: llm_ms is measured by the
        proxy and tool_ms by this process, so rounding can make the parts
        exceed the whole by a few milliseconds on a very fast turn.
        """
        with self._lock:
            return max(0, self.wall_ms - self.llm_ms - self.tool_ms)

    def to_dict(self) -> dict[str, Any]:
        with self._lock:
            return {
                "started_at": self.started_at,
                "first_token_at": self.first_token_at,
                "completed_at": self.completed_at,
                "wall_ms": self.wall_ms,
                "llm_ms": self.llm_ms,
                "tool_ms": self.tool_ms,
                "invocations": self.invocations,
                "tool_count": self.tool_count,
            }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> TurnTiming | None:
        """Rebuild from persisted form, or None if there is nothing stored.

        The restored object is already finished, so ``wall_ms`` replays the
        stored span rather than counting up from load time.
        """
        if not data:
            return None
        started = data.get("started_at")
        completed = data.get("completed_at")
        stored_wall = data.get("wall_ms")
        if stored_wall is None and started is not None and completed is not None:
            stored_wall = int((completed - started) * 1000)
        return cls(
            started_at=started,
            first_token_at=data.get("first_token_at"),
            completed_at=completed,
            llm_ms=int(data.get("llm_ms", 0) or 0),
            tool_ms=int(data.get("tool_ms", 0) or 0),
            invocations=int(data.get("invocations", 0) or 0),
            tool_count=int(data.get("tool_count", 0) or 0),
            _restored_wall_ms=int(stored_wall) if stored_wall is not None else None,
        )


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def format_duration_ms(ms: int | float | None) -> str:
    """Render a duration at roughly two significant figures.

    Sub-second work reads in milliseconds, a tool loop reads in minutes; one
    format for both would make one of them unreadable.
    """
    if ms is None:
        return "—"
    ms = int(ms)
    if ms < 1000:
        return f"{ms}ms"
    seconds = ms / 1000.0
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, secs = divmod(int(seconds), 60)
    if minutes < 60:
        return f"{minutes}m {secs:02d}s"
    hours, mins = divmod(minutes, 60)
    return f"{hours}h {mins:02d}m"


def format_gap(seconds: float | None) -> str:
    """Render a between-messages pause the way a person would say it."""
    if seconds is None or seconds < 0:
        return ""
    if seconds < 90:
        return f"{int(seconds)} seconds later"
    minutes = seconds / 60.0
    # Units change at their own boundary rather than at 1.5x, so an hour reads
    # as "1 hour later" instead of "60 minutes later".
    if minutes < 60:
        return f"{int(round(minutes))} minutes later"
    hours = minutes / 60.0
    if hours < 24:
        n = int(round(hours))
        return f"{n} hour{'s' if n != 1 else ''} later"
    days = hours / 24.0
    if days < 14:
        n = int(round(days))
        return f"{n} day{'s' if n != 1 else ''} later"
    weeks = days / 7.0
    if weeks < 9:
        n = int(round(weeks))
        return f"{n} week{'s' if n != 1 else ''} later"
    n = int(round(days / 30.44))
    return f"{n} month{'s' if n != 1 else ''} later"


def _plural(count: int, unit: str) -> str:
    """Render a count with its unit, singular at one."""
    return f"{count} {unit}" if count == 1 else f"{count} {unit}s"


def format_gap_precise(seconds: float | None) -> str:
    """Render a pause with a second unit, for model context rather than UI.

    The UI can round "2 hours later" freely because a human reading it also
    sees the timestamps.  The model gets only this string, so it keeps the
    remainder: "2 hours 14 minutes".
    """
    if seconds is None or seconds < 0:
        return ""
    total = int(seconds)
    if total < 60:
        return _plural(total, "second")
    minutes, secs = divmod(total, 60)
    if minutes < 60:
        if secs < 30:
            return _plural(minutes, "minute")
        return f"{_plural(minutes, 'minute')} {_plural(secs, 'second')}"
    hours, mins = divmod(minutes, 60)
    if hours < 24:
        if not mins:
            return _plural(hours, "hour")
        return f"{_plural(hours, 'hour')} {_plural(mins, 'minute')}"
    days, hrs = divmod(hours, 24)
    if not hrs:
        return _plural(days, "day")
    return f"{_plural(days, 'day')} {_plural(hrs, 'hour')}"


def local_now_description() -> str:
    """Describe the current local moment for the model's system prompt."""
    now = datetime.now().astimezone()
    return now.strftime("%A, %d %B %Y, %H:%M (%Z, UTC%z)")


def history_time_marker(
    question_iso: str | None,
    previous_end_epoch: float | None,
    is_first: bool,
) -> str:
    """Build the bracketed time note prefixed to a user message in history.

    The model otherwise reads a conversation as one continuous moment, so a
    question asked three days after the previous answer looks identical to a
    follow-up typed ten seconds later.  Two cases earn a marker:

    * the opening message, which anchors when the conversation started;
    * any later message that follows a pause past ``GAP_THRESHOLD_SECONDS``.

    Everything else gets nothing.  Marking every turn would add a line of
    noise per message for information the model can't act on, and — since
    these strings sit inside the cached history prefix — each one is bytes
    resent on every continuation of the tool loop.

    Returns a string ending in a newline, or "" when no marker applies.
    """
    asked_at = iso_to_epoch(question_iso)
    if asked_at is None:
        return ""
    stamp = datetime.fromtimestamp(asked_at).strftime("%Y-%m-%d %H:%M")
    if is_first:
        return f"[Sent {stamp}]\n"
    if previous_end_epoch is None:
        return ""
    gap = asked_at - previous_end_epoch
    if gap < GAP_THRESHOLD_SECONDS:
        return ""
    return f"[Sent {stamp} — {format_gap_precise(gap)} after the previous reply]\n"


def iso_to_epoch(value: str | None) -> float | None:
    """Parse an ISO timestamp from the conversation graph into epoch seconds.

    ``MessageNode.created_at`` is written by ``datetime.now().isoformat()``,
    which is naive local time.  Anything unparseable yields None so a corrupt
    or hand-edited value degrades to "no timing known" rather than raising in
    the middle of a render.
    """
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).timestamp()
    except (ValueError, TypeError):
        return None
