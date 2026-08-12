"""Tests for the conversation tools in internal_tools.py."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

import internal_tools as it
from internal_tools import _conv_all_text
from internal_tools import _conv_extract_turns
from internal_tools import _conv_format_transcript
from internal_tools import _conv_parse_id
from internal_tools import _conv_raw_turns
from internal_tools import _conv_tool_name
from internal_tools import call_tool
from internal_tools import dump_conversations
from internal_tools import get_conversation
from internal_tools import get_tool_details
from internal_tools import search_conversations
from internal_tools import TOOL_SCHEMAS


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

LEGACY_CHAT_DATA = {
    "chat_state": {
        "questions": ["What is Python?", "Can I use it for web?"],
        "answers": [
            {
                "components": [
                    {"type": "text", "content": "Python is a programming language."},
                    {
                        "type": "tool_call",
                        "content": json.dumps(
                            {
                                "tool_call": {
                                    "name": "internal_read_file",
                                    "id": "t1",
                                    "arguments": {"file_path": "foo.py"},
                                },
                            },
                        ),
                        "id": "t1",
                    },
                    {"type": "tool_result", "content": "# foo.py contents", "id": "t1"},
                ],
            },
            {
                "components": [
                    {
                        "type": "text",
                        "content": "Yes, frameworks like Flask and Django exist.",
                    },
                ],
            },
        ],
        "current_streaming_index": None,
        "question_images": [[], []],
    },
}

GRAPH_CHAT_DATA = {
    "chat_state": {
        "graph": {
            "active_node_id": "n3",
            "nodes": {
                "n1": {
                    "id": "n1",
                    "parent_id": None,
                    "role": "user",
                    "content": {
                        "components": [{"type": "text", "content": "Hello graph!"}],
                    },
                    "created_at": "2025-01-01T00:00:00",
                    "edge_type": "continuation",
                },
                "n2": {
                    "id": "n2",
                    "parent_id": "n1",
                    "role": "assistant",
                    "content": {
                        "components": [
                            {"type": "text", "content": "Hello from graph assistant!"},
                            {
                                "type": "tool_call",
                                "content": json.dumps(
                                    {
                                        "tool_call": {
                                            "name": "internal_get_time",
                                            "id": "g1",
                                            "arguments": {},
                                        },
                                    },
                                ),
                                "id": "g1",
                            },
                            {
                                "type": "tool_result",
                                "content": "2025-01-01 12:00:00",
                                "id": "g1",
                            },
                        ],
                    },
                    "created_at": "2025-01-01T00:00:01",
                    "edge_type": "continuation",
                },
                "n3": {
                    "id": "n3",
                    "parent_id": "n2",
                    "role": "user",
                    "content": {
                        "components": [{"type": "text", "content": "What time is it?"}],
                    },
                    "created_at": "2025-01-01T00:00:02",
                    "edge_type": "continuation",
                },
            },
        },
    },
}


def _make_db(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        """CREATE TABLE conversations (
            id INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            chat_data TEXT NOT NULL,
            created_date TEXT NOT NULL,
            closed_date TEXT NOT NULL,
            summary_generated INTEGER DEFAULT 0,
            original_id INTEGER DEFAULT NULL
        )""",
    )
    conn.execute(
        "INSERT INTO conversations VALUES (1, 'Python intro', ?, '2025-01-01T10:00:00', '2025-01-01T11:00:00', 0, NULL)",
        (json.dumps(LEGACY_CHAT_DATA),),
    )
    conn.execute(
        "INSERT INTO conversations VALUES (2, 'Graph conversation', ?, '2025-02-01T10:00:00', '2025-02-01T11:00:00', 0, NULL)",
        (json.dumps(GRAPH_CHAT_DATA),),
    )
    conn.execute(
        "INSERT INTO conversations VALUES (3, 'Kubernetes deployment notes', ?, '2025-03-01T10:00:00', '2025-03-01T11:00:00', 0, NULL)",
        (
            json.dumps(
                {
                    "chat_state": {
                        "questions": ["How to deploy k8s?"],
                        "answers": [
                            {
                                "components": [
                                    {"type": "text", "content": "Use kubectl apply."},
                                ],
                            },
                        ],
                        "current_streaming_index": None,
                        "question_images": [[]],
                    },
                },
            ),
        ),
    )
    conn.commit()
    conn.close()


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    path = str(tmp_path / "conversations.db")
    _make_db(path)
    return path


def _text(result: dict[str, Any]) -> str:
    """Extract text content from an internal tool result."""
    return str(result["content"][0]["text"])


def _args(db_path: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build an arguments dict with _db_path injected."""
    d = {"_db_path": db_path}
    if extra:
        d.update(extra)
    return d


