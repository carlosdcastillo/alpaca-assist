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

    def __init__(self, master: tk.Tk) -> None:
        # Initialize the core functionality first
        super().__init__(master)

        # Create widgets AFTER preferences are loaded
        self.create_widgets()
        self.create_menu()
        self.bind_shortcuts()

        # Set up protocol for when window is closed
        self.master.protocol("WM_DELETE_WINDOW", self.on_closing)

        # Load saved session
        self.load_session()

    def show_conversation_history(self) -> None:
        """Show the conversation history window."""
        ConversationHistoryWindow(self, self.master)

    def export_to_html(self) -> None:
        """Export the content of the currently focused text widget to HTML."""
        if isinstance(self.last_focused_widget, SyntaxHighlightedText):
            # Get the content from the focused widget
            content = self.last_focused_widget.get("1.0", tk.END).strip()

            if not content:
                return  # Silently return if no content

            # Determine the title based on which widget is focused
            current_tab_index = self.notebook.index(self.notebook.select())
            if 0 <= current_tab_index < len(self.tabs):
                current_tab = self.tabs[current_tab_index]

                # Check if the focused widget is the chat display or input field
                if self.last_focused_widget == current_tab.chat_display:
                    tab_name = self.notebook.tab(current_tab_index, "text")
                    title = f"Chat Export - {tab_name}"
                else:
                    title = "Input Field Export"
            else:
                title = "Exported Content"

            # Export and open with app's current settings (no message boxes)
            export_and_open(
                content,
                title,
                theme_name=str(self.preferences["theme"]),
                background_color=str(self.preferences["background_color"]),
                font_family=str(self.preferences["font_family"]),
                font_size=int(str(self.preferences["font_size"])),
            )
        else:
            # If no text widget is focused, export the current tab's chat display
            current_tab_index = self.notebook.index(self.notebook.select())
            if 0 <= current_tab_index < len(self.tabs):
                current_tab = self.tabs[current_tab_index]
                content = current_tab.chat_display.get("1.0", tk.END).strip()

                if not content:
                    return  # Silently return if no content

                tab_name = self.notebook.tab(current_tab_index, "text")
                title = f"Chat Export - {tab_name}"

                # Export and open with app's current settings (no message boxes)
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
            # If no text widget is focused, use the current tab's chat display
            current_tab_index = self.notebook.index(self.notebook.select())
            if 0 <= current_tab_index < len(self.tabs):
                current_tab = self.tabs[current_tab_index]
                find_dialog = FindDialog(self.master, current_tab.chat_display)
                find_dialog.show()

    def on_tab_changed(self, event: tk.Event) -> None:
        """Handle tab selection changes and update window title."""
        try:
            selected_tab_index = self.notebook.index(self.notebook.select())
            if 0 <= selected_tab_index < len(self.tabs):
                tab_name = self.notebook.tab(selected_tab_index, "text")
                self.master.title(f"Alpaca Assist - {tab_name}")
        except tk.TclError:
            # Handle case where no tab is selected
            self.master.title("Alpaca Assist")

    def show_preferences(self) -> None:
        """Show the preferences window."""
        PreferencesWindow(self)

    def undo_text(self) -> None:
        """Undo text in the currently focused widget."""
        if isinstance(self.last_focused_widget, SyntaxHighlightedText):
            self.last_focused_widget.undo(None)

    def show_mcp_config(self):
        """Show MCP server configuration window."""
        MCPConfigWindow(self, self.master)

    def create_widgets(self) -> None:
        # Create button frame
        self.button_frame = ttk.Frame(self.master)
        self.button_frame.pack(fill="x", padx=5, pady=(4, 2))  # Add top padding

        # Create a custom style for medium-height buttons
        self.style.configure(
            "Medium.TButton",
            padding=(9, 11),
        )  # Adjust vertical padding to a medium value

        # Detect platform
        is_macos_system = platform.system() == "Darwin"

        # Set modifier key text based on platform
        modifier_key = "Cmd" if is_macos_system else "Ctrl"

        if is_macos_system:
            # Set consistent button width
            button_width = 12  # Adjust this value as needed

            # Create New Tab button with tk.Button for macOS
            self.new_tab_button = tk.Button(
                self.button_frame,
                text="New Tab",
                command=self.create_tab,
                height=2,
                width=button_width,
            )
            self.new_tab_button.pack(side="left", padx=(3, 1))
            ToolTip(self.new_tab_button, f"New Tab ({modifier_key}+N)")

            # Create Delete Tab button with tk.Button for macOS
            self.delete_tab_button = tk.Button(
                self.button_frame,
                text="Close Tab",
                command=self.delete_tab,
                height=2,
                width=button_width,
            )
            self.delete_tab_button.pack(side="left", padx=(1, 1))
            ToolTip(self.delete_tab_button, f"Close Tab ({modifier_key}+W)")

            # Create Conversation History button with tk.Button for macOS
            self.history_button = tk.Button(
                self.button_frame,
                text="Conversation History",
                command=self.show_conversation_history,
                height=2,
                width=button_width,
            )
            self.history_button.pack(side="left", padx=(1, 3))
            ToolTip(self.history_button, f"Conversation History ({modifier_key}+Y)")
        else:
            # Set consistent button width
            button_width = 12  # Adjust this value as needed

            # Create a custom style for consistent width buttons
            self.style.configure(
                "FixedWidth.TButton",
                padding=(9, 11),
                width=button_width,
            )

            # Create New Tab button with ttk.Button for other platforms
            self.new_tab_button = ttk.Button(
                self.button_frame,
                text="New Tab",
                command=self.create_tab,
                style="FixedWidth.TButton",
            )
            self.new_tab_button.pack(side="left", padx=(3, 1))
            ToolTip(self.new_tab_button, f"New Tab ({modifier_key}+N)")

            # Create Delete Tab button with ttk.Button for other platforms
            self.delete_tab_button = ttk.Button(
                self.button_frame,
                text="Close Tab",
                command=self.delete_tab,
                style="FixedWidth.TButton",
            )
            self.delete_tab_button.pack(side="left", padx=(1, 1))
            ToolTip(self.delete_tab_button, f"Close Tab ({modifier_key}+W)")

            # Create Conversation History button with ttk.Button for other platforms
            self.history_button = ttk.Button(
                self.button_frame,
                text="History",
                command=self.show_conversation_history,
                style="FixedWidth.TButton",
            )
            self.history_button.pack(side="left", padx=(1, 3))
            ToolTip(self.history_button, f"Conversation History ({modifier_key}+Y)")

        # Create notebook
        self.notebook = ttk.Notebook(self.master)
        self.notebook.pack(
            expand=True,
            fill="both",
            padx=10,
            pady=(5, 10),
        )  # Adjust top padding

        self.notebook.bind("<<NotebookTabChanged>>", self.on_tab_changed)
        # Add focus event handlers
        self.master.bind("<FocusIn>", self.on_app_focus_in)
        self.master.bind("<FocusOut>", self.on_app_focus_out)

        # Initialize update flag
        self.update_enabled = True

    def bind_shortcuts(self) -> None:
        if is_macos():
            modifier = "Command"
        else:
            modifier = "Control"
        self.master.bind(f"<{modifier}-n>", lambda e: self.create_tab())
        self.master.bind(f"<{modifier}-w>", lambda e: self.delete_tab())
        # Fix the Ctrl+Enter binding to return "break" to prevent default behavior
        self.master.bind(f"<{modifier}-c>", lambda e: self.copy_text())
        self.master.bind(f"<{modifier}-v>", self.paste_text())
        self.master.bind(f"<{modifier}-b>", lambda e: self.copy_code_block())
        self.master.bind(f"<{modifier}-m>", lambda e: self.manage_file_completions())
        self.master.bind(f"<{modifier}-e>", self.go_to_end_of_line)
        self.master.bind(f"<{modifier}-a>", self.go_to_start_of_line)
        self.master.bind(f"<{modifier}-z>", lambda e: self.undo_text())
        self.master.bind(f"<{modifier}-comma>", lambda e: self.show_preferences())
        self.master.bind(
            f"<{modifier}-f>",
            lambda e: self.show_find_dialog(),
        )
        self.master.bind(
            f"<{modifier}-y>",
            lambda e: self.show_conversation_history(),
        )
        self.master.bind(
            f"<{modifier}-t>",
            lambda e: self.export_to_html(),
        )

    def on_app_focus_in(self, event: tk.Event) -> None:
        """Re-enable UI updates when app gains focus"""
        self.update_enabled = True

    def on_app_focus_out(self, event: tk.Event) -> None:
        """Reduce UI updates when app loses focus"""
        self.update_enabled = False

    def create_menu(self) -> None:
        menubar = tk.Menu(self.master)
        self.master.config(menu=menubar)

        if is_macos():
            modifier = "Command"
        else:
            modifier = "Control"
        # File menu
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

        # Edit menu
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
            label="Manage File Completions",
            command=self.manage_file_completions,
            accelerator=f"{modifier}+M",
        )
        # Chat menu
        chat_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Chat", menu=chat_menu)
        chat_menu.add_command(
            label="Submit Message",
            command=self.submit_current_tab,
            accelerator="Ctrl+Enter",
        )

        # Add to the File menu or create a new Tools menu
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

        # Help menu
        help_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Help", menu=help_menu)
        help_menu.add_command(label="About", command=self.show_about)

    def show_available_tools(self):
        """Show a window with all available MCP tools."""
        tools_window = tk.Toplevel(self.master)
        tools_window.title("Available MCP Tools")
        tools_window.geometry("700x500")

        # Create treeview to show tools
        tree = ttk.Treeview(tools_window, columns=("Server", "Tool", "Description"))
        tree.heading("#0", text="Name")
        tree.heading("Server", text="Server")
        tree.heading("Tool", text="Tool")
        tree.heading("Description", text="Description")

        # Populate with available tools
        tools = self.get_available_mcp_tools()  # This returns a list
        for tool in tools:  # Iterate directly over the list
            # Extract server name from the function name (format: "server_name_tool_name")
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

        # Add button to call selected tool
        def call_selected_tool():
            selection = tree.selection()
            if selection:
                item = tree.item(selection[0])
                server_name = item["values"][0]
                tool_name = item["values"][1]

                # Create a simple dialog for tool arguments
                self.show_tool_call_dialog(server_name, tool_name)

        ttk.Button(tools_window, text="Call Tool", command=call_selected_tool).pack(
            pady=5,
        )

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
                        # Insert result into current chat tab
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

        # Position relative to main window (like preferences window)
        completions_window.transient(self.master)
        completions_window.grab_set()

        # Center on parent window
        self.master.update_idletasks()
        x = (
            self.master.winfo_x() + (self.master.winfo_width() // 2) - 200
        )  # 200 = half of 400
        y = (
            self.master.winfo_y() + (self.master.winfo_height() // 2) - 150
        )  # 150 = half of 300
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
            self.save_file_completions()  # Save file completions when window closes
            completions_window.destroy()

        completions_window.protocol("WM_DELETE_WINDOW", on_closing)

    def submit_current_tab(self) -> str:
        current_tab_index = self.notebook.index(self.notebook.select())
        if 0 <= current_tab_index < len(self.tabs):
            current_tab = self.tabs[current_tab_index]
            current_tab.submit_message()
        return "break"  # Return "break" to prevent default behavior

    def show_about(self) -> None:
        about_text = (
            "Alpaca Assist\n\nVersion 0.08\n\nA chat application using the Ollama API."
        )
        tk.messagebox.showinfo("About", about_text)

    def paste_text(self) -> str:
        if isinstance(self.last_focused_widget, SyntaxHighlightedText):
            text = pyperclip.paste()

            # Delete any selected text first
            try:
                self.last_focused_widget.delete(tk.SEL_FIRST, tk.SEL_LAST)
            except tk.TclError:
                # No selection, continue
                pass

            # Process the text to ensure proper line handling
            # Replace all newlines with tkinter's internal newline representation
            processed_text = text.replace("\r\n", "\n").replace("\r", "\n")

            # Get current cursor position
            current_pos = self.last_focused_widget.index(tk.INSERT)

            # Insert the processed text
            self.last_focused_widget.insert(current_pos, processed_text)

            # Force a redraw and highlight
            self.last_focused_widget.update_idletasks()
            self.last_focused_widget.highlight_text()
        return "break"

    def copy_text(self) -> None:
        if isinstance(self.last_focused_widget, SyntaxHighlightedText):
            # Save the selection information before potentially losing focus
            has_selection = False
            selected_text = ""

            try:
                # Check if there's a selection and get it
                if self.last_focused_widget.tag_ranges(tk.SEL):
                    has_selection = True
                    selected_text = self.last_focused_widget.get(
                        tk.SEL_FIRST,
                        tk.SEL_LAST,
                    )
            except tk.TclError:
                # No selection
                has_selection = False

            # If we had a selection, use that text
            if has_selection:
                text = selected_text
            else:
                # Otherwise get all text
                text = self.last_focused_widget.get("1.0", tk.END).strip()

            # Copy to clipboard
            pyperclip.copy(text)

            # Restore focus to the text widget
            self.last_focused_widget.focus_set()

    def copy_code_block(self) -> None:
        if isinstance(self.last_focused_widget, SyntaxHighlightedText):
            # Save current cursor position
            current_cursor_pos = self.last_focused_widget.index(tk.INSERT)

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

                # Restore cursor position after highlighting
                self.last_focused_widget.mark_set(tk.INSERT, current_cursor_pos)
                self.last_focused_widget.see(current_cursor_pos)

                # Ensure the widget regains focus
                self.last_focused_widget.focus_set()
                return

            print("No code block found at the current cursor position.")

    def go_to_end_of_line(self, event: tk.Event) -> str | None:
        if isinstance(event.widget, SyntaxHighlightedText):
            event.widget.event_generate("<End>")
            return "break"  # This prevents the default behavior
        return None

    def go_to_start_of_line(self, event: tk.Event) -> str | None:
        if isinstance(event.widget, SyntaxHighlightedText):
            event.widget.event_generate("<Home>")
            return "break"  # This prevents the default behavior
        return None
