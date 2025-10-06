from dataclasses import dataclass
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

    def add_text(self, text: str) -> None:
        """Add text content to the answer."""
        if len(self.components) == 0:
            self.components.append(text)
        elif isinstance(self.components[-1], str):
            self.components[-1] = self.components[-1] + text
        else:
            self.components.append(text)

    def add_tool_call(self, content: str, tool_id: str) -> None:
        """Add a tool call to the answer."""
        self.components.append(ToolCall(content=content, id=tool_id))

    def add_tool_result(self, content: str, tool_id: str) -> None:
        """Add a tool result to the answer."""
        self.components.append(ToolResult(content=content, id=tool_id))

    def get_text_content(self) -> str:
        """Get all text content as a single string (for backward compatibility)."""
        text_parts = []
        for component in self.components:
            if isinstance(component, str):
                text_parts.append(component)
            elif isinstance(component, (ToolCall, ToolResult)):
                text_parts.append(component.content)
        return "\n".join(text_parts)

    def get_text_only_content(self) -> str:
        """Get only the text components, excluding tool calls and results."""
        text_parts = []
        for component in self.components:
            if isinstance(component, str):
                text_parts.append(component)
        return "".join(text_parts)

    def remove_tool_components(self) -> None:
        """Remove all tool call and tool result components, keeping only text."""
        self.components = [c for c in self.components if isinstance(c, str)]

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        serialized_components = []
        for component in self.components:
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


@dataclass
class ChatState:
    questions: list[str]
    answers: list[FullAnswer]
    current_streaming_index: int | None = None

    def add_question(self, question: str) -> int:
        """Add question and return answer index."""
        self.questions.append(question)
        self.answers.append(FullAnswer())
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

    def finish_streaming(self):
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
                lines.append("-" * 80)
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
            [FullAnswer(answer.components.copy()) for answer in self.answers],
            self.current_streaming_index,
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "questions": self.questions.copy(),
            "answers": [answer.to_dict() for answer in self.answers],
            "current_streaming_index": self.current_streaming_index,
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
