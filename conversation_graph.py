"""ConversationGraph — branching conversation data model.

Replaces ChatState with a DAG of MessageNode objects while exposing a
fully ChatState-compatible API so existing call sites require minimal changes.

Structure
---------
Each conversation is a DAG of MessageNodes.  Every node has a single
parent_id (or None for root) and may have multiple children (branches).
The *active path* is the linear chain from the root to active_node_id,
computed by walking up via parent_id and reversing.

ChatState-compatible surface
-----------------------------
    .questions              list[str]          — active-path user messages
    .answers                list[FullAnswer]    — active-path assistant answers
    .question_images        list[list[dict]]    — images per user message
    .current_streaming_index  int | None
    add_question()          create user + empty assistant node, return index
    append_to_answer()      append text to a streaming node
    add_tool_call_to_answer / add_tool_result_to_answer
    finish_streaming / is_streaming
    get_display_text / get_safe_copy / get_safe_copy_full
    pop_last_qa / truncate_to_last / compact_answers
    to_dict / from_dict

Branching API (new)
-------------------
    edit_question(pair_index, new_text)  → new answer_index
    regenerate_answer(pair_index)        → new answer_index
    fork_from(pair_index, new_question)  → new answer_index
    navigate_to(node_id, follow_to_leaf)
    get_siblings(node_id)                → list[str]
    get_sibling_position(node_id)        → (current_1based, total)

Threading
---------
Graph-structure mutations (_add_node, navigate_to, add_question, etc.) are
protected by _lock (RLock).  FullAnswer content mutations delegate to
FullAnswer._lock internally.  _active_pairs_cache is invalidated under
_lock whenever the active path changes.
"""
import threading
import uuid
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from typing import Any

from chat_state import FullAnswer


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class EdgeType:
    CONTINUATION = "continuation"
    BRANCH = "branch"
    EDIT_REGEN = "edit_regen"
    MODEL_SWITCH = "model_switch"
    COMPACTION = "compaction"
    HANDOFF = "handoff"


class MessageRole:
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


# ---------------------------------------------------------------------------
# MessageNode
# ---------------------------------------------------------------------------


@dataclass
class MessageNode:
    """A single message in the conversation DAG."""

    id: str
    parent_id: str | None
    role: str  # MessageRole constant
    content: FullAnswer
    created_at: str
    edge_type: str = EdgeType.CONTINUATION
    model_id: str | None = None
    images: list[str] = field(default_factory=list)
    # Set on assistant nodes only, once the turn's whole tool loop finishes.
    # See core.timing.TurnTiming for the shape.  User nodes carry their own
    # moment in created_at and need nothing further.
    timing: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "parent_id": self.parent_id,
            "role": self.role,
            "content": self.content.to_dict(),
            "created_at": self.created_at,
            "edge_type": self.edge_type,
            "model_id": self.model_id,
            "images": list(self.images),
        }
        # Omitted rather than null so nodes from before timing existed, and
        # user nodes which never carry it, serialise unchanged.
        if self.timing is not None:
            data["timing"] = dict(self.timing)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MessageNode":
        raw = data.get("content", {})
        if isinstance(raw, str):
            content = FullAnswer.from_string(raw)
        elif isinstance(raw, dict):
            content = FullAnswer.from_dict(raw)
        else:
            content = FullAnswer()
        return cls(
            id=data["id"],
            parent_id=data.get("parent_id"),
            role=data["role"],
            content=content,
            created_at=data.get("created_at", datetime.now().isoformat()),
            edge_type=data.get("edge_type", EdgeType.CONTINUATION),
            model_id=data.get("model_id"),
            images=list(data.get("images", [])),
            timing=data.get("timing"),
        )


# ---------------------------------------------------------------------------
# ConversationGraph
# ---------------------------------------------------------------------------


