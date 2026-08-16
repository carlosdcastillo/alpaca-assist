"""Tests for time awareness across the persistence and model-context layers.

core.timing's own units live in test_timing.py; this file covers the seams —
that timing survives a save/load round trip in both conversation models, that
old conversations without it still load, and that history gap markers reach the
messages sent to the model.
"""

from __future__ import annotations

import time
from datetime import datetime
from datetime import timedelta
from typing import Any
from unittest.mock import Mock

from chat_state import ChatState
from chat_state import FullAnswer
from chat_state import ToolCall
from chat_state import ToolResult
from conversation_graph import ConversationGraph
from core.chat_tab_tools import ToolHandler
from core.timing import GAP_THRESHOLD_SECONDS


class TestToolComponentTiming:
    def test_tool_result_duration_survives_a_round_trip(self) -> None:
        answer = FullAnswer()
        answer.add_tool_call('{"tool_call": {}}', "tc-1", started_at=1000.0)
        answer.add_tool_result("done", "tc-1", started_at=1000.5, duration_ms=12400)

        restored = FullAnswer.from_dict(answer.to_dict())

        call, result = restored.components
        assert isinstance(call, ToolCall)
        assert isinstance(result, ToolResult)
        assert call.started_at == 1000.0
        assert result.started_at == 1000.5
        assert result.duration_ms == 12400

    def test_untimed_components_omit_the_keys_entirely(self) -> None:
        """Old conversations must keep serialising byte-identically."""
        answer = FullAnswer()
        answer.add_tool_call('{"tool_call": {}}', "tc-1")
        answer.add_tool_result("done", "tc-1")

        components = answer.to_dict()["components"]

        assert components[0] == {
            "type": "tool_call",
            "content": '{"tool_call": {}}',
            "id": "tc-1",
        }
        assert components[1] == {
            "type": "tool_result",
            "content": "done",
            "id": "tc-1",
        }

    def test_components_saved_before_timing_existed_still_load(self) -> None:
        restored = FullAnswer.from_dict(
            {
                "components": [
                    {"type": "tool_call", "content": "{}", "id": "x"},
                    {"type": "tool_result", "content": "r", "id": "x"},
                ],
            },
        )
        call, result = restored.components
        assert isinstance(call, ToolCall) and call.started_at is None
        assert isinstance(result, ToolResult) and result.duration_ms is None


class TestConversationGraphTiming:
    def test_turn_timing_attaches_to_the_assistant_node(self) -> None:
        graph = ConversationGraph()
        index = graph.add_question("how long?")
        record = {"wall_ms": 4700, "llm_ms": 1200, "tool_ms": 3000}

        assert graph.set_turn_timing(index, record) is True
        assert graph.get_turn_timing(index) == record

    def test_turn_timing_survives_serialization(self) -> None:
        graph = ConversationGraph()
        index = graph.add_question("q")
        graph.set_turn_timing(index, {"wall_ms": 1234, "invocations": 2})

        restored = ConversationGraph.from_dict(graph.to_dict())

        assert restored.get_turn_timing(index) == {"wall_ms": 1234, "invocations": 2}

    def test_untimed_nodes_omit_the_key(self) -> None:
        graph = ConversationGraph()
        graph.add_question("q")
        nodes = graph.to_dict()["graph"]["nodes"].values()
        assert all("timing" not in node for node in nodes)

    def test_question_times_track_the_active_path(self) -> None:
        graph = ConversationGraph()
        graph.add_question("first")
        graph.add_question("second")

        times = graph.question_times

        assert len(times) == 2
        first, second = times
        assert first is not None and second is not None
        assert datetime.fromisoformat(first) <= datetime.fromisoformat(second)

    def test_turn_timings_parallel_the_answers(self) -> None:
        graph = ConversationGraph()
        graph.add_question("a")
        second = graph.add_question("b")
        graph.set_turn_timing(second, {"wall_ms": 99})

        assert graph.turn_timings == [None, {"wall_ms": 99}]

    def test_set_turn_timing_out_of_range_is_refused(self) -> None:
        graph = ConversationGraph()
        assert graph.set_turn_timing(3, {"wall_ms": 1}) is False
        assert graph.get_turn_timing(3) is None


