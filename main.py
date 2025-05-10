import json
import os
import queue
import re
import threading
import tkinter as tk
from tkinter import filedialog
from tkinter import messagebox
from tkinter import scrolledtext
from tkinter import ttk
from typing import Any
from typing import Callable
from typing import cast
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple
from typing import Union

import pyperclip  # type: ignore
import requests  # type: ignore
from pygments import lex  # type: ignore
from pygments.lexers import MarkdownLexer  # type: ignore
from pygments.styles import get_style_by_name  # type: ignore

from expansion_language import expand

BASE_URL: str = "http://localhost:11434/api/chat"


def parse_code_blocks(text: str) -> List[Tuple[int, str, int, int]]:
    """
    Parse code blocks from text, handling nested blocks correctly.

    Args:
        text (str): Input text containing code blocks

    Returns:
        List[Tuple[int, str, int, int]]: List of tuples (indentation_level, language, start_line, end_line)
    """
    lines: List[str] = text.split("\n")
    blocks: List[Tuple[int, str, int, int]] = []
    open_blocks: List[Dict[str, Union[int, str]]] = []  # Stack of open blocks

    for line_idx, line in enumerate(lines):
        line_num: int = line_idx + 1
        leading_spaces: int = len(line) - len(line.lstrip())
        stripped: str = line.strip()

        if stripped.startswith("```"):
            if stripped == "```":
                # This is a plain ``` which could be either opening or closing

                # Check if it's closing an existing block with the same indentation
                # Search from most recent to earliest (reverse order)
                matching_block_idx: Optional[int] = None
                for idx in range(len(open_blocks) - 1, -1, -1):
                    if open_blocks[idx]["indent"] == leading_spaces:
                        matching_block_idx = idx
                        break

                if matching_block_idx is not None:
                    block = open_blocks[matching_block_idx]
                    blocks.append(
                        (
                            cast(int, block["indent"]),
                            cast(str, block["language"]),
                            cast(int, block["start_line"]),
                            line_num,
                        ),
                    )

                    # Remove all these blocks from open_blocks
                    open_blocks = open_blocks[:matching_block_idx]
                else:
                    # It's opening a new block
                    open_blocks.append(
                        {
                            "indent": leading_spaces,
                            "language": "",
                            "start_line": line_num,
                        },
                    )
            else:
                # It's opening a new block with a language (```python, etc.)
                language: str = stripped[3:].strip()

                open_blocks.append(
                    {
                        "indent": leading_spaces,
                        "language": language,
                        "start_line": line_num,
                    },
                )

    return blocks


def backoff(x: str) -> str:
    parts = x.split(".")
    if len(parts) > 1:
        return ".".join(parts[:-1])
    else:
        return x


