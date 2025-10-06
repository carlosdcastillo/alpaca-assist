import tkinter as tk
from typing import Optional
from typing import Tuple
from typing import Union

from pygments.styles import get_all_styles  # type: ignore
from pygments.styles import get_style_by_name  # type: ignore

from text_utils import backoff
from text_utils import count_leading_chars
from text_utils import parse_code_blocks


class SyntaxHighlightingMixin:
    """Mixin class containing all syntax highlighting functionality."""

    def configure_initial_theme_tags(self) -> None:
        self.configure_theme_tags()

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

            # Track normal text color for comparison with bold
            normal_text_color = None

            # First pass: identify normal text color
            for token, style in styles_dict.items():
                if style != "":
                    cache[str(token)] = style

                if style == "":
                    b = backoff(str(token))
                    if b in cache:
                        style = cache[b]

                fg, bg = self.parse_style(style)

                # Track normal text color (Token.Text or Token)
                token_name = str(token)
                if token_name in ["Token.Text", "Token"] and fg:
                    normal_text_color = fg
                    break

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

                    # Check if this is a bold token that needs blue enhancement
                    token_name = str(token)
                    is_bold_token = (
                        "Strong" in token_name
                        or "Bold" in token_name
                        or token_name == "Token.Generic.Strong"
                        or token_name == "Token.Generic.Heading"
                        or token_name == "Token.Generic.Subheading"
                    )

                    # print(f"{token_name}: {is_bold_token}")
                    # If this is a bold token and its color matches normal text, make it more blue
                    if (
                        is_bold_token
                        and fg
                        and normal_text_color
                        and fg == normal_text_color
                    ):
                        # if is_bold_token:
                        fg = self._make_color_more_blue(fg)
                        # print(f"-----> {token_name}, {fg}")

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

    def _make_color_more_blue(self, color: str) -> str:
        """Make a color more blue-ish by increasing the blue component."""
        try:
            if color.lower() == "#000000":
                return "#4444dd"
            # Parse hex color
            if color.startswith("#"):
                hex_color = color[1:]

                # Handle both 3 and 6 character hex codes
                if len(hex_color) == 3:
                    r = int(hex_color[0] * 2, 16)
                    g = int(hex_color[1] * 2, 16)
                    b = int(hex_color[2] * 2, 16)
                elif len(hex_color) == 6:
                    r = int(hex_color[0:2], 16)
                    g = int(hex_color[2:4], 16)
                    b = int(hex_color[4:6], 16)
                else:
                    return color

                # Increase blue component and slightly decrease red/green
                # This creates a more noticeable blue tint
                r = max(0, int(r * 0.7))  # Reduce red by 30%
                g = max(0, int(g * 0.7))  # Reduce green by 30%
                b = min(255, int(b * 1.5) + 80)  # Increase blue by 50% and add 80

                # Return new color
                return f"#{r:02x}{g:02x}{b:02x}"

        except Exception as e:
            print(f"Error making color more blue: {e}")

        return color

    def parse_style(
        self,
        style: str | dict[str, str],
    ) -> tuple[str, str | None]:
        fg: str | None = None
        bg: str | None = None

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

    def _char_pos_to_tk_index(self, char_pos: int) -> str | None:
        """Convert character position to Tkinter line.column format, returning start of line."""
        try:
            # Get content without the automatic trailing newline
            content = self.get("1.0", "end-1c")
            if "\r" in content:
                print("Warning: Found \\r in content")

            # Handle boundary cases
            if char_pos < 0:
                return "1.0"
            if char_pos >= len(content):
                # Return start of last line
                lines = content.split("\n")
                return f"{len(lines)}.0"

            # Split content into lines using \n only
            lines = content.split("\n")

            current_pos = 0
            for line_num, line in enumerate(lines, 1):
                line_end = current_pos + len(line)

                if char_pos <= line_end:
                    # Return start of this line instead of exact position
                    tk_index = f"{line_num}.0"

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
            # Get text for the region and check if we should skip
            region_text = self.get(start_index, end_index)
            region_text = region_text.replace("\r\n", "\n").replace("\r", "\n")

            if not region_text.strip():
                return

            should_skip, reason = self._should_skip_highlighting(region_text)
            # if should_skip:
            #     print(f"Skipping regional highlighting: {reason}")
            #     # Just apply default formatting to the region
            #     self.tag_add("default", start_index, end_index)
            #     return

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

            leading = count_leading_chars(region_text, "\n")
            region_text = region_text.lstrip("\n")

            # Tokenize only the changed region
            tokens = self.token_cache.get_tokens(region_text, self.lexer)

            # Apply highlighting to the region
            current_pos = (
                str(int(start_index.split(".")[0]) + leading)
                + "."
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
            # Get text and check if we should skip highlighting
            text = self.get("1.0", tk.END)
            if not text.strip():
                return

            # should_skip, reason = self._should_skip_highlighting(text)
            # if should_skip:
            #     self.skip_highlighting_reason = reason
            #     print(f"Skipping highlighting: {reason}")
            #     # Just apply default formatting
            #     self.tag_add("default", "1.0", "end")
            #     return

            self.skip_highlighting_reason = None

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
