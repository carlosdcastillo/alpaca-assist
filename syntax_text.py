import tkinter as tk
from tkinter import scrolledtext
from typing import Optional
from typing import Tuple
from typing import Union

from pygments import lex  # type: ignore
from pygments.lexers import MarkdownLexer  # type: ignore
from pygments.styles import get_all_styles  # type: ignore
from pygments.styles import get_style_by_name  # type: ignore

from text_utils import backoff
from text_utils import count_leading_chars
from text_utils import parse_code_blocks
from token_cache import TokenCache


class SyntaxHighlightedText(scrolledtext.ScrolledText):
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
        self.after_id: Optional[str] = None
        self.tag_configure("separator", foreground="#888888")

        # Configure theme tags with the correct theme from the start
        if self.style and hasattr(self.style, "styles") and self.style.styles:
            self.configure_initial_theme_tags()

        # Setup undo functionality
        self.bind("<Control-z>", self.undo)
        self.config(undo=True, autoseparators=True, maxundo=-1)
        self.highlighting_enabled = True

    def insert_tab_spaces(self, event: tk.Event) -> str:
        """Insert 4 spaces when Tab is pressed."""
        self.insert(tk.INSERT, "    ")  # Insert 4 spaces
        return "break"  # Prevent default Tab behavior

    def insert(self, index, chars, *args):
        """Override insert to normalize line endings."""
        # Normalize line endings in the inserted text
        if isinstance(chars, str):
            chars = chars.replace("\r\n", "\n").replace("\r", "\n")

        # Call the parent insert method
        super().insert(index, chars, *args)

    def configure_initial_theme_tags(self) -> None:
        """Configure initial theme tags safely."""
        try:
            cache: dict[str, str] = {}
            # Configure tags for syntax highlighting
            for token, style in self.style.styles.items():
                try:
                    if style != "":
                        cache[str(token)] = style

                    if style == "":
                        b = backoff(str(token))
                        if b in cache:
                            style = cache[b]

                    fg, bg = self.parse_style(style)
                    if fg and "#" in fg:
                        if bg and "#" in bg:
                            self.tag_configure(str(token), foreground=fg, background=bg)
                        else:
                            self.tag_configure(str(token), foreground=fg)
                except Exception as token_error:
                    # Skip problematic tokens
                    continue
        except Exception as e:
            print(f"Error configuring initial theme tags: {e}")

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

    def configure_theme_tags(self) -> None:
        """Configure tags for the current theme."""
        try:

            # Clear existing tags but preserve important ones
            for tag in self.tag_names():
                if tag not in ["sel", "highlight", "separator", "default"]:
                    self.tag_delete(tag)

            # Multiple levels of safety checks
            if self.style is None:
                print("Style is None, cannot configure tags")
                return

            if not hasattr(self.style, "styles"):
                print("Style has no 'styles' attribute")
                return

            styles_dict = self.style.styles
            if styles_dict is None:
                print("Style.styles is None")
                return

            if not isinstance(styles_dict, dict):
                print(f"Style.styles is not a dict, it's: {type(styles_dict)}")
                return

            print(f"Configuring tags for {len(styles_dict)} style entries")

            cache: dict[str, str] = {}
            successful_configs = 0
            failed_configs = 0

            # Configure tags for syntax highlighting
            for token, style in styles_dict.items():
                try:
                    if style != "":
                        cache[str(token)] = style

                    if style == "":
                        b = backoff(str(token))
                        if b in cache:
                            style = cache[b]

                    fg, bg = self.parse_style(style)

                    # Only configure tags with valid colors
                    if fg and fg.startswith("#"):
                        try:
                            if bg and bg.startswith("#"):
                                self.tag_configure(
                                    str(token),
                                    foreground=fg,
                                    background=bg,
                                )
                            else:
                                self.tag_configure(str(token), foreground=fg)
                            successful_configs += 1
                        except tk.TclError as tcl_error:
                            print(
                                f"Tkinter error configuring token {token} with fg={fg}, bg={bg}: {tcl_error}",
                            )
                            failed_configs += 1
                            continue

                except Exception as token_error:
                    print(
                        f"Error configuring token {token} with style '{style}': {token_error}",
                    )
                    failed_configs += 1
                    continue

            print(
                f"Theme configuration complete: {successful_configs} successful, {failed_configs} failed",
            )

        except Exception as e:
            print(f"Error configuring theme tags: {e}")
            import traceback

            traceback.print_exc()

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

    def parse_style(
        self,
        style: Union[str, dict[str, str]],
    ) -> Tuple[str, Optional[str]]:
        fg: Optional[str] = None
        bg: Optional[str] = None

        if isinstance(style, str):
            parts = style.split()
            for part in parts:
                part = part.strip()
                if part.startswith("bg:"):
                    bg = part.split("bg:")[1]
                elif part.startswith("border:"):
                    # Skip border properties as Tkinter doesn't support them
                    continue
                elif part.startswith("#"):
                    # This is a color hex code
                    fg = part
                elif part in ["bold", "italic", "underline"]:
                    # Handle text formatting (could be extended later)
                    continue
                elif part.startswith("color:"):
                    # Handle CSS-style color property
                    fg = part.split("color:")[1]
                elif part.startswith("background:") or part.startswith(
                    "background-color:",
                ):
                    # Handle CSS-style background property
                    bg = part.split(":")[-1]

        elif isinstance(style, dict):
            fg = style.get("color")
            bg = style.get("bgcolor")

        # Set default foreground color if not found
        if not fg:
            fg = self.fg_color

        # Validate colors - ensure they start with # and are valid hex
        if fg and not fg.startswith("#"):
            if fg.startswith("color:"):
                fg = fg.split("color:")[-1]
            # If it's still not a valid hex color, use default
            if not (
                fg.startswith("#")
                and len(fg) in [4, 7]
                and all(c in "0123456789abcdefABCDEF" for c in fg[1:])
            ):
                fg = "#ffffff"

        if bg and not bg.startswith("#"):
            if bg.startswith("bg:"):
                bg = bg.split("bg:")[-1]
            # If it's not a valid hex color, ignore it
            if not (
                bg.startswith("#")
                and len(bg) in [4, 7]
                and all(c in "0123456789abcdefABCDEF" for c in bg[1:])
            ):
                bg = None

        return fg, bg

    def set_text(self, text: str) -> None:
        """Set text and highlight it."""
        self.delete("1.0", tk.END)
        self.insert("1.0", text)
        # Reset tracking variables since we're setting entirely new content
        self.last_highlighted_content = ""
        self.last_highlighted_length = 0
        self.highlight_text_full()  # Use full highlight for completely new content

    def highlight_text(self) -> None:
        """Main highlighting method - now uses incremental approach."""
        if not self.highlighting_enabled:
            return

        # Use incremental highlighting for better performance
        self.highlight_text_incremental()

    def highlight_text_incremental(self) -> None:
        """Only highlight changed portions of the text."""
        if self.highlighting_in_progress:
            return

        current_content = self.get("1.0", tk.END)
        current_length = len(current_content)

        # If content is significantly different, do full highlight
        if (
            not self.last_highlighted_content
            or current_length < self.last_highlighted_length * 0.8
            or current_length == 0
        ):
            self.highlight_text_full()
            return

        # Find where content changed
        change_start = self._find_change_start(
            self.last_highlighted_content,
            current_content,
        )

        # If no changes detected
        if change_start >= min(
            len(self.last_highlighted_content),
            len(current_content),
        ):
            if current_length > self.last_highlighted_length:
                # Only new content added at end
                self._highlight_from_char_position(self.last_highlighted_length)
            return

        # Check if the change is within a code block and adjust start position
        adjusted_change_start = self._adjust_change_start_for_code_block(
            change_start,
            current_content,
        )

        # Highlight from adjusted change point
        self._highlight_from_char_position(adjusted_change_start)

        # Update tracking variables
        self.last_highlighted_content = current_content
        self.last_highlighted_length = current_length

    def _adjust_change_start_for_code_block(
        self,
        change_start: int,
        content: str,
    ) -> int:
        """Adjust change start position to beginning of code block if change is within one."""
        try:
            # Parse all code blocks in the content
            code_blocks = parse_code_blocks(content)

            if not code_blocks:
                return change_start

            # Convert character position to line number
            lines_before_change = content[:change_start].count("\n")
            change_line = lines_before_change + 1  # 1-based line numbering

            # Check if the change is within any code block
            for indent_level, language, start_line, end_line in code_blocks:
                if start_line <= change_line <= end_line:
                    # Change is within this code block, adjust to start of block
                    # Convert start_line back to character position
                    lines = content.split("\n")
                    char_pos = 0
                    for i in range(start_line - 1):  # -1 because start_line is 1-based
                        if i < len(lines):
                            char_pos += len(lines[i]) + 1  # +1 for newline

                    # Return the character position of the code block start
                    # But don't go beyond the original change start
                    return min(change_start, char_pos)

            # No code block contains the change, return original position
            return change_start

        except Exception as e:
            print(f"Error adjusting change start for code block: {e}")
            return change_start

    def _highlight_from_char_position(self, char_pos: int) -> None:
        """Highlight from a character position to the end."""
        try:
            # Convert character position to Tkinter index
            tk_index = self._char_pos_to_tk_index(char_pos)
            if tk_index:
                self.highlight_region(tk_index, tk.END)
        except Exception as e:
            print(f"Error in incremental highlighting: {e}")
            self.highlight_text_full()

    def _char_pos_to_tk_index(self, char_pos: int) -> Optional[str]:
        """Convert character position to Tkinter line.column format."""
        try:
            # Get content without the automatic trailing newline
            content = self.get("1.0", "end-1c")
            if "\r" in content:
                print("in content")

            # Handle boundary cases
            if char_pos < 0:
                return "1.0"
            if char_pos >= len(content):
                return self.index("end-1c")

            # Split content into lines to handle multiple newlines correctly
            lines = content.split("\n")

            current_pos = 0
            for line_num, line in enumerate(lines, 1):
                line_end = current_pos + len(line)

                if char_pos <= line_end:
                    col_pos = char_pos - current_pos
                    tk_index = f"{line_num}.{col_pos}"

                    # Validate before returning
                    try:
                        self.index(tk_index)
                        return tk_index
                    except tk.TclError:
                        return None

                # +1 for the newline character
                current_pos = line_end + 1

            return None

        except (tk.TclError, ValueError, IndexError):
            return None

    def _find_change_start(self, old_content: str, new_content: str) -> int:
        """Find the character position where content starts to differ."""
        min_length = min(len(old_content), len(new_content))

        for i in range(min_length):
            if old_content[i] != new_content[i]:
                return i

        # Content is identical up to the shorter length
        return min_length

    def highlight_region(self, start_index: str, end_index: str) -> None:
        """Highlight a specific region without affecting the rest."""
        if self.highlighting_in_progress:
            return

        self.highlighting_in_progress = True

        try:
            # Save selection if it exists
            has_selection = False
            sel_start = None
            sel_end = None
            try:
                sel_start = self.index(tk.SEL_FIRST)
                sel_end = self.index(tk.SEL_LAST)
                has_selection = True
            except tk.TclError:
                pass

            # Only remove syntax tags in the region we're updating
            # Keep important tags like selection
            for tag in self.tag_names():
                if tag not in ["sel", "highlight", "separator", "default"]:
                    self.tag_remove(tag, start_index, end_index)

            # Get text for the region
            region_text = self.get(start_index, end_index)

            if not region_text.strip():
                return

            leading = count_leading_chars(region_text, "\n")
            region_text = region_text.lstrip("\n")

            # Tokenize only the changed region
            tokens = self.token_cache.get_tokens(region_text, self.lexer)

            # Apply highlighting to the region
            current_pos = (
                str(int(start_index.split(".")[0]) + leading)
                + str(".")
                + start_index.split(".")[1]
            )

            # pygments ignores leading \n, the above aligns tkinter's view with pygments view (DO NOT CHANGE)

            for token, content in tokens:
                # if content:
                content_length = len(content)
                try:
                    end_pos = self.index(f"{current_pos} + {content_length}c")
                    self.tag_add(str(token), current_pos, end_pos)
                    current_pos = end_pos
                except tk.TclError:
                    # If we can't calculate the position, break out
                    print("breaking")
                    break

            # Restore selection if it existed
            if has_selection and sel_start and sel_end:
                try:
                    self.tag_add(tk.SEL, sel_start, sel_end)
                except tk.TclError:
                    pass

        except Exception as e:
            print(f"Error during regional highlighting: {e}")
        finally:
            self.highlighting_in_progress = False

    def highlight_text_full(self) -> None:
        """Full highlighting - fallback for when incremental won't work."""
        if self.highlighting_in_progress:
            return

        self.highlighting_in_progress = True

        try:
            # Save the current selection if any
            try:
                sel_start = self.index(tk.SEL_FIRST)
                sel_end = self.index(tk.SEL_LAST)
                has_selection = True
            except tk.TclError:
                has_selection = False

            # Remove ALL syntax tags except selection and special ones
            for tag in self.tag_names():
                if tag not in ["sel", "highlight", "separator", "default"]:
                    self.tag_remove(tag, "1.0", "end")

            # Reapply default tag
            self.tag_add("default", "1.0", "end")

            # Get text and tokenize
            text = self.get("1.0", tk.END)
            if not text.strip():
                return

            # Apply syntax highlighting
            self.mark_set("highlight_start", "1.0")
            try:
                tokens = self.token_cache.get_tokens(text, self.lexer)
                for token, content in tokens:
                    if content:
                        content_length = len(content)
                        end_index = self.index(f"highlight_start + {content_length}c")
                        self.tag_add(str(token), "highlight_start", end_index)
                        self.mark_set("highlight_start", end_index)

            except Exception as e:
                print(f"Error during full highlighting: {e}")
            finally:
                try:
                    self.mark_unset("highlight_start")
                except:
                    pass

            # Restore the selection if it existed
            if has_selection:
                try:
                    self.tag_add(tk.SEL, sel_start, sel_end)
                except:
                    pass

            # Update tracking variables
            current_content = self.get("1.0", tk.END)
            self.last_highlighted_content = current_content
            self.last_highlighted_length = len(current_content)

            # Force a visual update
            self.update_idletasks()

        finally:
            self.highlighting_in_progress = False

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