class SyntaxHighlightedText(scrolledtext.ScrolledText):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.lexer = MarkdownLexer()
        self.style = get_style_by_name("lightbulb")
        self.configure(
            font=("Cascadia Mono", 12),
            bg="#000000",
            fg="#f8f8f2",
            insertbackground="white",
        )
        self.tag_configure("default", foreground="#ffffff")
        self.bind("<KeyRelease>", self.on_key_release)
        self.after_id: Optional[str] = None

        self.tag_configure("separator", foreground="#888888")

        cache: Dict[str, str] = {}
        # Configure tags for syntax highlighting
        for token, style in self.style.styles.items():
            if style != "":
                cache[str(token)] = style

            if style == "":
                b = backoff(str(token))
                if b in cache:
                    style = cache[b]

            fg, bg = self.parse_style(style)
            tag_config: Dict[str, str] = {"foreground": fg}
            if bg:
                tag_config["background"] = bg
                if "#" in fg and "#" in bg:
                    self.tag_configure(str(token), foreground=fg, background=bg)
                elif "#" in fg:
                    self.tag_configure(str(token), foreground=fg)
            else:
                if "#" in fg:
                    self.tag_configure(str(token), foreground=fg)

    def on_key_release(self, event: tk.Event) -> None:
        if self.after_id:
            self.after_cancel(self.after_id)
        self.after_id = self.after(10, self.highlight_text)

    def parse_style(
        self,
        style: Union[str, Dict[str, str]],
    ) -> Tuple[str, Optional[str]]:
        fg: Optional[str] = None
        bg: Optional[str] = None

        if isinstance(style, str):
            parts = style.split()
            for part in parts:
                if part.startswith("bg:"):
                    bg = part.split("bg:")[1]
                elif "#" in part:
                    fg = part
                else:
                    # Handle bold, italic, etc separately if needed
                    pass

        elif isinstance(style, dict):
            fg = style.get("color")
            bg = style.get("bgcolor")

        # Set default foreground color if not found
        if not fg:
            fg = "#ffffff"

        return fg, bg

    def set_text(self, text: str) -> None:
        self.delete("1.0", tk.END)
        self.insert("1.0", text)
        self.highlight_text()

    def highlight_text(self) -> None:
        # Save the current selection if any
        try:
            sel_start = self.index(tk.SEL_FIRST)
            sel_end = self.index(tk.SEL_LAST)
            has_selection = True
        except tk.TclError:
            has_selection = False

        # Remove syntax highlighting tags but not selection
        for tag in self.tag_names():
            if tag != "sel":  # Don't remove the selection tag
                self.tag_remove(tag, "1.0", "end")

        self.lexer = MarkdownLexer()
        text = self.get("1.0", tk.END)
        self.tag_remove("default", "1.0", tk.END)
        for start, end in self.get_ranges("1.0", tk.END):
            self.tag_add("default", start, end)

        self.mark_set("highlight_start", "1.0")
        for token, content in self.lexer.get_tokens(text):
            content_length = len(content)
            end_index = self.index(f"highlight_start + {content_length}c")
            self.tag_add(str(token), "highlight_start", end_index)
            self.mark_set("highlight_start", end_index)

        self.mark_unset("highlight_start")

        # Restore the selection if it existed
        if has_selection:
            self.tag_add(tk.SEL, sel_start, sel_end)

    def get_ranges(self, start: str, end: str) -> List[Tuple[str, str]]:
        ranges: List[Tuple[str, str]] = []
        current = start
        while self.compare(current, "<", end):
            ranges.append((current, f"{current} lineend"))
            current = self.index(f"{current} +1 lines")
        return ranges

    def highlight_code_block(self, start: str, end: str) -> None:
        # Remove any existing highlight
        self.tag_remove("highlight", "1.0", tk.END)

        # Get the text of the code block
        code_block = self.get(start, end)
        lines = code_block.split("\n")

        # Find the first and last non-backtick lines
        first_line = next(
            (i for i, line in enumerate(lines) if not line.strip().startswith("```")),
            0,
        )
        last_line = next(
            (
                len(lines) - 1 - i
                for i, line in enumerate(reversed(lines))
                if not line.strip().startswith("```")
            ),
            len(lines) - 1,
        )

        # Calculate the new start and end positions
        new_start = f"{int(start.split('.')[0]) + first_line}.0"
        new_end = f"{int(start.split('.')[0]) + last_line}.end"

        # Apply the highlight
        self.tag_add("highlight", new_start, new_end)
        self.tag_configure("highlight", background="yellow", foreground="black")

        # Schedule the removal of the highlight
        self.after(500, lambda: self.tag_remove("highlight", new_start, new_end))


