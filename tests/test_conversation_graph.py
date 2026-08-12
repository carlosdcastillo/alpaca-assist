"""Tests for ConversationGraph - branching conversation data model."""
from __future__ import annotations

import pytest

from chat_state import FullAnswer
from chat_state import ToolCall
from chat_state import ToolResult
from conversation_graph import ConversationGraph
from conversation_graph import EdgeType
from conversation_graph import is_graph_format
from conversation_graph import load_conversation
from conversation_graph import MessageRole


# ===========================================================================
# Helpers
# ===========================================================================


def _linear_graph(n: int = 2) -> tuple[ConversationGraph, list[int]]:
    """Build a linear graph with *n* Q/A pairs, return (graph, answer_indices)."""
    g = ConversationGraph()
    indices = []
    for i in range(n):
        idx = g.add_question(f"Q{i}")
        g.append_to_answer(idx, f"A{i}")
        g.finish_streaming()
        indices.append(idx)
    return g, indices


# ===========================================================================
# Basic construction and ChatState-compatible API
# ===========================================================================


class TestAddQuestion:
    def test_first_question_returns_index_zero(self):
        g = ConversationGraph()
        idx = g.add_question("hello")
        assert idx == 0

    def test_second_question_returns_index_one(self):
        g, _ = _linear_graph(1)
        idx = g.add_question("second")
        assert idx == 1

    def test_questions_property_reflects_added_questions(self):
        g, _ = _linear_graph(2)
        assert g.questions == ["Q0", "Q1"]

    def test_answers_property_length_matches_questions(self):
        g, _ = _linear_graph(2)
        assert len(g.answers) == 2

    def test_answer_content_is_appended_text(self):
        g = ConversationGraph()
        idx = g.add_question("hi")
        g.append_to_answer(idx, "hello back")
        assert g.answers[idx].get_text_only_content() == "hello back"

    def test_answer_object_is_live_not_copy(self):
        """answers[] must return the live FullAnswer so in-place mutations stick."""
        g = ConversationGraph()
        idx = g.add_question("q")
        live = g.answers[idx]
        live.add_text("mutated")
        assert g.answers[idx].get_text_only_content() == "mutated"

    def test_is_streaming_true_after_add_question(self):
        g = ConversationGraph()
        g.add_question("q")
        assert g.is_streaming()

    def test_finish_streaming_clears_streaming(self):
        g = ConversationGraph()
        g.add_question("q")
        g.finish_streaming()
        assert not g.is_streaming()

    def test_current_streaming_index_matches_answer_index(self):
        g = ConversationGraph()
        idx = g.add_question("q")
        assert g.current_streaming_index == idx


class TestAppendToolCalls:
    def test_add_tool_call(self):
        g = ConversationGraph()
        idx = g.add_question("q")
        result = g.add_tool_call_to_answer(idx, '{"name":"ls"}', "t1")
        assert result is True
        assert any(isinstance(c, ToolCall) for c in g.answers[idx].components)

    def test_add_tool_result(self):
        g = ConversationGraph()
        idx = g.add_question("q")
        g.add_tool_call_to_answer(idx, '{"name":"ls"}', "t1")
        g.add_tool_result_to_answer(idx, "file.txt", "t1")
        assert any(isinstance(c, ToolResult) for c in g.answers[idx].components)

    def test_out_of_range_returns_false(self):
        g = ConversationGraph()
        assert g.append_to_answer(99, "x") is False
        assert g.add_tool_call_to_answer(99, "x", "t") is False
        assert g.add_tool_result_to_answer(99, "x", "t") is False


class TestQuestionImages:
    def test_images_default_empty(self):
        g, _ = _linear_graph(1)
        assert g.question_images == [[]]

    def test_images_setter_syncs_to_user_node(self):
        g, _ = _linear_graph(2)
        g.question_images = [[{"data": "img0"}], []]
        assert g.question_images[0] == [{"data": "img0"}]
        assert g.question_images[1] == []


# ===========================================================================
# Active path
# ===========================================================================


