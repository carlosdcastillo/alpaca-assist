import json
import platform
import tkinter as tk
from tkinter import filedialog
from tkinter import messagebox
from tkinter import ttk
from typing import Any
from typing import Dict
from typing import Optional

import pyperclip

from chat_app_core import ChatAppCore
from conversation_history import ConversationHistoryWindow
from find_dialog import FindDialog
from mcp_config import MCPConfigWindow
from preferences import PreferencesWindow
from syntax_text import SyntaxHighlightedText
from text_utils import export_and_open
from text_utils import parse_code_blocks
from tooltip import ToolTip
from utils import is_macos


class ChatApp(ChatAppCore):
    """Complete ChatApp class that extends ChatAppCore with UI functionality."""

    def show_conversation_history(self) -> None:
        """Show the conversation history window."""
        ConversationHistoryWindow(self, self.master)

    def _get_export_content_and_title(self):
        """Get content and title for export based on focused widget."""
        if isinstance(self.last_focused_widget, SyntaxHighlightedText):
            content = self.last_focused_widget.get("1.0", tk.END).strip()
            if not content:
                return None, None

            current_tab_index = self.notebook.index(self.notebook.select())
            if 0 <= current_tab_index < len(self.tabs):
                current_tab = self.tabs[current_tab_index]
                if self.last_focused_widget == current_tab.chat_display:
                    tab_name = self.notebook.tab(current_tab_index, "text")
                    title = f"Chat Export - {tab_name}"
                else:
                    title = "Input Field Export"
            else:
                title = "Exported Content"
            return content, title
        else:
            return self._get_default_export_content()

    def _get_default_export_content(self):
        """Get default export content from current tab."""
        current_tab_index = self.notebook.index(self.notebook.select())
        if 0 <= current_tab_index < len(self.tabs):
            current_tab = self.tabs[current_tab_index]
            content = current_tab.chat_display.get("1.0", tk.END).strip()
            if not content:
                return None, None
            tab_name = self.notebook.tab(current_tab_index, "text")
            title = f"Chat Export - {tab_name}"
            return content, title
        return None, None

    def export_to_html(self) -> None:
        """Export the content of the currently focused text widget to HTML."""
        content, title = self._get_export_content_and_title()
        if content is None:
            return

        export_and_open(
            content,
            title,
            theme_name=str(self.preferences["theme"]),
            background_color=str(self.preferences["background_color"]),
            font_family=str(self.preferences["font_family"]),
            font_size=int(str(self.preferences["font_size"])),
        )

    def show_find_dialog(self) -> None:
        """Show find dialog for the currently focused text widget."""
        if isinstance(self.last_focused_widget, SyntaxHighlightedText):
            find_dialog = FindDialog(self.master, self.last_focused_widget)
            find_dialog.show()
        else:
            current_tab_index = self.notebook.index(self.notebook.select())
            if 0 <= current_tab_index < len(self.tabs):
                current_tab = self.tabs[current_tab_index]
                find_dialog = FindDialog(self.master, current_tab.chat_display)
                find_dialog.show()

    def undo_text(self) -> None:
        """Undo text in the currently focused widget."""
        if isinstance(self.last_focused_widget, SyntaxHighlightedText):
            self.last_focused_widget.undo(None)

    def on_app_focus_in(self, event: tk.Event) -> None:
        """Re-enable UI updates when app gains focus"""
        self.update_enabled = True

    def on_app_focus_out(self, event: tk.Event) -> None:
        """Reduce UI updates when app loses focus"""
        self.update_enabled = False

    def show_tool_call_dialog(self, server_name: str, tool_name: str):
        """Show dialog to input arguments and call MCP tool."""
        dialog = tk.Toplevel(self.master)
        dialog.title(f"Call Tool: {tool_name}")
        dialog.geometry("400x300")
        ttk.Label(dialog, text=f"Server: {server_name}").pack(pady=5)
        ttk.Label(dialog, text=f"Tool: {tool_name}").pack(pady=5)
        ttk.Label(dialog, text="Arguments (JSON):").pack(pady=(10, 0))
        args_text = tk.Text(dialog, height=10, width=50)
        args_text.pack(pady=5, padx=10, fill="both", expand=True)
        args_text.insert("1.0", "{}")

        def execute_tool():
            try:
                args_json = args_text.get("1.0", tk.END).strip()
                arguments = json.loads(args_json) if args_json else {}

                def handle_result(result):
                    if result:
                        current_tab_index = self.notebook.index(self.notebook.select())
                        if 0 <= current_tab_index < len(self.tabs):
                            current_tab = self.tabs[current_tab_index]
                            current_tab.input_field.insert(
                                tk.END,
                                f"\n\nMCP Tool Result ({tool_name}):\n{json.dumps(result, indent=2)}",
                            )
                    dialog.destroy()

                self.call_mcp_tool(server_name, tool_name, arguments, handle_result)
            except json.JSONDecodeError:
                messagebox.showerror("Error", "Invalid JSON in arguments")
            except Exception as e:
                messagebox.showerror("Error", f"Failed to call tool: {e}")

        button_frame = ttk.Frame(dialog)
        button_frame.pack(pady=10)
        ttk.Button(button_frame, text="Execute", command=execute_tool).pack(
            side="left",
            padx=5,
        )
        ttk.Button(button_frame, text="Cancel", command=dialog.destroy).pack(
            side="left",
            padx=5,
        )

    def manage_file_completions(self) -> None:
        completions_window = tk.Toplevel(self.master)
        completions_window.title("Manage File Completions")
        completions_window.transient(self.master)
        completions_window.grab_set()
        self.master.update_idletasks()
        x = self.master.winfo_x() + self.master.winfo_width() // 2 - 200
        y = self.master.winfo_y() + self.master.winfo_height() // 2 - 150
        completions_window.geometry(f"400x300+{x}+{y}")
        listbox = tk.Listbox(completions_window, width=50, selectmode=tk.MULTIPLE)
        listbox.pack(padx=10, pady=10, fill=tk.BOTH, expand=True)
        for completion in self.file_completions:
            listbox.insert(tk.END, completion)

        def add_completions() -> None:
            new_completions = filedialog.askopenfilenames(
                title="Select files",
                filetypes=[("All files", "*.*")],
                parent=completions_window,
            )
            if new_completions:
                for completion in new_completions:
                    if completion not in self.file_completions:
                        self.file_completions.append(completion)
                        listbox.insert(tk.END, completion)

        def remove_completions() -> None:
            selected = listbox.curselection()
            if selected:
                for index in reversed(selected):
                    listbox.delete(index)
                    del self.file_completions[index]

        button_frame = ttk.Frame(completions_window)
        button_frame.pack(pady=5)
        add_button = ttk.Button(button_frame, text="Add Files", command=add_completions)
        add_button.pack(side=tk.LEFT, padx=5)
        remove_button = ttk.Button(
            button_frame,
            text="Remove Selected",
            command=remove_completions,
        )
        remove_button.pack(side=tk.LEFT)

        def on_closing() -> None:
            self.update_tabs_file_completions()
            self.save_file_completions()
            completions_window.destroy()

        completions_window.protocol("WM_DELETE_WINDOW", on_closing)

    def submit_current_tab(self) -> str:
        current_tab_index = self.notebook.index(self.notebook.select())
        if 0 <= current_tab_index < len(self.tabs):
            current_tab = self.tabs[current_tab_index]
            current_tab.submit_message()
        return "break"

    def show_about(self) -> None:
        about_text = (
            "Alpaca Assist\n\nVersion 0.10\n\nA chat application using the Ollama API."
        )
        tk.messagebox.showinfo("About", about_text)

    def copy_text(self) -> None:
        if isinstance(self.last_focused_widget, SyntaxHighlightedText):
            has_selection = False
            selected_text = ""
            try:
                if self.last_focused_widget.tag_ranges(tk.SEL):
                    has_selection = True
                    selected_text = self.last_focused_widget.get(
                        tk.SEL_FIRST,
                        tk.SEL_LAST,
                    )
            except tk.TclError:
                has_selection = False
            if has_selection:
                text = selected_text
            else:
                text = self.last_focused_widget.get("1.0", tk.END).strip()
            pyperclip.copy(text)
            self.last_focused_widget.focus_set()

    def _find_containing_code_blocks(self, text, line):
        """Find code blocks containing the current line."""
        code_blocks = parse_code_blocks(text)
        containing_blocks = []
        for indent_level, language, start_line, end_line in code_blocks:
            if start_line <= line <= end_line:
                containing_blocks.append(
                    (indent_level, language, start_line, end_line),
                )
        return containing_blocks

    def _extract_code_content(self, target_widget, start_line, end_line):
        """Extract and clean code content from the text widget."""
        start_index = f"{start_line}.0"
        end_index = f"{end_line}.end"
        code_content = target_widget.get(start_index, end_index)
        lines = code_content.split("\n")

        # Remove markdown code block markers
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]

        return lines, start_index, end_index

    def _clean_code_indentation(self, lines):
        """Remove common indentation from code lines."""
        if not lines:
            return lines

        non_empty_lines = [line for line in lines if line.strip()]
        if not non_empty_lines:
            return lines

        min_indent = min(len(line) - len(line.lstrip()) for line in non_empty_lines)
        return [line[min_indent:] if line.strip() else line for line in lines]

    def _copy_code_and_highlight(
        self,
        cleaned_code,
        language,
        start_index,
        end_index,
        current_cursor_pos,
        target_widget,
    ):
        """Copy code to clipboard and highlight the block."""
        pyperclip.copy(cleaned_code)
        print(f"Code block copied to clipboard! Language: {language}")
        target_widget.highlight_code_block(start_index, end_index)
        target_widget.mark_set(tk.INSERT, current_cursor_pos)
        target_widget.see(current_cursor_pos)
        target_widget.focus_set()

    def go_to_end_of_line(self, event: tk.Event) -> str | None:
        if isinstance(event.widget, SyntaxHighlightedText):
            event.widget.event_generate("<End>")
            return "break"
        return None

    def go_to_start_of_line(self, event: tk.Event) -> str | None:
        if isinstance(event.widget, SyntaxHighlightedText):
            event.widget.event_generate("<Home>")
            return "break"
        return None

    def show_mcp_config(self):
        """Show MCP server configuration window."""
        MCPConfigWindow(self, self.master)

    def get_selected_model(self) -> str:
        """Get the currently selected model from the combo box."""
        return self.selected_model.get()

    def go_to_next_qa(self) -> None:
        """Go to next question/answer (wrapper for menu)."""
        self.navigate_next_qa()

    def go_to_previous_qa(self) -> None:
        """Go to previous question/answer (wrapper for menu)."""
        self.navigate_previous_qa()

    def _navigate_to_line(self, line_num):
        """Navigate to a specific line in the chat display."""
        current_tab_index = self.notebook.index(self.notebook.select())
        if 0 <= current_tab_index < len(self.tabs):
            current_tab = self.tabs[current_tab_index]
            chat_display = current_tab.chat_display
            target_pos = f"{line_num}.0"
            chat_display.mark_set(tk.INSERT, target_pos)
            chat_display.see(target_pos)
            chat_display.focus_set()

    def _find_next_line_after_current(self, lines, current_line_num):
        """Find the next line number after the current position."""
        for line_num in lines:
            if line_num > current_line_num:
                return line_num
        return None

    def _find_previous_line_before_current(self, lines, current_line_num):
        """Find the previous line number before the current position."""
        for line_num in reversed(lines):
            if line_num < current_line_num:
                return line_num
        return None

    def navigate_next_qa(self) -> None:
        """Navigate to next question/answer.
        - If on question: go to immediate next A:
        - If on answer: go to immediate next Q:
        """
        # Prevent navigation during streaming
        if getattr(self, "is_streaming", False):
            print("Cannot navigate during streaming")
            return

        current_tab_index = self.notebook.index(self.notebook.select())
        if 0 <= current_tab_index < len(self.tabs):
            current_tab = self.tabs[current_tab_index]
            chat_display = current_tab.chat_display
            current_pos = chat_display.index(tk.INSERT)
            current_line_num = int(current_pos.split(".")[0])

            if self.is_on_question():
                answer_lines = self.get_answer_lines()
                next_line = self._find_next_line_after_current(
                    answer_lines,
                    current_line_num,
                )
                if next_line:
                    self._navigate_to_line(next_line)
            elif self.is_on_answer():
                question_lines = self.get_question_lines()
                next_line = self._find_next_line_after_current(
                    question_lines,
                    current_line_num,
                )
                if next_line:
                    self._navigate_to_line(next_line)

    def navigate_previous_qa(self) -> None:
        """Navigate to previous question/answer.
        - If on question: go to immediate previous A:
        - If on answer: go to immediate previous Q:
        """
        # Prevent navigation during streaming
        if getattr(self, "is_streaming", False):
            print("Cannot navigate during streaming")
            return

        current_tab_index = self.notebook.index(self.notebook.select())
        if 0 <= current_tab_index < len(self.tabs):
            current_tab = self.tabs[current_tab_index]
            chat_display = current_tab.chat_display
            current_pos = chat_display.index(tk.INSERT)
            current_line_num = int(current_pos.split(".")[0])

            if self.is_on_question():
                answer_lines = self.get_answer_lines()
                prev_line = self._find_previous_line_before_current(
                    answer_lines,
                    current_line_num,
                )
                if prev_line:
                    self._navigate_to_line(prev_line)
            elif self.is_on_answer():
                question_lines = self.get_question_lines()
                prev_line = self._find_previous_line_before_current(
                    question_lines,
                    current_line_num,
                )
                if prev_line:
                    self._navigate_to_line(prev_line)

    def _get_current_line_content(self):
        """Get the content of the current line."""
        current_tab_index = self.notebook.index(self.notebook.select())
        if 0 <= current_tab_index < len(self.tabs):
            current_tab = self.tabs[current_tab_index]
            chat_display = current_tab.chat_display
            current_pos = chat_display.index(tk.INSERT)
            current_line_num = int(current_pos.split(".")[0])
            line_content = chat_display.get(
                f"{current_line_num}.0",
                f"{current_line_num}.end",
            )
            return line_content, current_line_num
        return None, None

    def _get_last_qa_lines_before_current(self, current_line_num):
        """Get the last Q and A line numbers before the current position."""
        question_lines = self.get_question_lines()
        answer_lines = self.get_answer_lines()

        last_q_line = None
        last_a_line = None

        for q_line in question_lines:
            if q_line < current_line_num:
                last_q_line = q_line

        for a_line in answer_lines:
            if a_line < current_line_num:
                last_a_line = a_line

        return last_q_line, last_a_line

    def is_on_answer(self) -> bool:
        """Check if cursor is currently on an answer or any of its subsequent lines.

        Returns:
            True if cursor is on an A: line or any subsequent lines until next Q: line
        """
        line_content, current_line_num = self._get_current_line_content()
        if line_content is None:
            return False

        if line_content.startswith("A: "):
            return True
        if line_content.startswith("Q: "):
            return False

        last_q_line, last_a_line = self._get_last_qa_lines_before_current(
            current_line_num,
        )

        if last_q_line is None and last_a_line is None:
            return False
        if last_a_line is None:
            return False
        elif last_q_line is None:
            return True
        else:
            return last_a_line > last_q_line

    def is_on_question(self) -> bool:
        """Check if cursor is currently on a question or any of its subsequent lines.

        Returns:
            True if cursor is on a Q: line or any subsequent lines until next A: line
        """
        line_content, current_line_num = self._get_current_line_content()
        if line_content is None:
            return False

        if line_content.startswith("Q: "):
            return True
        if line_content.startswith("A: "):
            return False

        last_q_line, last_a_line = self._get_last_qa_lines_before_current(
            current_line_num,
        )

        if last_q_line is None and last_a_line is None:
            return False
        if last_q_line is None:
            return False
        elif last_a_line is None:
            return True
        else:
            return last_q_line > last_a_line

    def set_selected_model(self, model: str) -> None:
        """Set the selected model in the combo box."""
        if model in self.available_models:
            self.selected_model.set(model)
        else:
            self.available_models.append(model)
            self.model_combo["values"] = self.available_models
            self.selected_model.set(model)

    def on_model_selection_changed(self, event: tk.Event) -> None:
        """Handle model selection changes and save to preferences."""
        selected_model = self.selected_model.get()
        self.preferences["selected_model"] = selected_model
        self.save_preferences()
        print(f"Model selection changed to: {selected_model}")

    def create_widgets(self) -> None:
        """Create widgets with improved cross-platform styling."""
        self._configure_cross_platform_styles()
        self.button_frame = ttk.Frame(self.master)
        self.button_frame.pack(fill="x", padx=5, pady=(4, 2))
        self.new_tab_button = ttk.Button(
            self.button_frame,
            text="New Tab",
            command=self.create_tab,
            style="App.TButton",
        )
        self.new_tab_button.pack(side="left", padx=(3, 1))
        modifier_key = "Cmd" if platform.system() == "Darwin" else "Ctrl"
        ToolTip(self.new_tab_button, f"New Tab ({modifier_key}+N)")
        self.delete_tab_button = ttk.Button(
            self.button_frame,
            text="Close Tab",
            command=self.delete_tab,
            style="App.TButton",
        )
        self.delete_tab_button.pack(side="left", padx=(1, 1))
        ToolTip(self.delete_tab_button, f"Close Tab ({modifier_key}+W)")
        history_text = (
            "History" if platform.system() != "Darwin" else "Conversation History"
        )
        self.history_button = ttk.Button(
            self.button_frame,
            text=history_text,
            command=self.show_conversation_history,
            style="App.TButton",
        )
        self.history_button.pack(side="left", padx=(1, 10))
        ToolTip(self.history_button, f"Conversation History ({modifier_key}+Y)")
        self.model_frame = ttk.Frame(self.button_frame)
        self.model_frame.pack(side="left", padx=(0, 3))
        ttk.Label(self.model_frame, text="Model:", style="App.TLabel").pack(
            side="left",
            padx=(0, 5),
        )
        self.available_models = [
            "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
            "us.anthropic.claude-haiku-4-5-20251001-v1:0",
            "us.anthropic.claude-opus-4-1-20250805-v1:0",
            "us.anthropic.claude-sonnet-4-20250514-v1:0",
            "us.anthropic.claude-3-7-sonnet-20250219-v1:0",
            "us.anthropic.claude-3-5-sonnet-20241022-v2:0",
            "us.amazon.nova-premier-v1:0",
            "openai.gpt-oss-120b-1:0",
            "granite-code:8b",
            "codellama:13b",
        ]
        preferred_model = self.preferences.get(
            "selected_model",
            self.preferences.get("default_model", self.available_models[0]),
        )
        if preferred_model not in self.available_models:
            self.available_models.append(preferred_model)
        self.selected_model = tk.StringVar(value=preferred_model)
        self.model_combo = ttk.Combobox(
            self.model_frame,
            textvariable=self.selected_model,
            values=self.available_models,
            state="readonly",
            width=35,
            style="App.TCombobox",
        )
        self.model_combo.pack(side="left")
        self.model_combo.bind("<<ComboboxSelected>>", self.on_model_selection_changed)
        ToolTip(self.model_combo, "Select the AI model to use for chat")
        self.notebook = ttk.Notebook(self.master, style="App.TNotebook")
        self.notebook.pack(expand=True, fill="both", padx=10, pady=(5, 10))
        self.notebook.bind("<<NotebookTabChanged>>", self.on_tab_changed)
        self.master.bind("<FocusIn>", self.on_app_focus_in)
        self.master.bind("<FocusOut>", self.on_app_focus_out)
        self.update_enabled = True

    def on_tab_changed(self, event: tk.Event) -> None:
        """Handle tab selection changes and update window title."""
        try:
            selected_tab_index = self.notebook.index(self.notebook.select())
            if 0 <= selected_tab_index < len(self.tabs):
                tab_name = self.notebook.tab(selected_tab_index, "text")
                self.master.title(f"Alpaca Assist - {tab_name}")
                current_tab = self.tabs[selected_tab_index]
                current_tab.chat_display.focus_set()
        except tk.TclError:
            self.master.title("Alpaca Assist")

    def show_preferences(self) -> None:
        """Show the preferences window."""
        PreferencesWindow(self)

    def show_prompt_manager(self) -> None:
        """Show the prompt manager window."""
        from prompt_manager import PromptManagerWindow

        PromptManagerWindow(
            self.master,
            self.prompt_manager,
            preferences=self.preferences,
        ).show()

    def bind_global_shortcuts(self) -> None:
        """Bind global shortcuts that apply to all tabs."""
        pass

    def paste_text(self, event: tk.Event = None) -> str:
        if isinstance(self.last_focused_widget, SyntaxHighlightedText):
            text = pyperclip.paste()
            try:
                self.last_focused_widget.delete(tk.SEL_FIRST, tk.SEL_LAST)
            except tk.TclError:
                pass
            processed_text = text.replace("\r\n", "\n").replace("\r", "\n")
            current_pos = self.last_focused_widget.index(tk.INSERT)
            self.last_focused_widget.insert(current_pos, processed_text)
            self.last_focused_widget.update_idletasks()
            self.last_focused_widget.highlight_text()
            self.last_focused_widget.focus_set()
            if event:
                return "break"
        return "break"

    def _check_streaming_status(self):
        """Check if any tab is currently streaming."""
        for tab in self.tabs:
            if hasattr(tab, "is_streaming") and tab.is_streaming:
                return True
        return False

    def _show_compaction_result(self, success):
        """Show the result of the compaction operation."""
        if success:
            print("Compaction completed successfully")
            messagebox.showinfo(
                "Compaction Complete",
                "Tool call execution details have been permanently removed from the conversation.",
            )
        else:
            print("No tool calls found to compact")
            messagebox.showinfo(
                "Nothing to Compact",
                "No tool call execution details found in the conversation.",
            )

    def compact_conversation(self) -> None:
        """Compact the current tab's conversation by removing tool call execution details."""
        try:
            if self._check_streaming_status():
                print("Cannot compact while streaming is active")
                messagebox.showwarning(
                    "Cannot Compact",
                    "Cannot compact conversation while streaming is active.",
                )
                return

            current_tab_index = self.notebook.index(self.notebook.select())
            if 0 <= current_tab_index < len(self.tabs):
                current_tab = self.tabs[current_tab_index]
                success = self._perform_compaction(current_tab)
                self._show_compaction_result(success)

        except Exception as e:
            print(f"Error during compaction: {e}")
            messagebox.showerror(
                "Compaction Error",
                f"An error occurred during compaction: {str(e)}",
            )

    def __init__(self, master: tk.Tk) -> None:
        super().__init__(master)
        from prompt_manager import PromptManager
        from compaction import Compactor

        self.prompt_manager = PromptManager()
        self.compactor = Compactor()
        self.create_widgets()
        self.create_menu()
        self.bind_shortcuts()
        self.master.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.load_session()
        self.check_mcp_status()
        self.master.after(5000, self.check_mcp_status)

    def create_menu(self) -> None:
        menubar = tk.Menu(self.master)
        self.master.config(menu=menubar)
        if is_macos():
            modifier = "Command"
        else:
            modifier = "Control"
        file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="File", menu=file_menu)
        file_menu.add_command(
            label="New Tab",
            command=self.create_tab,
            accelerator=f"{modifier}+N",
        )
        file_menu.add_command(
            label="Close Tab",
            command=self.delete_tab,
            accelerator=f"{modifier}+W",
        )
        file_menu.add_separator()
        file_menu.add_command(
            label="Conversation History...",
            command=self.show_conversation_history,
            accelerator=f"{modifier}+Y",
        )
        file_menu.add_separator()
        file_menu.add_command(
            label="Export to HTML",
            command=self.export_to_html,
            accelerator=f"{modifier}+T",
        )
        file_menu.add_separator()
        file_menu.add_command(
            label="Preferences...",
            command=self.show_preferences,
            accelerator=f"{modifier}+,",
        )
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.master.quit)
        edit_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Edit", menu=edit_menu)
        edit_menu.add_command(
            label="Undo",
            command=self.undo_text,
            accelerator=f"{modifier}+Z",
        )
        edit_menu.add_separator()
        edit_menu.add_command(
            label="Copy",
            command=self.copy_text,
            accelerator=f"{modifier}+C",
        )
        edit_menu.add_command(
            label="Paste",
            command=self.paste_text,
            accelerator=f"{modifier}+V",
        )
        edit_menu.add_command(
            label="Copy Code Block",
            command=self.copy_code_block,
            accelerator=f"{modifier}+B",
        )
        edit_menu.add_separator()
        edit_menu.add_command(
            label="Find...",
            command=self.show_find_dialog,
            accelerator=f"{modifier}+F",
        )
        edit_menu.add_separator()
        edit_menu.add_command(
            label="Manage File Completions...",
            command=self.manage_file_completions,
            accelerator=f"{modifier}+M",
        )
        edit_menu.add_command(
            label="Manage Prompts...",
            command=self.show_prompt_manager,
        )
        chat_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Chat", menu=chat_menu)
        chat_menu.add_command(
            label="Submit Message",
            command=self.submit_current_tab,
            accelerator="Ctrl+Enter",
        )
        chat_menu.add_separator()
        chat_menu.add_command(
            label="Next Question/Answer",
            command=self.go_to_next_qa,
            accelerator="Ctrl+Page Down",
        )
        chat_menu.add_command(
            label="Previous Question/Answer",
            command=self.go_to_previous_qa,
            accelerator="Ctrl+Page Up",
        )
        chat_menu.add_separator()
        chat_menu.add_command(
            label="Compact",
            command=self.compact_conversation,
            accelerator=f"{modifier}+P",
        )
        tools_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Tools", menu=tools_menu)
        tools_menu.add_command(
            label="MCP Server Configuration...",
            command=self.show_mcp_config,
        )
        tools_menu.add_command(
            label="Available MCP Tools",
            command=self.show_available_tools,
        )
        help_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Help", menu=help_menu)
        help_menu.add_command(label="About", command=self.show_about)

    def on_window_configure(self, event: tk.Event) -> None:
        """Handle window configuration changes (resize, etc.)."""
        pass

    def _check_tab_content(self, tab_data):
        """Check if tab has content worth saving."""
        has_questions = tab_data.get("chat_state", {}).get("questions") and any(
            q.strip() for q in tab_data["chat_state"]["questions"]
        )
        has_answers = tab_data.get("chat_state", {}).get("answers") and any(
            a.strip() if isinstance(a, str) else a
            for a in tab_data.get("chat_history_answers", [])
        )
        return has_questions, has_answers

    def _check_unsaved_input(self, current_tab):
        """Check if tab has unsaved input."""
        if hasattr(current_tab, "input_field"):
            input_text = current_tab.input_field.get("1.0", tk.END).strip()
            return bool(input_text)
        return False

    def _handle_tab_cleanup(self, current_tab_index, current_tab):
        """Handle cleanup and removal of tab."""
        current_tab.cleanup_resources()
        self.notebook.forget(current_tab_index)
        self.tabs.pop(current_tab_index)

    def _handle_remaining_tabs(self, current_tab_index):
        """Handle focus and title updates for remaining tabs."""
        if len(self.tabs) > 0:
            new_index = min(current_tab_index, len(self.tabs) - 1)
            if new_index >= 0:
                self.notebook.select(new_index)
                tab_name = self.notebook.tab(new_index, "text")
                self.master.title(f"Alpaca Assist - {tab_name}")
        else:
            self.master.title("Alpaca Assist")
            self.create_tab()

    def delete_tab(self) -> None:
        """Delete the currently selected tab."""
        current_tab_index = self.notebook.index(self.notebook.select())
        if 0 <= current_tab_index < len(self.tabs):
            current_tab = self.tabs[current_tab_index]
            tab_data = current_tab.get_serializable_data()

            has_questions, has_answers = self._check_tab_content(tab_data)
            has_unsaved_input = self._check_unsaved_input(current_tab)
            has_content = has_questions or has_answers or has_unsaved_input

            if has_content:
                print(
                    f"DEBUG: Saving tab to history before closing (Q:{len(tab_data.get('chat_state', {}).get('questions', []))}, A:{len(tab_data.get('chat_history_answers', []))})",
                )
                self.store_tab_in_database(current_tab)
            else:
                print("DEBUG: Tab has no content, not saving to history")

            self._handle_tab_cleanup(current_tab_index, current_tab)
            self._handle_remaining_tabs(current_tab_index)

    def _configure_button_styles(self):
        """Configure button styles for different platforms."""
        bg_color = self.master.cget("bg")
        if platform.system() == "Darwin":
            sz = 12
        else:
            sz = 9
        self.style.configure(
            "App.TButton",
            padding=(12, 8),
            font=("TkDefaultFont", sz),
            focuscolor="none",
        )
        if platform.system() == "Darwin":
            self.style.configure("App.TButton", padding=(10, 6), borderwidth=1)
        elif platform.system() == "Linux":
            self.style.configure("App.TButton", padding=(8, 6), relief="raised")

    def _configure_label_styles(self):
        """Configure label styles for different platforms."""
        if platform.system() == "Darwin":
            label_font_size = 12
        else:
            label_font_size = 9
        self.style.configure("App.TLabel", font=("TkDefaultFont", label_font_size))

    def _configure_combobox_styles(self):
        """Configure combobox styles."""
        self.style.configure(
            "App.TCombobox",
            fieldbackground="white",
            selectbackground="#0078d4",
        )

    def _configure_notebook_styles(self):
        """Configure notebook styles for different platforms."""
        self.style.configure("App.TNotebook", tabposition="n")
        if platform.system() == "Darwin":
            tab_font_size = 12
        else:
            tab_font_size = 9
        self.style.configure(
            "App.TNotebook.Tab",
            padding=(12, 8),
            font=("TkDefaultFont", tab_font_size),
        )

    def _configure_cross_platform_styles(self) -> None:
        """Configure consistent TTK styles across all platforms."""
        self._configure_button_styles()
        self._configure_label_styles()
        self._configure_combobox_styles()
        self._configure_notebook_styles()

    def _perform_compaction(self, current_tab):
        """Perform the actual compaction operation."""
        if not hasattr(self, "compactor"):
            print("Compactor not initialized")
            return False

        success = self.compactor.compact_tab(current_tab)

        # Finalize rendering if the chat display is a MarkdownViewerAdapter
        if success and hasattr(current_tab.chat_display, "finalize_rendering"):
            current_tab.chat_display.finalize_rendering()

        return success

    def bind_tab_shortcuts(self, tab, modifier: str) -> None:
        """Bind shortcuts to a specific tab's widgets."""

        def navigate_next_handler(e):
            self.navigate_next_qa()
            return "break"

        def navigate_previous_handler(e):
            self.navigate_previous_qa()
            return "break"

        tab.chat_display.bind(f"<{modifier}-v>", self.paste_text)
        tab.input_field.bind(f"<{modifier}-v>", self.paste_text)

    def bind_shortcuts(self) -> None:
        if is_macos():
            modifier = "Command"
        else:
            modifier = "Control"
        self.master.bind(f"<{modifier}-n>", lambda e: self.create_tab())
        self.master.bind(f"<{modifier}-w>", lambda e: self.delete_tab())
        self.master.bind(f"<{modifier}-c>", lambda e: self.copy_text())
        self.master.bind(f"<{modifier}-b>", lambda e: self.copy_code_block())
        self.master.bind(f"<{modifier}-m>", lambda e: self.manage_file_completions())
        self.master.bind(f"<{modifier}-e>", self.go_to_end_of_line)
        self.master.bind(f"<{modifier}-a>", self.go_to_start_of_line)
        self.master.bind(f"<{modifier}-z>", lambda e: self.undo_text())
        self.master.bind(f"<{modifier}-comma>", lambda e: self.show_preferences())
        self.master.bind(f"<{modifier}-f>", lambda e: self.show_find_dialog())
        self.master.bind(f"<{modifier}-y>", lambda e: self.show_conversation_history())
        self.master.bind(f"<{modifier}-t>", lambda e: self.export_to_html())
        self.master.bind(f"<{modifier}-p>", lambda e: self.compact_conversation())

        def navigate_next_handler(e):
            self.navigate_next_qa()
            return "break"

        def navigate_previous_handler(e):
            self.navigate_previous_qa()
            return "break"

        # Use Ctrl+Page_Down and Ctrl+Page_Up for navigation
        self.master.bind("<Control-Page_Down>", navigate_next_handler)
        self.master.bind("<Control-Page_Up>", navigate_previous_handler)

        for tab in self.tabs:
            self.bind_tab_shortcuts(tab, modifier)

    def copy_code_block(self):
        """Copy the code block at the cursor position to clipboard."""
        from text_utils import copy_code_block_from_widget

        try:
            # Determine which widget to use
            target_widget = None

            if isinstance(self.last_focused_widget, SyntaxHighlightedText):
                target_widget = self.last_focused_widget
            else:
                # Fall back to current tab's chat display
                current_tab = self.notebook.index(self.notebook.select())
                if current_tab < len(self.tabs):
                    target_widget = self.tabs[current_tab].chat_display

            if not target_widget:
                print("Error: No valid text widget found for copying code")
                return

            # Use the centralized function from text_utils
            success = copy_code_block_from_widget(target_widget, self.preferences)

            if success:
                print("Code block copied successfully!")

        except Exception as e:
            print(f"Error in copy_code_block: {type(e).__name__}: {e}")
            import traceback

            traceback.print_exc()

    def get_answer_lines(self) -> list[int]:
        """Get all line numbers that start with 'A:'.

        Returns:
            List of line numbers (1-indexed) that start with 'A:'
        """
        current_tab_index = self.notebook.index(self.notebook.select())
        if 0 <= current_tab_index < len(self.tabs):
            current_tab = self.tabs[current_tab_index]
            chat_display = current_tab.chat_display

            # Get the raw markdown content buffer if available (for MarkdownViewerAdapter)
            if hasattr(chat_display, "_content_buffer"):
                content = chat_display._content_buffer
            else:
                content = chat_display.get("1.0", tk.END)

            lines = content.split("\n")
            answer_lines = []
            for i, line in enumerate(lines, 1):
                if line.startswith("A: "):
                    # Convert content line to rendered line if we have the mapping
                    if hasattr(chat_display, "_char_to_content_line_map"):
                        rendered_line = self._convert_content_line_to_rendered(
                            chat_display,
                            i - 1,  # Convert to 0-indexed
                        )
                        if rendered_line is not None:
                            answer_lines.append(rendered_line)
                    else:
                        answer_lines.append(i)
            return answer_lines
        return []

    def get_question_lines(self) -> list[int]:
        """Get all line numbers that start with 'Q:'.

        Returns:
            List of line numbers (1-indexed) that start with 'Q:'
        """
        current_tab_index = self.notebook.index(self.notebook.select())
        if 0 <= current_tab_index < len(self.tabs):
            current_tab = self.tabs[current_tab_index]
            chat_display = current_tab.chat_display

            # Get the raw markdown content buffer if available (for MarkdownViewerAdapter)
            if hasattr(chat_display, "_content_buffer"):
                content = chat_display._content_buffer
            else:
                content = chat_display.get("1.0", tk.END)

            lines = content.split("\n")
            question_lines = []
            for i, line in enumerate(lines, 1):
                if line.startswith("Q: "):
                    # Convert content line to rendered line if we have the mapping
                    if hasattr(chat_display, "_char_to_content_line_map"):
                        rendered_line = self._convert_content_line_to_rendered(
                            chat_display,
                            i - 1,  # Convert to 0-indexed
                        )
                        if rendered_line is not None:
                            question_lines.append(rendered_line)
                    else:
                        question_lines.append(i)
            return question_lines
        return []

    def _convert_content_line_to_rendered(
        self,
        chat_display,
        content_line_0indexed: int,
    ) -> int | None:
        """Convert a content line number (0-indexed) to rendered line number (1-indexed).

        Uses the _char_to_content_line_map to find where this content line appears
        in the rendered display.
        """
        # Find the first character that maps to this content line
        for rendered_idx, content_idx in enumerate(
            chat_display._char_to_content_line_map,
        ):
            if content_idx == content_line_0indexed:
                # Count newlines up to this position to get rendered line number (1-indexed)
                rendered_line = (
                    chat_display._rendered_display_buffer[:rendered_idx].count("\n") + 1
                )
                return rendered_line
        return None

    def show_available_tools(self):
        """Show a window with all available MCP tools."""
        tools_window = tk.Toplevel(self.master)
        tools_window.title("Available MCP Tools")
        tools_window.geometry("700x500")
        tools_window.transient(self.master)
        tools_window.grab_set()
        self.master.update_idletasks()
        x = self.master.winfo_x() + self.master.winfo_width() // 2 - 350
        y = self.master.winfo_y() + self.master.winfo_height() // 2 - 250
        tools_window.geometry(f"700x500+{x}+{y}")
        tree = ttk.Treeview(tools_window, columns=("Server", "Tool", "Description"))
        tree.heading("#0", text="Name")
        tree.heading("Server", text="Server")
        tree.heading("Tool", text="Tool")
        tree.heading("Description", text="Description")
        tools = self.get_available_mcp_tools()
        for tool in tools:
            function_name = tool["function"]["name"]
            if "_" in function_name:
                server_name = function_name.split("_")[0]
                tool_name = "_".join(function_name.split("_")[1:])
            else:
                server_name = "Unknown"
                tool_name = function_name
            tree.insert(
                "",
                "end",
                text=tool_name,
                values=(
                    server_name,
                    tool_name,
                    tool["function"].get("description", ""),
                ),
            )
        tree.pack(fill="both", expand=True, padx=10, pady=10)

        def call_selected_tool():
            selection = tree.selection()
            if selection:
                item = tree.item(selection[0])
                server_name = item["values"][0]
                tool_name = item["values"][1]
                self.show_tool_call_dialog(server_name, tool_name)

        ttk.Button(tools_window, text="Call Tool", command=call_selected_tool).pack(
            pady=5,
        )
