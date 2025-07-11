import platform
import re
import tkinter as tk
from tkinter import ttk
from typing import List
from typing import Optional
from typing import Tuple


class FindDialog:
    def __init__(self, parent: tk.Tk, text_widget: tk.Text):
        self.parent = parent
        self.text_widget = text_widget
        self.dialog: Optional[tk.Toplevel] = None
        self.search_var = tk.StringVar()
        self.case_sensitive_var = tk.BooleanVar()
        self.whole_word_var = tk.BooleanVar()
        self.current_match_index = 0
        self.matches: List[Tuple[str, str]] = []  # List of (start, end) positions
        self.highlight_tag = "find_highlight"
        self.current_tag = "find_current"

        # Configure highlight tags
        self.text_widget.tag_configure(
            self.highlight_tag,
            background="#FFFF00",
            foreground="#000000",
        )
        self.text_widget.tag_configure(
            self.current_tag,
            background="#FF8C00",
            foreground="#000000",
        )

    def show(self):
        """Show the find dialog."""
        if self.dialog:
            self.dialog.lift()
            self.dialog.focus_set()
            return

        width = 450 if platform.system() == "Darwin" else 380

        self.dialog = tk.Toplevel(self.parent)
        self.dialog.title("Find")
        self.dialog.geometry(f"{width}x140")
        self.dialog.resizable(False, False)

        # Make dialog modal
        self.dialog.transient(self.parent)
        self.dialog.grab_set()

        # Center the dialog
        self.dialog.geometry(
            "+%d+%d"
            % (
                self.parent.winfo_rootx() + 50,
                self.parent.winfo_rooty() + 50,
            ),
        )

        self.create_widgets()

        # Bind events
        self.dialog.bind("<Return>", lambda e: self.find_next())
        self.dialog.bind("<Escape>", lambda e: self.close())
        self.dialog.protocol("WM_DELETE_WINDOW", self.close)

        # Focus on search entry
        self.search_entry.focus_set()

        # If there's selected text, use it as default search term
        try:
            selected_text = self.text_widget.get(tk.SEL_FIRST, tk.SEL_LAST)
            if selected_text:
                self.search_var.set(selected_text)
                self.search_entry.select_range(0, tk.END)
        except tk.TclError:
            pass

    def create_widgets(self):
        """Create the dialog widgets."""
        # Main frame with reduced padding
        main_frame = ttk.Frame(self.dialog, padding="8")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Search frame
        search_frame = ttk.Frame(main_frame)
        search_frame.pack(fill=tk.X, pady=(0, 8))

        ttk.Label(search_frame, text="Find:").pack(side=tk.LEFT)
        self.search_entry = ttk.Entry(
            search_frame,
            textvariable=self.search_var,
            width=30,  # Increased width slightly
        )
        self.search_entry.pack(side=tk.LEFT, padx=(5, 0), fill=tk.X, expand=True)

        # Options frame
        options_frame = ttk.Frame(main_frame)
        options_frame.pack(fill=tk.X, pady=(0, 8))

        ttk.Checkbutton(
            options_frame,
            text="Case sensitive",
            variable=self.case_sensitive_var,
        ).pack(side=tk.LEFT)

        ttk.Checkbutton(
            options_frame,
            text="Whole word",
            variable=self.whole_word_var,
        ).pack(side=tk.LEFT, padx=(15, 0))

        # Buttons frame
        buttons_frame = ttk.Frame(main_frame)
        buttons_frame.pack(fill=tk.X, pady=(0, 8))

        # Left side buttons
        left_buttons = ttk.Frame(buttons_frame)
        left_buttons.pack(side=tk.LEFT)

        ttk.Button(
            left_buttons,
            text="Find Next",
            command=self.find_next,
        ).pack(side=tk.LEFT)

        ttk.Button(
            left_buttons,
            text="Find Previous",
            command=self.find_previous,
        ).pack(side=tk.LEFT, padx=(5, 0))

        ttk.Button(
            left_buttons,
            text="Find All",
            command=self.find_all,
        ).pack(side=tk.LEFT, padx=(5, 0))

        # Right side close button
        ttk.Button(
            buttons_frame,
            text="Close",
            command=self.close,
        ).pack(side=tk.RIGHT)

        fontsize = 12 if platform.system() == "Darwin" else 9
        # Status label with explicit height
        self.status_label = ttk.Label(
            main_frame,
            text="",
            font=("TkDefaultFont", 9),  # Slightly smaller font for status
        )
        self.status_label.pack(pady=(5, 0), fill=tk.X)

        # Bind search entry changes
        self.search_var.trace("w", self.on_search_changed)

    def on_search_changed(self, *args):
        """Handle search text changes."""
        if self.search_var.get():
            self.find_all()
        else:
            self.clear_highlights()

    def find_all(self):
        """Find all occurrences of the search term."""
        search_text = self.search_var.get()
        if not search_text:
            self.clear_highlights()
            return

        # Clear previous highlights
        self.clear_highlights()

        # Get all text
        content = self.text_widget.get("1.0", tk.END)

        # Prepare search pattern
        if self.whole_word_var.get():
            pattern = r"\b" + re.escape(search_text) + r"\b"
        else:
            pattern = re.escape(search_text)

        flags = 0 if self.case_sensitive_var.get() else re.IGNORECASE

        # Find all matches
        self.matches = []
        for match in re.finditer(pattern, content, flags):
            start_pos = self.char_to_tk_index(match.start())
            end_pos = self.char_to_tk_index(match.end())
            if start_pos and end_pos:
                self.matches.append((start_pos, end_pos))

        # Highlight all matches
        for start, end in self.matches:
            self.text_widget.tag_add(self.highlight_tag, start, end)

        # Update status
        if self.matches:
            self.current_match_index = 0
            self.highlight_current_match()
            self.status_label.config(text=f"Found {len(self.matches)} matches")
        else:
            self.status_label.config(text="No matches found")

    def find_next(self):
        """Find next occurrence."""
        if not self.matches:
            self.find_all()
            return

        if self.matches:
            self.current_match_index = (self.current_match_index + 1) % len(
                self.matches,
            )
            self.highlight_current_match()
            self.scroll_to_current_match()

    def find_previous(self):
        """Find previous occurrence."""
        if not self.matches:
            self.find_all()
            return

        if self.matches:
            self.current_match_index = (self.current_match_index - 1) % len(
                self.matches,
            )
            self.highlight_current_match()
            self.scroll_to_current_match()

    def highlight_current_match(self):
        """Highlight the current match differently."""
        # Remove current highlight
        self.text_widget.tag_remove(self.current_tag, "1.0", tk.END)

        if self.matches and 0 <= self.current_match_index < len(self.matches):
            start, end = self.matches[self.current_match_index]
            self.text_widget.tag_add(self.current_tag, start, end)
            self.status_label.config(
                text=f"Match {self.current_match_index + 1} of {len(self.matches)}",
            )

    def scroll_to_current_match(self):
        """Scroll to show the current match."""
        if self.matches and 0 <= self.current_match_index < len(self.matches):
            start, _ = self.matches[self.current_match_index]
            self.text_widget.see(start)

    def char_to_tk_index(self, char_pos: int) -> Optional[str]:
        """Convert character position to Tkinter index."""
        try:
            content = self.text_widget.get("1.0", "end-1c")
            if char_pos < 0 or char_pos > len(content):
                return None

            lines = content.split("\n")
            current_pos = 0

            for line_num, line in enumerate(lines, 1):
                line_end = current_pos + len(line)
                if char_pos <= line_end:
                    col_pos = char_pos - current_pos
                    return f"{line_num}.{col_pos}"
                current_pos = line_end + 1

            return None
        except Exception:
            return None

    def clear_highlights(self):
        """Clear all search highlights."""
        self.text_widget.tag_remove(self.highlight_tag, "1.0", tk.END)
        self.text_widget.tag_remove(self.current_tag, "1.0", tk.END)
        self.matches = []
        self.current_match_index = 0
        self.status_label.config(text="")

    def close(self):
        """Close the find dialog."""
        if self.dialog:
            self.clear_highlights()
            self.dialog.destroy()
            self.dialog = None