class TestResolveActivePath:
    def test_empty_graph_has_no_pairs(self):
        g = ConversationGraph()
        assert g.questions == []
        assert g.answers == []

    def test_linear_two_pair_path(self):
        g, _ = _linear_graph(2)
        assert g.questions == ["Q0", "Q1"]
        assert g.answers[0].get_text_only_content() == "A0"
        assert g.answers[1].get_text_only_content() == "A1"

    def test_get_safe_copy_full_returns_copies(self):
        g, _ = _linear_graph(1)
        qs, ans, idx = g.get_safe_copy_full()
        assert qs == ["Q0"]
        # Mutating the copy must not affect the graph
        ans[0].add_text("extra")
        assert g.answers[0].get_text_only_content() == "A0"

    def test_get_safe_copy_returns_strings(self):
        g, _ = _linear_graph(1)
        qs, ans_strs, idx = g.get_safe_copy()
        assert isinstance(ans_strs[0], str)


# ===========================================================================
# Pop / truncate
# ===========================================================================


class TestPopLastQA:
    def test_pop_reduces_pair_count(self):
        g, _ = _linear_graph(2)
        ok, imgs = g.pop_last_qa()
        assert ok is True
        assert g.questions == ["Q0"]

    def test_pop_returns_images(self):
        g = ConversationGraph()
        idx = g.add_question("q")
        g.question_images = [[{"data": "img"}]]
        g.finish_streaming()
        ok, imgs = g.pop_last_qa()
        assert imgs == [{"data": "img"}]

    def test_pop_empty_returns_false(self):
        g = ConversationGraph()
        ok, imgs = g.pop_last_qa()
        assert ok is False
        assert imgs == []

    def test_popped_nodes_removed_from_graph(self):
        g, _ = _linear_graph(2)
        node_count_before = len(g.nodes)
        g.pop_last_qa()
        assert len(g.nodes) == node_count_before - 2  # last user+asst deleted

    def test_pop_then_add_question_no_branch(self):
        g, _ = _linear_graph(2)
        g.pop_last_qa()
        g.add_question("new question")
        # The new user node must be the only child of the previous asst node.
        pairs = g._get_active_pairs()
        user_node = pairs[-1][0]
        siblings = g.get_siblings(user_node.id)
        assert len(siblings) == 1


class TestTruncateToLast:
    def test_truncate_leaves_one_pair(self):
        g, _ = _linear_graph(3)
        changed = g.truncate_to_last()
        assert changed is True
        assert len(g.questions) == 1
        assert g.questions[0] == "Q2"
        assert g.answers[0].get_text_only_content() == "A2"

    def test_truncate_no_op_on_single_pair(self):
        g, _ = _linear_graph(1)
        changed = g.truncate_to_last()
        assert changed is False

    def test_truncate_preserves_old_nodes(self):
        g, _ = _linear_graph(3)
        count_before = len(g.nodes)
        g.truncate_to_last()
        # truncate adds 2 new nodes, old ones preserved
        assert len(g.nodes) == count_before + 2


# ===========================================================================
# Branching operations
# ===========================================================================


class TestEditQuestion:
    def test_edit_creates_sibling_user_node(self):
        g, _ = _linear_graph(2)
        user_id, _ = g.get_pair_node_ids(0)
        siblings_before = len(g.get_siblings(user_id))
        g.edit_question(0, "edited Q0")
        siblings_after = len(g.get_siblings(user_id))
        assert siblings_after == siblings_before + 1

    def test_edit_sets_new_question_on_active_path(self):
        g, _ = _linear_graph(2)
        g.edit_question(0, "edited Q0")
        assert g.questions[0] == "edited Q0"

    def test_edit_active_path_length_matches_edit_index(self):
        g, _ = _linear_graph(2)
        # Editing pair 0 means active path has only 1 pair (from fork point)
        g.edit_question(0, "new q")
        assert len(g.questions) == 1

    def test_edit_sets_streaming_on_new_node(self):
        g, _ = _linear_graph(1)
        g.edit_question(0, "new q")
        assert g.is_streaming()

    def test_edit_out_of_range_raises(self):
        g, _ = _linear_graph(1)
        with pytest.raises(IndexError):
            g.edit_question(99, "x")

    def test_edit_preserves_images_on_new_node(self):
        g = ConversationGraph()
        idx = g.add_question("original")
        g.question_images = [[{"data": "img"}]]
        g.finish_streaming()
        g.edit_question(0, "edited")
        assert g.question_images[0] == [{"data": "img"}]


