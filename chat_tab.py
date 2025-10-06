from tkinter import ttk
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from chat_tab_core import ChatTabCore
from chat_tab_streaming_advanced import ChatTabStreaming


class ChatTab(ChatTabCore, ChatTabStreaming):
    """Complete ChatTab implementation combining core functionality and streaming capabilities."""

    def __init__(
        self,
        parent: "ChatApp",
        notebook: ttk.Notebook,
        file_completions: list[str],
        preferences: dict[str, Any] | None = None,
    ) -> None:
        # Initialize the core functionality
        ChatTabCore.__init__(self, parent, notebook, file_completions, preferences)
        ChatTabStreaming.__init__(self)
        # Streaming functionality is mixed in via multiple inheritance
