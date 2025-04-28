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

import pyperclip
import requests
from pygments import lex
from pygments.lexers import MarkdownLexer
from pygments.styles import get_style_by_name

from expansion_language import expand

BASE_URL = "http://localhost:11435/api/generate"


def backoff(x):
    parts = x.split(".")
    if len(parts) > 1:
        return ".".join(parts[:-1])
    else:
        return x


class SyntaxHighlightedText(scrolledtext.ScrolledText):
    def __init__(self, *args, **kwargs):
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
        self.after_id = None

        self.tag_configure("separator", foreground="#888888")

        cache = {}
        # Configure tags for syntax highlighting
        for token, style in self.style.styles.items():
            if style != "":
                cache[str(token)] = style

            if style == "":
                b = backoff(str(token))
                if b in cache:
                    style = cache[b]

            fg, bg = self.parse_style(style)
            tag_config = {"foreground": fg}
            if bg:
                tag_config["background"] = bg
                if "#" in fg and "#" in bg:
                    self.tag_configure(str(token), foreground=fg, background=bg)
                elif "#" in fg:
                    self.tag_configure(str(token), foreground=fg)
            else:
                if "#" in fg:
                    self.tag_configure(str(token), foreground=fg, background=bg)

    def on_key_release(self, event):
        if self.after_id:
            self.after_cancel(self.after_id)
        self.after_id = self.after(10, self.highlight_text)

    def parse_style(self, style):
        fg = None
        bg = None

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

    def set_text(self, text):
        self.delete("1.0", tk.END)
        self.insert("1.0", text)
        self.highlight_text()

    def highlight_text(self):
        for tag in self.tag_names():
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

    def get_ranges(self, start, end):
        ranges = []
        current = start
        while self.compare(current, "<", end):
            ranges.append((current, f"{current} lineend"))
            current = self.index(f"{current} +1 lines")
        return ranges

    def highlight_code_block(self, start, end):
        self.tag_add("highlight", start, end)
        self.tag_configure("highlight", background="yellow", foreground="black")
        self.after(500, lambda: self.tag_remove("highlight", start, end))


class ChatTab:
    def __init__(self, parent, notebook, file_completions):
        self.parent = parent
        self.notebook = notebook
        self.chat_history_questions = []
        self.chat_history_answers = []
        self.output_queue = queue.Queue()
        self.input_queue = queue.Queue()

        self.frame = ttk.Frame(notebook)
        notebook.add(self.frame, text=f"Tab {len(parent.tabs) + 1}")

        self.create_widgets()
        self.summary_generated = False
        self.file_completions = file_completions

    def create_widgets(self):
        chat_frame = ttk.Frame(self.frame)
        chat_frame.pack(expand=True, fill="both", padx=10, pady=10)

        self.chat_display = SyntaxHighlightedText(
            chat_frame,
            wrap=tk.WORD,
            height=20,
        )
        self.chat_display.pack(expand=True, fill="both")
        self.chat_display.bind("<FocusIn>", self.parent.update_last_focused)
        self.chat_display.bind(
            "<KeyRelease>",
            lambda e: self.chat_display.highlight_text(),
        )

        input_frame = ttk.Frame(self.frame)
        input_frame.pack(fill="x", padx=10, pady=5)

        self.input_field = SyntaxHighlightedText(
            input_frame,
            height=7,
            wrap=tk.WORD,
        )
        self.input_field.pack(side="left", expand=True, fill="both")
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

    def submit_message(self):
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

    def update_chat_display(self):
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

    def get_summary(self):
        threading.Thread(
            target=self._fetch_summary,
            daemon=True,
        ).start()

    def _fetch_summary(self):
        summary_queue = queue.Queue()
        threading.Thread(
            target=self.parent.fetch_api_response_summary,
            args=(self, summary_queue),
            daemon=True,
        ).start()

        # Wait for the response
        response = summary_queue.get()

        # Update the tab name on the main thread
        self.parent.master.after(
            0,
            lambda: self.parent.update_tab_name(self, response.strip()),
        )

    def update_file_completions(
        self,
        new_completions,
    ):  # Renamed from update_file_options
        self.file_completions = new_completions

    def show_autocomplete_menu(self):
        completions = self.file_completions  # Use the instance variable
        menu = tk.Menu(self.input_field, tearoff=0)
        for completion in completions:
            menu.add_command(
                label=completion,
                command=lambda c=completion: self.insert_completion(c),
            )

        # Get the current cursor position
        cursor_pos = self.input_field.index(tk.INSERT)
        x, y, _, height = self.input_field.bbox(cursor_pos)

        # Calculate the absolute position of the cursor
        x_root = self.input_field.winfo_rootx() + x
        y_root = self.input_field.winfo_rooty() + y + height

        # Post the menu at the calculated position
        menu.post(x_root, y_root)

    def insert_completion(self, option):
        cursor_position = self.input_field.index(tk.INSERT)
        self.input_field.insert(cursor_position, option + " ")
        self.input_field.focus_set()

    def check_for_autocomplete(self, event):
        if event.char in [":", "/"]:  # Trigger on both ':' and '/'
            current_line = self.input_field.get("insert linestart", "insert")
            if current_line.endswith("/file:") or current_line.endswith("/file"):
                self.show_autocomplete_menu()


