"""Local child conversations and their parent-owned task results.

The normal ChatTab tool loop does all execution. This coordinator only owns
task identity, admission, cancellation and the join between conversations.
"""

from __future__ import annotations

import copy
import json
import threading
import time
import uuid
from typing import Any

TOOL_NAMES = {"spawn_agent", "wait_agents", "cancel_agent"}
TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "internal_spawn_agent",
            "description": (
                "Delegate a bounded task to a visible child conversation. Returns a task_id "
                "immediately. Supply all needed context; children do not see your history. "
                "They share your filesystem, so avoid overlapping edits. Use wait_agents "
                "to collect final answers. At most four children run at once; no nesting."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {"type": "string"},
                    "context": {"type": "string"},
                    "title": {"type": "string"},
                },
                "required": ["task"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "internal_wait_agents",
            "description": (
                "Wait for all listed child tasks, up to timeout_seconds (0–60, default 60). "
                "Returns final answers/statuses and pending task IDs. If pending is nonempty, "
                "wait again. Results remain readable on repeated waits."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                    },
                    "timeout_seconds": {"type": "number", "minimum": 0, "maximum": 60},
                },
                "required": ["task_ids"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "internal_cancel_agent",
            "description": "Cancel one of your child tasks. Already finished results are unchanged.",
            "parameters": {
                "type": "object",
                "properties": {"task_id": {"type": "string"}},
                "required": ["task_id"],
                "additionalProperties": False,
            },
        },
    },
]


