from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest
from mcp import ClientSession
from mcp import StdioServerParameters
from mcp.client.stdio import stdio_client

import conversation_mcp_server
from anthropic_ollama_server import _cli_mcp_servers
from chat_state import ChatState
from core.app_core import AppCore
from core.chat_tab_base import ChatTabBase
from core.subagents import SubagentCoordinator
from database import ConversationDatabase


@pytest.mark.asyncio
async def test_lists_only_conversation_tools() -> None:
    tools = await conversation_mcp_server.list_tools()
    assert {tool.name for tool in tools} == conversation_mcp_server.TOOL_NAMES


@pytest.mark.asyncio
async def test_search_reads_explicit_database_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "history" / "conversations.db"
    db_path.parent.mkdir()
    db = ConversationDatabase(str(db_path))
    conversation_id = db.allocate_conversation_id()
    db.store_conversation(
        conversation_id,
        "Backcountry skiing plans",
        {
            "chat_state": {
                "questions": ["Which avalanche course should I take?"],
                "answers": [
                    {
                        "components": [
                            {"type": "text", "content": "Start with an AIARE 1."},
                        ],
                    },
                ],
            },
        },
    )
    monkeypatch.setenv(conversation_mcp_server.DATABASE_ENV, str(db_path))

    result = await conversation_mcp_server.call_tool(
        "search_conversations",
        {"query": "avalanche", "search_content": True},
    )

    assert "Backcountry skiing plans" in result[0].text
    assert f"alpaca://conv/{conversation_id}" in result[0].text


@pytest.mark.asyncio
async def test_dump_uses_explicit_database_but_writes_requested_export(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "conversations.db"
    db = ConversationDatabase(str(db_path))
    conversation_id = db.allocate_conversation_id()
    db.store_conversation(
        conversation_id,
        "Woodworking",
        {"chat_state": {"questions": ["How do I cut dovetails?"], "answers": []}},
    )
    export_path = tmp_path / "history.json"
    monkeypatch.setenv(conversation_mcp_server.DATABASE_ENV, str(db_path))

    result = await conversation_mcp_server.call_tool(
        "dump_conversations",
        {"output_path": str(export_path), "include_content": True},
    )

    assert "1 conversation(s)" in result[0].text
    exported = json.loads(export_path.read_text())
    assert exported[0]["title"] == "Woodworking"
    assert exported[0]["turns"][0]["question"] == "How do I cut dovetails?"


@pytest.mark.asyncio
async def test_live_and_saved_history_over_real_stdio(tmp_path, monkeypatch):
    """Use the CLI's actual configuration, MCP subprocess and app control channel."""
    monkeypatch.chdir(tmp_path)
    core = AppCore.__new__(AppCore)
    core.db = ConversationDatabase(str(tmp_path / "conversations.db"))
    core.preferences = {}
    core.tabs = {}
    core.subagents = SubagentCoordinator(core)
    core._pack_tasks = None
    core._pack_tasks_lock = threading.Lock()

    def tab(title, question, answer):
        item = ChatTabBase(title, title, core, core.db.allocate_conversation_id())
        item.chat_state = ChatState.from_dict(
            {
                "questions": [question],
                "answers": [
                    {
                        "components": [
                            {"type": "text", "content": answer},
                            {
                                "type": "tool_result",
                                "id": "r1",
                                "content": "live tool evidence",
                            },
                        ],
                    },
                ],
            },
        )
        return item

    closed = tab("History closed", "saved question", "saved answer")
    core.store_tab_in_database(closed)
    live = tab("History open", "unsaved needle", "fresh answer")
    revived = tab("History stale", "old question", "stale answer")
    core.store_tab_in_database(revived)
    revived.title = "History revived"
    revived.chat_state = live.chat_state
    core.tabs = {live.tab_id: live, revived.tab_id: revived}
    service = core.pack_task_service()
    try:
        spec = _cli_mcp_servers(
            str(tmp_path / "media.jsonl"),
            str(tmp_path),
            pack_control=service.control_file,
        )["conversation-history"]
        async with stdio_client(StdioServerParameters(**spec)) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                async def text(name, args):
                    result = await session.call_tool(name, args)
                    assert not result.isError
                    return "\n".join(c.text for c in result.content if c.type == "text")

                search = await text("search_conversations", {"query": "History"})
                assert "History closed" in search
                assert "History open" in search
                assert "History revived" in search
                assert "History stale" not in search
                assert search.count(f"alpaca://conv/{revived.conversation_id}") == 1
                content = await text(
                    "search_conversations",
                    {
                        "query": "unsaved needle",
                        "search_content": True,
                    },
                )
                assert "History open" in content and "History revived" in content
                for item, expected in [
                    (live, "fresh answer"),
                    (revived, "fresh answer"),
                    (closed, "saved answer"),
                ]:
                    transcript = await text(
                        "get_conversation",
                        {"conversation_id": item.conversation_id},
                    )
                    assert expected in transcript
                    assert "stale answer" not in transcript
                details = await text(
                    "get_tool_details",
                    {
                        "conversation_id": live.conversation_id,
                        "turn_index": 0,
                    },
                )
                assert "live tool evidence" in details
                export = tmp_path / "merged.json"
                assert "3 conversation(s)" in await text(
                    "dump_conversations",
                    {"output_path": str(export)},
                )
                rows = json.loads(export.read_text())
                assert {r["id"] for r in rows} == {
                    live.conversation_id,
                    revived.conversation_id,
                    closed.conversation_id,
                }
                assert (
                    next(r for r in rows if r["id"] == live.conversation_id)["turns"][
                        0
                    ]["answer"]
                    == "fresh answer"
                )
                assert core.db.get_conversation(live.conversation_id) is None

                # A running MCP process must fetch current state, not a startup snapshot.
                live.chat_state.append_to_answer(0, " updated while open")
                assert "updated while open" in await text(
                    "get_conversation",
                    {"conversation_id": live.conversation_id},
                )

                # Closing persists the conversation; the same MCP session sees it once.
                core.store_tab_in_database(live)
                del core.tabs[live.tab_id]
                assert "fresh answer" in await text(
                    "get_conversation",
                    {"conversation_id": live.conversation_id},
                )
                assert "3 conversation(s)" in await text(
                    "dump_conversations",
                    {"output_path": str(export)},
                )
                assert "not found" in await text(
                    "get_conversation",
                    {"conversation_id": 999999},
                )
    finally:
        service.close()