class ChatApp:
    def __init__(self, master):
        self.master = master
        master.title("Alpaca Assist")

        self.style = ttk.Style()
        self.style.configure("Custom.TButton", padding=(10, 10), width=15)

        self.file_completions = []
        self.last_focused_widget = None
        self.tabs = []
        self.load_file_completions()

        self.create_widgets()
        self.create_menu()
        self.bind_shortcuts()

        self.start_ui_update()  # Start periodic UI updates

    def bind_shortcuts(self):
        self.master.bind("<Control-n>", lambda e: self.create_tab())
        self.master.bind("<Control-w>", lambda e: self.delete_tab())
        self.master.bind("<Control-Return>", lambda e: self.submit_current_tab())
        self.master.bind("<Control-c>", lambda e: self.copy_text())
        self.master.bind("<Control-v>", lambda e: self.paste_text())
        self.master.bind("<Control-b>", lambda e: self.copy_code_block())
        self.master.bind("<Control-m>", lambda e: self.manage_file_completions())

    def start_ui_update(self):
        def update_ui():
            self.master.update_idletasks()
            self.master.after(100, update_ui)  # Schedule next update in 100ms

        update_ui()

    def create_menu(self):
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

    def manage_file_completions(self):  # Renamed from manage_file_options
        completions_window = tk.Toplevel(self.master)
        completions_window.title("Manage File Completions")
        completions_window.geometry("400x300")

        listbox = tk.Listbox(completions_window, width=50, selectmode=tk.MULTIPLE)
        listbox.pack(padx=10, pady=10, fill=tk.BOTH, expand=True)

        for completion in self.file_completions:
            listbox.insert(tk.END, completion)

        def add_completions():
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

        def remove_completions():
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

        def on_closing():
            self.update_tabs_file_completions()
            self.save_file_completions()  # Save file completions when window closes
            completions_window.destroy()

        completions_window.protocol("WM_DELETE_WINDOW", on_closing)

    def create_widgets(self):
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

    def submit_current_tab(self):
        current_tab_index = self.notebook.index(self.notebook.select())
        if 0 <= current_tab_index < len(self.tabs):
            current_tab = self.tabs[current_tab_index]
            current_tab.submit_message()

    def show_about(self):
        about_text = (
            "Alpaca Assist\n\nVersion 0.01\n\nA chat application using the Ollama API."
        )
        tk.messagebox.showinfo("About", about_text)

    def save_file_completions(self):
        with open("file_completions.json", "w") as f:
            json.dump(self.file_completions, f, indent=2)

    def load_file_completions(self):
        if os.path.exists("file_completions.json"):
            with open("file_completions.json", "r") as f:
                self.file_completions = json.load(f)

    def update_tabs_file_completions(self):
        for tab in self.tabs:
            tab.update_file_completions(self.file_completions)
        self.save_file_completions()  # Save file completions after updating

    def create_tab(self):
        new_tab = ChatTab(self, self.notebook, self.file_completions)
        self.tabs.append(new_tab)
        self.notebook.select(new_tab.frame)

    def delete_tab(self):
        if len(self.tabs) > 1:
            current_tab = self.notebook.select()
            tab_index = self.notebook.index(current_tab)
            self.notebook.forget(current_tab)
            del self.tabs[tab_index]

    def update_last_focused(self, event):
        self.last_focused_widget = event.widget

    def paste_text(self):
        if isinstance(self.last_focused_widget, SyntaxHighlightedText):
            text = pyperclip.paste()
            self.last_focused_widget.insert(tk.INSERT, text)
            self.last_focused_widget.highlight_text()

    def copy_text(self):
        if isinstance(self.last_focused_widget, SyntaxHighlightedText):
            if self.last_focused_widget.tag_ranges(tk.SEL):
                text = self.last_focused_widget.get(tk.SEL_FIRST, tk.SEL_LAST)
            else:
                text = self.last_focused_widget.get("1.0", tk.END).strip()
            pyperclip.copy(text)

    def copy_code_block(self):
        if isinstance(self.last_focused_widget, SyntaxHighlightedText):
            text = self.last_focused_widget.get("1.0", tk.END)
            cursor_pos = self.last_focused_widget.index(tk.INSERT)
            line, col = map(int, cursor_pos.split("."))

            code_blocks = re.finditer(r"(?m)^```[\s\S]*?^```", text, re.MULTILINE)

            for block in code_blocks:
                start_line = text[: block.start()].count("\n") + 1
                end_line = text[: block.end()].count("\n") + 1

                if start_line <= line <= end_line:
                    code_content = block.group()
                    lines = code_content.split("\n")
                    lines = lines[1:-1]
                    if lines and lines[0].strip().startswith("```"):
                        lines = lines[1:]
                    cleaned_code = "\n".join(lines)

                    pyperclip.copy(cleaned_code)
                    print("Code block copied to clipboard!")

                    # Highlight the code block, excluding the first and last lines
                    start_index = f"{start_line + 1}.0"
                    end_index = f"{end_line - 1}.end"
                    self.last_focused_widget.highlight_code_block(
                        start_index,
                        end_index,
                    )
                    return

            print("No code block found at the current cursor position.")

    def update_tab_name(self, tab, summary):
        tab_index = self.tabs.index(tab)
        self.notebook.tab(tab_index, text=summary)

    def fetch_api_response_summary(self, tab, output_queue):
        data_payload = {
            "model": "granite-code:8b",
            "prompt": "generate a summary of the history of this chat in 1-5 words, do not reply anthing other than the summary, do not end the summary in a period (dot)",
            "chat_history_questions": tab.chat_history_questions[:1]
            if tab.chat_history_questions
            else [],
            "chat_history_answers": tab.chat_history_answers[:1]
            if tab.chat_history_answers
            else [],
        }

        try:
            with requests.post(
                BASE_URL,
                json=data_payload,
                stream=True,
            ) as response:
                if response.status_code == 200:
                    full_response = ""
                    for line in response.iter_lines(decode_unicode=True):
                        if line:
                            try:
                                data = json.loads(line.strip())
                                full_response += data["response"]
                            except json.JSONDecodeError:
                                print(
                                    f"Failed to decode JSON line for payload: {data_payload}",
                                )
                    output_queue.put(full_response)
                else:
                    output_queue.put(
                        f"Error: Received status code {response.status_code}",
                    )
        except requests.exceptions.RequestException as e:
            output_queue.put(f"An error occurred: {e}")

    def fetch_api_response(self, tab):
        while True:
            try:
                data_payload = tab.input_queue.get(timeout=3)
                if data_payload is None:
                    break
                with requests.post(
                    BASE_URL,
                    json=data_payload,
                    stream=True,
                ) as response:
                    if response.status_code == 200:
                        for line in response.iter_lines(decode_unicode=True):
                            if line:
                                try:
                                    data = json.loads(line.strip())
                                    tab.output_queue.put(data)
                                    self.master.after(
                                        0,
                                        lambda: self.process_api_response(tab),
                                    )
                                except json.JSONDecodeError:
                                    print(
                                        f"Failed to decode JSON line for payload: {data_payload}",
                                    )
            except requests.exceptions.RequestException as e:
                print(f"An error occurred with payload {data_payload}: {e}")
            except queue.Empty:
                break

    def process_api_response(self, tab):
        try:
            while True:
                result = tab.output_queue.get_nowait()
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


class ToolTip:
    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tooltip = None
        self.widget.bind("<Enter>", self.show_tooltip)
        self.widget.bind("<Leave>", self.hide_tooltip)

    def show_tooltip(self, event=None):
        x, y, _, _ = self.widget.bbox("insert")
        x += self.widget.winfo_rootx() + 25
        y += self.widget.winfo_rooty() + 25

        self.tooltip = tk.Toplevel(self.widget)
        self.tooltip.wm_overrideredirect(True)
        self.tooltip.wm_geometry(f"+{x}+{y}")

        label = tk.Label(
            self.tooltip,
            text=self.text,
            background="#ffffe0",
            relief="solid",
            borderwidth=1,
        )
        label.pack()

    def hide_tooltip(self, event=None):
        if self.tooltip:
            self.tooltip.destroy()
            self.tooltip = None


def main():
    root = tk.Tk()
    app = ChatApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