# ---------------------------------------------------------------------------
# TestToolRegistry
# ---------------------------------------------------------------------------


class TestToolRegistry:
    def test_exactly_four_conv_schemas(self) -> None:
        conv_schemas = [
            s
            for s in TOOL_SCHEMAS
            if s["function"]["name"].startswith("internal_")
            and s["function"]["name"].split("internal_")[1]
            in {
                "search_conversations",
                "get_conversation",
                "get_tool_details",
                "dump_conversations",
            }
        ]
        assert len(conv_schemas) == 4

    def test_schema_names_match_handlers(self) -> None:
        handler_names = {
            "search_conversations",
            "get_conversation",
            "get_tool_details",
            "dump_conversations",
        }
        schema_tool_names = {
            s["function"]["name"].replace("internal_", "")
            for s in TOOL_SCHEMAS
            if s["function"]["name"] in {f"internal_{n}" for n in handler_names}
        }
        assert schema_tool_names == handler_names

    def test_call_tool_dispatches_to_search(self, db_path: str) -> None:
        result = call_tool("search_conversations", _args(db_path, {"query": "Python"}))
        assert result["isError"] is False
        assert "Python intro" in _text(result)

    def test_call_tool_unknown_name_errors(self) -> None:
        result = call_tool("nonexistent_tool", {})
        assert result["isError"] is True
        assert "Unknown" in _text(result)


# ---------------------------------------------------------------------------
# TestUriParser
# ---------------------------------------------------------------------------


class TestUriParser:
    def test_bare_int(self) -> None:
        assert _conv_parse_id(42) == 42

    def test_string_int(self) -> None:
        assert _conv_parse_id("42") == 42

    def test_alpaca_uri(self) -> None:
        assert _conv_parse_id("alpaca://conv/42") == 42

    def test_raven_conversation_uri(self) -> None:
        assert _conv_parse_id("raven://conversation/42") == 42

    def test_raven_short_uri(self) -> None:
        assert _conv_parse_id("raven://42") == 42

    def test_garbage_returns_none(self) -> None:
        assert _conv_parse_id("not-a-number") is None

    def test_empty_string_returns_none(self) -> None:
        assert _conv_parse_id("") is None


# ---------------------------------------------------------------------------
# TestSearchConversations
# ---------------------------------------------------------------------------


class TestSearchConversations:
    def test_title_match(self, db_path: str) -> None:
        result = search_conversations(_args(db_path, {"query": "Python"}))
        text = _text(result)
        assert "Python intro" in text
        assert "alpaca://conv/1" in text

    def test_title_no_match(self, db_path: str) -> None:
        result = search_conversations(_args(db_path, {"query": "zzznomatch"}))
        assert "No conversations found" in _text(result)

    def test_case_insensitive(self, db_path: str) -> None:
        result = search_conversations(_args(db_path, {"query": "python"}))
        assert "Python intro" in _text(result)

    def test_content_search_finds_text(self, db_path: str) -> None:
        result = search_conversations(
            _args(db_path, {"query": "kubectl", "search_content": True}),
        )
        assert "Kubernetes" in _text(result)

    def test_content_search_no_match(self, db_path: str) -> None:
        result = search_conversations(
            _args(db_path, {"query": "zzznomatch", "search_content": True}),
        )
        assert "No conversations found" in _text(result)

    def test_match_type_title(self, db_path: str) -> None:
        result = search_conversations(_args(db_path, {"query": "Kubernetes"}))
        assert "Title" in _text(result)

    def test_match_type_content(self, db_path: str) -> None:
        result = search_conversations(
            _args(db_path, {"query": "kubectl", "search_content": True}),
        )
        assert "Content" in _text(result)

    def test_limit_respected(self, db_path: str) -> None:
        result = search_conversations(_args(db_path, {"query": "o", "limit": 1}))
        assert _text(result).count("alpaca://conv/") == 1

    def test_missing_query_errors(self, db_path: str) -> None:
        result = search_conversations(_args(db_path))
        assert result["isError"] is True


# ---------------------------------------------------------------------------
# TestGetConversation
# ---------------------------------------------------------------------------


