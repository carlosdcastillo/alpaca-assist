"""Subagent contracts through real tabs, tools, persistence and HTTP streams.

Only the model is scripted; no coordinator, stream processor, tool executor,
conversation state, or database is mocked. HeadlessAPI skips the DOM fold ack.
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from chat_state import ToolResult
from core.app_core import AppCore
from webview_api import WebViewAPI


def eventually(predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    assert predicate(), "Timed out waiting for the observable outcome"


def text(value):
    return [{"message": {"content": value}, "done": False}, {"done": True}]


def calls(*specs):
    return [
        {
            "message": {
                "tool_calls": [
                    {"function": {"name": name, "arguments": args}}
                    for name, args in specs
                ],
            },
        },
        {"done": True},
    ]


class HeadlessAPI(WebViewAPI):
    def wait_for_fold_rendered(self, *args, **kwargs):
        return True


@pytest.fixture
def stack(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    requests = []
    errors = []
    gates = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(payload)
            try:
                response = server.respond(payload)
                status, frames = (
                    response if isinstance(response, tuple) else (200, response)
                )
            except Exception as exc:
                errors.append(exc)
                status, frames = 500, [{"error": str(exc)}]
            self.send_response(status)
            self.send_header("Content-Type", "application/x-ndjson")
            self.end_headers()
            try:
                for frame in frames:
                    self.wfile.write((json.dumps(frame) + "\n").encode())
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.respond = lambda payload: text("ordinary answer")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    app = SimpleNamespace()
    api = HeadlessAPI(app)
    core = AppCore(api)
    app.core = core
    core.preferences.update(
        api_url=f"http://127.0.0.1:{server.server_port}",
        model="test-model",
    )
    _, parent = core.create_tab("Parent")
    parent._summary_handler._generated = True
    state = SimpleNamespace(
        core=core,
        api=api,
        parent=parent,
        server=server,
        requests=requests,
        gates=gates,
    )
    yield state
    for gate in gates:
        gate.set()
    for tab in list(core.tabs.values()):
        tab.stop_streaming()
    eventually(
        lambda: all(
            tab._tool_handler._pending_count == 0 for tab in core.tabs.values()
        ),
    )
    core.stop_autosave()
    asyncio.run_coroutine_threadsafe(
        core.mcp_manager.shutdown(),
        core.event_loop,
    ).result(3)
    core.event_loop.call_soon_threadsafe(core.event_loop.stop)
    core.mcp_thread.join(2)
    server.shutdown()
    server.server_close()
    thread.join(2)
    assert not errors


def spawn(stack, task="child", call_id="spawn-1"):
    parent = stack.parent
    if parent._current_answer_index == -1:
        parent._current_answer_index = parent.chat_state.add_question("parent task")
    result = stack.core.subagents.call(
        parent,
        "spawn_agent",
        {"task": task},
        parent._current_answer_index,
        call_id,
    )
    assert result["success"], result
    child = next(
        tab
        for tab in stack.core.tabs.values()
        if tab.conversation_id == result["child_conversation_id"]
    )
    return result, child


def wait(stack, *ids, timeout=0, parent=None):
    parent = parent or stack.parent
    return stack.core.subagents.call(
        parent,
        "wait_agents",
        {"task_ids": list(ids), "timeout_seconds": timeout},
        parent._current_answer_index,
        "wait",
    )


def test_model_spawns_parallel_children_and_joins_after_child_tool_loops(
    stack,
    tmp_path,
):
    """A per-invocation callback would report too soon and fail this test."""
    gate = threading.Event()
    stack.gates.append(gate)
    (tmp_path / "numbers.txt").write_text("17, 26, 4", encoding="utf-8")
    counts = {}

    def respond(payload):
        question = next(
            m["content"] for m in payload["messages"] if m["role"] == "user"
        )
        key = payload["conversation_id"]
        counts[key] = counts.get(key, 0) + 1
        step = counts[key]
        names = {tool["function"]["name"] for tool in payload.get("tools", [])}
        if "Delegate two independent tasks; join their findings." in question:
            assert "internal_spawn_agent" in names
            if step == 1:
                return calls(
                    (
                        "internal_spawn_agent",
                        {
                            "task": "Read numbers.txt and report the sum",
                            "title": "Sum audit",
                        },
                    ),
                    (
                        "internal_spawn_agent",
                        {
                            "task": "Write evidence.txt with verified evidence",
                            "title": "Evidence writer",
                        },
                    ),
                )
            ids = list(
                dict.fromkeys(re.findall(r"agent-[a-f0-9]{32}", json.dumps(payload))),
            )
            assert len(ids) == 2
            if step == 2:
                return calls(("internal_wait_agents", {"task_ids": ids}))
            history = json.dumps(payload["messages"])
            assert "Sum is 47" in history and "Evidence file written" in history
            return text("Both children verified: sum 47; evidence.txt written.")
        assert "internal_spawn_agent" not in names
        if "Read numbers.txt" in question:
            if step == 1:
                # The app's normal text-embedded tool-call path, not only native calls.
                return text(
                    json.dumps(
                        {
                            "tool_call": {
                                "id": "read-numbers",
                                "name": "internal_read_file",
                                "arguments": {
                                    "filepath": str(tmp_path / "numbers.txt"),
                                },
                            },
                        },
                    ),
                )
            assert "17, 26, 4" in json.dumps(payload["messages"])
            assert gate.wait(8)
            return text("Sum is 47")
        if step == 1:
            return calls(
                (
                    "internal_write_file",
                    {
                        "filepath": str(tmp_path / "evidence.txt"),
                        "content": "verified evidence",
                    },
                ),
            )
        assert "Successfully wrote" in json.dumps(payload["messages"])
        return text("Evidence file written")

    stack.server.respond = respond
    sent = stack.api.send_message(
        stack.parent.tab_id,
        "Delegate two independent tasks; join their findings.",
    )
    assert sent["success"]
    eventually(
        lambda: len(stack.parent.subagent_tasks) == 2
        and any(
            count == 2
            for key, count in counts.items()
            if key != stack.parent.conversation_id
        ),
    )
    tasks = list(stack.parent.subagent_tasks.values())
    sum_task = next(
        record
        for record in tasks
        if any(
            tab.conversation_id == record["child_conversation_id"]
            and tab.title == "Sum audit"
            for tab in stack.core.tabs.values()
        )
    )
    assert sum_task["status"] == "running"  # child's first invocation already ended
    assert stack.parent.current_turn_timing is not None
    assert not stack.api.send_message(
        stack.parent.tab_id,
        "must not replace the waiting turn",
    )["success"]
    gate.set()
    eventually(lambda: stack.parent.current_turn_timing is None)
    assert (
        stack.parent.chat_state.answers[0].get_text_only_content()
        == "Both children verified: sum 47; evidence.txt written."
    )
    assert (tmp_path / "evidence.txt").read_text() == "verified evidence"
    assert (
        counts[stack.parent.conversation_id] == 3
    )  # spawn -> wait -> final, exactly once
    assert all(record["status"] == "completed" for record in tasks)
    results = [
        part
        for part in stack.parent.chat_state.answers[0].components
        if isinstance(part, ToolResult)
    ]
    assert len(results) == 3
    assert (
        "Sum is 47" in results[-1].content
        and "Evidence file written" in results[-1].content
    )
    ids = [record["task_id"] for record in tasks]
    assert wait(stack, *ids) == wait(stack, *ids)  # rereading is stable, not redelivery


@pytest.mark.parametrize("failure", ["http", "backend", "eof"])
def test_child_transport_failures_are_not_successful_answers(stack, failure):
    def respond(payload):
        if failure == "http":
            return 503, [{"error": "unavailable"}]
        if failure == "backend":
            return [{"done": True, "done_reason": "error", "error": "provider failed"}]
        return [{"message": {"content": "incomplete answer"}, "done": False}]

    stack.server.respond = respond
    task, child = spawn(stack)
    result = wait(stack, task["task_id"], timeout=4)
    assert not result["pending"]
    assert result["results"][0]["status"] == "failed"
    assert result["results"][0]["error"]
    assert result["results"][0]["result"] == ""
    child.on_turn_finished(0)  # late drain cannot overwrite the terminal outcome
    assert wait(stack, task["task_id"]) == result


@pytest.mark.parametrize(
    "action",
    ["cancel", "close_child", "stop_parent", "close_parent"],
)
def test_cancellation_wakes_waiters_and_late_completion_cannot_overwrite(stack, action):
    gate = threading.Event()
    stack.gates.append(gate)
    stack.server.respond = lambda payload: (
        text("too late") if gate.wait(8) else text("timeout")
    )
    task, child = spawn(stack)
    eventually(lambda: len(stack.requests) == 1)
    joined = []
    waiter = threading.Thread(
        target=lambda: joined.append(wait(stack, task["task_id"], timeout=6)),
    )
    waiter.start()
    if action == "cancel":
        result = stack.core.subagents.call(
            stack.parent,
            "cancel_agent",
            {"task_id": task["task_id"]},
            0,
            "cancel",
        )
        assert result["status"] == "cancelled"
    elif action == "close_child":
        assert stack.api.close_tab(child.tab_id)["success"]
    elif action == "stop_parent":
        assert stack.api.stop_streaming(stack.parent.tab_id)["success"]
    else:
        assert stack.api.close_tab(stack.parent.tab_id)["success"]
    waiter.join(2)
    assert not waiter.is_alive()
    record = stack.parent.subagent_tasks[task["task_id"]]
    assert record["status"] == "cancelled"
    gate.set()
    eventually(lambda: child._streaming_handler._current_thread is None)
    assert record["status"] == "cancelled" and record["result"] == ""
    assert (
        not child.chat_state.answers[0].get_text_only_content().startswith("too late")
    )


def test_ownership_limits_nesting_idempotency_and_timeout(stack):
    gate = threading.Event()
    stack.gates.append(gate)
    stack.server.respond = lambda payload: (
        text("done") if gate.wait(8) else text("timeout")
    )
    task, child = spawn(stack)
    duplicate, same_child = spawn(stack)
    assert duplicate["task_id"] == task["task_id"] and same_child is child
    assert len(stack.core.tabs) == 2
    assert wait(stack, task["task_id"])["pending"] == [task["task_id"]]
    _, stranger = stack.core.create_tab("Unrelated conversation")
    stranger._summary_handler._generated = True
    assert not wait(stack, task["task_id"], parent=stranger)["success"]
    assert not stack.core.subagents.call(
        stranger,
        "cancel_agent",
        {"task_id": task["task_id"]},
        0,
        "foreign",
    )["success"]
    assert not stack.core.subagents.call(
        child,
        "spawn_agent",
        {"task": "nested"},
        0,
        "nested",
    )["success"]
    for index in range(3):
        spawn(stack, call_id=f"extra-{index}")
    rejected = stack.core.subagents.call(
        stack.parent,
        "spawn_agent",
        {"task": "fifth"},
        0,
        "fifth",
    )
    assert not rejected["success"]
    assert len(stack.parent.subagent_tasks) == 4
    for model in ("claude-code/sonnet", "codex/test"):
        stranger.preferences = {"model": model}
        assert not any(
            tool["function"]["name"] == "internal_spawn_agent"
            for tool in stack.core.get_available_mcp_tools(stranger)
        )
    gate.set()


def test_saved_results_survive_restore_but_running_tasks_are_interrupted_and_clones_do_not_own_children(
    stack,
):
    completed, child = spawn(stack)
    assert (
        wait(stack, completed["task_id"], timeout=4)["results"][0]["result"]
        == "ordinary answer"
    )
    gate = threading.Event()
    stack.gates.append(gate)
    stack.server.respond = lambda payload: (
        text("late") if gate.wait(8) else text("timeout")
    )
    running, running_child = spawn(stack, call_id="running")
    stack.core.save_session()
    saved_tabs, _ = stack.core.load_session()
    parent_data = next(
        data
        for data in saved_tabs
        if data["conversation_id"] == stack.parent.conversation_id
    )
    child_data = next(
        data
        for data in saved_tabs
        if data["conversation_id"] == running_child.conversation_id
    )
    _, clone = stack.core.create_tab("Clone")
    clone.load_from_data(parent_data)
    assert clone.subagent_tasks == {}
    clone.load_from_data(child_data)
    assert clone.subagent is None
    # Simulate a restart, allocating different runtime tab IDs with the same stable IDs.
    stack.core.tabs.pop(stack.parent.tab_id)
    _, restored = stack.core.create_tab(
        "Restored",
        conversation_id=stack.parent.conversation_id,
    )
    restored.load_from_data(parent_data)
    assert restored.tab_id != stack.parent.tab_id
    result = wait(stack, completed["task_id"], running["task_id"], parent=restored)
    assert [record["status"] for record in result["results"]] == [
        "completed",
        "interrupted",
    ]
    assert result["results"][0]["result"] == "ordinary answer"
    gate.set()
    eventually(lambda: running_child._streaming_handler._current_thread is None)
    assert restored.subagent_tasks[running["task_id"]]["status"] == "interrupted"


def test_normal_chat_remains_a_single_conversation(stack):
    assert stack.api.send_message(stack.parent.tab_id, "hello")["success"]
    eventually(lambda: stack.parent.current_turn_timing is None)
    assert (
        stack.parent.chat_state.answers[0].get_text_only_content() == "ordinary answer"
    )
    assert len(stack.core.tabs) == 1 and not stack.parent.subagent_tasks
