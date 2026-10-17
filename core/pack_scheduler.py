"""Durable, process-owned Pack schedules. A claimed launch is never retried.

The separate SQLite ownership transaction is held for this object's lifetime.
It excludes other controllers and is released by the OS even after a crash.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
import uuid
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)
STORE = "pack_schedules.db"
TASK_FIELDS = ("host", "project", "model", "prompt")
TASK_SCHEMA = {key: {"type": "string", "minLength": 1} for key in TASK_FIELDS}
WHEN_SCHEMA = {
    "type": "object",
    "description": (
        'One of {"kind":"once","at":"ISO8601 with UTC offset"}, '
        '{"kind":"delay","seconds":60}, or '
        '{"kind":"daily"|"weekly","time":"HH:MM",'
        '"timezone":"America/Los_Angeles","weekday":0}. '
        "weekday is required only for weekly: Monday=0, Sunday=6. "
        "DST: skip nonexistent local times; repeated times fire once, at the first occurrence."
    ),
}
TOOLS = [
    {
        "name": "create_pack_schedule",
        "description": (
            "Schedule remote code execution in a NEW Pack tab/workspace per occurrence. "
            "Only when explicitly authorized by the user. Persisted, best effort while "
            "this owning app/daemon runs; no wake/autostart. No overlapping runs per "
            "schedule. Missed recurring slots coalesce into one run. Reuse request_id "
            "for safe retries. Models: claude-code/sonnet, codex/<model>, or regular ID."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                **TASK_SCHEMA,
                "request_id": {"type": "string", "minLength": 1},
                "when": WHEN_SCHEMA,
            },
            "required": [*TASK_FIELDS, "request_id", "when"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_pack_schedules",
        "description": (
            "List persisted schedules, next UTC run, active occurrence, and recent runs. "
            "finished means the turn ended, NOT that the requested work succeeded. "
            "needs_inspection blocks further launches, even across restarts."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"schedule_id": {"type": "string"}},
            "additionalProperties": False,
        },
    },
    {
        "name": "update_pack_schedule",
        "description": (
            "Replace a schedule's full task specification and time. Refused while an "
            "occurrence is active or needs inspection. Paused schedules stay paused."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                **TASK_SCHEMA,
                "schedule_id": {"type": "string"},
                "when": WHEN_SCHEMA,
            },
            "required": [*TASK_FIELDS, "schedule_id", "when"],
            "additionalProperties": False,
        },
    },
    {
        "name": "control_pack_schedule",
        "description": (
            "Pause/resume/cancel future launches; never stops a claimed/running task. "
            "acknowledge clears a needs_inspection occurrence ONLY after the user "
            "has inspected its remote session and confirmed it is no longer running. "
            "Requires occurrence_id; never retries that occurrence. Cancel is terminal."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "schedule_id": {"type": "string"},
                "action": {"enum": ["pause", "resume", "cancel", "acknowledge"]},
                "occurrence_id": {"type": "string"},
            },
            "required": ["schedule_id", "action"],
            "additionalProperties": False,
        },
    },
]


def normalize_when(when: dict[str, Any], now: float) -> dict[str, Any]:
    kind = when.get("kind")
    if kind == "delay" and set(when) == {"kind", "seconds"}:
        seconds = when["seconds"]
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
            raise ValueError("seconds must be a number")
        if not 0 < seconds <= 366 * 86400:
            raise ValueError("delay must be positive and at most 366 days")
        return {
            "kind": "once",
            "at": datetime.fromtimestamp(now + seconds, UTC).isoformat(),
        }
    if kind == "once" and set(when) == {"kind", "at"}:
        at = datetime.fromisoformat(when["at"])
        if at.tzinfo is None or at.timestamp() <= now:
            raise ValueError(
                "at must be a future timestamp with an explicit UTC offset",
            )
        return {"kind": "once", "at": at.astimezone(UTC).isoformat()}
    fields = {"kind", "time", "timezone"}
    if kind == "weekly":
        fields.add("weekday")
        if type(when.get("weekday")) is not int or not 0 <= when["weekday"] <= 6:
            raise ValueError("weekday must be an integer from 0 (Monday) to 6")
    if kind not in {"daily", "weekly"} or set(when) != fields:
        raise ValueError("invalid schedule time specification")
    parsed = datetime.strptime(when["time"], "%H:%M")
    if parsed.strftime("%H:%M") != when["time"]:
        raise ValueError("time must use HH:MM")
    ZoneInfo(when["timezone"])
    return dict(when)


def next_after(when: dict[str, Any], now: float) -> float | None:
    if when["kind"] == "once":
        at = datetime.fromisoformat(when["at"]).timestamp()
        return at if at > now else None
    zone = ZoneInfo(when["timezone"])
    today = datetime.fromtimestamp(now, zone).date()
    hour, minute = map(int, when["time"].split(":"))
    for offset in range(15):
        day = today + timedelta(days=offset)
        if when["kind"] == "weekly" and day.weekday() != when["weekday"]:
            continue
        local = datetime(day.year, day.month, day.day, hour, minute, tzinfo=zone)
        timestamp = local.timestamp()
        # zoneinfo accepts nonexistent wall times; the UTC round trip rejects them.
        if datetime.fromtimestamp(timestamp, zone).replace(
            tzinfo=None,
        ) != local.replace(tzinfo=None):
            continue
        if timestamp > now:
            return timestamp
    raise ValueError("no upcoming occurrence")


class PackScheduler:
    def __init__(self, service: Any, path: str | Path = STORE) -> None:
        self.service = service
        self._lock = threading.RLock()
        self._tick_lock = threading.Lock()
        self._stop = threading.Event()
        path = Path(path).absolute()
        path.touch(mode=0o600, exist_ok=True)
        Path(str(path) + ".owner.db").touch(mode=0o600, exist_ok=True)
        self._owner = sqlite3.connect(
            str(path) + ".owner.db",
            timeout=0,
            check_same_thread=False,
        )
        try:
            self._owner.execute("BEGIN EXCLUSIVE")
        except sqlite3.OperationalError as exc:
            self._owner.close()
            raise RuntimeError(
                "Schedules are already owned by another live controller",
            ) from exc
        self._db = sqlite3.connect(path, check_same_thread=False)
        with self._db:
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS schedules (id TEXT PRIMARY KEY, request TEXT UNIQUE, data TEXT)",
            )
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS occurrences (id TEXT PRIMARY KEY, schedule_id TEXT, data TEXT)",
            )
            for row in self._db.execute("SELECT data FROM occurrences").fetchall():
                run = json.loads(row[0])
                if run["status"] in {"claiming", "running"}:
                    run.update(
                        status="needs_inspection",
                        error="Controller restarted; delivery/completion is uncertain. Inspect session before acknowledging.",
                    )
                    self._save_run(run)
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        with self._lock:
            if self._thread is None:
                self._thread = threading.Thread(target=self._work, daemon=True)
                self._thread.start()

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
        with self._lock:
            self._db.close()
            self._owner.close()

    def _work(self) -> None:
        while not self._stop.wait(1):
            try:
                self.tick()
            except Exception:
                logger.exception(
                    "Pack scheduler tick failed; no launch will be retried",
                )

    def _schedule(self, schedule_id: str) -> dict[str, Any]:
        row = self._db.execute(
            "SELECT data FROM schedules WHERE id=?",
            (schedule_id,),
        ).fetchone()
        if row is None:
            raise ValueError("Unknown schedule_id")
        schedule: dict[str, Any] = json.loads(row[0])
        return schedule

    def _save(self, schedule: dict[str, Any]) -> None:
        self._db.execute(
            "UPDATE schedules SET data=? WHERE id=?",
            (json.dumps(schedule), schedule["schedule_id"]),
        )

    def _save_run(self, run: dict[str, Any]) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO occurrences VALUES (?, ?, ?)",
            (run["occurrence_id"], run["schedule_id"], json.dumps(run)),
        )

    def call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        with self._lock, self._db:
            if method == "list_pack_schedules":
                schedules: list[dict[str, Any]] = (
                    [self._schedule(params["schedule_id"])]
                    if "schedule_id" in params
                    else [
                        json.loads(r[0])
                        for r in self._db.execute("SELECT data FROM schedules")
                    ]
                )
                for schedule in schedules:
                    schedule["recent_runs"] = [
                        json.loads(r[0])
                        for r in self._db.execute(
                            "SELECT data FROM occurrences WHERE schedule_id=? ORDER BY rowid DESC LIMIT 20",
                            (schedule["schedule_id"],),
                        )
                    ]
                return {"schedules": schedules}
            if method == "create_pack_schedule":
                if not params["request_id"].strip():
                    raise ValueError("request_id must not be blank")
                request = json.dumps(params, sort_keys=True)
                rows = self._db.execute(
                    "SELECT data FROM schedules WHERE request=?",
                    (params["request_id"],),
                ).fetchone()
                if rows is not None:
                    existing: dict[str, Any] = json.loads(rows[0])
                    if existing["original_request"] != request:
                        raise ValueError(
                            "request_id already used with different arguments",
                        )
                    return existing
                schedule = {
                    "schedule_id": uuid.uuid4().hex,
                    "state": "scheduled",
                    "active_occurrence": None,
                    "original_request": request,
                }
            else:
                schedule = self._schedule(params["schedule_id"])
            if method in {"create_pack_schedule", "update_pack_schedule"}:
                if schedule["active_occurrence"] or schedule["state"] == "cancelled":
                    raise ValueError(
                        "Cannot update an active, uncertain, or cancelled schedule",
                    )
                self.service.validate_task({key: params[key] for key in TASK_FIELDS})
                now = time.time()
                when = normalize_when(params["when"], now)
                schedule.update(
                    task={key: params[key] for key in TASK_FIELDS},
                    when=when,
                    next_run=next_after(when, now),
                )
                if schedule["state"] == "finished":
                    schedule["state"] = "scheduled"
                if method == "create_pack_schedule":
                    self._db.execute(
                        "INSERT INTO schedules VALUES (?, ?, ?)",
                        (
                            schedule["schedule_id"],
                            params["request_id"],
                            json.dumps(schedule),
                        ),
                    )
                else:
                    self._save(schedule)
                return schedule
            if method != "control_pack_schedule":
                raise ValueError("Unknown schedule tool")
            action = params["action"]
            if action == "acknowledge":
                occurrence_id = params.get("occurrence_id")
                if not occurrence_id or occurrence_id != schedule["active_occurrence"]:
                    raise ValueError("Specify the active occurrence_id")
                run = json.loads(
                    self._db.execute(
                        "SELECT data FROM occurrences WHERE id=?",
                        (occurrence_id,),
                    ).fetchone()[0],
                )
                if run["status"] != "needs_inspection":
                    raise ValueError(
                        "Only needs_inspection occurrences can be acknowledged",
                    )
                run["status"] = "acknowledged"
                self._save_run(run)
                schedule["active_occurrence"] = None
                if schedule["next_run"] is None and schedule["state"] != "cancelled":
                    schedule["state"] = "finished"
            elif action == "cancel":
                schedule["state"] = "cancelled"
            elif action == "pause":
                if schedule["state"] != "scheduled":
                    raise ValueError("Only scheduled tasks can be paused")
                schedule["state"] = "paused"
            elif action == "resume":
                if schedule["state"] != "paused":
                    raise ValueError("Only paused schedules can be resumed")
                schedule["state"] = "scheduled"
            self._save(schedule)
            return schedule

    def tick(self, now: float | None = None) -> None:
        """Dispatch due occurrences; public deterministic clock hook for checks."""
        now = time.time() if now is None else now
        with self._tick_lock:
            with self._lock:
                ids = [row[0] for row in self._db.execute("SELECT id FROM schedules")]
            for schedule_id in ids:
                if self._stop.is_set():
                    return
                newly_claimed = False
                with self._lock, self._db:
                    schedule = self._schedule(schedule_id)
                    active = schedule["active_occurrence"]
                    if active:
                        run = json.loads(
                            self._db.execute(
                                "SELECT data FROM occurrences WHERE id=?",
                                (active,),
                            ).fetchone()[0],
                        )
                    elif (
                        schedule["state"] != "scheduled"
                        or schedule["next_run"] is None
                        or schedule["next_run"] > now
                    ):
                        continue
                    else:
                        newly_claimed = True
                        active = uuid.uuid4().hex
                        run = {
                            "occurrence_id": active,
                            "schedule_id": schedule_id,
                            "session_id": active,
                            "status": "claiming",
                            "due_at": schedule["next_run"],
                            "claimed_at": now,
                        }
                        schedule.update(
                            active_occurrence=active,
                            next_run=next_after(schedule["when"], now),
                        )
                        self._save(schedule)
                        self._save_run(run)
                if run["status"] == "needs_inspection":
                    continue
                try:
                    if run["status"] == "claiming":
                        if not newly_claimed:
                            raise RuntimeError(
                                "Previous launch outcome was not saved; inspect session",
                            )
                        task = self.service.call(
                            "start_pack_task",
                            {**schedule["task"], "request_id": active},
                            task_id=active,
                        )
                        run.update(status="running", task_id=task["task_id"])
                    else:
                        task = self.service.call("get_pack_task", {"task_id": active})
                        run["launch"] = task
                        if task["status"] == "failed":
                            raise RuntimeError(task["error"])
                        if task["status"] == "started":
                            tab = self.service.core.tabs[task["tab_id"]]
                            status = tab.get_task_status(task["answer_index"])
                            if status["status"] == "finished":
                                run.update(
                                    status="finished",
                                    finished_at=now,
                                    timing=status["timing"],
                                )
                except Exception as exc:
                    run.update(status="needs_inspection", error=str(exc))
                with self._lock, self._db:
                    self._save_run(run)
                    schedule = self._schedule(schedule_id)
                    if run["status"] == "finished":
                        schedule["active_occurrence"] = None
                        if (
                            schedule["next_run"] is None
                            and schedule["state"] != "cancelled"
                        ):
                            schedule["state"] = "finished"
                        self._save(schedule)