class TestGetConversation:
    def test_returns_qa_pairs(self, db_path: str) -> None:
        result = get_conversation(_args(db_path, {"conversation_id": 1}))
        text = _text(result)
        assert "What is Python?" in text
        assert "Python is a programming language." in text
        assert "Can I use it for web?" in text
        assert "Flask and Django" in text

    def test_text_only_by_default(self, db_path: str) -> None:
        result = get_conversation(_args(db_path, {"conversation_id": 1}))
        assert "tool_call" not in _text(result)
        assert "tool_result" not in _text(result)

    def test_tools_used_listed(self, db_path: str) -> None:
        result = get_conversation(_args(db_path, {"conversation_id": 1}))
        assert "internal_read_file" in _text(result)

    def test_include_tool_calls_flag(self, db_path: str) -> None:
        result = get_conversation(
            _args(db_path, {"conversation_id": 1, "include_tool_calls": True}),
        )
        assert "Tool call" in _text(result)
        assert "Tool result" in _text(result)

    def test_alpaca_uri_accepted(self, db_path: str) -> None:
        result = get_conversation(
            _args(db_path, {"conversation_id": "alpaca://conv/1"}),
        )
        assert "What is Python?" in _text(result)

    def test_unknown_id_errors(self, db_path: str) -> None:
        result = get_conversation(_args(db_path, {"conversation_id": 9999}))
        assert result["isError"] is True
        assert "not found" in _text(result)

    def test_invalid_id_errors(self, db_path: str) -> None:
        result = get_conversation(_args(db_path, {"conversation_id": "garbage"}))
        assert result["isError"] is True

    def test_graph_format_supported(self, db_path: str) -> None:
        result = get_conversation(_args(db_path, {"conversation_id": 2}))
        text = _text(result)
        assert "Hello graph!" in text
        assert "Hello from graph assistant!" in text

    def test_graph_tool_listed(self, db_path: str) -> None:
        result = get_conversation(_args(db_path, {"conversation_id": 2}))
        assert "internal_get_time" in _text(result)

    def test_header_includes_id_and_title(self, db_path: str) -> None:
        result = get_conversation(_args(db_path, {"conversation_id": 1}))
        assert "#1" in _text(result)
        assert "Python intro" in _text(result)


# ---------------------------------------------------------------------------
# TestGetToolDetails
# ---------------------------------------------------------------------------


class TestGetToolDetails:
    def test_tool_call_extracted(self, db_path: str) -> None:
        result = get_tool_details(
            _args(db_path, {"conversation_id": 1, "turn_index": 0}),
        )
        text = _text(result)
        assert "CALL" in text
        assert "internal_read_file" in text

    def test_tool_result_extracted(self, db_path: str) -> None:
        result = get_tool_details(
            _args(db_path, {"conversation_id": 1, "turn_index": 0}),
        )
        assert "RESULT" in _text(result)
        assert "foo.py contents" in _text(result)

    def test_turn_with_no_tools(self, db_path: str) -> None:
        result = get_tool_details(
            _args(db_path, {"conversation_id": 1, "turn_index": 1}),
        )
        assert "no tool calls" in _text(result)

    def test_turn_index_out_of_range_errors(self, db_path: str) -> None:
        result = get_tool_details(
            _args(db_path, {"conversation_id": 1, "turn_index": 99}),
        )
        assert result["isError"] is True
        assert "out of range" in _text(result)

    def test_unknown_conv_errors(self, db_path: str) -> None:
        result = get_tool_details(
            _args(db_path, {"conversation_id": 9999, "turn_index": 0}),
        )
        assert result["isError"] is True
        assert "not found" in _text(result)

    def test_missing_turn_index_errors(self, db_path: str) -> None:
        result = get_tool_details(_args(db_path, {"conversation_id": 1}))
        assert result["isError"] is True

    def test_alpaca_uri_accepted(self, db_path: str) -> None:
        result = get_tool_details(
            _args(db_path, {"conversation_id": "alpaca://conv/1", "turn_index": 0}),
        )
        assert "internal_read_file" in _text(result)

    def test_graph_tool_details(self, db_path: str) -> None:
        result = get_tool_details(
            _args(db_path, {"conversation_id": 2, "turn_index": 0}),
        )
        text = _text(result)
        assert "internal_get_time" in text
        assert "RESULT" in text


# ---------------------------------------------------------------------------
# TestDumpConversations
# ---------------------------------------------------------------------------


