import sys
import tkinter as tk
from tkinter import scrolledtext
from typing import Optional
from typing import Tuple
from typing import Union

from pygments import lex  # type: ignore
from pygments.lexers import MarkdownLexer  # type: ignore
from pygments.styles import get_all_styles  # type: ignore
from pygments.styles import get_style_by_name  # type: ignore

from syntax_text_highlighting import SyntaxHighlightingMixin
from text_utils import backoff
from text_utils import count_leading_chars
from text_utils import parse_code_blocks
from token_cache import TokenCache


def is_macos() -> bool:
    return sys.platform == "darwin"


class SyntaxHighlightedText(SyntaxHighlightingMixin, scrolledtext.ScrolledText):
    # Add these constants at the class level
    MAX_LINE_LENGTH = 1000  # Skip highlighting for lines longer than this
    MAX_TOTAL_LENGTH = 50000  # Skip highlighting if total content is too large

    def __init__(
        self,
        *args,
        theme_name: str = "default",
        background_color: str = "black",
        font_family: str = "Cascadia Mono",
        font_size: int = 12,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.lexer = MarkdownLexer()
        self.token_cache = TokenCache(max_size=50)

        # Add new tracking variables for incremental highlighting
        self.last_highlighted_content = ""
        self.last_highlighted_length = 0
        self.highlighting_in_progress = False
        self.skip_highlighting_reason = None  # Track why highlighting was skipped

        # Initialize with the specified theme instead of safe default
        try:
            self.style = get_style_by_name(theme_name)
        except:
            # Only fall back if the specified theme doesn't exist
            try:
                self.style = get_style_by_name("default")
            except:
                from pygments.styles import get_all_styles

                available_themes = list(get_all_styles())
                self.style = (
                    get_style_by_name(available_themes[0]) if available_themes else None
                )

        # Set initial colors based on background preference
        if background_color == "black":
            self.bg_color = "#000000"
            self.fg_color = "#f8f8f2"
            self.cursor_color = "white"
        else:  # white
            self.bg_color = "#ffffff"
            self.fg_color = "#000000"
            self.cursor_color = "black"

        self.configure(
            font=(font_family, font_size),
            bg=self.bg_color,
            fg=self.fg_color,
            insertbackground=self.cursor_color,
        )
        self.tag_configure("default", foreground=self.fg_color)
        self.bind("<KeyRelease>", self.on_key_release)
        # Add Tab key binding for 4 spaces
        self.bind("<Tab>", self.insert_tab_spaces)
        self.after_id: str | None = None
        self.tag_configure("separator", foreground="#888888")

        # Configure theme tags with the correct theme from the start
        if self.style and hasattr(self.style, "styles") and self.style.styles:
            self.configure_initial_theme_tags()

        # Setup undo functionality
        self.bind("<Control-z>", self.undo)
        self.config(undo=True, autoseparators=True, maxundo=-1)
        self.highlighting_enabled = True
        # Add this near the end of __init__, after other bindings:
        if is_macos():
            modifier = "Command"
        else:
            modifier = "Control"

        # Override the default transpose behavior
        self.bind(f"<{modifier}-t>", lambda e: "break")

        # Add server content tracking
        self.server_mode = False  # Track if we're receiving server content
        self.config(undo=True, autoseparators=True, maxundo=-1)

    def _should_skip_highlighting(self, text: str) -> tuple[bool, str]:
        """Check if highlighting should be skipped due to performance concerns."""

        # Check total length
        # if len(text) > self.MAX_TOTAL_LENGTH:
        #     return True, f"Text too large ({len(text)} chars > {self.MAX_TOTAL_LENGTH})"

        # Check individual line lengths
        lines = text.split("\n")
        for i, line in enumerate(lines):
            if len(line) > self.MAX_LINE_LENGTH:
                return (
                    True,
                    f"Line {i+1} too long ({len(line)} chars > {self.MAX_LINE_LENGTH})",
                )

        return False, ""

    def get_highlighting_status(self) -> str:
        """Get the current highlighting status for debugging."""
        if not self.highlighting_enabled:
            return "Highlighting disabled"
        elif self.skip_highlighting_reason:
            return f"Highlighting skipped: {self.skip_highlighting_reason}"
        else:
            return "Highlighting active"

    def set_server_mode(self, enabled: bool) -> None:
        """Enable/disable server mode to control undo behavior."""
        old_server_mode = getattr(self, "server_mode", False)
        self.server_mode = enabled

        if enabled and not old_server_mode:
            # Entering server mode - create separator
            try:
                self.edit_separator()
            except tk.TclError:
                pass
        elif not enabled and old_server_mode:
            # Exiting server mode - create separator
            try:
                self.edit_separator()
            except tk.TclError:
                pass

    def insert_tab_spaces(self, event: tk.Event) -> str:
        """Insert 4 spaces when Tab is pressed."""
        self.insert(tk.INSERT, "    ")  # Insert 4 spaces
        return "break"  # Prevent default Tab behavior

    def insert(self, index, chars, *args):
        """Override insert to handle server vs user content differently."""
        # Normalize line endings in the inserted text
        if isinstance(chars, str):
            chars = chars.replace("\r\n", "\n").replace("\r", "\n")

        if self.server_mode:
            # Server content - disable undo recording temporarily
            old_undo_state = self.cget("undo")
            self.config(undo=False)

            # Call the parent insert method
            super().insert(index, chars, *args)

            # Re-enable undo
            self.config(undo=old_undo_state)
        else:
            # User content - normal undo behavior
            super().insert(index, chars, *args)

    def delete(self, index1, index2=None):
        """Override delete to handle server vs user content differently."""
        if self.server_mode:
            # Server-initiated deletions shouldn't be undoable
            old_undo_state = self.cget("undo")
            self.config(undo=False)

            super().delete(index1, index2)

            self.config(undo=old_undo_state)
        else:
            # User deletions should be undoable
            super().delete(index1, index2)

    def configure_selection_colors(self) -> None:
        """Configure selection colors for better readability."""
        if self.bg_color == "#000000":  # Dark theme
            # Light selection background with dark text for contrast
            selection_bg = "#4A90E2"  # Nice blue
            selection_fg = "#FFFFFF"  # White text
        else:  # Light theme
            # Dark selection background with light text for contrast
            selection_bg = "#316AC5"  # Darker blue
            selection_fg = "#FFFFFF"  # White text

        # Configure the selection colors
        self.configure(
            selectbackground=selection_bg,
            selectforeground=selection_fg,
            inactiveselectbackground=selection_bg,  # Keep same color when widget loses focus
        )

    def update_background_color(self, background_color: str) -> None:
        """Update the background color of the text widget."""
        if background_color == "black":
            self.bg_color = "#000000"
            self.fg_color = "#f8f8f2"
            self.cursor_color = "white"
        elif background_color == "white":
            self.bg_color = "#ffffff"
            self.fg_color = "#000000"
            self.cursor_color = "black"

        self.configure(
            bg=self.bg_color,
            fg=self.fg_color,
            insertbackground=self.cursor_color,
        )
        self.tag_configure("default", foreground=self.fg_color)

        # Add this line to update selection colors too
        self.configure_selection_colors()

        # Reapply syntax highlighting with new colors
        self.highlight_text()

    def update_font(self, font_family: str, font_size: int) -> None:
        """Update the font of the text widget with validation."""
        try:
            # Test the font first
            import tkinter.font as tkfont

            test_font = tkfont.Font(family=font_family, size=font_size)
            test_font.metrics()  # This will raise an exception if font doesn't work

            # Apply the font
            self.configure(font=(font_family, font_size))

        except Exception as e:
            print(f"Error applying font {font_family} at size {font_size}: {e}")
            # Fallback to a safe font
            self.configure(font=("Courier", font_size))

    def update_theme(self, theme_name: str) -> None:
        """Update the syntax highlighting theme."""
        try:
            # Get the new style
            try:
                style = get_style_by_name(theme_name)
            except:
                # Fall back to default if theme doesn't exist
                try:
                    style = get_style_by_name("default")
                except:
                    from pygments.styles import get_all_styles

                    available_themes = list(get_all_styles())
                    style = (
                        get_style_by_name(available_themes[0])
                        if available_themes
                        else None
                    )

            if style is None:
                print(f"Could not load any theme, keeping current theme")
                return

            self.style = style
            self.configure_theme_tags()

            # Force full re-highlight after theme change
            self.last_highlighted_content = ""
            self.last_highlighted_length = 0
            self.highlight_text_full()  # Use full highlight instead of incremental

        except Exception as e:
            print(f"Error updating theme '{theme_name}': {e}")

    def undo(self, event=None) -> str:
        """Perform undo operation."""
        try:
            self.edit_undo()
            # Reapply syntax highlighting after undo
            self.highlight_text()
        except tk.TclError:
            # Nothing to undo
            pass
        return "break"  # Prevent default behavior

    def on_key_release(self, event: tk.Event) -> None:
        if self.highlighting_enabled:
            if self.after_id:
                self.after_cancel(self.after_id)
            # Reduced delay since incremental highlighting is faster
            self.after_id = self.after(50, self.highlight_text)

    def set_highlighting_enabled(self, enabled: bool) -> None:
        """Enable or disable automatic syntax highlighting."""
        self.highlighting_enabled = enabled
        if enabled:
            # Force a full re-highlight when re-enabled after being disabled
            # This ensures content is properly highlighted after rebuilds
            self.after_idle(self.highlight_text_full)

    def set_text(self, text: str) -> None:
        """Set text and highlight it."""
        # Normalize line endings
        text = text.replace("\r\n", "\n").replace("\r", "\n")

        # Use server mode when setting text programmatically
        self.set_server_mode(True)
        self.delete("1.0", tk.END)
        self.insert("1.0", text)
        self.set_server_mode(False)

        # Reset tracking variables since we're setting entirely new content
        self.last_highlighted_content = ""
        self.last_highlighted_length = 0
        self.highlight_text_full()
