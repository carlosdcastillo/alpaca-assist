import json
import os
import platform
import tkinter as tk
from datetime import datetime
from tkinter import filedialog
from tkinter import messagebox
from tkinter import ttk
from typing import Any
from typing import cast
from typing import Dict
from typing import List
from typing import Optional

import pyperclip

from chat_tab import ChatTab
from conversation_history import ConversationHistoryWindow
from database import ConversationDatabase
from find_dialog import FindDialog
from preferences import DEFAULT_PREFERENCES
from preferences import PreferencesWindow
from syntax_text import SyntaxHighlightedText
from text_utils import export_and_open
from text_utils import parse_code_blocks
from tooltip import ToolTip
from utils import is_macos


class ChatApp:
    def __init__(self, master: tk.Tk) -> None:
        self.master = master
        master.title("Alpaca Assist")

        # Initialize database
        self.db = ConversationDatabase()

        # Initialize and load preferences FIRST
        self.preferences = DEFAULT_PREFERENCES.copy()
        self.load_preferences()

        # Set window geometry from preferences
        master.geometry(str(self.preferences["window_geometry"]))

        # Initialize other attributes
        self.style = ttk.Style()
        self.style.configure("Custom.TButton", padding=(10, 10), width=15)
        self.style.configure("TNotebook.Tab", padding=(4, 4))

        self.file_completions: List[str] = []
        self.last_focused_widget: Optional[SyntaxHighlightedText] = None
        self.tabs: List[ChatTab] = []
        self.load_file_completions()

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

    def load_preferences(self) -> None:
        """Load preferences from file."""
        if os.path.exists("preferences.json"):
            try:
                with open("preferences.json", "r") as f:
                    saved_prefs = json.load(f)
                    self.preferences.update(saved_prefs)
            except Exception as e:
                print(f"Error loading preferences: {e}")

    def save_preferences(self) -> None:
        """Save preferences to file."""
        try:
            with open("preferences.json", "w") as f:
                json.dump(self.preferences, f, indent=2)
        except Exception as e:
            print(f"Error saving preferences: {e}")

    def apply_preferences(self) -> None:
        """Apply all preferences to the application."""
        self.apply_appearance_preferences(self.preferences)

    def apply_appearance_preferences(self, prefs: Dict[str, Any]) -> None:
        """Apply appearance-related preferences."""
        # Update all text widgets with new font, theme, and background
        for i, tab in enumerate(self.tabs):
            # Apply in specific order: font first, then background, then theme
            tab.chat_display.update_font(prefs["font_family"], prefs["font_size"])
            tab.input_field.update_font(prefs["font_family"], prefs["font_size"])

            tab.chat_display.update_background_color(prefs["background_color"])
            tab.input_field.update_background_color(prefs["background_color"])

            # Apply theme last so it can override background-specific colors
            tab.chat_display.update_theme(prefs["theme"])
            tab.input_field.update_theme(prefs["theme"])

            # Update undo settings
            tab.chat_display.config(maxundo=prefs["max_undo_levels"])
            tab.input_field.config(maxundo=prefs["max_undo_levels"])

    def show_preferences(self) -> None:
        """Show the preferences window."""
        PreferencesWindow(self)

    def undo_text(self) -> None:
        """Undo text in the currently focused widget."""
        if isinstance(self.last_focused_widget, SyntaxHighlightedText):
            self.last_focused_widget.undo(None)

    def on_closing(self) -> None:
        """Handle application closing by saving session and quitting."""
        # Save current window geometry
        self.preferences["window_geometry"] = self.master.geometry()
        self.save_preferences()

        if self.preferences["auto_save"]:
            self.save_session()
        self.master.destroy()

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

    def save_session(self) -> None:
        """Save all tabs and their contents to disk."""
        # Get the currently selected tab index
        current_tab_index = (
            self.notebook.index(self.notebook.select()) if self.tabs else 0
        )

        session_data: Dict[str, Any] = {
            "tabs": [],
            "window": {
                "geometry": self.master.geometry(),
            },
            "selected_tab_index": current_tab_index,
            "version": "1.1",  # Add version for future compatibility
        }

        for tab in self.tabs:
            tab_data: Dict[str, Any] = {
                "name": self.notebook.tab(self.tabs.index(tab), "text"),
                **tab.get_serializable_data(),  # Use the new serialization method
            }
            session_data["tabs"].append(tab_data)

        try:
            with open("chat_session.json", "w") as f:
                json.dump(session_data, f, indent=2)
            print("Session saved successfully")
        except Exception as e:
            print(f"Error saving session: {e}")

    def load_session(self) -> None:
        """Load saved tabs and their contents from disk."""
        if not os.path.exists("chat_session.json"):
            self.create_tab()
            return

        try:
            with open("chat_session.json", "r") as f:
                session_data: Dict[str, Any] = json.load(f)

            # Restore window geometry if available
            if "window" in session_data and "geometry" in session_data["window"]:
                self.master.geometry(
                    cast(Dict[str, str], session_data["window"])["geometry"],
                )

            # Clear any default tabs
            if self.tabs:
                for tab in self.tabs:
                    self.notebook.forget(tab.frame)
                self.tabs = []

            for tab_data in cast(List[Dict[str, Any]], session_data.get("tabs", [])):
                new_tab: ChatTab = ChatTab(self, self.notebook, self.file_completions)

                # Use the new loading method
                new_tab.load_from_data(tab_data)

                # Add the tab to the list
                self.tabs.append(new_tab)

                # Update the tab's display
                new_tab.rebuild_display_from_state()

                # Set the tab name
                tab_name: str = tab_data.get("name", f"Tab {len(self.tabs)}")
                self.notebook.tab(self.tabs.index(new_tab), text=tab_name)

            # If no tabs were loaded, create a default one
            if not self.tabs:
                self.create_tab()

            # Select the previously selected tab if available
            selected_tab_index = session_data.get("selected_tab_index", 0)
            if 0 <= selected_tab_index < len(self.tabs):
                self.notebook.select(selected_tab_index)
                # Update window title with selected tab name
                tab_name = self.notebook.tab(selected_tab_index, "text")
                self.master.title(f"Alpaca Assist - {tab_name}")

            print("Session loaded successfully")
        except Exception as e:
            print(f"Error loading session: {e}")
            self.create_tab()

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

        # Help menu
        help_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Help", menu=help_menu)
        help_menu.add_command(label="About", command=self.show_about)

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
            "Alpaca Assist\n\nVersion 0.06\n\nA chat application using the Ollama API."
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

    def create_tab(self, tab_name: Optional[str] = None) -> None:
        """Create a new tab."""
        tab = ChatTab(self, self.notebook, self.file_completions, self.preferences)
        self.tabs.append(tab)
        if tab_name is None:
            tab_name = f"Chat {len(self.tabs)}"
        self.notebook.add(tab.frame, text=tab_name)
        self.notebook.select(len(self.tabs) - 1)

        # Update window title with the new tab name
        self.master.title(f"Alpaca Assist - {tab_name}")

    def handle_ctrl_return(self, tab) -> str:
        """Handle Ctrl+Return event for a specific tab."""
        tab.submit_message()
        return "break"

    def store_tab_in_database(self, tab: ChatTab) -> None:
        """Store a tab's conversation in the database before closing it."""
        # Get tab data
        tab_data = tab.get_serializable_data()

        # Skip empty conversations
        if not tab_data.get("chat_state", {}).get("questions") or not tab_data.get(
            "chat_state",
            {},
        ).get("answers"):
            return

        # Get tab title
        tab_index = self.tabs.index(tab)
        tab_title = self.notebook.tab(tab_index, "text")

        # Add creation timestamp if not present
        if "created_date" not in tab_data:
            tab_data["created_date"] = datetime.now().isoformat()

        # Store in database
        try:
            conv_id = self.db.store_conversation(tab_title, tab_data)

            # Check if this was an update or new conversation
            if tab_data.get("original_conversation_id"):
                print(f"Updated conversation '{tab_title}' with ID {conv_id}")
            else:
                print(f"Stored new conversation '{tab_title}' with ID {conv_id}")

        except Exception as e:
            print(f"Error storing conversation: {e}")
            messagebox.showerror("Database Error", f"Failed to store conversation: {e}")

    def delete_tab(self) -> None:
        """Delete tab and automatically store in database if it has content."""
        if len(self.tabs) <= 1:
            return

        current_tab = self.notebook.select()
        tab_index = self.notebook.index(current_tab)
        tab = self.tabs[tab_index]

        # Check if conversation has content and automatically store it
        tab_data = tab.get_serializable_data()
        has_content = (
            tab_data.get("chat_state", {}).get("questions")
            and tab_data.get("chat_state", {}).get("answers")
            and any(q.strip() for q in tab_data["chat_state"]["questions"])
            and any(a.strip() for a in tab_data["chat_state"]["answers"])
        )

        if has_content:
            # Automatically save the conversation without asking
            self.store_tab_in_database(tab)

        # Clean up and remove tab
        tab.cleanup_resources()
        self.notebook.forget(current_tab)
        del self.tabs[tab_index]

    def update_last_focused(self, event: tk.Event) -> None:
        self.last_focused_widget = cast(SyntaxHighlightedText, event.widget)

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

    def update_tab_name(self, tab: ChatTab, summary: str) -> None:
        tab_index = self.tabs.index(tab)
        self.notebook.tab(tab_index, text=summary)

        # Update window title if this is the currently selected tab
        current_tab_index = self.notebook.index(self.notebook.select())
        if tab_index == current_tab_index:
            self.master.title(f"Alpaca Assist - {summary}")

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