class TestChatStateTiming:
    def test_question_time_recorded_per_turn(self) -> None:
        state = ChatState(questions=[], answers=[])
        before = datetime.now()
        state.add_question("q")
        stamp = state.question_times[0]
        assert stamp is not None
        recorded = datetime.fromisoformat(stamp)
        assert before <= recorded <= datetime.now()

    def test_turn_timing_round_trips(self) -> None:
        state = ChatState(questions=[], answers=[])
        index = state.add_question("q")
        state.set_turn_timing(index, {"wall_ms": 5000})

        restored = ChatState.from_dict(state.to_dict())

        assert restored.get_turn_timing(0) == {"wall_ms": 5000}

    def test_legacy_state_pads_timing_lists_to_the_question_count(self) -> None:
        restored = ChatState.from_dict({"questions": ["a", "b", "c"], "answers": []})
        assert restored.question_times == [None, None, None]
        assert restored.turn_timings == [None, None, None]

    def test_pop_keeps_the_parallel_lists_aligned(self) -> None:
        state = ChatState(questions=[], answers=[])
        state.add_question("a")
        state.add_question("b")
        state.pop_last_qa()

        assert len(state.question_times) == len(state.questions) == 1
        assert len(state.turn_timings) == 1

    def test_truncate_keeps_the_parallel_lists_aligned(self) -> None:
        state = ChatState(questions=[], answers=[])
        state.add_question("a")
        state.add_question("b")
        last_time = state.question_times[-1]
        state.truncate_to_last()

        assert state.questions == ["b"]
        assert state.question_times == [last_time]
        assert len(state.turn_timings) == 1


def _handler_with_history(
    questions: list[str],
    question_times: list[Any],
    turn_timings: list[Any],
) -> ToolHandler:
    """Build a ToolHandler over a plain ChatState with fabricated timings."""
    state = ChatState(questions=[], answers=[])
    for q in questions:
        state.add_question(q)
    state.question_times = list(question_times)
    state.turn_timings = list(turn_timings)

    chat = Mock()
    chat.chat_state = state
    return ToolHandler(chat, Mock())


class TestHistoryGapMarkersInModelContext:
    def test_opening_message_is_anchored_with_a_date(self) -> None:
        asked = datetime(2026, 8, 16, 9, 12)
        handler = _handler_with_history(["hello"], [asked.isoformat()], [None])

        messages = handler.prepare_continuation_messages(0)

        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == "[Sent 2026-08-16 09:12]\nhello"

    def test_long_pause_is_reported_relative_to_the_previous_reply(self) -> None:
        first = datetime(2026, 8, 16, 9, 12)
        replied = first + timedelta(minutes=1)
        second = replied + timedelta(hours=2, minutes=14)
        handler = _handler_with_history(
            ["hello", "still there?"],
            [first.isoformat(), second.isoformat()],
            [{"completed_at": replied.timestamp()}, None],
        )

        messages = handler.prepare_continuation_messages(1)

        user_messages = [m for m in messages if m["role"] == "user"]
        assert (
            "2 hours 14 minutes after the previous reply" in user_messages[1]["content"]
        )
        assert user_messages[1]["content"].endswith("still there?")

    def test_prompt_follow_up_gets_no_marker(self) -> None:
        first = datetime(2026, 8, 16, 9, 12)
        replied = first + timedelta(seconds=20)
        second = replied + timedelta(seconds=30)
        handler = _handler_with_history(
            ["hello", "and again"],
            [first.isoformat(), second.isoformat()],
            [{"completed_at": replied.timestamp()}, None],
        )

        messages = handler.prepare_continuation_messages(1)

        user_messages = [m for m in messages if m["role"] == "user"]
        assert user_messages[1]["content"] == "and again"

    def test_gap_is_measured_from_the_reply_not_the_question(self) -> None:
        """A long turn must not read as the user having stepped away."""
        asked = datetime(2026, 8, 16, 9, 0)
        # The model worked for 20 minutes, then the user replied 1 minute later.
        replied = asked + timedelta(minutes=20)
        second = replied + timedelta(minutes=1)
        handler = _handler_with_history(
            ["long job", "thanks"],
            [asked.isoformat(), second.isoformat()],
            [{"completed_at": replied.timestamp()}, None],
        )

        messages = handler.prepare_continuation_messages(1)

        user_messages = [m for m in messages if m["role"] == "user"]
        assert user_messages[1]["content"] == "thanks"

    def test_without_turn_timing_the_question_time_is_the_fallback(self) -> None:
        first = datetime(2026, 8, 16, 9, 0)
        second = first + timedelta(seconds=GAP_THRESHOLD_SECONDS + 60)
        handler = _handler_with_history(
            ["one", "two"],
            [first.isoformat(), second.isoformat()],
            [None, None],
        )

        messages = handler.prepare_continuation_messages(1)

        user_messages = [m for m in messages if m["role"] == "user"]
        assert "after the previous reply" in user_messages[1]["content"]

    def test_conversation_without_timestamps_is_unmarked(self) -> None:
        handler = _handler_with_history(["one", "two"], [None, None], [None, None])

        messages = handler.prepare_continuation_messages(1)

        user_messages = [m for m in messages if m["role"] == "user"]
        assert [m["content"] for m in user_messages] == ["one", "two"]


class TestSystemPromptClock:
    def test_current_time_block_is_appended(self) -> None:
        from core.app_core import AppCore

        core = Mock(spec=AppCore)
        core.get_skills_xml = Mock(return_value="<skills/>")
        prompt = AppCore.get_system_prompt(core, Mock(project_name=None))

        assert "<current_time>" in prompt
        assert str(datetime.now().year) in prompt
        # The clock must come last: everything before it is stable text that
        # benefits from sitting in a fixed position call to call.
        assert prompt.index("<current_time>") > prompt.index("<skills/>")


