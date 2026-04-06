import json
import threading
from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import List
from typing import Optional
from typing import Union


@dataclass
class ToolCall:
    """Represents a tool call with content and ID."""

    content: str
    id: str


@dataclass
class ToolResult:
    """Represents a tool result with content and ID."""

    content: str
    id: str


AnswerComponent = Union[str, ToolCall, ToolResult]


@dataclass
class FullAnswer:
    """Represents a complete answer containing text, tool calls, and tool results."""

    components: list[AnswerComponent]

    def __init__(self, components: list[AnswerComponent] | None = None):
        self.components = components or []
        self._lock = threading.RLock()

    def add_text(self, text: str) -> None:
        """Add text content to the answer."""
        with self._lock:
            if len(self.components) == 0:
                self.components.append(text)
            elif isinstance(self.components[-1], str):
                self.components[-1] = self.components[-1] + text
            else:
                self.components.append(text)

    def add_tool_call(self, content: str, tool_id: str) -> None:
        """Add a tool call to the answer."""
        with self._lock:
            self.components.append(ToolCall(content=content, id=tool_id))

    def add_tool_call_if_absent(self, content: str, tool_id: str) -> bool:
        """Add ToolCall only if no ToolCall with this tool_id already exists.

        Used as a fallback when the queue-processor event times out, to avoid
        double-adding the same ToolCall if the queue processor also ran.
        Returns True if added, False if already present.
        """
        with self._lock:
            if any(
                isinstance(c, ToolCall) and c.id == tool_id for c in self.components
            ):
                return False
            self.components.append(ToolCall(content=content, id=tool_id))
            return True

    def add_tool_result(self, content: str, tool_id: str) -> None:
        """Add a tool result to the answer."""
        formatted_content = self._format_json_if_parseable(content)
        with self._lock:
            self.components.append(ToolResult(content=formatted_content, id=tool_id))

    def get_text_content(self) -> str:
        """Get all text content as a single string (for backward compatibility)."""
        with self._lock:
            text_parts = []
            for component in self.components:
                if isinstance(component, str):
                    text_parts.append(component)
                elif isinstance(component, (ToolCall, ToolResult)):
                    text_parts.append(component.content)
            return "\n".join(text_parts)

    def get_text_only_content(self) -> str:
        """Get only the text components, excluding tool calls and results."""
        with self._lock:
            text_parts = []
            for component in self.components:
                if isinstance(component, str):
                    text_parts.append(component)
            return "".join(text_parts)

    def trim_text(self, n: int) -> None:
        """Remove the last *n* characters from text (str) components.

        Walks backwards through self.components.  For each str component,
        trims up to n characters from the end.  ToolCall and ToolResult
        components are skipped unchanged.  Empty str components are removed
        after trimming.

        Used by the fabrication-correction loop to excise narrated-but-not-
        executed content from the answer before re-prompting the model.

        Args:
            n: Total characters to remove from the tail of all text.
        """
        with self._lock:
            remaining = n
            for i in range(len(self.components) - 1, -1, -1):
                if remaining <= 0:
                    break
                component = self.components[i]
                if not isinstance(component, str):
                    continue
                if len(component) <= remaining:
                    remaining -= len(component)
                    self.components[i] = ""
                else:
                    self.components[i] = component[:-remaining]
                    remaining = 0
            # Drop empty text components left behind by the trim.
            self.components = [
                c for c in self.components if not (isinstance(c, str) and c == "")
            ]

    def get_tool_interactions(self) -> list[tuple[str, str, str | None]]:
        """Get tool calls and their corresponding results for building continuation messages.

        Returns a list of (tool_call_content, tool_id, optional_result_content) tuples.
        This matches the Rust implementation's get_tool_interactions() method.
        """
        interactions: list[tuple[str, str, str | None]] = []
        pending_tool_call: tuple[str, str] | None = None

        with self._lock:
            components_snapshot = list(self.components)

        for component in components_snapshot:
            if isinstance(component, ToolCall):
                # If there's a pending tool call without a result, add it
                if pending_tool_call is not None:
                    interactions.append(
                        (pending_tool_call[0], pending_tool_call[1], None),
                    )
                pending_tool_call = (component.content, component.id)
            elif isinstance(component, ToolResult):
                if pending_tool_call is not None:
                    # Match result to the pending tool call
                    interactions.append(
                        (pending_tool_call[0], pending_tool_call[1], component.content),
                    )
                    pending_tool_call = None
                else:
                    # Orphan result - shouldn't happen but handle gracefully
                    interactions.append(("", component.id, component.content))
            # Text components don't affect tool interaction tracking

        # Don't forget any trailing tool call without a result
        if pending_tool_call is not None:
            interactions.append((pending_tool_call[0], pending_tool_call[1], None))

        return interactions

    def has_tool_interactions(self) -> bool:
        """Check if this answer contains any tool interactions."""
        with self._lock:
            return any(isinstance(c, (ToolCall, ToolResult)) for c in self.components)

    def consolidate_pre_tool_text(self) -> None:
        """Move stray str components after the last ToolCall back to before it.

        Race condition: ToolCall is saved synchronously in the tool thread while
        pre-tool ContentUpdates are still being drained by the queue processor on
        the main thread.  Those late-arriving text chunks land *after* ToolCall in
        components, causing the reload display to show pre-tool text between the
        ▶ Tool Call and ▶ Tool Result fold widgets.

        This method must be called (from the tool thread) *before* indicator text
        is appended so that:
          1. All stray pre-tool str components are merged back to before ToolCall.
          2. Indicator text is then appended cleanly as the first str after ToolCall.

        Only str components between the last ToolCall and the first subsequent
        ToolResult (or end of list) are moved — ToolResult components are untouched.
        """
        with self._lock:
            last_tc = next(
                (
                    i
                    for i in range(len(self.components) - 1, -1, -1)
                    if isinstance(self.components[i], ToolCall)
                ),
                None,
            )
            if last_tc is None:
                return

            end_idx = next(
                (
                    i
                    for i in range(last_tc + 1, len(self.components))
                    if isinstance(self.components[i], ToolResult)
                ),
                len(self.components),
            )

            stray_indices = [
                i
                for i in range(last_tc + 1, end_idx)
                if isinstance(self.components[i], str) and self.components[i]
            ]
            if not stray_indices:
                return

            stray_text = "".join(
                c for i in stray_indices if isinstance(c := self.components[i], str)
            )
            for i in reversed(stray_indices):
                del self.components[i]

            prev = self.components[last_tc - 1] if last_tc > 0 else None
            if isinstance(prev, str):
                self.components[last_tc - 1] = prev + stray_text
            else:
                self.components.insert(last_tc, stray_text)

    def remove_tool_components(self) -> None:
        """Remove all tool call and tool result components, keeping only text."""
        with self._lock:
            self.components = [c for c in self.components if isinstance(c, str)]

    def copy(self) -> "FullAnswer":
        """Return a thread-safe snapshot copy of this answer."""
        with self._lock:
            return FullAnswer(list(self.components))

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        with self._lock:
            components_snapshot = list(self.components)
        serialized_components = []
        for component in components_snapshot:
            if isinstance(component, str):
                serialized_components.append({"type": "text", "content": component})
            elif isinstance(component, ToolCall):
                serialized_components.append(
                    {
                        "type": "tool_call",
                        "content": component.content,
                        "id": component.id,
                    },
                )
            elif isinstance(component, ToolResult):
                serialized_components.append(
                    {
                        "type": "tool_result",
                        "content": component.content,
                        "id": component.id,
                    },
                )
        return {"components": serialized_components}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FullAnswer":
        """Create FullAnswer from dictionary."""
        components = []
        for comp_data in data.get("components", []):
            comp_type = comp_data.get("type")
            if comp_type == "text":
                components.append(comp_data["content"])
            elif comp_type == "tool_call":
                components.append(
                    ToolCall(content=comp_data["content"], id=comp_data["id"]),
                )
            elif comp_type == "tool_result":
                components.append(
                    ToolResult(content=comp_data["content"], id=comp_data["id"]),
                )
        return cls(components)

    @classmethod
    def from_string(cls, text: str) -> "FullAnswer":
        """Create FullAnswer from a simple string (for backward compatibility)."""
        return cls([text] if text else [])

    def _format_json_if_parseable(self, content: str) -> str:
        """Format content as JSON with indentation if it's valid JSON."""
        stripped_content = content.strip()
        is_code_block = stripped_content.startswith(
            "```",
        ) and stripped_content.endswith("```")
        if is_code_block:
            lines = stripped_content.split("\n")
            if len(lines) > 2:
                # Only reformat when the language annotation is "json" or absent.
                # Never overwrite a non-json language (e.g. "python") with "json"
                # just because the inner content happens to be valid JSON.
                fence_lang = lines[0].lstrip("`").strip().lower()
                if fence_lang not in ("", "json"):
                    return content
                inner_content = "\n".join(lines[1:-1])
                try:
                    parsed = json.loads(inner_content)
                    formatted_json = json.dumps(parsed, indent=2, ensure_ascii=False)
                    return f"```json\n{formatted_json}\n```"
                except (json.JSONDecodeError, TypeError):
                    return content
        else:
            try:
                parsed = json.loads(content)
                return json.dumps(parsed, indent=2, ensure_ascii=False)
            except (json.JSONDecodeError, TypeError):
                return content
        return content