class ConversationGraph:
    """DAG-based conversation model with a ChatState-compatible API."""

    def __init__(
        self,
        graph_id: str | None = None,
        title: str = "",
        created_at: str | None = None,
    ) -> None:
        self.id: str = graph_id or _new_id()
        self.title: str = title
        self.created_at: str = created_at or _now()
        self.active_node_id: str | None = None
        self.nodes: dict[str, MessageNode] = {}
        # Derived index — not serialised; rebuilt from parent_id links on load.
        self.children_index: dict[str, list[str]] = {}
        self.current_streaming_node_id: str | None = None
        self._lock = threading.RLock()
        self._active_pairs_cache: (
            list[tuple[MessageNode, MessageNode | None]] | None
        ) = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _add_node(self, node: MessageNode) -> None:
        """Register a node and update children_index.  Must be called under _lock."""
        self.nodes[node.id] = node
        if node.parent_id is not None:
            self.children_index.setdefault(node.parent_id, []).append(node.id)
        self._active_pairs_cache = None

    def _resolve_active_path(self) -> list[MessageNode]:
        """Walk up from active_node_id via parent_id, then reverse.  O(depth)."""
        if self.active_node_id is None:
            return []
        path: list[MessageNode] = []
        node_id: str | None = self.active_node_id
        while node_id is not None:
            node = self.nodes.get(node_id)
            if node is None:
                break
            path.append(node)
            node_id = node.parent_id
        path.reverse()
        return path

    def _get_active_pairs(
        self,
    ) -> list[tuple[MessageNode, MessageNode | None]]:
        """Return (user, assistant|None) pairs from the active path.

        Result is cached; invalidated whenever structure or active_node_id
        changes.
        """
        with self._lock:
            if self._active_pairs_cache is not None:
                return self._active_pairs_cache
            path = self._resolve_active_path()
            pairs: list[tuple[MessageNode, MessageNode | None]] = []
            pending_user: MessageNode | None = None
            for node in path:
                if node.role == MessageRole.USER:
                    if pending_user is not None:
                        pairs.append((pending_user, None))
                    pending_user = node
                elif node.role == MessageRole.ASSISTANT:
                    if pending_user is not None:
                        pairs.append((pending_user, node))
                        pending_user = None
                # System nodes contribute to path but not to Q/A pairs.
            if pending_user is not None:
                pairs.append((pending_user, None))
            self._active_pairs_cache = pairs
            return pairs

    def _follow_to_leaf(self, node_id: str) -> str:
        """Follow the first-child chain to the deepest descendant."""
        current = node_id
        while True:
            children = self.children_index.get(current, [])
            if not children:
                return current
            current = children[0]

    def _rebuild_children_index(self) -> None:
        """Rebuild children_index from parent_id fields.  Called after deserialisation."""
        self.children_index = {}
        for node in self.nodes.values():
            if node.parent_id is not None:
                self.children_index.setdefault(node.parent_id, []).append(node.id)

    # ------------------------------------------------------------------
    # ChatState-compatible properties
    # ------------------------------------------------------------------

    @property
    def questions(self) -> list[str]:
        return [
            pair[0].content.get_text_only_content() for pair in self._get_active_pairs()
        ]

    @property
    def answers(self) -> list[FullAnswer]:
        """Return the *live* FullAnswer objects so callers can mutate them in place."""
        return [
            pair[1].content if pair[1] is not None else FullAnswer()
            for pair in self._get_active_pairs()
        ]

    @property
    def question_images(self) -> list[list[str]]:
        return [list(pair[0].images) for pair in self._get_active_pairs()]

    @question_images.setter
    def question_images(self, value: list[list[str]]) -> None:
        pairs = self._get_active_pairs()
        for i, imgs in enumerate(value):
            if i < len(pairs):
                pairs[i][0].images = list(imgs)

    def set_question_images(
        self,
        pair_index: int,
        images: list[str],
    ) -> None:
        """Set images on the user node at pair_index (supports index-style mutation)."""
        with self._lock:
            pairs = self._get_active_pairs()
            if pair_index < len(pairs):
                pairs[pair_index][0].images = list(images)

    @property
    def current_streaming_index(self) -> int | None:
        if self.current_streaming_node_id is None:
            return None
        for i, (_, asst) in enumerate(self._get_active_pairs()):
            if asst is not None and asst.id == self.current_streaming_node_id:
                return i
        return None

    @current_streaming_index.setter
    def current_streaming_index(self, value: int | None) -> None:
        if value is None:
            self.current_streaming_node_id = None
            return
        pairs = self._get_active_pairs()
        if 0 <= value < len(pairs):
            asst_node = pairs[value][1]
            if asst_node is not None:
                self.current_streaming_node_id = asst_node.id

    # ------------------------------------------------------------------
    # ChatState-compatible mutators
    # ------------------------------------------------------------------

    def add_question(self, question: str) -> int:
        """Create a user + empty assistant node pair; return answer index."""
        with self._lock:
            user_node = MessageNode(
                id=_new_id(),
                parent_id=self.active_node_id,
                role=MessageRole.USER,
                content=FullAnswer.from_string(question),
                created_at=_now(),
            )
            asst_node = MessageNode(
                id=_new_id(),
                parent_id=user_node.id,
                role=MessageRole.ASSISTANT,
                content=FullAnswer(),
                created_at=_now(),
            )
            self._add_node(user_node)
            self._add_node(asst_node)
            self.active_node_id = asst_node.id
            self._active_pairs_cache = None
            self.current_streaming_node_id = asst_node.id
            return len(self._get_active_pairs()) - 1

    def append_to_answer(self, answer_index: int, content: str) -> bool:
        pairs = self._get_active_pairs()
        if 0 <= answer_index < len(pairs):
            _, asst = pairs[answer_index]
            if asst is not None:
                asst.content.add_text(content)
                return True
        return False

    def add_tool_call_to_answer(
        self,
        answer_index: int,
        content: str,
        tool_id: str,
        started_at: float | None = None,
    ) -> bool:
        pairs = self._get_active_pairs()
        if 0 <= answer_index < len(pairs):
            _, asst = pairs[answer_index]
            if asst is not None:
                asst.content.add_tool_call(content, tool_id, started_at)
                return True
        return False

    def add_tool_result_to_answer(
        self,
        answer_index: int,
        content: str,
        tool_id: str,
        started_at: float | None = None,
        duration_ms: int | None = None,
    ) -> bool:
        pairs = self._get_active_pairs()
        if 0 <= answer_index < len(pairs):
            _, asst = pairs[answer_index]
            if asst is not None:
                asst.content.add_tool_result(
                    content,
                    tool_id,
                    started_at,
                    duration_ms,
                )
                return True
        return False

    # ------------------------------------------------------------------
    # Turn timing
    # ------------------------------------------------------------------

    def set_turn_timing(self, answer_index: int, timing: dict[str, Any]) -> bool:
        """Attach a finished turn's timing record to its assistant node."""
        with self._lock:
            pairs = self._get_active_pairs()
            if 0 <= answer_index < len(pairs):
                _, asst = pairs[answer_index]
                if asst is not None:
                    asst.timing = dict(timing)
                    return True
        return False

    def get_turn_timing(self, answer_index: int) -> dict[str, Any] | None:
        """Return the stored timing record for an answer, if any."""
        pairs = self._get_active_pairs()
        if 0 <= answer_index < len(pairs):
            _, asst = pairs[answer_index]
            if asst is not None:
                return asst.timing
        return None

    @property
    def question_times(self) -> list[str | None]:
        """Active-path user message timestamps, ISO local time."""
        return [pair[0].created_at for pair in self._get_active_pairs()]

    @property
    def turn_timings(self) -> list[dict[str, Any] | None]:
        """Active-path turn timing records, parallel to answers."""
        return [
            pair[1].timing if pair[1] is not None else None
            for pair in self._get_active_pairs()
        ]

    def finish_streaming(self) -> None:
        self.current_streaming_node_id = None

    def is_streaming(self) -> bool:
        return self.current_streaming_node_id is not None

    def get_display_text(self, include_tool_content: bool = True) -> str:
        lines: list[str] = []
        for i, (user_node, asst_node) in enumerate(self._get_active_pairs()):
            if i > 0:
                lines.append("")
                lines.append("─" * 80)
                lines.append("")
            lines.append(f"Q: {user_node.content.get_text_only_content()}")
            answer_line = "A: "
            if asst_node is not None:
                answer_text = (
                    asst_node.content.get_text_content()
                    if include_tool_content
                    else asst_node.content.get_text_only_content()
                )
                if answer_text:
                    answer_line += answer_text
            lines.append(answer_line)
        return "\n".join(lines)

    def get_safe_copy_full(
        self,
    ) -> tuple[list[str], list[FullAnswer], int | None]:
        pairs = self._get_active_pairs()
        questions = [p[0].content.get_text_only_content() for p in pairs]
        answers = [
            p[1].content.copy() if p[1] is not None else FullAnswer() for p in pairs
        ]
        return (questions, answers, self.current_streaming_index)

    def get_safe_copy(self) -> tuple[list[str], list[str], int | None]:
        questions, answers, idx = self.get_safe_copy_full()
        return (questions, [a.get_text_only_content() for a in answers], idx)

    def _delete_subtree(self, node_id: str) -> None:
        """Remove node_id and all its descendants from nodes and children_index.
        Caller is responsible for removing node_id from its parent's children list.
        Must be called under _lock."""
        stack = [node_id]
        while stack:
            nid = stack.pop()
            self.nodes.pop(nid, None)
            children = self.children_index.pop(nid, [])
            stack.extend(children)

    def pop_last_qa(self) -> tuple[bool, list[str]]:
        """Remove the last Q/A pair and move active_node_id back to the previous node."""
        with self._lock:
            pairs = self._get_active_pairs()
            if not pairs:
                return (False, [])
            last_user, _ = pairs[-1]
            images = list(last_user.images)
            parent_id = last_user.parent_id
            self.active_node_id = parent_id
            self._active_pairs_cache = None
            self.current_streaming_node_id = None
            # Delete the subtree so it doesn't appear as a ghost branch later.
            self._delete_subtree(last_user.id)
            if parent_id is not None:
                children = self.children_index.get(parent_id, [])
                if last_user.id in children:
                    children.remove(last_user.id)
            return (True, images)

    def truncate_to_last(self) -> bool:
        """Copy the last Q/A pair into new root nodes and set them as active.

        Earlier pairs become unreachable from the active path but remain in
        the graph (accessible via children_index from their original parents).
        """
        with self._lock:
            pairs = self._get_active_pairs()
            if len(pairs) <= 1:
                return False
            last_user, last_asst = pairs[-1]
            new_user = MessageNode(
                id=_new_id(),
                parent_id=None,
                role=MessageRole.USER,
                content=FullAnswer.from_string(
                    last_user.content.get_text_only_content(),
                ),
                created_at=_now(),
                images=list(last_user.images),
            )
            new_asst_content = (
                last_asst.content.copy() if last_asst is not None else FullAnswer()
            )
            new_asst = MessageNode(
                id=_new_id(),
                parent_id=new_user.id,
                role=MessageRole.ASSISTANT,
                content=new_asst_content,
                created_at=_now(),
            )
            self._add_node(new_user)
            self._add_node(new_asst)
            self.active_node_id = new_asst.id
            self._active_pairs_cache = None
            self.current_streaming_node_id = None
            return True

    def compact_answers(
        self,
        compacted_answer_strings: list[str] | None = None,
    ) -> None:
        """Remove tool components from answers, or replace with compacted text."""
        pairs = self._get_active_pairs()
        if compacted_answer_strings is not None:
            for i, text in enumerate(compacted_answer_strings):
                if i < len(pairs):
                    asst_node = pairs[i][1]
                    if asst_node is not None:
                        asst_node.content = FullAnswer.from_string(text)
        else:
            for _, asst in pairs:
                if asst is not None:
                    asst.content.remove_tool_components()

    # ------------------------------------------------------------------
    # Branching operations (new API)
    # ------------------------------------------------------------------

    def edit_question(self, pair_index: int, new_text: str) -> int:
        """Create a sibling branch with an edited question at pair_index.

        Returns the answer_index for the new empty assistant node.
        """
        with self._lock:
            pairs = self._get_active_pairs()
            if pair_index < 0 or pair_index >= len(pairs):
                raise IndexError(f"pair_index {pair_index} out of range")
            original_user = pairs[pair_index][0]
            # New user is a sibling of original_user (same parent).
            new_user = MessageNode(
                id=_new_id(),
                parent_id=original_user.parent_id,
                role=MessageRole.USER,
                content=FullAnswer.from_string(new_text),
                created_at=_now(),
                edge_type=EdgeType.EDIT_REGEN,
                images=list(original_user.images),
            )
            new_asst = MessageNode(
                id=_new_id(),
                parent_id=new_user.id,
                role=MessageRole.ASSISTANT,
                content=FullAnswer(),
                created_at=_now(),
                edge_type=EdgeType.EDIT_REGEN,
            )
            self._add_node(new_user)
            self._add_node(new_asst)
            self.active_node_id = new_asst.id
            self._active_pairs_cache = None
            self.current_streaming_node_id = new_asst.id
            return len(self._get_active_pairs()) - 1

    def regenerate_answer(self, pair_index: int) -> int:
        """Create a sibling branch for the assistant node at pair_index.

        Returns the answer_index for the new empty assistant node.
        """
        with self._lock:
            pairs = self._get_active_pairs()
            if pair_index < 0 or pair_index >= len(pairs):
                raise IndexError(f"pair_index {pair_index} out of range")
            original_user, original_asst = pairs[pair_index]
            if original_asst is None:
                raise ValueError(f"No assistant node at pair_index {pair_index}")
            # New assistant is a sibling of original_asst (same parent = user node).
            new_asst = MessageNode(
                id=_new_id(),
                parent_id=original_user.id,
                role=MessageRole.ASSISTANT,
                content=FullAnswer(),
                created_at=_now(),
                edge_type=EdgeType.EDIT_REGEN,
            )
            self._add_node(new_asst)
            self.active_node_id = new_asst.id
            self._active_pairs_cache = None
            self.current_streaming_node_id = new_asst.id
            return len(self._get_active_pairs()) - 1

    def fork_question(self, pair_index: int, new_question: str) -> int:
        """Fork by creating a sibling question at pair_index.

        Creates a new user node with the same parent as the original user node
        (i.e. a sibling of the original question), then an empty assistant child.
        Returns the answer_index for the new empty assistant node.
        """
        with self._lock:
            pairs = self._get_active_pairs()
            if pair_index < 0 or pair_index >= len(pairs):
                raise IndexError(f"pair_index {pair_index} out of range")
            original_user = pairs[pair_index][0]
            new_user = MessageNode(
                id=_new_id(),
                parent_id=original_user.parent_id,
                role=MessageRole.USER,
                content=FullAnswer.from_string(new_question),
                created_at=_now(),
                edge_type=EdgeType.BRANCH,
            )
            new_asst = MessageNode(
                id=_new_id(),
                parent_id=new_user.id,
                role=MessageRole.ASSISTANT,
                content=FullAnswer(),
                created_at=_now(),
                edge_type=EdgeType.BRANCH,
            )
            self._add_node(new_user)
            self._add_node(new_asst)
            self.active_node_id = new_asst.id
            self._active_pairs_cache = None
            self.current_streaming_node_id = new_asst.id
            return len(self._get_active_pairs()) - 1

    def fork_from(self, pair_index: int, new_question: str) -> int:
        """Fork a new branch from the assistant node at pair_index.

        Returns the answer_index for the new empty assistant node.
        """
        with self._lock:
            pairs = self._get_active_pairs()
            if pair_index < 0 or pair_index >= len(pairs):
                raise IndexError(f"pair_index {pair_index} out of range")
            _, fork_point = pairs[pair_index]
            if fork_point is None:
                raise ValueError(f"No assistant node at pair_index {pair_index}")
            new_user = MessageNode(
                id=_new_id(),
                parent_id=fork_point.id,
                role=MessageRole.USER,
                content=FullAnswer.from_string(new_question),
                created_at=_now(),
                edge_type=EdgeType.BRANCH,
            )
            new_asst = MessageNode(
                id=_new_id(),
                parent_id=new_user.id,
                role=MessageRole.ASSISTANT,
                content=FullAnswer(),
                created_at=_now(),
                edge_type=EdgeType.BRANCH,
            )
            self._add_node(new_user)
            self._add_node(new_asst)
            self.active_node_id = new_asst.id
            self._active_pairs_cache = None
            self.current_streaming_node_id = new_asst.id
            return len(self._get_active_pairs()) - 1

    def navigate_to(self, node_id: str, follow_to_leaf: bool = False) -> None:
        """Set active_node_id, optionally following first-child chain to leaf."""
        with self._lock:
            if node_id not in self.nodes:
                raise KeyError(f"Node {node_id!r} not in graph")
            target = self._follow_to_leaf(node_id) if follow_to_leaf else node_id
            self.active_node_id = target
            self._active_pairs_cache = None
            self.current_streaming_node_id = None

    def get_siblings(self, node_id: str) -> list[str]:
        """Return all sibling node IDs (children of this node's parent).

        The node itself is included in the list.
        """
        node = self.nodes.get(node_id)
        if node is None:
            return []
        # After None check, mypy correctly narrows node to MessageNode
        if node.parent_id is None:
            # Root-level nodes — siblings are all root nodes.
            return [n.id for n in self.nodes.values() if n.parent_id is None]
        return list(self.children_index.get(node.parent_id, [node_id]))

    def get_sibling_position(self, node_id: str) -> tuple[int, int]:
        """Return (1-based current index, total sibling count) for display.

        Example: sibling 2 of 3 → (2, 3), displayed as '◀ 2/3 ▶'.
        """
        siblings = self.get_siblings(node_id)
        if node_id in siblings:
            return (siblings.index(node_id) + 1, len(siblings))
        return (1, 1)

    def navigate_to_prev_sibling(self, node_id: str) -> str | None:
        """Navigate to the previous sibling; return the new active node_id or None."""
        siblings = self.get_siblings(node_id)
        if node_id not in siblings:
            return None
        idx = siblings.index(node_id)
        if idx == 0:
            return None
        target = siblings[idx - 1]
        self.navigate_to(target, follow_to_leaf=True)
        return self.active_node_id

    def navigate_to_next_sibling(self, node_id: str) -> str | None:
        """Navigate to the next sibling; return the new active node_id or None."""
        siblings = self.get_siblings(node_id)
        if node_id not in siblings:
            return None
        idx = siblings.index(node_id)
        if idx >= len(siblings) - 1:
            return None
        target = siblings[idx + 1]
        self.navigate_to(target, follow_to_leaf=True)
        return self.active_node_id

    def get_pair_node_ids(self, pair_index: int) -> tuple[str, str | None]:
        """Return (user_node_id, assistant_node_id | None) for a pair index."""
        pairs = self._get_active_pairs()
        if pair_index < 0 or pair_index >= len(pairs):
            raise IndexError(f"pair_index {pair_index} out of range")
        user, asst = pairs[pair_index]
        return (user.id, asst.id if asst is not None else None)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        with self._lock:
            return {
                "graph": {
                    "id": self.id,
                    "title": self.title,
                    "created_at": self.created_at,
                    "active_node_id": self.active_node_id,
                    "nodes": {nid: node.to_dict() for nid, node in self.nodes.items()},
                },
            }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ConversationGraph":
        """Deserialise from the new graph format (top-level 'graph' key)."""
        g = data.get("graph", data)  # tolerate both wrapped and unwrapped
        graph = cls(
            graph_id=g.get("id"),
            title=g.get("title", ""),
            created_at=g.get("created_at"),
        )
        for node_dict in g.get("nodes", {}).values():
            graph.nodes[node_dict["id"]] = MessageNode.from_dict(node_dict)
        graph._rebuild_children_index()
        graph.active_node_id = g.get("active_node_id")
        return graph

    @classmethod
    def from_chat_state_dict(cls, data: dict[str, Any]) -> "ConversationGraph":
        """Migrate from the old ChatState JSON format (one-way, lazy)."""
        questions: list[str] = data.get("questions", [])
        answers_data: list[Any] = data.get("answers", [])
        # Legacy format had list[list[dict[str, Any]]] for question_images
        images_list: list[list[dict[str, Any]]] = data.get(
            "question_images",
            [[] for _ in questions],
        )
        while len(images_list) < len(questions):
            images_list.append([])

        graph = cls()
        prev_id: str | None = None

        for i, q in enumerate(questions):
            a_data = answers_data[i] if i < len(answers_data) else {}
            # Convert legacy dict format to plain strings
            legacy_images = images_list[i]
            images: list[str] = []
            for img in legacy_images:
                if isinstance(img, dict) and "data" in img:
                    images.append(img["data"])
                elif isinstance(img, str):
                    images.append(img)

            user_node = MessageNode(
                id=_new_id(),
                parent_id=prev_id,
                role=MessageRole.USER,
                content=FullAnswer.from_string(q),
                created_at=_now(),
                images=images,
            )
            graph._add_node(user_node)

            if isinstance(a_data, str):
                answer = FullAnswer.from_string(a_data)
            elif isinstance(a_data, dict):
                answer = FullAnswer.from_dict(a_data)
            else:
                answer = FullAnswer()

            asst_node = MessageNode(
                id=_new_id(),
                parent_id=user_node.id,
                role=MessageRole.ASSISTANT,
                content=answer,
                created_at=_now(),
            )
            graph._add_node(asst_node)
            prev_id = asst_node.id

        if prev_id is not None:
            graph.active_node_id = prev_id

        return graph


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _new_id() -> str:
    return str(uuid.uuid4())


def _now() -> str:
    return datetime.now().isoformat()


def is_graph_format(data: dict[str, Any]) -> bool:
    """Return True if *data* is in the new ConversationGraph format."""
    return "graph" in data