class ChatTab:
    def __init__(
        self,
        parent: "ChatApp",
        notebook: ttk.Notebook,
        file_completions: List[str],
    ) -> None:
        self.parent = parent
        self.notebook = notebook
        self.chat_history_questions: List[str] = []
        self.chat_history_answers: List[str] = []
        self.output_queue: queue.Queue = queue.Queue()
        self.input_queue: queue.Queue = queue.Queue()

        self.frame: ttk.Frame = ttk.Frame(notebook)
        notebook.add(self.frame, text=f"Tab {len(parent.tabs) + 1}")

        self.create_widgets()
        self.summary_generated: bool = False
        self.file_completions: List[str] = file_completions
        self.chat_display: SyntaxHighlightedText
        self.input_field: SyntaxHighlightedText

    def create_widgets(self) -> None:
        chat_frame = ttk.Frame(self.frame)
        chat_frame.pack(expand=True, fill="both", padx=10, pady=10)

        # Create a PanedWindow for the splitter functionality
        self.paned_window = ttk.PanedWindow(chat_frame, orient=tk.VERTICAL)
        self.paned_window.pack(expand=True, fill="both")

        # Create frame for chat display
        chat_display_frame = ttk.Frame(self.paned_window)
        self.paned_window.add(
            chat_display_frame,
            weight=3,
        )  # Give more weight to chat display

        self.chat_display = SyntaxHighlightedText(
            chat_display_frame,
            wrap=tk.WORD,
            height=20,
        )
        self.chat_display.pack(expand=True, fill="both")
        self.chat_display.bind("<Control-e>", self.go_to_end_of_line)
        self.chat_display.bind("<Control-a>", self.go_to_start_of_line)
        self.chat_display.bind("<FocusIn>", self.parent.update_last_focused)
        self.chat_display.bind(
            "<KeyRelease>",
            lambda e: self.chat_display.highlight_text(),
        )

        # Create frame for input field
        input_frame = ttk.Frame(self.paned_window)
        self.paned_window.add(input_frame, weight=1)  # Give less weight to input field

        input_field_frame = ttk.Frame(input_frame)
        input_field_frame.pack(fill="both", expand=True, side="left")

        self.input_field = SyntaxHighlightedText(
            input_field_frame,
            height=7,
            wrap=tk.WORD,
        )
        self.input_field.pack(side="left", expand=True, fill="both")
        self.input_field.bind("<Control-e>", self.go_to_end_of_line)
        self.input_field.bind("<Control-a>", self.go_to_start_of_line)
        self.input_field.bind("<FocusIn>", self.parent.update_last_focused)
        self.input_field.bind("<KeyRelease>", self.check_for_autocomplete)
        self.input_field.bind(
            "<KeyRelease>",
            lambda e: self.input_field.highlight_text(),
            add=True,
        )
        self.input_field.bind("<KeyPress>", self.check_for_autocomplete)
        self.input_field.bind(
            "<KeyPress>",
            lambda e: self.input_field.highlight_text(),
            add=True,
        )

        button_frame = ttk.Frame(input_frame)
        button_frame.pack(side="left", padx=5, fill="y")

        submit_button = ttk.Button(
            button_frame,
            text="Submit",
            command=self.submit_message,
            style="Custom.TButton",
        )
        submit_button.pack(pady=2, fill="x")
        ToolTip(submit_button, "Submit (Ctrl+Enter)")

        paste_button = ttk.Button(
            button_frame,
            text="Paste",
            command=self.parent.paste_text,
            style="Custom.TButton",
        )
        paste_button.pack(pady=2, fill="x")
        ToolTip(paste_button, "Paste (Ctrl+V)")

        copy_button = ttk.Button(
            button_frame,
            text="Copy",
            command=self.parent.copy_text,
            style="Custom.TButton",
        )
        copy_button.pack(pady=2, fill="x")
        ToolTip(copy_button, "Copy (Ctrl+C)")

        copy_code_button = ttk.Button(
            button_frame,
            text="Copy Code",
            command=self.parent.copy_code_block,
            style="Custom.TButton",
        )
        copy_code_button.pack(pady=2, fill="x")
        ToolTip(copy_code_button, "Copy Code (Ctrl+B)")

    def submit_message(self) -> None:
        message = self.input_field.get("1.0", tk.END)
        if message:
            self.chat_history_questions.append(message)
            self.chat_history_answers.append("")
            self.update_chat_display()
            self.input_queue.put(
                {
                    "model": "granite-code:8b",
                    "prompt": expand(message),
                    "chat_history_questions": self.chat_history_questions[0:-1],
                    "chat_history_answers": self.chat_history_answers[0:-1],
                    "is_first_submission": len(self.chat_history_questions) == 1,
                },
            )
            threading.Thread(
                target=self.parent.fetch_api_response,
                args=(self,),
                daemon=True,
            ).start()
            self.input_field.delete("1.0", tk.END)

    def update_chat_display(self) -> None:
        self.chat_display.delete("1.0", tk.END)
        for question, answer in zip(
            self.chat_history_questions,
            self.chat_history_answers,
        ):
            self.chat_display.insert(tk.END, f"Q: {question}\n")
            self.chat_display.insert(tk.END, f"A: {answer}\n")

            sep = "-" * 80
            self.chat_display.insert(tk.END, f"\n{sep}\n")
            self.chat_display.highlight_text()

        self.chat_display.see(tk.END)

    def get_summary(self) -> None:
        threading.Thread(
            target=self._fetch_summary,
            daemon=True,
        ).start()

    def _fetch_summary(self) -> None:
        summary_queue: queue.Queue = queue.Queue()
        threading.Thread(
            target=self.parent.fetch_api_response_summary,
            args=(self, summary_queue),
            daemon=True,
        ).start()

        # Wait for the response
        response: str = summary_queue.get()

        # Update the tab name on the main thread
        self.parent.master.after(
            0,
            lambda: self.parent.update_tab_name(self, response.strip()),
        )

    def update_file_completions(
        self,
        new_completions: List[str],
    ) -> None:  # Renamed from update_file_options
        self.file_completions = new_completions

    def show_autocomplete_menu(self) -> None:
        """
        Display the autocomplete menu with file path completions.
        """
        completions = self.file_completions  # Use the instance variable
        menu = tk.Menu(self.input_field, tearoff=0)

        def create_completion_handler(c: str) -> Callable[[], None]:
            """Create a properly typed callback function for menu commands."""
            return lambda: self.insert_completion(c)

        for completion in completions:
            menu.add_command(
                label=completion,
                command=create_completion_handler(completion),
            )

        # Get the current cursor position
        bbox_result = self.input_field.bbox("insert")
        if bbox_result is not None:
            x, y, _, h = bbox_result
            # Display the menu below the cursor
            menu.post(
                self.input_field.winfo_rootx() + x,
                self.input_field.winfo_rooty() + y + h,
            )

    def insert_completion(self, option: str) -> None:
        cursor_position = self.input_field.index(tk.INSERT)
        self.input_field.insert(cursor_position, option + " ")
        self.input_field.focus_set()

    def check_for_autocomplete(self, event: tk.Event) -> None:
        if event.char in [":", "/"]:  # Trigger on both ':' and '/'
            current_line = self.input_field.get("insert linestart", "insert")
            if current_line.endswith("/file:") or current_line.endswith("/file"):
                self.show_autocomplete_menu()

    def go_to_end_of_line(self, event: tk.Event) -> str:
        widget = event.widget
        widget.mark_set(tk.INSERT, "insert lineend")
        return "break"  # This prevents the default behavior

    def go_to_start_of_line(self, event: tk.Event) -> str:
        widget = event.widget
        widget.mark_set(tk.INSERT, "insert linestart")
        return "break"  # This prevents the default behavior