class TestTurnTimingFinalization:
    def test_finalize_persists_and_pushes_once(self) -> None:
        from core.chat_tab_base import ChatTabBase
        from core.timing import TurnTiming

        app_core = Mock()
        tab = ChatTabBase("tab-1", "Chat", app_core, conversation_id=1)
        index = tab.chat_state.add_question("q")
        tab.current_turn_timing = TurnTiming.start()
        tab.current_turn_timing.add_invocation(1500)
        tab.current_turn_timing.add_tool(2500)

        tab.finalize_turn_timing(index)

        stored = tab.chat_state.get_turn_timing(index)
        assert stored is not None
        assert stored["llm_ms"] == 1500
        assert stored["tool_ms"] == 2500
        assert stored["completed_at"] is not None
        app_core.api.on_turn_timing.assert_called_once_with("tab-1", index, stored)

        # A second call (stop and error paths can both reach here) must not
        # re-push or overwrite.
        app_core.api.on_turn_timing.reset_mock()
        tab.finalize_turn_timing(index)
        app_core.api.on_turn_timing.assert_not_called()

    def test_finalize_without_a_running_turn_is_a_no_op(self) -> None:
        from core.chat_tab_base import ChatTabBase

        app_core = Mock()
        tab = ChatTabBase("tab-1", "Chat", app_core, conversation_id=1)
        tab.finalize_turn_timing(0)
        app_core.api.on_turn_timing.assert_not_called()

    def test_wall_time_covers_the_whole_tool_loop(self) -> None:
        from core.timing import TurnTiming

        turn = TurnTiming.start()
        turn.add_invocation(10)
        time.sleep(0.03)
        turn.add_tool(5)
        turn.add_invocation(10)
        turn.finish()

        # The point of recording three clocks: wall exceeds the sum of the
        # parts, and the difference is real waiting the user experienced.
        assert turn.wall_ms > turn.llm_ms + turn.tool_ms
        assert turn.invocations == 2


class TestTurnEndSignal:
    """The turn closes when the tool loop drains, not per LLM invocation.

    Hooking this to on_streaming_complete instead stops the clock at the end
    of the first call, which for a tool-loop turn records a fraction of the
    real duration and none of the tool time.
    """

    @staticmethod
    def _handler() -> tuple[ToolHandler, Mock, Mock]:
        chat = Mock()
        chat.stop_streaming_flag.is_set.return_value = False
        continue_cb = Mock()
        return ToolHandler(chat, continue_cb), chat, continue_cb

    def test_tool_free_stream_closes_the_turn_when_it_ends(self) -> None:
        handler, chat, continue_cb = self._handler()

        handler.mark_stream_active()
        handler.mark_stream_finished(3)

        continue_cb.assert_not_called()
        chat.finalize_turn_timing.assert_called_once_with(3)

    def test_pending_tool_keeps_the_turn_open(self) -> None:
        handler, chat, _ = self._handler()
        handler._has_tool_calls = True

        handler.mark_stream_active()
        handler.mark_stream_active()  # stands in for the tool's own slot
        handler.mark_stream_finished(0)

        # One unit still outstanding — nothing may be finalised yet.
        chat.finalize_turn_timing.assert_not_called()

    def test_continuation_does_not_close_the_turn(self) -> None:
        handler, chat, continue_cb = self._handler()
        handler._has_tool_calls = True

        handler.mark_stream_active()
        handler.mark_stream_finished(0)

        continue_cb.assert_called_once_with(0)
        chat.finalize_turn_timing.assert_not_called()

    def test_turn_closes_once_the_last_continuation_is_tool_free(self) -> None:
        handler, chat, continue_cb = self._handler()

        # Round one: a tool call, so this drain continues instead of closing.
        handler._has_tool_calls = True
        handler.mark_stream_active()
        handler.mark_stream_finished(0)
        chat.finalize_turn_timing.assert_not_called()

        # Round two: the continuation's stream asks for nothing further.
        handler.mark_stream_active()
        handler.mark_stream_finished(0)

        assert continue_cb.call_count == 1
        chat.finalize_turn_timing.assert_called_once_with(0)

    def test_stopped_turn_still_closes_rather_than_hanging(self) -> None:
        handler, chat, continue_cb = self._handler()
        chat.stop_streaming_flag.is_set.return_value = True
        handler._has_tool_calls = True

        handler.mark_stream_active()
        handler.mark_stream_finished(0)

        # The continuation is suppressed, so nothing further will drain this
        # loop — the turn has to close here or its clock never stops.
        continue_cb.assert_not_called()
        chat.finalize_turn_timing.assert_called_once_with(0)