@dataclass
class ChatState:
    questions: list[str]
    answers: list[FullAnswer]
    question_images: list[list[str]] = field(default_factory=list)
    current_streaming_index: int | None = None

    def add_question(self, question: str) -> int:
        """Add question and return answer index."""
        self.questions.append(question)
        self.answers.append(FullAnswer())
        self.question_images.append([])
        answer_index = len(self.answers) - 1
        self.current_streaming_index = answer_index
        return answer_index

    def append_to_answer(self, answer_index: int, content: str) -> bool:
        """Append text content to answer."""
        if 0 <= answer_index < len(self.answers):
            self.answers[answer_index].add_text(content)
            return True
        return False

    def add_tool_call_to_answer(
        self,
        answer_index: int,
        content: str,
        tool_id: str,
    ) -> bool:
        """Add a tool call to the specified answer."""
        if 0 <= answer_index < len(self.answers):
            self.answers[answer_index].add_tool_call(content, tool_id)
            return True
        return False

    def add_tool_result_to_answer(
        self,
        answer_index: int,
        content: str,
        tool_id: str,
    ) -> bool:
        """Add a tool result to the specified answer."""
        if 0 <= answer_index < len(self.answers):
            self.answers[answer_index].add_tool_result(content, tool_id)
            return True
        return False

    def finish_streaming(self) -> None:
        """Mark streaming as complete."""
        self.current_streaming_index = None

    def is_streaming(self) -> bool:
        """Check if any answers are currently being streamed."""
        return self.current_streaming_index is not None

    def get_display_text(self, include_tool_content: bool = True) -> str:
        """Generate display text from state.

        Args:
            include_tool_content: If False, excludes tool calls and results (for compacted view)

        Returns:
            Formatted text for display
        """
        lines = []
        for i, (question, answer) in enumerate(zip(self.questions, self.answers)):
            if i > 0:
                lines.append("")
                lines.append("─" * 80)
                lines.append("")
            lines.append(f"Q: {question}")
            answer_line = "A: "
            if include_tool_content:
                answer_text = answer.get_text_content()
            else:
                answer_text = answer.get_text_only_content()
            if answer_text:
                answer_line += answer_text
            lines.append(answer_line)
        return "\n".join(lines)

    def get_safe_copy_full(self) -> tuple[list[str], list[FullAnswer], int | None]:
        """Get a thread-safe copy of the current state with full answer data."""
        return (
            self.questions.copy(),
            [answer.copy() for answer in self.answers],
            self.current_streaming_index,
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "questions": self.questions.copy(),
            "answers": [answer.to_dict() for answer in self.answers],
            "current_streaming_index": self.current_streaming_index,
            "question_images": [list(imgs) for imgs in self.question_images],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ChatState":
        """Create ChatState from dictionary."""
        questions = data.get("questions", [])
        answers_data = data.get("answers", [])
        answers = []
        for answer_data in answers_data:
            if isinstance(answer_data, str):
                answers.append(FullAnswer.from_string(answer_data))
            elif isinstance(answer_data, dict):
                answers.append(FullAnswer.from_dict(answer_data))
            else:
                answers.append(FullAnswer())
        state = cls(questions=questions, answers=answers)
        raw_images = data.get("question_images", [])
        if raw_images:
            state.question_images = [list(imgs) for imgs in raw_images]
        else:
            state.question_images = [[] for _ in questions]
        return state

    def compact_answers(
        self,
        compacted_answer_strings: list[str] | None = None,
    ) -> None:
        """Remove tool call and tool result components from all answers, keeping only text.

        Args:
            compacted_answer_strings: Optional list of compacted answer strings to replace current answers.
                                    If None, removes tool components from existing answers.
        """
        if compacted_answer_strings is not None:
            self.answers = [
                FullAnswer.from_string(text) for text in compacted_answer_strings
            ]
        else:
            for answer in self.answers:
                answer.remove_tool_components()

    def get_safe_copy(self) -> tuple[list[str], list[str], int | None]:
        """Get a thread-safe copy of the current state (backward compatibility)."""
        answer_strings = [answer.get_text_only_content() for answer in self.answers]
        return (self.questions.copy(), answer_strings, self.current_streaming_index)

    def pop_last_qa(self) -> tuple[bool, list[str]]:
        """Remove the last question-answer pair.

        Returns:
            Tuple of (success, images) where images are the popped question's images.
        """
        if not self.questions:
            return (False, [])

        self.questions.pop()
        self.answers.pop()
        images = self.question_images.pop() if self.question_images else []
        self.current_streaming_index = None
        return (True, images)

    def truncate_to_last(self) -> bool:
        """Remove all question-answer pairs except the last one.

        Returns:
            True if truncation occurred, False if there was nothing to truncate
            (zero or one Q/A pair).
        """
        if len(self.questions) <= 1:
            return False

        # Keep only the last question and answer
        self.questions = [self.questions[-1]]
        self.answers = [self.answers[-1]]
        if self.question_images:
            self.question_images = [self.question_images[-1]]

        # Reset streaming index since list structure changed
        self.current_streaming_index = None

        return True
