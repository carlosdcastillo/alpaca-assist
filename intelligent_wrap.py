"""
Intelligent wrap module for ChatTab - handles smart text wrapping that preserves code blocks.
Now properly uses ChatState as the single source of truth.
"""
import re
import tkinter as tk
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple


class IntelligentWrapper:
    """Handles intelligent text wrapping that preserves code blocks."""

    def __init__(self):
        """Initialize the IntelligentWrapper."""
        self.wrapped_widgets = {}
        self.is_wrapped = {}
        self.original_text = {}
        self.resize_timers = {}
        self.verbose = False

    def get_tab_id(self, widget: Any) -> str:
        """Get a unique identifier for the tab containing this widget."""
        try:
            tab = getattr(widget, "tab", None)
            if tab and hasattr(tab, "tab_id"):
                return tab.tab_id
            return str(id(widget))
        except Exception as e:
            print(f"Error getting tab ID: {e}")
            return str(id(widget))

    def is_widget_wrapped(self, widget: Any) -> bool:
        """Check if a widget is currently wrapped."""
        tab_id = self.get_tab_id(widget)
        return tab_id in self.wrapped_widgets and self.wrapped_widgets[tab_id]

    def get_original_text(self, tab_id: str) -> str | None:
        """Get the original text for a tab before wrapping was applied."""
        return self.original_text.get(tab_id)

    def set_original_text(self, tab_id: str, text: str, reason: str = "") -> None:
        """Set the original text for a tab before wrapping is applied."""
        self.original_text[tab_id] = text
        if self.verbose and reason:
            print(f"Set original text for tab {tab_id} (reason: {reason})")

    def apply_intelligent_wrap(
        self,
        widget: Any,
        force_recalculate: bool = False,
    ) -> None:
        """
        Apply intelligent wrapping to the widget.
        Now works directly with ChatState as the source of truth.
        """
        try:
            tab = getattr(widget, "tab", None)
            if not tab or not hasattr(tab, "chat_state"):
                print("Widget has no associated tab or chat_state")
                return
            tab_id = self.get_tab_id(widget)
            questions, answers, metadata = tab.chat_state.get_safe_copy()
            if not questions and (not answers):
                print("No content to wrap")
                return
            wrap_width = self.calculate_wrap_width(widget)
            wrapped_content = self._build_wrapped_content(
                questions,
                answers,
                wrap_width,
            )
            self._update_widget_text(widget, wrapped_content)
            self.wrapped_widgets[tab_id] = True
            self.is_wrapped[tab_id] = True
            if hasattr(tab.chat_state, "set_metadata"):
                tab.chat_state.set_metadata("wrap_enabled", True)
                tab.chat_state.set_metadata("wrap_width", wrap_width)
            if self.verbose:
                print(f"Applied intelligent wrap to tab {tab_id}")
        except Exception as e:
            print(f"Error applying intelligent wrap: {e}")
            import traceback

            traceback.print_exc()

    def remove_intelligent_wrap(self, widget: Any) -> None:
        """
        Remove intelligent wrapping from the widget.
        Now works directly with ChatState as the source of truth.
        """
        try:
            tab = getattr(widget, "tab", None)
            if not tab or not hasattr(tab, "chat_state"):
                print("Widget has no associated tab or chat_state")
                return
            tab_id = self.get_tab_id(widget)
            questions, answers, metadata = tab.chat_state.get_safe_copy()
            if not questions and (not answers):
                print("No content to unwrap")
                return
            unwrapped_content = self._build_unwrapped_content(questions, answers)
            self._update_widget_text(widget, unwrapped_content)
            self.wrapped_widgets[tab_id] = False
            self.is_wrapped[tab_id] = False
            if hasattr(tab.chat_state, "set_metadata"):
                tab.chat_state.set_metadata("wrap_enabled", False)
            if self.verbose:
                print(f"Removed intelligent wrap from tab {tab_id}")
        except Exception as e:
            print(f"Error removing intelligent wrap: {e}")
            import traceback

            traceback.print_exc()

    def wrap_text(self, text: str, wrap_width: int) -> str:
        """
        Wrap text intelligently, preserving code blocks.
        """
        if not text or wrap_width <= 0:
            return text
        lines = text.split("\n")
        wrapped_lines = []
        i = 0
        while i < len(lines):
            line = lines[i]
            if self.should_preserve_line(line):
                wrapped_lines.append(line)
            elif line.strip().startswith("```"):
                wrapped_lines.append(line)
                i += 1
                while i < len(lines) and (not lines[i].strip().startswith("```")):
                    wrapped_lines.append(lines[i])
                    i += 1
                if i < len(lines):
                    wrapped_lines.append(lines[i])
            elif len(line) <= wrap_width:
                wrapped_lines.append(line)
            else:
                wrapped = self._wrap_single_line(line, wrap_width)
                wrapped_lines.extend(wrapped)
            i += 1
        return "\n".join(wrapped_lines)

    def _wrap_single_line(self, line: str, wrap_width: int) -> list[str]:
        """Wrap a single line of text, preserving indentation."""
        if len(line) <= wrap_width:
            return [line]
        indent = ""
        for char in line:
            if char in " \t":
                indent += char
            else:
                break
        content = line[len(indent) :]
        if len(line) <= wrap_width:
            return [line]
        wrapped = []
        current_line = ""
        words = content.split()
        for word in words:
            if len(word) > wrap_width - len(indent):
                if current_line:
                    wrapped.append(indent + current_line)
                    current_line = ""
                remaining_word = word
                while len(remaining_word) > wrap_width - len(indent):
                    wrapped.append(indent + remaining_word[: wrap_width - len(indent)])
                    remaining_word = remaining_word[wrap_width - len(indent) :]
                if remaining_word:
                    current_line = remaining_word
            elif not current_line:
                current_line = word
            elif len(indent) + len(current_line) + 1 + len(word) <= wrap_width:
                current_line += " " + word
            else:
                wrapped.append(indent + current_line)
                current_line = word
        if current_line:
            wrapped.append(indent + current_line)
        return wrapped if wrapped else [line]

    def calculate_wrap_width(self, widget: Any) -> int:
        """Calculate the appropriate wrap width for the widget."""
        try:
            widget.update_idletasks()
            width = widget.winfo_width()
            font = widget.cget("font")
            if font:
                char_width = 8
                try:
                    from tkinter import font as tkfont

                    font_obj = tkfont.Font(font=font)
                    char_width = font_obj.measure("m")
                except:
                    pass
                wrap_width = width // char_width - 3
            else:
                wrap_width = width // 8 - 3
            return wrap_width
        except Exception as e:
            print(f"Error calculating wrap width: {e}")
            return 80

    def should_preserve_line(self, line: str) -> bool:
        return False

    def handle_window_resize(self, widget: Any) -> None:
        """Handle window resize events for wrapped widgets."""
        try:
            tab_id = self.get_tab_id(widget)
            if tab_id in self.resize_timers:
                widget.after_cancel(self.resize_timers[tab_id])
            if self.is_widget_wrapped(widget):
                timer_id = widget.after(
                    500,
                    lambda: self._delayed_rewrap(widget, tab_id),
                )
                self.resize_timers[tab_id] = timer_id
        except Exception as e:
            print(f"Error handling window resize: {e}")

    def _delayed_rewrap(self, widget: Any, tab_id: str) -> None:
        """Re-wrap content after window resize."""
        try:
            if tab_id in self.resize_timers:
                del self.resize_timers[tab_id]
            if self.is_widget_wrapped(widget):
                self.apply_intelligent_wrap(widget, force_recalculate=True)
        except Exception as e:
            print(f"Error in delayed rewrap: {e}")

    def cleanup_tab(self, widget: Any) -> None:
        """Clean up resources when a tab is closed."""
        try:
            tab_id = self.get_tab_id(widget)
            if tab_id in self.resize_timers:
                widget.after_cancel(self.resize_timers[tab_id])
                del self.resize_timers[tab_id]
            if tab_id in self.wrapped_widgets:
                del self.wrapped_widgets[tab_id]
            if tab_id in self.is_wrapped:
                del self.is_wrapped[tab_id]
            if tab_id in self.original_text:
                del self.original_text[tab_id]
            if self.verbose:
                print(f"Cleaned up resources for tab {tab_id}")
        except Exception as e:
            print(f"Error cleaning up tab: {e}")

    def handle_job_submit(self, widget: Any) -> None:
        """Handle job submission - temporarily disable wrap during streaming."""
        try:
            tab_id = self.get_tab_id(widget)
            if self.is_widget_wrapped(widget):
                if not hasattr(self, "_streaming_wrapped_tabs"):
                    self._streaming_wrapped_tabs = set()
                self._streaming_wrapped_tabs.add(tab_id)
                self.remove_intelligent_wrap(widget)
                if self.verbose:
                    print(f"Temporarily disabled wrap for streaming in tab {tab_id}")
        except Exception as e:
            print(f"Error handling job submit: {e}")

    def handle_job_complete(self, widget: Any) -> None:
        """Handle job completion - re-enable wrap if it was enabled before."""
        try:
            tab_id = self.get_tab_id(widget)
            if (
                hasattr(self, "_streaming_wrapped_tabs")
                and tab_id in self._streaming_wrapped_tabs
            ):
                self._streaming_wrapped_tabs.remove(tab_id)
                self.apply_intelligent_wrap(widget)
                if self.verbose:
                    print(f"Re-enabled wrap after streaming in tab {tab_id}")
        except Exception as e:
            print(f"Error handling job complete: {e}")

    def handle_job_stop(self, widget: Any) -> None:
        """Handle job stop - same as job complete."""
        self.handle_job_complete(widget)

    def _safe_highlight(self, widget: Any) -> None:
        """Safely apply syntax highlighting."""
        try:
            if hasattr(widget, "highlight_text"):
                widget.highlight_text()
        except Exception as e:
            print(f"Error applying syntax highlighting: {e}")

    def toggle_intelligent_wrap(self, widget: Any) -> bool:
        """
        Toggle intelligent wrap for the widget.
        Returns the new wrap state.
        """
        try:
            tab_id = self.get_tab_id(widget)
            if self.is_widget_wrapped(widget):
                self.remove_intelligent_wrap(widget)
                self.is_wrapped[tab_id] = False
                self.wrapped_widgets[tab_id] = False
                return False
            else:
                self.apply_intelligent_wrap(widget)
                self.is_wrapped[tab_id] = True
                self.wrapped_widgets[tab_id] = True
                return True
        except Exception as e:
            print(f"Error toggling intelligent wrap: {e}")
            import traceback

            traceback.print_exc()
            return False

    def _build_wrapped_content(
        self,
        questions: list[str],
        answers: list[str],
        wrap_width: int,
    ) -> str:
        """Build wrapped content from questions and answers."""
        result = []
        for i, (q, a) in enumerate(zip(questions, answers)):
            if i > 0:
                result.append("\n\n")
            q_wrap_width = max(20, wrap_width - 3)
            wrapped_q = self.wrap_text(q, q_wrap_width)
            result.append(f"Q: {wrapped_q}\n\n")
            a_wrap_width = max(20, wrap_width - 3)
            wrapped_a = self.wrap_text(a, a_wrap_width)
            result.append(f"A: {wrapped_a}")
        return "".join(result)

    def _build_unwrapped_content(self, questions: list[str], answers: list[str]) -> str:
        """Build unwrapped content from questions and answers."""
        result = []
        for i, (q, a) in enumerate(zip(questions, answers)):
            if i > 0:
                result.append("\n\n")
            result.append(f"Q: {q}\n\n")
            result.append(f"A: {a}")
        return "".join(result)

    def _update_widget_text(
        self,
        widget: Any,
        text: str,
        is_streaming: bool = False,
    ) -> None:
        """Update widget text and re-apply syntax highlighting."""
        try:
            current_pos = widget.index(tk.INSERT)
            widget.config(state=tk.NORMAL)
            widget.delete("1.0", tk.END)
            widget.insert("1.0", text)
            if hasattr(widget, "highlight_text_full"):
                widget.highlight_text_full()
            elif hasattr(widget, "highlight_text"):
                if hasattr(widget, "last_highlighted_content"):
                    widget.last_highlighted_content = None
                if hasattr(widget, "last_highlighted_length"):
                    widget.last_highlighted_length = 0
                widget.highlight_text()
            if is_streaming:
                widget.see(tk.END)
            else:
                widget.mark_set(tk.INSERT, current_pos)
                widget.see(current_pos)
        except Exception as e:
            print(f"Error updating widget text: {e}")