class TestDumpConversations:
    def test_file_written(self, db_path: str, tmp_path: Path) -> None:
        out = str(tmp_path / "out.json")
        dump_conversations(_args(db_path, {"output_path": out}))
        assert Path(out).exists()

    def test_count_correct(self, db_path: str, tmp_path: Path) -> None:
        out = str(tmp_path / "out.json")
        result = dump_conversations(_args(db_path, {"output_path": out}))
        assert "3 conversation(s)" in _text(result)

    def test_content_included_by_default(self, db_path: str, tmp_path: Path) -> None:
        out = str(tmp_path / "out.json")
        dump_conversations(_args(db_path, {"output_path": out}))
        data = json.loads(Path(out).read_text(encoding="utf-8"))
        assert "turns" in data[0]
        assert data[0]["turns"][0]["question"] != ""

    def test_include_content_false_omits_turns(
        self,
        db_path: str,
        tmp_path: Path,
    ) -> None:
        out = str(tmp_path / "out.json")
        dump_conversations(
            _args(db_path, {"output_path": out, "include_content": False}),
        )
        data = json.loads(Path(out).read_text(encoding="utf-8"))
        assert "turns" not in data[0]

    def test_include_tool_names_adds_tools(self, db_path: str, tmp_path: Path) -> None:
        out = str(tmp_path / "out.json")
        dump_conversations(
            _args(db_path, {"output_path": out, "include_tool_names": True}),
        )
        data = json.loads(Path(out).read_text(encoding="utf-8"))
        python_conv = next(d for d in data if d["title"] == "Python intro")
        assert any("tools_used" in t for t in python_conv["turns"])

    def test_tool_results_excluded(self, db_path: str, tmp_path: Path) -> None:
        out = str(tmp_path / "out.json")
        dump_conversations(_args(db_path, {"output_path": out}))
        assert "foo.py contents" not in Path(out).read_text(encoding="utf-8")

    def test_invalid_path_errors(self, db_path: str) -> None:
        result = dump_conversations(
            _args(db_path, {"output_path": "/nonexistent/deeply/nested/out.json"}),
        )
        assert result["isError"] is True


# ---------------------------------------------------------------------------
# TestExtractTurns (unit tests for the extraction helpers)
# ---------------------------------------------------------------------------


class TestExtractTurns:
    def test_legacy_qa_count(self) -> None:
        assert len(_conv_extract_turns(LEGACY_CHAT_DATA)) == 2

    def test_legacy_question_text(self) -> None:
        assert _conv_extract_turns(LEGACY_CHAT_DATA)[0][0] == "What is Python?"

    def test_legacy_answer_text(self) -> None:
        assert (
            "Python is a programming language."
            in _conv_extract_turns(LEGACY_CHAT_DATA)[0][1]
        )

    def test_legacy_tools_used(self) -> None:
        assert "internal_read_file" in _conv_extract_turns(LEGACY_CHAT_DATA)[0][2]

    def test_legacy_turn_no_tools(self) -> None:
        assert _conv_extract_turns(LEGACY_CHAT_DATA)[1][2] == []

    def test_graph_qa_count(self) -> None:
        assert len(_conv_extract_turns(GRAPH_CHAT_DATA)) >= 1

    def test_graph_first_question(self) -> None:
        assert _conv_extract_turns(GRAPH_CHAT_DATA)[0][0] == "Hello graph!"

    def test_graph_first_answer(self) -> None:
        assert (
            "Hello from graph assistant!" in _conv_extract_turns(GRAPH_CHAT_DATA)[0][1]
        )

    def test_graph_tool_name(self) -> None:
        assert "internal_get_time" in _conv_extract_turns(GRAPH_CHAT_DATA)[0][2]

    def test_tool_name_from_content(self) -> None:
        content = json.dumps(
            {"tool_call": {"name": "my_tool", "id": "x", "arguments": {}}},
        )
        assert _conv_tool_name(content) == "my_tool"

    def test_tool_name_from_garbage(self) -> None:
        assert _conv_tool_name("not json") is None

    def test_all_text_extracts_questions_and_answers(self) -> None:
        text = _conv_all_text(LEGACY_CHAT_DATA)
        assert "What is Python?" in text
        assert "Python is a programming language." in text

    def test_raw_turns_legacy_components(self) -> None:
        raw = _conv_raw_turns(LEGACY_CHAT_DATA)
        assert len(raw) == 2
        _, comps = raw[0]
        types = {c.get("type") for c in comps if isinstance(c, dict)}
        assert "tool_call" in types
        assert "tool_result" in types