class ChatApp:
    def __init__(self, master: tk.Tk) -> None:
        self.master = master
        master.title("Alpaca Assist")

        self.style = ttk.Style()
        self.style.configure("Custom.TButton", padding=(10, 10), width=15)

        self.file_completions: List[str] = []
        self.last_focused_widget: Optional[SyntaxHighlightedText] = None
        self.tabs: List[ChatTab] = []
        self.load_file_completions()

        self.create_widgets()
        self.create_menu()
        self.bind_shortcuts()

        self.start_ui_update()  # Start periodic UI updates
        self.notebook: ttk.Notebook
        self.new_tab_button: ttk.Button
        self.delete_tab_button: ttk.Button
        self.button_frame: ttk.Frame

    def bind_shortcuts(self) -> None:
        self.master.bind("<Control-n>", lambda e: self.create_tab())
        self.master.bind("<Control-w>", lambda e: self.delete_tab())
        self.master.bind("<Control-Return>", lambda e: self.submit_current_tab())
        self.master.bind("<Control-c>", lambda e: self.copy_text())
        self.master.bind("<Control-v>", lambda e: self.paste_text())
        self.master.bind("<Control-b>", lambda e: self.copy_code_block())
        self.master.bind("<Control-m>", lambda e: self.manage_file_completions())
        self.master.bind("<Control-e>", self.go_to_end_of_line)
        self.master.bind("<Control-a>", self.go_to_start_of_line)

    def start_ui_update(self) -> None:
        def update_ui() -> None:
            self.master.update_idletasks()
            self.master.after(100, update_ui)  # Schedule next update in 100ms

        update_ui()

    def create_menu(self) -> None:
        menubar = tk.Menu(self.master)
        self.master.config(menu=menubar)

        # File menu
        file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="File", menu=file_menu)
        file_menu.add_command(
            label="New Tab",
            command=self.create_tab,
            accelerator="Ctrl+N",
        )
        file_menu.add_command(
            label="Close Tab",
            command=self.delete_tab,
            accelerator="Ctrl+W",
        )
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.master.quit)

        # Edit menu
        edit_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Edit", menu=edit_menu)
        edit_menu.add_command(
            label="Copy",
            command=self.copy_text,
            accelerator="Ctrl+C",
        )
        edit_menu.add_command(
            label="Paste",
            command=self.paste_text,
            accelerator="Ctrl+V",
        )
        edit_menu.add_command(
            label="Copy Code Block",
            command=self.copy_code_block,
            accelerator="Ctrl+B",
        )
        edit_menu.add_separator()
        edit_menu.add_command(
            label="Manage File Completions",
            command=self.manage_file_completions,
            accelerator="Ctrl+M",
        )
        # Chat menu
        chat_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Chat", menu=chat_menu)
        chat_menu.add_command(
            label="Submit Message",
            command=self.submit_current_tab,
            accelerator="Ctrl+Enter",
        )

        # Help menu
        help_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Help", menu=help_menu)
        help_menu.add_command(label="About", command=self.show_about)

    def manage_file_completions(self) -> None:  # Renamed from manage_file_options
        completions_window = tk.Toplevel(self.master)
        completions_window.title("Manage File Completions")
        completions_window.geometry("400x300")

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
            self.save_file_completions()  # Save file completions when window closes
            completions_window.destroy()

        completions_window.protocol("WM_DELETE_WINDOW", on_closing)

    def create_widgets(self) -> None:
        # Create button frame
        self.button_frame = ttk.Frame(self.master)
        self.button_frame.pack(fill="x", padx=5, pady=(5, 5))  # Add top padding

        # Create New Tab button
        self.new_tab_button = ttk.Button(
            self.button_frame,
            text="New Tab",
            command=self.create_tab,
        )
        self.new_tab_button.pack(side="left", padx=5)
        ToolTip(self.new_tab_button, "New Tab (Ctrl+N)")

        # Create Delete Tab button
        self.delete_tab_button = ttk.Button(
            self.button_frame,
            text="Delete Tab",
            command=self.delete_tab,
        )
        self.delete_tab_button.pack(side="left")
        ToolTip(self.delete_tab_button, "Delete Tab (Ctrl+W)")

        # Create notebook
        self.notebook = ttk.Notebook(self.master)
        self.notebook.pack(
            expand=True,
            fill="both",
            padx=10,
            pady=(5, 10),
        )  # Adjust top padding

        # Create initial tab
        self.create_tab()

    def submit_current_tab(self) -> None:
        current_tab_index = self.notebook.index(self.notebook.select())
        if 0 <= current_tab_index < len(self.tabs):
            current_tab = self.tabs[current_tab_index]
            current_tab.submit_message()

    def show_about(self) -> None:
        about_text = (
            "Alpaca Assist\n\nVersion 0.02\n\nA chat application using the Ollama API."
        )
        tk.messagebox.showinfo("About", about_text)

    def save_file_completions(self) -> None:
        with open("file_completions.json", "w") as f:
            json.dump(self.file_completions, f, indent=2)

    def load_file_completions(self) -> None:
        if os.path.exists("file_completions.json"):
            with open("file_completions.json", "r") as f:
                self.file_completions = json.load(f)

    def update_tabs_file_completions(self) -> None:
        for tab in self.tabs:
            tab.update_file_completions(self.file_completions)
        self.save_file_completions()  # Save file completions after updating

    def create_tab(self) -> None:
        new_tab = ChatTab(self, self.notebook, self.file_completions)
        self.tabs.append(new_tab)
        self.notebook.select(new_tab.frame)

    def delete_tab(self) -> None:
        if len(self.tabs) > 1:
            current_tab = self.notebook.select()
            tab_index = self.notebook.index(current_tab)
            self.notebook.forget(current_tab)
            del self.tabs[tab_index]

    def update_last_focused(self, event: tk.Event) -> None:
        self.last_focused_widget = cast(SyntaxHighlightedText, event.widget)

    def paste_text(self) -> None:
        if isinstance(self.last_focused_widget, SyntaxHighlightedText):
            text = pyperclip.paste()
            self.last_focused_widget.insert(tk.INSERT, text)
            self.last_focused_widget.highlight_text()

    def copy_text(self) -> None:
        if isinstance(self.last_focused_widget, SyntaxHighlightedText):
            if self.last_focused_widget.tag_ranges(tk.SEL):
                text = self.last_focused_widget.get(tk.SEL_FIRST, tk.SEL_LAST)
            else:
                text = self.last_focused_widget.get("1.0", tk.END).strip()
            pyperclip.copy(text)

    def copy_code_block(self) -> None:
        if isinstance(self.last_focused_widget, SyntaxHighlightedText):
            text = self.last_focused_widget.get("1.0", tk.END)
            cursor_pos = self.last_focused_widget.index(tk.INSERT)
            line, col = map(int, cursor_pos.split("."))

            # Parse all code blocks in the text
            code_blocks = parse_code_blocks(text)

            # Find all code blocks that contain the current cursor line
            containing_blocks = []
            for indent_level, language, start_line, end_line in code_blocks:
                if start_line <= line <= end_line:
                    containing_blocks.append(
                        (indent_level, language, start_line, end_line),
                    )

            if containing_blocks:
                # Sort blocks by size (smallest first) to find the most specific block
                containing_blocks.sort(key=lambda block: block[3] - block[2])

                # Get the smallest block that contains the cursor
                indent_level, language, start_line, end_line = containing_blocks[0]

                # Extract the code content
                start_index = f"{start_line}.0"
                end_index = f"{end_line}.end"
                code_content = self.last_focused_widget.get(start_index, end_index)

                # Clean the code content (remove backticks and language specifier)
                lines = code_content.split("\n")

                # Remove the opening and closing backtick lines
                if lines and lines[0].strip().startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]

                # Remove any common leading whitespace
                if lines:
                    non_empty_lines = [line for line in lines if line.strip()]
                    if non_empty_lines:
                        min_indent = min(
                            len(line) - len(line.lstrip()) for line in non_empty_lines
                        )
                        lines = [
                            line[min_indent:] if line.strip() else line
                            for line in lines
                        ]

                cleaned_code = "\n".join(lines)

                pyperclip.copy(cleaned_code)
                print(f"Code block copied to clipboard! Language: {language}")

                # Highlight the code block
                self.last_focused_widget.highlight_code_block(start_index, end_index)
                return

            print("No code block found at the current cursor position.")

    def update_tab_name(self, tab: ChatTab, summary: str) -> None:
        tab_index = self.tabs.index(tab)
        self.notebook.tab(tab_index, text=summary)

    def fetch_api_response_summary(
        self,
        tab: ChatTab,
        output_queue: queue.Queue,
    ) -> None:
        messages: List[Dict[str, str]] = [
            {
                "role": "user",
                "content": "Generate a summary of the history of this chat in 1-5 words. Do not reply with anything other than the summary, and do not end the summary with a period (dot).",
            },
        ]
        if tab.chat_history_questions:
            messages.insert(
                0,
                {"role": "user", "content": tab.chat_history_questions[0]},
            )
        if tab.chat_history_answers:
            messages.insert(
                1,
                {"role": "assistant", "content": tab.chat_history_answers[0]},
            )

        data_payload: Dict[str, Any] = {
            "model": "codellama:13b",
            "messages": messages,
            "stream": True,
        }

        full_summary: str = ""
        try:
            with requests.post(BASE_URL, json=data_payload, stream=True) as response:
                if response.status_code == 200:
                    for line in response.iter_lines(decode_unicode=True):
                        if line:
                            try:
                                data = json.loads(line.strip())
                                if "message" in data and "content" in data["message"]:
                                    full_summary += data["message"]["content"]
                            except json.JSONDecodeError:
                                print(f"Failed to decode JSON line: {line}")
                else:
                    print(f"Error: Received status code {response.status_code}")
        except requests.exceptions.RequestException as e:
            print(f"An error occurred: {e}")

        output_queue.put(full_summary.strip())  # Put the complete summary in the queue

    def fetch_api_response(self, tab: ChatTab) -> None:
        while True:
            try:
                data_payload: Dict[str, Any] = tab.input_queue.get(timeout=3)
                if data_payload is None:
                    break

                # Prepare the messages for the Ollama API
                messages: List[Dict[str, str]] = []
                for q, a in zip(
                    data_payload["chat_history_questions"],
                    data_payload["chat_history_answers"],
                ):
                    messages.append({"role": "user", "content": q})
                    messages.append({"role": "assistant", "content": a})
                messages.append({"role": "user", "content": data_payload["prompt"]})

                ollama_payload: Dict[str, Any] = {
                    "model": data_payload["model"],
                    "messages": messages,
                    "stream": True,
                }

                with requests.post(
                    BASE_URL,
                    json=ollama_payload,
                    stream=True,
                ) as response:
                    if response.status_code == 200:
                        for line in response.iter_lines(decode_unicode=True):
                            if line:
                                print(line)
                                try:
                                    data = json.loads(line.strip())
                                    if (
                                        "message" in data
                                        and "content" in data["message"]
                                    ):
                                        tab.output_queue.put(
                                            {
                                                "response": data["message"]["content"],
                                                "done": data.get("done", False),
                                            },
                                        )
                                        self.master.after(
                                            0,
                                            lambda: self.process_api_response(tab),
                                        )
                                except json.JSONDecodeError:
                                    print(
                                        f"Failed to decode JSON line for payload: {ollama_payload}",
                                    )
                    else:
                        print(f"Error: Received status code {response.status_code}")
            except requests.exceptions.RequestException as e:
                print(f"An error occurred with payload {data_payload}: {e}")
            except queue.Empty:
                break

    def process_api_response(self, tab: ChatTab) -> None:
        try:
            while True:
                result: Dict[str, Any] = tab.output_queue.get_nowait()
                tab.chat_history_answers[-1] += result["response"]
                tab.update_chat_display()

                if (
                    result.get("done")
                    and not tab.summary_generated
                    and len(tab.chat_history_questions) == 1
                ):
                    tab.summary_generated = True
                    tab.get_summary()  # This now starts an asynchronous process
        except queue.Empty:
            pass

    def go_to_end_of_line(self, event: tk.Event) -> Optional[str]:
        if isinstance(event.widget, SyntaxHighlightedText):
            event.widget.event_generate("<End>")
            return "break"  # This prevents the default behavior
        return None

    def go_to_start_of_line(self, event: tk.Event) -> Optional[str]:
        if isinstance(event.widget, SyntaxHighlightedText):
            event.widget.event_generate("<Home>")
            return "break"  # This prevents the default behavior
        return None


