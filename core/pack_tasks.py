"""App-owned Pack task launch service and authenticated loopback control channel.

The same tool contract is used by ordinary models and the CLI MCP adapter.
Immediate launch records are memory-only; schedules use a separate durable store.
"""

from __future__ import annotations

import json
import secrets
import tempfile
import threading
import uuid
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.request import Request
from urllib.request import urlopen

from core.pack_scheduler import TOOLS as SCHEDULE_TOOLS
from core.pack_scheduler import PackScheduler
from core.projects import list_projects
from core.projects import load_project

TOOLS = [
    {
        "name": "start_pack_task",
        "description": (
            "Start a task in a NEW Pack tab on an SSH host with a configured project. "
            "This executes code remotely using the host's existing permissions. "
            "Use only when the user requests remote work. Returns immediately; poll "
            "get_pack_task. Reuse request_id on retries to avoid duplicate launches. "
            "Model is explicit: e.g. claude-code/sonnet, codex/gpt-5.6-sol, or a "
            "regular model identifier. No backend fallback. Not a scheduler."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                key: {"type": "string", "minLength": 1}
                for key in ("request_id", "host", "project", "model", "prompt")
            },
            "required": ["request_id", "host", "project", "model", "prompt"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_pack_task",
        "description": (
            "Get launch status by task_id. started means the prompt was accepted, "
            "NOT completed. Failed launches are never automatically retried. "
            "If delivery is uncertain, inspect the retained tab before retrying."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"task_id": {"type": "string"}},
            "required": ["task_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_pack_targets",
        "description": (
            "List configured SSH hosts and project names on this controller. "
            "A host may also be an explicit SSH alias supplied by the user."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
] + SCHEDULE_TOOLS


class PackTaskService:
    def __init__(self, core: Any) -> None:
        self.core = core
        self._lock = threading.Lock()
        self._scheduler_lock = threading.Lock()
        self._scheduler: PackScheduler | None = None
        self._tasks: dict[str, dict[str, Any]] = {}
        self._requests: dict[str, tuple[dict[str, str], str]] = {}
        token = secrets.token_urlsafe(32)
        service = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                if not secrets.compare_digest(
                    self.headers.get("Authorization", ""),
                    f"Bearer {token}",
                ):
                    self.send_error(403)
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 1_000_000:
                        raise ValueError("invalid request size")
                    request = json.loads(self.rfile.read(length))
                    result = service.call(request["method"], request.get("params", {}))
                    body = json.dumps({"result": result}).encode()
                except Exception as exc:
                    body = json.dumps({"error": str(exc)}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args: Any) -> None:
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.control = {
            "url": f"http://127.0.0.1:{self._server.server_port}",
            "token": token,
        }
        # mkstemp permissions are owner-only. Pass the path, not this secret,
        # through HTTP payloads and Codex's process-list-visible -c arguments.
        with tempfile.NamedTemporaryFile(
            mode="w",
            prefix="alpaca_pack_control_",
            suffix=".json",
            delete=False,
        ) as control_file:
            json.dump(self.control, control_file)
            self.control_file = control_file.name
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def close(self) -> None:
        if self._scheduler is not None:
            self._scheduler.close()
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)
        Path(self.control_file).unlink(missing_ok=True)

    def scheduler(self) -> PackScheduler:
        with self._scheduler_lock:
            if self._scheduler is None:
                self._scheduler = PackScheduler(self)
                self._scheduler.start()
            return self._scheduler

    @staticmethod
    def validate_task(params: dict[str, str]) -> Any:
        host = params["host"]
        if host.startswith("-") or any(c.isspace() or ord(c) < 32 for c in host):
            raise ValueError("host must be a single SSH destination, not SSH options")
        if any(not value.strip() for value in params.values()):
            raise ValueError("task arguments must not be blank")
        return load_project(params["project"], host=host)

    def call(
        self,
        method: str,
        params: dict[str, Any],
        *,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        schema = next((t["inputSchema"] for t in TOOLS if t["name"] == method), None)
        if schema is None:
            raise ValueError(f"Unknown Pack task tool: {method}")
        from jsonschema import validate

        validate(params, schema)
        if method in {tool["name"] for tool in SCHEDULE_TOOLS}:
            return self.scheduler().call(method, params)
        if method == "list_pack_targets":
            from webview_api import WebViewAPI

            return {
                "hosts": WebViewAPI._read_pack_hosts(),
                "projects": [p.to_info() for p in list_projects()],
            }
        with self._lock:
            if method == "get_pack_task":
                return dict(self._tasks[params["task_id"]])
            request_id = params["request_id"]
            if request_id in self._requests:
                previous, task_id = self._requests[request_id]
                if previous != params:
                    raise ValueError("request_id already used with different arguments")
                return dict(self._tasks[task_id])
            project = self.validate_task(params)
            task_id = task_id or uuid.uuid4().hex
            payload = project.to_payload(task_id)
            task = {
                "task_id": task_id,
                "session_id": task_id,
                "status": "starting",
                "host": params["host"],
                "project": params["project"],
                "model": params["model"],
                "workspace_path": payload["workspace_path"],
            }
            self._tasks[task_id] = task
            self._requests[request_id] = (dict(params), task_id)
            threading.Thread(
                target=self._launch,
                args=(task_id, dict(params), payload),
                daemon=True,
            ).start()
            return dict(task)

    def _launch(
        self,
        task_id: str,
        params: dict[str, str],
        payload: dict[str, Any],
    ) -> None:
        try:
            tab_id, tab = self.core.create_pack_tab(
                params["host"],
                task_id,
                title=f"Task: {params['project']}",
                project_payload=payload,
            )
            with self._lock:
                self._tasks[task_id].update(
                    tab_id=tab_id,
                    conversation_id=tab.conversation_id,
                )
            api = self.core.api
            if api is not None and hasattr(api, "on_pack_task_created"):
                api.on_pack_task_created(tab)
            # Select the model only after remote validation, not by seeding
            # unvalidated first-launch preferences before project setup.
            tab.start_task(params["model"], params["prompt"])
            with self._lock:
                self._tasks[task_id].update(
                    status="started",
                    workspace_path=tab.workspace_path,
                    answer_index=tab._current_answer_index,
                )
        except Exception as exc:
            with self._lock:
                self._tasks[task_id].update(status="failed", error=str(exc))


def call_control(control: dict[str, str], method: str, params: dict[str, Any]) -> Any:
    request = Request(
        control["url"],
        data=json.dumps({"method": method, "params": params}).encode(),
        headers={
            "Authorization": f"Bearer {control['token']}",
            "Content-Type": "application/json",
        },
    )
    with urlopen(request, timeout=15) as response:
        reply = json.load(response)
    if "error" in reply:
        raise RuntimeError(reply["error"])
    return reply["result"]