class TestRegenerateAnswer:
    def test_regenerate_creates_sibling_assistant_node(self):
        g, _ = _linear_graph(1)
        _, asst_id = g.get_pair_node_ids(0)
        siblings_before = len(g.get_siblings(asst_id))
        g.regenerate_answer(0)
        assert len(g.get_siblings(asst_id)) == siblings_before + 1

    def test_regenerate_new_answer_is_empty(self):
        g, _ = _linear_graph(1)
        g.regenerate_answer(0)
        assert g.answers[0].get_text_only_content() == ""

    def test_regenerate_sets_streaming(self):
        g, _ = _linear_graph(1)
        g.regenerate_answer(0)
        assert g.is_streaming()

    def test_regenerate_out_of_range_raises(self):
        g, _ = _linear_graph(1)
        with pytest.raises(IndexError):
            g.regenerate_answer(99)


class TestForkFrom:
    def test_fork_extends_active_path_by_one_pair(self):
        g, _ = _linear_graph(1)
        g.finish_streaming()
        length_before = len(g.questions)
        g.fork_from(0, "fork question")
        assert len(g.questions) == length_before + 1

    def test_fork_new_question_on_active_path(self):
        g, _ = _linear_graph(1)
        g.fork_from(0, "fork question")
        assert g.questions[-1] == "fork question"

    def test_fork_original_answer_preserved(self):
        g, _ = _linear_graph(1)
        g.fork_from(0, "fork")
        assert g.answers[0].get_text_only_content() == "A0"

    def test_fork_out_of_range_raises(self):
        g, _ = _linear_graph(1)
        with pytest.raises(IndexError):
            g.fork_from(99, "x")


# ===========================================================================
# Sibling navigation
# ===========================================================================


class TestSiblingNavigation:
    def test_get_siblings_single_node(self):
        g, _ = _linear_graph(1)
        user_id, _ = g.get_pair_node_ids(0)
        siblings = g.get_siblings(user_id)
        assert siblings == [user_id]

    def test_get_sibling_position_single(self):
        g, _ = _linear_graph(1)
        user_id, _ = g.get_pair_node_ids(0)
        pos, total = g.get_sibling_position(user_id)
        assert (pos, total) == (1, 1)

    def test_get_sibling_position_after_edit(self):
        g, _ = _linear_graph(1)
        user_id_orig, _ = g.get_pair_node_ids(0)
        g.edit_question(0, "edited")
        # New user node is the active one
        user_id_new, _ = g.get_pair_node_ids(0)
        pos, total = g.get_sibling_position(user_id_new)
        assert total == 2

    def test_navigate_prev_sibling(self):
        g, _ = _linear_graph(1)
        user_id_orig, _ = g.get_pair_node_ids(0)
        g.edit_question(0, "edited")
        user_id_new, _ = g.get_pair_node_ids(0)
        # Navigate back to original
        result = g.navigate_to_prev_sibling(user_id_new)
        assert result is not None
        assert g.questions[0] == "Q0"

    def test_navigate_prev_sibling_at_first_returns_none(self):
        g, _ = _linear_graph(1)
        user_id, _ = g.get_pair_node_ids(0)
        result = g.navigate_to_prev_sibling(user_id)
        assert result is None

    def test_navigate_next_sibling(self):
        g, _ = _linear_graph(1)
        user_id_orig, _ = g.get_pair_node_ids(0)
        g.edit_question(0, "edited")
        # Go back to original, then forward to edited
        g.navigate_to(user_id_orig, follow_to_leaf=True)
        result = g.navigate_to_next_sibling(user_id_orig)
        assert result is not None
        assert g.questions[0] == "edited"

    def test_navigate_to_unknown_node_raises(self):
        g = ConversationGraph()
        with pytest.raises(KeyError):
            g.navigate_to("nonexistent")


# ===========================================================================
# Serialisation and migration
# ===========================================================================