class ToolTip:
    def __init__(self, widget: tk.Widget, text: str) -> None:
        self.widget = widget
        self.text = text
        self.tooltip: Optional[tk.Toplevel] = None
        self.widget.bind("<Enter>", self.show_tooltip)
        self.widget.bind("<Leave>", self.hide_tooltip)

    def show_tooltip(self, event: Optional[tk.Event] = None) -> None:
        """Show the tooltip at the current cursor position."""
        # Get the position of the text cursor
        # Explicitly cast the return value to the expected tuple type or None
        bbox_result: Optional[tuple[int, int, int, int]] = self.widget.bbox("insert")  # type: ignore

        if not bbox_result:  # If bbox returns None
            return  # Exit the method early

        # Now that we know bbox_result is not None, we can safely unpack it
        x, y, _, _ = bbox_result

        # Position the tooltip window
        x += self.widget.winfo_rootx() + 25
        y += self.widget.winfo_rooty() + 25

        # Create the tooltip window
        self.tooltip = tk.Toplevel(self.widget)
        self.tooltip.wm_overrideredirect(True)
        self.tooltip.wm_geometry(f"+{x}+{y}")

        # Add the tooltip text
        label = tk.Label(
            self.tooltip,
            text=self.text,
            background="#ffffe0",
            relief="solid",
            borderwidth=1,
        )
        label.pack()

    def hide_tooltip(self, event: Optional[tk.Event] = None) -> None:
        if self.tooltip:
            self.tooltip.destroy()
            self.tooltip = None


def main() -> None:
    root = tk.Tk()
    app = ChatApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