class SubagentCoordinator:
    MAX_RUNNING = 4

    def __init__(self, core: Any) -> None:
        self.core = core
        self.condition = threading.Condition(threading.RLock())

    def available(self, tab: Any) -> bool:
        from core.chat_tab import ChatTab

        return (
            isinstance(tab, ChatTab)
            and callable(getattr(self.core.api, "_safe_evaluate_js", None))
            and self.core.tabs.get(tab.tab_id) is tab
            and not tab.subagent
            and not str(tab.preferences.get("model", "")).startswith(
                ("claude-code/", "codex/"),
            )
        )

    def call(
        self,
        parent: Any,
        name: str,
        arguments: dict[str, Any],
        answer_index: int,
        call_id: str,
    ) -> dict[str, Any]:
        try:
            if not self.available(parent):
                raise ValueError(
                    "Subagents require a local parent using an app-managed model; nesting is disabled",
                )
            if name == "spawn_agent":
                return self.spawn(parent, arguments, answer_index, call_id)
            if name == "wait_agents":
                return self.wait(parent, arguments, answer_index)
            if name == "cancel_agent":
                with self.condition:
                    record = self._owned(parent, arguments.get("task_id"))
                    child = self._cancel(record)
                    result = {"success": True, **copy.deepcopy(record)}
                if child is not None:
                    child.stop_streaming()
                return result
            raise ValueError("Unknown subagent tool")
        except (ValueError, TypeError) as exc:
            return {"success": False, "error": str(exc)}

    def _owned(self, parent: Any, task_id: Any) -> dict[str, Any]:
        if not isinstance(task_id, str) or task_id not in parent.subagent_tasks:
            raise ValueError(
                "Unknown task or task does not belong to this conversation",
            )
        record: dict[str, Any] = parent.subagent_tasks[task_id]
        if record["parent_conversation_id"] != parent.conversation_id:
            raise ValueError("Task does not belong to this conversation")
        return record

    def spawn(
        self,
        parent: Any,
        args: dict[str, Any],
        answer_index: int,
        call_id: str,
    ) -> dict[str, Any]:
        task = args.get("task")
        context = args.get("context", "")
        title = args.get("title", "Subagent")
        if not isinstance(task, str) or not task.strip():
            raise ValueError("task must be a nonempty string")
        if not isinstance(context, str) or not isinstance(title, str):
            raise ValueError("context and title must be strings")
        with self.condition:
            if (
                parent.stop_streaming_flag.is_set()
                or answer_index != parent._current_answer_index
            ):
                raise ValueError("Parent turn is no longer active")
            for record in parent.subagent_tasks.values():
                if (
                    record["parent_answer_index"] == answer_index
                    and record["spawn_call_id"] == call_id
                ):
                    return {"success": True, **copy.deepcopy(record)}
            running = sum(
                record["status"] == "running"
                for tab in list(self.core.tabs.values())
                for record in getattr(tab, "subagent_tasks", {}).values()
            )
            if running >= self.MAX_RUNNING:
                raise ValueError(
                    "Four subagents are already running; wait or cancel before spawning more",
                )
            child_id, child = self.core.create_tab(title.strip()[:80] or "Subagent")
            task_id = f"agent-{uuid.uuid4().hex}"
            record = {
                "task_id": task_id,
                "parent_conversation_id": parent.conversation_id,
                "child_conversation_id": child.conversation_id,
                "parent_answer_index": answer_index,
                "spawn_call_id": call_id,
                "status": "running",
                "result": "",
                "error": "",
                "transcript": f"alpaca://conv/{child.conversation_id}",
            }
            parent.subagent_tasks[task_id] = record
            child.subagent = copy.deepcopy(record)
            child.preferences = parent.preferences.copy()
            for field in (
                "project_name",
                "workspace_path",
                "project_fingerprint",
                "project_runbook",
            ):
                setattr(child, field, getattr(parent, field))
            child._summary_handler._generated = True
            api = self.core.api
            try:
                api._safe_evaluate_js(
                    f"app.tabManager.createTabUI({json.dumps(child_id)}, {json.dumps(child.title)}, false);"
                    f"app.tabManager.setConversationId({json.dumps(child_id)}, {child.conversation_id});",
                )
                self.notify(child)
                prompt = (
                    "You are a subagent completing a delegated task. Work only on this task. "
                    "Your final answer is returned to the parent automatically after all tools finish. "
                    "Report findings, evidence, changes and limitations clearly. You cannot spawn children.\n\n"
                    f"Task:\n{task}\n\nContext supplied by parent:\n{context}"
                )
                child.handle_user_message(prompt, [])
            except Exception as exc:
                self.finish(child, 0, "failed", str(exc))
            return {"success": True, **copy.deepcopy(record)}

    def wait(
        self,
        parent: Any,
        args: dict[str, Any],
        answer_index: int,
    ) -> dict[str, Any]:
        ids = args.get("task_ids")
        timeout = args.get("timeout_seconds", 60)
        if (
            not isinstance(ids, list)
            or not ids
            or not all(isinstance(item, str) for item in ids)
        ):
            raise ValueError("task_ids must be a nonempty list of strings")
        if not isinstance(timeout, (int, float)) or not 0 <= timeout <= 60:
            raise ValueError("timeout_seconds must be between 0 and 60")
        deadline = time.monotonic() + timeout
        with self.condition:
            records = [self._owned(parent, task_id) for task_id in dict.fromkeys(ids)]
            while any(record["status"] == "running" for record in records):
                if (
                    parent.stop_streaming_flag.is_set()
                    or answer_index != parent._current_answer_index
                ):
                    return {
                        "success": False,
                        "error": "Parent turn stopped while waiting",
                    }
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self.condition.wait(min(remaining, 0.2))
            return {
                "success": True,
                "results": copy.deepcopy(
                    [record for record in records if record["status"] != "running"],
                ),
                "pending": [
                    record["task_id"]
                    for record in records
                    if record["status"] == "running"
                ],
            }

    def finish(
        self,
        child: Any,
        answer_index: int,
        status: str,
        error: str = "",
    ) -> None:
        with self.condition:
            info = child.subagent
            # Only the delegated first turn reports. Later human follow-ups are normal chat.
            if not info or answer_index != 0 or info["status"] != "running":
                return
            result = ""
            if status == "completed" and child.chat_state.answers:
                result = child.chat_state.answers[0].get_text_only_content()
            info.update(status=status, result=result, error=error)
            for parent in list(self.core.tabs.values()):
                if parent.conversation_id == info["parent_conversation_id"]:
                    record = parent.subagent_tasks.get(info["task_id"])
                    if record is not None and record["status"] == "running":
                        record.update(status=status, result=result, error=error)
                    break
            self.condition.notify_all()
            self.notify(child)

    def _cancel(self, record: dict[str, Any]) -> Any:
        if record["status"] != "running":
            return None
        for child in list(self.core.tabs.values()):
            if child.conversation_id == record["child_conversation_id"]:
                self.finish(child, 0, "cancelled")
                return child
        record.update(status="interrupted", error="Child tab is no longer open")
        self.condition.notify_all()
        return None

    def stop_tab(self, tab: Any) -> None:
        with self.condition:
            children = [
                self._cancel(record)
                for record in getattr(tab, "subagent_tasks", {}).values()
            ]
            if getattr(tab, "subagent", None):
                self.finish(tab, 0, "cancelled")
        # Closing a streaming HTTP response can block. Never hold the task
        # lock here: the stream's finally path also reports through it.
        for child in children:
            if child is not None:
                child.stop_streaming()

    def snapshot(self, tab: Any) -> dict[str, Any]:
        with self.condition:
            data = {}
            if tab.subagent:
                data["subagent"] = copy.deepcopy(tab.subagent)
            if tab.subagent_tasks:
                data["subagent_tasks"] = copy.deepcopy(tab.subagent_tasks)
            return data

    def restore(self, tab: Any, data: dict[str, Any]) -> None:
        with self.condition:
            tab.subagent = copy.deepcopy(data.get("subagent"))
            # Clones/handoffs get fresh conversation IDs and must not acquire
            # ownership of the source conversation's children.
            if (
                tab.subagent
                and tab.subagent["child_conversation_id"] != tab.conversation_id
            ):
                tab.subagent = None
            tab.subagent_tasks = {
                key: copy.deepcopy(record)
                for key, record in data.get("subagent_tasks", {}).items()
                if record["parent_conversation_id"] == tab.conversation_id
            }
            for record in [tab.subagent, *tab.subagent_tasks.values()]:
                if record and record["status"] == "running":
                    record.update(
                        status="interrupted",
                        error="App restarted; local tasks are not automatically replayed",
                    )
            self.notify(tab)

    def notify(self, tab: Any) -> None:
        if tab.subagent and self.core.api is not None:
            info = {
                key: tab.subagent[key]
                for key in ("parent_conversation_id", "task_id", "status")
            }
            self.core.api._safe_evaluate_js(
                f"app.tabManager.setSubagentInfo({json.dumps(tab.tab_id)}, {json.dumps(info)});",
            )