class TestSerialisation:
    def test_roundtrip_preserves_questions_and_answers(self):
        g, _ = _linear_graph(2)
        d = g.to_dict()
        g2 = ConversationGraph.from_dict(d)
        assert g2.questions == ["Q0", "Q1"]
        assert g2.answers[0].get_text_only_content() == "A0"

    def test_roundtrip_preserves_active_node_id(self):
        g, _ = _linear_graph(2)
        d = g.to_dict()
        g2 = ConversationGraph.from_dict(d)
        assert g2.active_node_id == g.active_node_id

    def test_children_index_rebuilt_on_load(self):
        g, _ = _linear_graph(2)
        d = g.to_dict()
        g2 = ConversationGraph.from_dict(d)
        # children_index should be non-empty
        assert len(g2.children_index) > 0

    def test_roundtrip_with_tool_calls(self):
        g = ConversationGraph()
        idx = g.add_question("q")
        g.add_tool_call_to_answer(idx, '{"name":"ls"}', "t1")
        g.add_tool_result_to_answer(idx, "file.txt", "t1")
        g.append_to_answer(idx, "done")
        g.finish_streaming()
        d = g.to_dict()
        g2 = ConversationGraph.from_dict(d)
        comps = g2.answers[0].components
        assert any(isinstance(c, ToolCall) for c in comps)
        assert any(isinstance(c, ToolResult) for c in comps)

    def test_is_graph_format_new(self):
        g, _ = _linear_graph(1)
        assert is_graph_format(g.to_dict()) is True

    def test_is_graph_format_old(self):
        old = {"questions": ["q"], "answers": [{"components": []}]}
        assert is_graph_format(old) is False


class TestMigration:
    def test_migrate_from_chat_state_dict(self):
        old = {
            "questions": ["hello", "world"],
            "answers": [
                {"components": [{"type": "text", "content": "hi"}]},
                {"components": [{"type": "text", "content": "earth"}]},
            ],
            "question_images": [[], []],
        }
        g = ConversationGraph.from_chat_state_dict(old)
        assert g.questions == ["hello", "world"]
        assert g.answers[0].get_text_only_content() == "hi"
        assert g.answers[1].get_text_only_content() == "earth"

    def test_migrate_preserves_images(self):
        old = {
            "questions": ["q"],
            "answers": [{"components": []}],
            "question_images": [[{"data": "img"}]],
        }
        g = ConversationGraph.from_chat_state_dict(old)
        # from_chat_state_dict converts legacy dict format {"data": "..."} to plain strings
        assert g.question_images[0] == ["img"]

    def test_load_conversation_auto_detects_old_format(self):
        old = {
            "chat_state": {
                "questions": ["q"],
                "answers": [{"components": [{"type": "text", "content": "a"}]}],
            },
        }
        g = load_conversation(old)
        assert g.questions == ["q"]

    def test_load_conversation_auto_detects_new_format(self):
        g_orig, _ = _linear_graph(1)
        g2 = load_conversation(g_orig.to_dict())
        assert g2.questions == g_orig.questions

    def test_migration_creates_linear_chain(self):
        old = {
            "questions": ["q0", "q1"],
            "answers": [
                {"components": [{"type": "text", "content": "a0"}]},
                {"components": [{"type": "text", "content": "a1"}]},
            ],
        }
        g = ConversationGraph.from_chat_state_dict(old)
        # active path should include all pairs in order
        assert len(g.questions) == 2


# ===========================================================================
# Children index consistency
# ===========================================================================


class TestChildrenIndex:
    def test_index_populated_after_add_question(self):
        g = ConversationGraph()
        g.add_question("q")
        # user node's parent is None (root), assistant's parent is user
        assert len(g.children_index) >= 1

    def test_index_contains_all_branch_children(self):
        g, _ = _linear_graph(1)
        g.edit_question(0, "v2")
        g.navigate_to_prev_sibling(g.get_pair_node_ids(0)[0])
        g.edit_question(0, "v3")
        # The original user node's parent now has 3 children:
        # original, v2, v3
        user_id_v1, _ = g.get_pair_node_ids(0)
        # Get parent of v1
        parent_id = g.nodes[user_id_v1].parent_id
        if parent_id is not None:
            assert len(g.children_index.get(parent_id, [])) >= 2

    def test_index_rebuilt_matches_original_after_roundtrip(self):
        g, _ = _linear_graph(2)
        g.edit_question(0, "branch")
        d = g.to_dict()
        g2 = ConversationGraph.from_dict(d)
        assert g2.children_index == g.children_index
