"""Public Pack-task contract; transport is an in-process remote peer.

The MCP test uses a real stdio subprocess and authenticated loopback channel.
Real SSH/daemon proof is run separately; these tests need no SSH credentials.
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest
from mcp import ClientSession
from mcp import StdioServerParameters
from mcp.client.stdio import stdio_client

from anthropic_ollama_server import _build_claude_mcp_config_file
from anthropic_ollama_server import _codex_mcp_overrides
from chat_state import ChatState
from core.app_core import AppCore
from core.pack_tasks import call_control
from core.projects import list_projects
from core.projects import load_project
from pack_daemon import PackDaemonAdapter
from pack_daemon import make_dispatcher
from webview_api import WebViewAPI


@pytest.fixture
def controller(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    project = tmp_path / "projects" / "reports"
    project.mkdir(parents=True)
    (project / "project.toml").write_text('[project]\nrepo_url = "fixture-repo"\n')
    monkeypatch.setattr(
        "core.pack_tasks.load_project",
        partial(load_project, projects_dir=project.parent),
    )
    monkeypatch.setattr(
        "core.pack_tasks.list_projects",
        partial(list_projects, projects_dir=project.parent),
    )
    ready = threading.Event()
    targets = []

    class Transport:
        connected = False

        def __init__(self, host, session_id):
            self.target = SimpleNamespace(
                title="Reports",
                chat_state=ChatState([], []),
                is_streaming=False,
                preferences={"model": "wrong-model"},
                _current_answer_index=-1,
                current_turn_timing=None,
                _tool_handler=SimpleNamespace(
                    _pending_lock=threading.Lock(),
                    _pending_count=0,
                ),
                _streaming_handler=SimpleNamespace(_current_thread=None),
                get_serializable_data=lambda: {
                    "chat_state": {"questions": [], "answers": []},
                },
            )
            target = self.target

            def submit(prompt, images):
                target._current_answer_index = target.chat_state.add_question(prompt)

            target.handle_user_message = submit
            self.dispatch = make_dispatcher(
                target,
                PackDaemonAdapter(),
                False,
                SimpleNamespace(save_preferences=lambda: None),
            )
            targets.append(target)

        def on_notification(self, *args):
            pass

        def on_disconnect(self, *args):
            pass

        def connect(self, model=None):
            self.connected = True

        def send_request(self, method, params, **kwargs):
            if method == "configure_project":
                assert ready.wait(5), "test did not release project setup"
                if params["repo_url"] == "fixture-repo":
                    return {"workspace_path": "/resolved/reports-workspace"}
                raise RuntimeError("project clone failed")
            return self.dispatch(method, params)

    monkeypatch.setattr("core.pack_tab.PackTransport", Transport)

    class OfferedModels:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "models": [
                    {"name": name}
                    for name in (
                        "claude-code/sonnet",
                        "codex/gpt-5.6-sol",
                        "qwen3.6:27b",
                    )
                ],
            }

    monkeypatch.setattr("requests.get", lambda *a, **kw: OfferedModels())
    app = SimpleNamespace()
    api = WebViewAPI(app)
    core = AppCore(api=api)
    app.core = core
    yield core, api, targets, ready
    ready.set()
    if core._pack_tasks is not None:
        core._pack_tasks.close()
        assert not Path(core._pack_tasks.control_file).exists()
    asyncio.run_coroutine_threadsafe(
        core.mcp_manager.shutdown(),
        core.event_loop,
    ).result(5)
    core.event_loop.call_soon_threadsafe(core.event_loop.stop)
    core.mcp_thread.join(5)


def wait_for_launch(service, task_id):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        state = service.call("get_pack_task", {"task_id": task_id})
        if state["status"] != "starting":
            return state
        time.sleep(0.01)
    pytest.fail("launch did not finish")


def arguments(model="codex/gpt-5.6-sol"):
    return dict(
        request_id="report-daily-1",
        host="builder",
        project="reports",
        model=model,
        prompt="Update the quarterly report; preserve the appendix.",
    )


@pytest.mark.parametrize(
    "model",
    ["claude-code/sonnet", "codex/gpt-5.6-sol", "qwen3.6:27b"],
)
def test_launch_waits_for_project_and_delivers_once(controller, model):
    core, api, targets, ready = controller
    names = {t["function"]["name"] for t in core.get_available_mcp_tools()}
    assert "packtasks_start_pack_task" in names
    replies = []
    core.call_mcp_tool("packtasks", "start_pack_task", arguments(model), replies.append)
    first = json.loads(replies[0]["content"][0]["text"])
    service = core.pack_task_service()
    assert first["status"] == "starting"
    assert (
        service.call("start_pack_task", arguments(model))["task_id"] == first["task_id"]
    )
    assert all(not t.chat_state.questions for t in targets)
    ready.set()
    state = wait_for_launch(service, first["task_id"])
    assert state["status"] == "started"
    assert state["model"] == model
    assert state["workspace_path"] == "/resolved/reports-workspace"
    assert len(core.tabs) == 1
    assert targets[-1].chat_state.questions == [arguments()["prompt"]]
    assert targets[-1].preferences["model"] == model
    assert core.preferences.get("model") != model
    with pytest.raises(ValueError, match="different arguments"):
        service.call(
            "start_pack_task",
            {**arguments(model), "prompt": "different task"},
        )
    # Python's actual queued UI event creates a Pack tab without switching focus.
    updates = api.get_pending_js()
    assert any(
        "createTabUI" in update and "false, true" in update for update in updates
    )


def test_unknown_model_fails_without_submitting_or_falling_back(controller):
    core, _, targets, ready = controller
    ready.set()
    service = core.pack_task_service()
    task = service.call("start_pack_task", arguments("unavailable-model"))
    state = wait_for_launch(service, task["task_id"])
    assert state["status"] == "failed"
    assert "not offered" in state["error"]
    assert not targets[-1].chat_state.questions
    assert targets[-1].preferences["model"] == "wrong-model"


def test_project_failure_is_reported_without_retry_or_prompt(controller, tmp_path):
    core, _, targets, ready = controller
    (tmp_path / "projects" / "reports" / "project.toml").write_text(
        '[project]\nrepo_url = "broken-repo"\n',
    )
    ready.set()
    service = core.pack_task_service()
    task = service.call("start_pack_task", arguments())
    state = wait_for_launch(service, task["task_id"])
    assert state["status"] == "failed"
    assert "project clone failed" in state["error"]
    assert len(targets) == 1
    assert not targets[0].chat_state.questions


def test_invalid_project_and_ssh_options_create_no_tabs(controller):
    core, _, _, _ = controller
    service = core.pack_task_service()
    with pytest.raises(ValueError, match="SSH destination"):
        service.call("start_pack_task", {**arguments(), "host": "-oProxyCommand=bad"})
    with pytest.raises(FileNotFoundError):
        service.call("start_pack_task", {**arguments(), "project": "missing"})
    assert not core.tabs


def test_discovery_returns_host_and_project_identifiers(controller, tmp_path):
    core, _, _, _ = controller
    (tmp_path / "pack.json").write_text(
        json.dumps(
            [
                {"hostname": "builder", "display_name": "Build machine"},
            ],
        ),
    )
    result = core.pack_task_service().call("list_pack_targets", {})
    assert result["hosts"] == [{"hostname": "builder", "display_name": "Build machine"}]
    assert [p["name"] for p in result["projects"]] == ["reports"]


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["claude", "codex"])
async def test_real_mcp_subprocess_launches_through_authenticated_controller(
    controller,
    backend,
):
    core, _, targets, ready = controller
    ready.set()
    service = core.pack_task_service()
    with pytest.raises(HTTPError) as denied:
        call_control({**service.control, "token": "wrong"}, "list_pack_targets", {})
    assert denied.value.code == 403
    if backend == "claude":
        config = Path(
            _build_claude_mcp_config_file(
                "events.jsonl",
                None,
                pack_control=service.control_file,
            ),
        )
        try:
            spec = json.loads(config.read_text())["mcpServers"]["alpaca-pack-tasks"]
        finally:
            config.unlink()
    else:
        overrides = _codex_mcp_overrides(
            "events.jsonl",
            None,
            pack_control=service.control_file,
        )
        spec = {}
        for value in overrides[1::2]:
            key, encoded = value.split("=", 1)
            prefix = "mcp_servers.alpaca-pack-tasks."
            if key.startswith(prefix):
                field = key.removeprefix(prefix)
                if field.startswith("env."):
                    spec.setdefault("env", {})[field.removeprefix("env.")] = json.loads(
                        encoded,
                    )
                else:
                    spec[field] = json.loads(encoded)
    assert service.control["token"] not in json.dumps(spec)
    if sys.platform != "win32":
        assert Path(service.control_file).stat().st_mode & 0o777 == 0o600
    params = StdioServerParameters(**spec)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            assert {t.name for t in tools.tools} == {
                "start_pack_task",
                "get_pack_task",
                "list_pack_targets",
                "create_pack_schedule",
                "list_pack_schedules",
                "update_pack_schedule",
                "control_pack_schedule",
            }
            result = await session.call_tool("start_pack_task", arguments())
            assert not result.isError
            task = json.loads(result.content[0].text)
            state = await asyncio.to_thread(wait_for_launch, service, task["task_id"])
            result = await session.call_tool(
                "get_pack_task",
                {"task_id": state["task_id"]},
            )
            assert json.loads(result.content[0].text)["status"] == "started"
            assert targets[-1].chat_state.questions == [arguments()["prompt"]]


@pytest.fixture
def scheduling(controller, monkeypatch):
    from datetime import datetime

    from core.pack_scheduler import PackScheduler

    now = datetime.fromisoformat("2026-10-17T12:00:00+00:00").timestamp()
    monkeypatch.setattr("core.pack_scheduler.time", SimpleNamespace(time=lambda: now))
    # Explicit ticks make boundary/crash interleavings deterministic. A separate
    # live-stack proof exercises the unmodified automatic worker.
    monkeypatch.setattr(PackScheduler, "start", lambda self: None)
    core, _, targets, ready = controller
    ready.set()
    service = core.pack_task_service()
    return service, service.scheduler(), targets, now


def scheduled_spec(**overrides):
    return {**arguments(), "when": {"kind": "delay", "seconds": 10}, **overrides}


def schedule_state(service, schedule_id):
    return service.call("list_pack_schedules", {"schedule_id": schedule_id})[
        "schedules"
    ][0]


def test_scheduled_launch_boundary_and_whole_turn_completion(scheduling):
    service, scheduler, targets, now = scheduling
    created = service.call("create_pack_schedule", scheduled_spec())
    sid = created["schedule_id"]
    scheduler.tick(now + 9.999)
    assert not targets
    scheduler.tick(now + 10)
    state = schedule_state(service, sid)
    occurrence = state["active_occurrence"]
    launch = wait_for_launch(service, occurrence)
    assert launch["status"] == "started"
    assert launch["session_id"] == occurrence
    scheduler.tick(now + 11)
    # The peer is not streaming, but there is no whole-turn end record yet.
    assert not targets[0].is_streaming
    assert schedule_state(service, sid)["recent_runs"][0]["status"] == "running"
    targets[0].chat_state.set_turn_timing(0, {"wall_ms": 23, "tool_count": 2})
    # Stop/error can write timing before an outstanding tool has returned.
    targets[0]._tool_handler._pending_count = 1
    scheduler.tick(now + 12)
    assert schedule_state(service, sid)["recent_runs"][0]["status"] == "running"
    targets[0]._tool_handler._pending_count = 0
    scheduler.tick(now + 13)
    state = schedule_state(service, sid)
    assert state["state"] == "finished"
    assert state["active_occurrence"] is None
    assert state["recent_runs"][0]["timing"]["tool_count"] == 2
    scheduler.tick(now + 864000)
    assert len(targets) == 1


def test_recurring_missed_runs_coalesce_without_overlap(scheduling):
    service, scheduler, targets, now = scheduling
    created = service.call(
        "create_pack_schedule",
        scheduled_spec(when={"kind": "daily", "time": "13:00", "timezone": "UTC"}),
    )
    sid = created["schedule_id"]
    overdue = now + 5 * 86400 + 3600
    scheduler.tick(overdue)
    first = schedule_state(service, sid)["active_occurrence"]
    assert wait_for_launch(service, first)["status"] == "started"
    assert schedule_state(service, sid)["next_run"] == overdue + 86400
    scheduler.tick(overdue + 3 * 86400)
    assert len(targets) == 1
    targets[0].chat_state.set_turn_timing(0, {"wall_ms": 100})
    scheduler.tick(overdue + 3 * 86400)
    scheduler.tick(overdue + 3 * 86400)
    second = schedule_state(service, sid)["active_occurrence"]
    assert second != first
    assert wait_for_launch(service, second)["status"] == "started"
    assert len(targets) == 2
    scheduler.tick(overdue + 3 * 86400)
    assert len(targets) == 2


def test_schedule_controls_update_and_durable_idempotency(scheduling):
    from core.pack_scheduler import PackScheduler

    service, scheduler, targets, now = scheduling
    spec = scheduled_spec()
    created = service.call("create_pack_schedule", spec)
    sid = created["schedule_id"]
    with pytest.raises(RuntimeError, match="another live controller"):
        PackScheduler(service)
    service.call("control_pack_schedule", {"schedule_id": sid, "action": "pause"})
    scheduler.tick(now + 100)
    assert not targets
    updated = {
        k: v
        for k, v in scheduled_spec(model="claude-code/sonnet").items()
        if k != "request_id"
    }
    updated["schedule_id"] = sid
    assert service.call("update_pack_schedule", updated)["state"] == "paused"
    scheduler.close()
    service._scheduler = None
    scheduler = service.scheduler()
    assert service.call("create_pack_schedule", spec)["schedule_id"] == sid
    with pytest.raises(ValueError, match="different arguments"):
        service.call("create_pack_schedule", {**spec, "prompt": "changed"})
    service.call("control_pack_schedule", {"schedule_id": sid, "action": "resume"})
    scheduler.tick(now + 100)
    assert (
        wait_for_launch(service, schedule_state(service, sid)["active_occurrence"])[
            "model"
        ]
        == "claude-code/sonnet"
    )
    service.call("control_pack_schedule", {"schedule_id": sid, "action": "cancel"})
    scheduler.tick(now + 200)
    assert len(targets) == 1
    with pytest.raises(ValueError, match="paused"):
        service.call("control_pack_schedule", {"schedule_id": sid, "action": "resume"})
    with pytest.raises(ValueError, match="active"):
        service.call("update_pack_schedule", updated)


@pytest.mark.parametrize("submit_before_crash", [False, True])
def test_crash_boundary_never_replays_and_requires_inspection(
    scheduling,
    monkeypatch,
    submit_before_crash,
):
    service, scheduler, targets, now = scheduling
    sid = service.call("create_pack_schedule", scheduled_spec())["schedule_id"]
    original = service.call

    def crash(method, params, **kwargs):
        if method == "start_pack_task":
            if submit_before_crash:
                original(method, params, **kwargs)
            raise SystemExit("simulated process death")
        return original(method, params, **kwargs)

    monkeypatch.setattr(service, "call", crash)
    with pytest.raises(SystemExit):
        scheduler.tick(now + 10)
    occurrence = schedule_state(service, sid)["active_occurrence"]
    if submit_before_crash:
        assert wait_for_launch(service, occurrence)["status"] == "started"
    scheduler.close()
    service._scheduler = None
    monkeypatch.setattr(service, "call", original)
    scheduler = service.scheduler()
    for _ in range(3):
        scheduler.tick(now + 999999)
    state = schedule_state(service, sid)
    assert state["recent_runs"][0]["status"] == "needs_inspection"
    assert state["recent_runs"][0]["session_id"] == occurrence
    assert len(targets) == int(submit_before_crash)
    with pytest.raises(ValueError, match="occurrence_id"):
        service.call(
            "control_pack_schedule",
            {"schedule_id": sid, "action": "acknowledge", "occurrence_id": "wrong"},
        )
    service.call(
        "control_pack_schedule",
        {"schedule_id": sid, "action": "acknowledge", "occurrence_id": occurrence},
    )
    scheduler.tick(now + 1000000)
    assert schedule_state(service, sid)["state"] == "finished"
    assert len(targets) == int(submit_before_crash)


@pytest.mark.parametrize(
    "model",
    ["claude-code/sonnet", "codex/gpt-5.6-sol", "qwen3.6:27b"],
)
@pytest.mark.asyncio
async def test_scheduling_through_real_mcp(scheduling, model):
    service, scheduler, targets, now = scheduling
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(Path(__file__).resolve().parents[1] / "pack_task_mcp_server.py")],
        env={"ALPACA_PACK_CONTROL_FILE": service.control_file},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "create_pack_schedule",
                scheduled_spec(model=model),
            )
            assert not result.isError
            sid = json.loads(result.content[0].text)["schedule_id"]
            scheduler.tick(now + 10)
            occurrence = schedule_state(service, sid)["active_occurrence"]
            assert (await asyncio.to_thread(wait_for_launch, service, occurrence))[
                "status"
            ] == "started"
            assert targets[-1].preferences["model"] == model
            result = await session.call_tool(
                "list_pack_schedules",
                {"schedule_id": sid},
            )
            assert (
                json.loads(result.content[0].text)["schedules"][0]["active_occurrence"]
                == occurrence
            )


@pytest.mark.parametrize(
    "now,when,expected",
    [
        (
            "2027-03-13T11:00:00+00:00",
            {"kind": "daily", "time": "02:30", "timezone": "America/Los_Angeles"},
            "2027-03-15T09:30:00+00:00",
        ),
        (
            "2027-03-07T11:00:00+00:00",
            {
                "kind": "weekly",
                "time": "02:30",
                "timezone": "America/Los_Angeles",
                "weekday": 6,
            },
            "2027-03-21T09:30:00+00:00",
        ),
        (
            "2026-11-01T08:29:00+00:00",
            {"kind": "daily", "time": "01:30", "timezone": "America/Los_Angeles"},
            "2026-11-01T08:30:00+00:00",
        ),
        (
            "2026-11-01T08:31:00+00:00",
            {"kind": "daily", "time": "01:30", "timezone": "America/Los_Angeles"},
            "2026-11-02T09:30:00+00:00",
        ),
        (
            "2026-10-17T12:00:00+00:00",
            {"kind": "daily", "time": "18:00", "timezone": "Asia/Kathmandu"},
            "2026-10-17T12:15:00+00:00",
        ),
        (
            "2026-10-17T12:00:00+00:00",
            {"kind": "once", "at": "2026-10-18T09:00:00-07:00"},
            "2026-10-18T16:00:00+00:00",
        ),
    ],
)
def test_schedule_calendar_contract(scheduling, monkeypatch, now, when, expected):
    from datetime import datetime

    service, _, targets, _ = scheduling
    timestamp = datetime.fromisoformat(now).timestamp()
    monkeypatch.setattr(
        "core.pack_scheduler.time",
        SimpleNamespace(time=lambda: timestamp),
    )
    created = service.call("create_pack_schedule", scheduled_spec(when=when))
    assert created["next_run"] == datetime.fromisoformat(expected).timestamp()
    assert not targets


@pytest.mark.parametrize(
    "when",
    [
        {"kind": "once", "at": "2026-10-18T12:00:00"},
        {"kind": "once", "at": "2026-10-17T11:00:00Z"},
        {"kind": "delay", "seconds": -1},
        {"kind": "delay", "seconds": True},
        {"kind": "daily", "time": "9:00", "timezone": "UTC"},
        {"kind": "daily", "time": "09:00", "timezone": "not-a-zone"},
        {"kind": "weekly", "time": "09:00", "timezone": "UTC", "weekday": 7},
    ],
)
def test_invalid_schedule_is_not_saved_or_launched(scheduling, when):
    service, scheduler, targets, now = scheduling
    with pytest.raises((ValueError, KeyError)):
        service.call("create_pack_schedule", scheduled_spec(when=when))
    assert service.call("list_pack_schedules", {}) == {"schedules": []}
    scheduler.tick(now + 1000000)
    assert not targets
