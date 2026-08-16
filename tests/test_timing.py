"""Tests for core.timing — turn timing, gap detection, and formatting."""
import time
from datetime import datetime
from datetime import timedelta

from core import timing
from core.timing import TurnTiming


class TestTurnTimingAccumulation:
    def test_start_records_wall_clock_and_begins_counting(self) -> None:
        before = time.time()
        t = TurnTiming.start()
        assert t.started_at is not None
        assert before <= t.started_at <= time.time()
        assert t.completed_at is None
        assert t.wall_ms >= 0

    def test_invocations_and_latency_accumulate_across_a_tool_loop(self) -> None:
        t = TurnTiming.start()
        t.add_invocation(1200)
        t.add_invocation(800)
        t.add_invocation(400)
        assert t.invocations == 3
        assert t.llm_ms == 2400

    def test_invocation_without_metrics_still_counts(self) -> None:
        """A stream that died before reporting metrics still happened."""
        t = TurnTiming.start()
        t.add_invocation(1000)
        t.add_invocation(None)
        assert t.invocations == 2
        assert t.llm_ms == 1000

    def test_tools_accumulate_with_a_count(self) -> None:
        t = TurnTiming.start()
        t.add_tool(500)
        t.add_tool(2500)
        assert t.tool_count == 2
        assert t.tool_ms == 3000

    def test_negative_tool_duration_is_clamped(self) -> None:
        t = TurnTiming.start()
        t.add_tool(-50)
        assert t.tool_ms == 0

    def test_first_token_only_records_once(self) -> None:
        """Continuations produce their own first token; only the original counts."""
        t = TurnTiming.start()
        t.mark_first_token()
        first = t.first_token_at
        time.sleep(0.01)
        t.mark_first_token()
        assert t.first_token_at == first

    def test_ttft_is_none_when_no_token_arrived(self) -> None:
        assert TurnTiming.start().ttft_ms is None

    def test_ttft_measures_from_start_to_first_token(self) -> None:
        t = TurnTiming.start()
        time.sleep(0.02)
        t.mark_first_token()
        assert t.ttft_ms is not None
        assert t.ttft_ms >= 15


class TestTurnTimingFinish:
    def test_finish_freezes_the_elapsed_span(self) -> None:
        t = TurnTiming.start()
        time.sleep(0.02)
        t.finish()
        frozen = t.wall_ms
        time.sleep(0.02)
        assert t.wall_ms == frozen

    def test_finish_is_idempotent(self) -> None:
        """Several paths can end one turn; the first must win."""
        t = TurnTiming.start()
        t.finish()
        completed = t.completed_at
        frozen = t.wall_ms
        time.sleep(0.02)
        t.finish()
        assert t.completed_at == completed
        assert t.wall_ms == frozen

    def test_overhead_is_wall_minus_model_and_tools(self) -> None:
        t = TurnTiming.start()
        t.add_invocation(10)
        t.add_tool(10)
        time.sleep(0.05)
        t.finish()
        assert t.overhead_ms == t.wall_ms - 20

    def test_overhead_never_goes_negative(self) -> None:
        """llm_ms is measured by the proxy, tool_ms here — they can overshoot."""
        t = TurnTiming.start()
        t.add_invocation(999_999)
        t.finish()
        assert t.overhead_ms == 0


class TestTurnTimingSerialization:
    def test_round_trip_preserves_the_recorded_span(self) -> None:
        t = TurnTiming.start()
        t.mark_first_token()
        t.add_invocation(1500)
        t.add_tool(3000)
        time.sleep(0.02)
        t.finish()

        restored = TurnTiming.from_dict(t.to_dict())
        assert restored is not None
        assert restored.wall_ms == t.wall_ms
        assert restored.llm_ms == 1500
        assert restored.tool_ms == 3000
        assert restored.invocations == 1
        assert restored.tool_count == 1
        assert restored.first_token_at == t.first_token_at

    def test_restored_timing_does_not_count_up_from_load_time(self) -> None:
        t = TurnTiming.start()
        t.finish()
        restored = TurnTiming.from_dict(t.to_dict())
        assert restored is not None
        first = restored.wall_ms
        time.sleep(0.02)
        assert restored.wall_ms == first

    def test_from_dict_of_nothing_is_none(self) -> None:
        assert TurnTiming.from_dict(None) is None
        assert TurnTiming.from_dict({}) is None

    def test_wall_ms_derived_when_only_endpoints_stored(self) -> None:
        start = time.time()
        restored = TurnTiming.from_dict(
            {"started_at": start, "completed_at": start + 2.5},
        )
        assert restored is not None
        assert restored.wall_ms == 2500


