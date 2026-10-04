from __future__ import annotations

import json
from pathlib import Path

import pytest

import conversation_mcp_server
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
