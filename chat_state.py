from dataclasses import dataclass
from typing import Any
from typing import List
from typing import Optional


@dataclass
class ChatState:
    questions: List[str]
    answers: List[str]
    current_streaming_index: Optional[int] = None

    def add_question(self, question: str) -> int:
        """Add question and return answer index."""
        self.questions.append(question)
        self.answers.append("")
        answer_index = len(self.answers) - 1
        self.current_streaming_index = answer_index
        return answer_index

    def append_to_answer(self, answer_index: int, content: str) -> bool:
        """Append content to answer."""
        if 0 <= answer_index < len(self.answers):
            self.answers[answer_index] += content
            return True
        return False

    def finish_streaming(self):
        """Mark streaming as complete."""
        self.current_streaming_index = None

    def is_streaming(self) -> bool:
        """Check if any answers are currently being streamed."""
        return self.current_streaming_index is not None

    def get_safe_copy(self) -> tuple[List[str], List[str], Optional[int]]:
        """Get a thread-safe copy of the current state."""
        return (
            self.questions.copy(),
            self.answers.copy(),
            self.current_streaming_index,
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "questions": self.questions.copy(),
            "answers": self.answers.copy(),
            "current_streaming_index": self.current_streaming_index,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ChatState":
        """Create ChatState from dictionary."""
        state = cls(
            questions=data.get("questions", []),
            answers=data.get("answers", []),
        )
        # Don't restore streaming index as it's runtime state
        return state