class TestFormatDuration:
    def test_sub_second_reads_in_milliseconds(self) -> None:
        assert timing.format_duration_ms(0) == "0ms"
        assert timing.format_duration_ms(999) == "999ms"

    def test_seconds_keep_one_decimal(self) -> None:
        assert timing.format_duration_ms(1000) == "1.0s"
        assert timing.format_duration_ms(47_300) == "47.3s"

    def test_minutes_and_hours_switch_units(self) -> None:
        assert timing.format_duration_ms(60_000) == "1m 00s"
        assert timing.format_duration_ms(194_000) == "3m 14s"
        assert timing.format_duration_ms(3_600_000) == "1h 00m"
        assert timing.format_duration_ms(5_460_000) == "1h 31m"

    def test_none_is_an_em_dash(self) -> None:
        assert timing.format_duration_ms(None) == "—"


class TestFormatGap:
    def test_gap_units_escalate(self) -> None:
        assert timing.format_gap(30) == "30 seconds later"
        assert timing.format_gap(600) == "10 minutes later"
        assert timing.format_gap(3540) == "59 minutes later"
        assert timing.format_gap(7200) == "2 hours later"
        assert timing.format_gap(86400 * 3) == "3 days later"
        assert timing.format_gap(86400 * 21) == "3 weeks later"
        assert timing.format_gap(86400 * 90) == "3 months later"

    def test_singular_units_are_not_pluralized(self) -> None:
        assert timing.format_gap(3600) == "1 hour later"
        assert timing.format_gap(86400 * 2) == "2 days later"

    def test_negative_and_none_produce_nothing(self) -> None:
        assert timing.format_gap(-1) == ""
        assert timing.format_gap(None) == ""

    def test_precise_form_keeps_the_remainder(self) -> None:
        assert timing.format_gap_precise(8040) == "2 hours 14 minutes"
        assert timing.format_gap_precise(7200) == "2 hours"
        assert timing.format_gap_precise(45) == "45 seconds"

    def test_precise_form_uses_singular_units(self) -> None:
        # This string is read by the model, so "1 days 23 hours" is not just
        # untidy — it is a sentence the model has to work around.
        assert timing.format_gap_precise(169200) == "1 day 23 hours"
        assert timing.format_gap_precise(90000) == "1 day 1 hour"
        assert timing.format_gap_precise(3660) == "1 hour 1 minute"
        assert timing.format_gap_precise(91) == "1 minute 31 seconds"
        assert timing.format_gap_precise(1) == "1 second"


class TestHistoryTimeMarker:
    def test_first_message_is_anchored_with_its_date(self) -> None:
        asked = datetime(2026, 8, 16, 14, 32).isoformat()
        marker = timing.history_time_marker(asked, None, is_first=True)
        assert marker == "[Sent 2026-08-16 14:32]\n"

    def test_short_pause_gets_no_marker(self) -> None:
        """Every-turn markers would be noise the model can't act on."""
        now = datetime.now()
        marker = timing.history_time_marker(
            now.isoformat(),
            (now - timedelta(seconds=30)).timestamp(),
            is_first=False,
        )
        assert marker == ""

    def test_long_pause_names_the_gap(self) -> None:
        asked = datetime(2026, 8, 16, 16, 46)
        previous_end = (asked - timedelta(hours=2, minutes=14)).timestamp()
        marker = timing.history_time_marker(
            asked.isoformat(),
            previous_end,
            is_first=False,
        )
        assert marker == (
            "[Sent 2026-08-16 16:46 — 2 hours 14 minutes after the previous reply]\n"
        )

    def test_threshold_is_the_boundary(self) -> None:
        asked = datetime(2026, 8, 16, 12, 0)
        just_under = (
            asked - timedelta(seconds=timing.GAP_THRESHOLD_SECONDS - 1)
        ).timestamp()
        just_over = (
            asked - timedelta(seconds=timing.GAP_THRESHOLD_SECONDS + 1)
        ).timestamp()
        assert timing.history_time_marker(asked.isoformat(), just_under, False) == ""
        assert timing.history_time_marker(asked.isoformat(), just_over, False) != ""

    def test_missing_timestamps_degrade_to_no_marker(self) -> None:
        """Conversations saved before timing existed must render unchanged."""
        assert timing.history_time_marker(None, None, is_first=True) == ""
        assert timing.history_time_marker("not a date", 0.0, is_first=True) == ""
        assert (
            timing.history_time_marker(
                datetime.now().isoformat(),
                None,
                is_first=False,
            )
            == ""
        )


class TestIsoToEpoch:
    def test_round_trips_an_iso_local_timestamp(self) -> None:
        now = datetime.now()
        assert timing.iso_to_epoch(now.isoformat()) == now.timestamp()

    def test_unparseable_values_are_none(self) -> None:
        assert timing.iso_to_epoch(None) is None
        assert timing.iso_to_epoch("") is None
        assert timing.iso_to_epoch("yesterday-ish") is None


class TestLocalNowDescription:
    def test_describes_the_current_moment_with_a_zone(self) -> None:
        described = timing.local_now_description()
        now = datetime.now().astimezone()
        assert now.strftime("%A") in described
        assert str(now.year) in described
        assert "UTC" in described
