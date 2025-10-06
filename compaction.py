"""
Compaction module for ChatTab - handles compacting tool calls and results in chat display.
Now properly uses ChatState as the single source of truth and works with FullAnswer structure.
Compaction is irreversible and committed directly to the ChatState - tool calls and results
are permanently replaced with summaries in the actual chat state.
"""
import re
import tkinter as tk
from typing import Any
from typing import List
from typing import Optional
from typing import Tuple

from chat_state import ChatState
from chat_state import FullAnswer
from chat_state import ToolCall
from chat_state import ToolResult


class Compactor:
    """Handles compacting tool calls and their results in the chat display."""

    def __init__(self):
        """Initialize the Compactor."""
        self.verbose = False

    def can_compact(self, tab: Any) -> bool:
        """Check if the tab can be compacted."""
        try:
            if not hasattr(tab, "chat_state"):
                if self.verbose:
                    print("Tab has no chat_state")
                return False
            questions, answers, _ = tab.chat_state.get_safe_copy_full()
            if not answers:
                if self.verbose:
                    print("No answers to compact")
                return False
            for answer in answers:
                if self._contains_tool_components(answer):
                    if self.verbose:
                        print(f"Found tool components in answer")
                    return True
            if self.verbose:
                print("No tool components found in any answer")
            return False
        except Exception as e:
            print(f"Error checking if tab can be compacted: {e}")
            return False

    def _contains_tool_components(self, answer: FullAnswer) -> bool:
        """Check if answer contains tool calls or tool results."""
        for component in answer.components:
            if isinstance(component, (ToolCall, ToolResult)):
                return True
        return False

    def compact_tab(self, tab: Any) -> bool:
        """
        Compact the tab by collapsing tool calls and results.
        This operation is irreversible and commits changes directly to the ChatState.
        Tool components are permanently replaced with summaries in the actual chat state.
        """
        try:
            if not self.can_compact(tab):
                print("Tab cannot be compacted")
                return False

            # Work directly on the ChatState - no copies, permanent changes
            any_compacted = False
            for i, answer in enumerate(tab.chat_state.answers):
                if self._contains_tool_components(answer):
                    self._compact_answer_in_place(answer)
                    any_compacted = True
                    if self.verbose:
                        print(f"Compacted answer {i + 1} in ChatState")

            if not any_compacted:
                print("No changes made during compaction")
                return False

            # Update display from the now-modified ChatState
            self._update_display_from_state(tab)
            if hasattr(tab, "is_compacted"):
                tab.is_compacted = True
            print(f"Successfully compacted tab (irreversible, committed to ChatState)")
            return True
        except Exception as e:
            print(f"Error compacting tab: {e}")
            import traceback

            traceback.print_exc()
            return False

    def _compact_answer_in_place(self, answer: FullAnswer) -> None:
        """Compact a FullAnswer by replacing tool components with summaries."""
        new_components = []
        i = 0
        indices = []
        indices2 = []
        inserted = 0
        while i < len(answer.components):
            component = answer.components[i]
            if isinstance(component, ToolCall):
                tool_name = self._extract_tool_name_from_component(component)
                tool_result = None
                j = i + 1
                while j < len(answer.components):
                    next_component = answer.components[j]
                    if (
                        isinstance(next_component, ToolResult)
                        and next_component.id == component.id
                    ):
                        tool_result = next_component
                        break
                    elif not isinstance(next_component, str):
                        break
                    j += 1
                if tool_result:
                    summary = (
                        f"\n📦 [Tool: {tool_name}] (compacted - executed successfully)\n"
                    )
                    i = j + 1
                else:
                    summary = f"\n📦 [Tool: {tool_name}] (compacted - no result found)\n"
                    i += 1
                new_components.append(summary)
                inserted += 1
            elif isinstance(component, ToolResult):
                already_handled = False
                for k in range(len(new_components)):
                    if (
                        isinstance(new_components[k], str)
                        and component.id in new_components[k]
                    ):
                        already_handled = True
                        break
                if not already_handled:
                    summary = f"\n📦 [Tool Result: {component.id}] (compacted)\n"
                    new_components.append(summary)
                    i += 1
                    inserted += 1
            else:
                indices.append(i)
                indices2.append(inserted)
                new_components.append("placeholder")
                inserted += 1
                i += 1
        print("inserted:")
        print(inserted)
        print("indices2:")
        print(indices2)
        new_c = self.clear([answer.components[i] for i in indices])
        for k, new in zip(indices2, new_c):
            new_components[k] = new
            print(f"guevo: {new}")
        # Permanently replace the components in the FullAnswer
        answer.components = new_components

    def _extract_tool_name_from_component(self, tool_call: ToolCall) -> str:
        """Extract the tool name from a ToolCall component."""
        try:
            import json

            tool_data = json.loads(tool_call.content)
            if "tool_call" in tool_data and "name" in tool_data["tool_call"]:
                return tool_data["tool_call"]["name"]
        except (json.JSONDecodeError, KeyError):
            pass
        name_match = re.search('"name"\\s*:\\s*"([^"]+)"', tool_call.content)
        if name_match:
            return name_match.group(1)
        return "Unknown"

    def clear(
        self,
        lst,
        start_pattern="🔧 **Executing tool:",
        end_pattern="\n-=-=-=-=-\n",
    ):
        """
        Remove content between start_pattern and end_pattern from a list of strings.
        Handles both same-item and adjacent-item spanning patterns.

        Args:
            lst: List of strings to process
            start_pattern: Pattern marking the start of content to remove
            end_pattern: Pattern marking the end of content to remove

        Returns:
            List of strings with patterns removed
        """
        if not lst:
            return lst

        result = []
        i = 0

        while i < len(lst):
            current = lst[i]

            # Check if start pattern exists in current item
            start_idx = current.find(start_pattern)

            if start_idx != -1:
                # Found start pattern
                end_idx = current.find(end_pattern, start_idx)

                if end_idx != -1:
                    # Both patterns in same item
                    end_idx += len(end_pattern)
                    cleaned = current[:start_idx] + current[end_idx:]

                    # Check for more patterns in the same item
                    if cleaned.find(start_pattern) != -1:
                        # Recursively clean the same item
                        result.append(
                            self.clear([cleaned], start_pattern, end_pattern)[0],
                        )
                    else:
                        result.append(cleaned)
                else:
                    # Start pattern found but end pattern not in same item
                    # Check next item for end pattern
                    if i + 1 < len(lst):
                        next_item = lst[i + 1]
                        end_idx_next = next_item.find(end_pattern)

                        if end_idx_next != -1:
                            # End pattern found in next item
                            end_idx_next += len(end_pattern)

                            # Keep part before start pattern from current item
                            cleaned_current = current[:start_idx]
                            # Keep part after end pattern from next item
                            cleaned_next = next_item[end_idx_next:]

                            # Combine the cleaned parts
                            combined = cleaned_current + cleaned_next
                            result.append(cleaned_current)
                            result.append(
                                self.clear([cleaned_next], start_pattern, end_pattern)[
                                    0
                                ],
                            )

                            # Skip the next item since we've processed it
                            i += 1
                        else:
                            # End pattern not found in next item either
                            # Keep the current item as is (or you might want different behavior)
                            result.append(current)
                    else:
                        # No next item available
                        result.append(current)
            else:
                # No start pattern in current item
                result.append(current)

            i += 1

        return result

    def _update_display_from_state(self, tab: Any) -> None:
        """Update the display from the chat state."""
        try:
            questions, answers, metadata = tab.chat_state.get_safe_copy_full()
            tab.chat_display.config(state=tk.NORMAL)
            tab.chat_display.delete("1.0", tk.END)
            for i, (q, answer) in enumerate(zip(questions, answers)):
                if i > 0:
                    tab.chat_display.insert(tk.END, "\n\n")
                tab.chat_display.insert(tk.END, f"Q: {q}\n\n")
                tab.chat_display.insert(tk.END, "A: ")
                for component in answer.components:
                    if isinstance(component, str):
                        if self.verbose:
                            print(f"handling component: {component[:50]}...")
                        tab.chat_display.insert(tk.END, component)
                    elif isinstance(component, (ToolCall, ToolResult)):
                        if self.verbose:
                            print(
                                f"Warning: Found uncompacted {type(component).__name__} in display update",
                            )
                        tab.chat_display.insert(
                            tk.END,
                            f"\n[{type(component).__name__}]\n",
                        )
            if hasattr(tab.chat_display, "highlight_text"):
                tab.chat_display.highlight_text()
            tab.chat_display.see(tk.END)
        except Exception as e:
            print(f"Error updating display from state: {e}")
            import traceback

            traceback.print_exc()
