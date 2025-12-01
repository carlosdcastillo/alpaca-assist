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
            self._clear_existing_tags()

            if not self._validate_style():
                return

            styles_dict = self.style.styles
            print(f"Configuring tags for {len(styles_dict)} style entries")

            cache: dict[str, str] = {}
            normal_text_color = self._find_normal_text_color(styles_dict, cache)
            successful_configs, failed_configs = self._configure_all_tokens(
                styles_dict,
                cache,
                normal_text_color,
            )

            print(
                f"Theme configuration complete: {successful_configs} successful, {failed_configs} failed",
            )

        except Exception as e:
            print(f"Error configuring theme tags: {e}")
            import traceback

            traceback.print_exc()

    def _clear_existing_tags(self) -> None:
        """Clear existing tags but preserve important ones."""
        for tag in self.tag_names():
            if tag not in ["sel", "highlight", "separator", "default"]:
                self.tag_delete(tag)

    def _validate_style(self) -> bool:
        """Validate that style is properly configured."""
        if self.style is None:
            print("Style is None, cannot configure tags")
            return False

        if not hasattr(self.style, "styles"):
            print("Style has no 'styles' attribute")
            return False

        styles_dict = self.style.styles
        if styles_dict is None:
            print("Style.styles is None")
            return False

        if not isinstance(styles_dict, dict):
            print(f"Style.styles is not a dict, it's: {type(styles_dict)}")
            return False

        return True

    def _find_normal_text_color(
        self,
        styles_dict: dict,
        cache: dict[str, str],
    ) -> str | None:
        """Find the normal text color for comparison with bold tokens."""
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
                return fg

        return None

    def _configure_all_tokens(
        self,
        styles_dict: dict,
        cache: dict[str, str],
        normal_text_color: str | None,
    ) -> tuple[int, int]:
        """Configure tags for all tokens in the style dictionary."""
        successful_configs = 0
        failed_configs = 0

        for token, style in styles_dict.items():
            try:
                processed_style = self._process_token_style(token, style, cache)
                fg, bg = self.parse_style(processed_style)

                # Apply blue enhancement for bold tokens if needed
                fg = self._apply_bold_enhancement(token, fg, normal_text_color)

                # Configure the tag if we have valid colors
                if self._configure_token_tag(token, fg, bg):
                    successful_configs += 1
                else:
                    failed_configs += 1

            except Exception as token_error:
                print(
                    f"Error configuring token {token} with style '{style}': {token_error}",
                )
                failed_configs += 1

        return successful_configs, failed_configs

    def _process_token_style(self, token, style: str, cache: dict[str, str]) -> str:
        """Process and cache token style."""
        if style != "":
            cache[str(token)] = style
            return style

        # Use backoff to find cached style
        b = backoff(str(token))
        if b in cache:
            return cache[b]

        return style

    def _apply_bold_enhancement(
        self,
        token,
        fg: str | None,
        normal_text_color: str | None,
    ) -> str | None:
        """Apply blue enhancement to bold tokens if they match normal text color."""
        if not fg or not normal_text_color:
            return fg

        token_name = str(token)
        is_bold_token = (
            "Strong" in token_name
            or "Bold" in token_name
            or token_name == "Token.Generic.Strong"
            or token_name == "Token.Generic.Heading"
            or token_name == "Token.Generic.Subheading"
        )

        if is_bold_token and fg == normal_text_color:
            return self._make_color_more_blue(fg)

        return fg

    def _configure_token_tag(self, token, fg: str | None, bg: str | None) -> bool:
        """Configure a single token tag with the given colors."""
        if not fg or not fg.startswith("#"):
            return False

        try:
            if bg and bg.startswith("#"):
                self.tag_configure(str(token), foreground=fg, background=bg)
            else:
                self.tag_configure(str(token), foreground=fg)
            return True
        except tk.TclError as tcl_error:
            print(
                f"Tkinter error configuring token {token} with fg={fg}, bg={bg}: {tcl_error}",
            )
            return False

    def _make_color_more_blue(self, color: str) -> str:
        """Make a color more blue-ish by increasing the blue component."""
        try:
            if color.lower() == "#000000":
                return "#4444dd"

            if not color.startswith("#"):
                return color

            return self._process_hex_color(color)

        except Exception as e:
            print(f"Error making color more blue: {e}")
            return color

    def _process_hex_color(self, color: str) -> str:
        """Process hex color to make it more blue."""
        hex_color = color[1:]

        if len(hex_color) == 3:
            r, g, b = self._parse_short_hex(hex_color)
        elif len(hex_color) == 6:
            r, g, b = self._parse_long_hex(hex_color)
        else:
            return color

        # Increase blue component and slightly decrease red/green
        r = max(0, int(r * 0.7))  # Reduce red by 30%
        g = max(0, int(g * 0.7))  # Reduce green by 30%
        b = min(255, int(b * 1.5) + 80)  # Increase blue by 50% and add 80

        return f"#{r:02x}{g:02x}{b:02x}"

    def _parse_short_hex(self, hex_color: str) -> tuple[int, int, int]:
        """Parse 3-character hex color."""
        r = int(hex_color[0] * 2, 16)
        g = int(hex_color[1] * 2, 16)
        b = int(hex_color[2] * 2, 16)
        return r, g, b

    def _parse_long_hex(self, hex_color: str) -> tuple[int, int, int]:
        """Parse 6-character hex color."""
        r = int(hex_color[0:2], 16)
        g = int(hex_color[2:4], 16)
        b = int(hex_color[4:6], 16)
        return r, g, b

    def parse_style(
        self,
        style: str | dict[str, str],
    ) -> tuple[str, str | None]:
        """Parse style string or dict into foreground and background colors."""
        if isinstance(style, str):
            return self._parse_string_style(style)
        elif isinstance(style, dict):
            return self._parse_dict_style(style)

        return self.fg_color, None

    def _parse_string_style(self, style: str) -> tuple[str, str | None]:
        """Parse string-based style."""
        fg: str | None = None
        bg: str | None = None

        parts = style.split()
        for part in parts:
            part = part.strip()
            fg, bg = self._process_style_part(part, fg, bg)

        return self._validate_and_default_colors(fg, bg)

    def _parse_dict_style(self, style: dict[str, str]) -> tuple[str, str | None]:
        """Parse dictionary-based style."""
        fg = style.get("color")
        bg = style.get("bgcolor")

        return self._validate_and_default_colors(fg, bg)

    def _process_style_part(
        self,
        part: str,
        fg: str | None,
        bg: str | None,
    ) -> tuple[str | None, str | None]:
        """Process a single part of a style string."""
        if part.startswith("bg:"):
            bg = part.split("bg:")[1]
        elif part.startswith("border:"):
            # Skip border properties as Tkinter doesn't support them
            pass
        elif part.startswith("#"):
            # This is a color hex code
            fg = part
        elif part in ["bold", "italic", "underline"]:
            # Handle text formatting (could be extended later)
            pass
        elif part.startswith("color:"):
            # Handle CSS-style color property
            fg = part.split("color:")[1]
        elif part.startswith("background:") or part.startswith("background-color:"):
            # Handle CSS-style background property
            bg = part.split(":")[-1]

        return fg, bg

    def _validate_and_default_colors(
        self,
        fg: str | None,
        bg: str | None,
    ) -> tuple[str, str | None]:
        """Validate colors and apply defaults."""
        # Set default foreground color if not found
        if not fg:
            fg = self.fg_color

        # Validate and fix foreground color
        fg = self._validate_color(fg, "#ffffff")

        # Validate background color
        bg = self._validate_color(bg, None)

        return fg, bg

    def _validate_color(self, color: str | None, default: str | None) -> str | None:
        """Validate a color string and return default if invalid."""
        if not color:
            return default

        if not color.startswith("#"):
            if color.startswith("color:"):
                color = color.split("color:")[-1]
            elif color.startswith("bg:"):
                color = color.split("bg:")[-1]

        # Check if it's a valid hex color
        if not (
            color.startswith("#")
            and len(color) in [4, 7]
            and all(c in "0123456789abcdefABCDEF" for c in color[1:])
        ):
            return default

        return color

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

        # Check if we need full highlighting
        if self._should_do_full_highlight(current_length):
            self.highlight_text_full()
            return

        # Find and process changes
        self._process_incremental_changes(current_content, current_length)

    def _should_do_full_highlight(self, current_length: int) -> bool:
        """Determine if we should do full highlighting instead of incremental."""
        return (
            not self.last_highlighted_content
            or current_length < self.last_highlighted_length * 0.8
            or current_length == 0
        )

    def _process_incremental_changes(
        self,
        current_content: str,
        current_length: int,
    ) -> None:
        """Process incremental changes to the content."""
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
            code_blocks = parse_code_blocks(content)
            if not code_blocks:
                return change_start

            change_line = self._get_change_line(change_start, content)
            return self._find_code_block_start(
                code_blocks,
                change_line,
                change_start,
                content,
            )

        except Exception as e:
            print(f"Error adjusting change start for code block: {e}")
            return change_start

    def _get_change_line(self, change_start: int, content: str) -> int:
        """Convert character position to line number."""
        lines_before_change = content[:change_start].count("\n")
        return lines_before_change + 1  # 1-based line numbering

    def _find_code_block_start(
        self,
        code_blocks: list,
        change_line: int,
        change_start: int,
        content: str,
    ) -> int:
        """Find the start of code block if change is within one."""
        for indent_level, language, start_line, end_line in code_blocks:
            if start_line <= change_line <= end_line:
                # Change is within this code block, adjust to start of block
                char_pos = self._line_to_char_pos(start_line, content)
                return min(change_start, char_pos)

        return change_start

    def _line_to_char_pos(self, line_num: int, content: str) -> int:
        """Convert line number to character position."""
        lines = content.split("\n")
        char_pos = 0
        for i in range(line_num - 1):  # -1 because line_num is 1-based
            if i < len(lines):
                char_pos += len(lines[i]) + 1  # +1 for newline
        return char_pos

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
            content = self.get("1.0", "end-1c")
            if "\r" in content:
                print("Warning: Found \\r in content")

            # Handle boundary cases
            if char_pos < 0:
                return "1.0"

            if char_pos >= len(content):
                lines = content.split("\n")
                return f"{len(lines)}.0"

            return self._find_line_for_char_pos(char_pos, content)

        except (tk.TclError, ValueError, IndexError):
            return None

    def _find_line_for_char_pos(self, char_pos: int, content: str) -> str | None:
        """Find the line that contains the given character position."""
        lines = content.split("\n")
        current_pos = 0

        for line_num, line in enumerate(lines, 1):
            line_end = current_pos + len(line)

            if char_pos <= line_end:
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
            region_text = self._prepare_region_text(start_index, end_index)
            if not region_text:
                return

            selection_info = self._save_selection()
            self._clean_region_tags(start_index, end_index)
            self._apply_region_highlighting(region_text, start_index)
            self._restore_selection(selection_info)

        except Exception as e:
            print(f"Error during regional highlighting: {e}")
        finally:
            self.highlighting_in_progress = False

    def _prepare_region_text(self, start_index: str, end_index: str) -> str | None:
        """Prepare region text for highlighting."""
        region_text = self.get(start_index, end_index)
        region_text = region_text.replace("\r\n", "\n").replace("\r", "\n")

        if not region_text.strip():
            return None

        should_skip, reason = self._should_skip_highlighting(region_text)
        return region_text

    def _save_selection(self) -> dict:
        """Save current selection state."""
        try:
            sel_start = self.index(tk.SEL_FIRST)
            sel_end = self.index(tk.SEL_LAST)
            return {"has_selection": True, "start": sel_start, "end": sel_end}
        except tk.TclError:
            return {"has_selection": False}

    def _restore_selection(self, selection_info: dict) -> None:
        """Restore selection state."""
        if selection_info["has_selection"]:
            try:
                self.tag_add(tk.SEL, selection_info["start"], selection_info["end"])
            except tk.TclError:
                pass

    def _clean_region_tags(self, start_index: str, end_index: str) -> None:
        """Clean syntax tags from the region."""
        for tag in self.tag_names():
            if tag not in ["sel", "highlight", "separator", "default"]:
                self.tag_remove(tag, start_index, end_index)

    def _apply_region_highlighting(self, region_text: str, start_index: str) -> None:
        """Apply syntax highlighting to the region."""
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

        for token, content in tokens:
            content_length = len(content)
            try:
                end_pos = self.index(f"{current_pos} + {content_length}c")
                self.tag_add(str(token), current_pos, end_pos)
                current_pos = end_pos
            except tk.TclError:
                print("breaking")
                break

    def highlight_text_full(self) -> None:
        """Full highlighting - fallback for when incremental won't work."""
        if self.highlighting_in_progress:
            return

        self.highlighting_in_progress = True

        try:
            text = self.get("1.0", tk.END)
            if not text.strip():
                return

            self.skip_highlighting_reason = None

            selection_info = self._save_selection()
            self._clean_all_syntax_tags()
            self._apply_full_highlighting(text)
            self._restore_selection(selection_info)
            self._update_highlighting_state(text)

        finally:
            self.highlighting_in_progress = False

    def _clean_all_syntax_tags(self) -> None:
        """Remove all syntax tags except selection and special ones."""
        for tag in self.tag_names():
            if tag not in ["sel", "highlight", "separator", "default"]:
                self.tag_remove(tag, "1.0", "end")

        # Reapply default tag
        self.tag_add("default", "1.0", "end")

    def _apply_full_highlighting(self, text: str) -> None:
        """Apply syntax highlighting to the entire text."""
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

    def _update_highlighting_state(self, text: str) -> None:
        """Update tracking variables after highlighting."""
        current_content = self.get("1.0", tk.END)
        self.last_highlighted_content = current_content
        self.last_highlighted_length = len(current_content)

        # Force a visual update
        self.update_idletasks()

    def highlight_code_block(self, start: str, end: str) -> None:
        """Highlight a code block temporarily."""
        # Remove any existing highlight
        self.tag_remove("highlight", "1.0", tk.END)

        # Get the text of the code block
        code_block = self.get(start, end)
        lines = code_block.split("\n")

        # Find the content lines (non-backtick lines)
        first_line, last_line = self._find_content_lines(lines)

        # Calculate the new start and end positions
        new_start = f"{int(start.split('.')[0]) + first_line}.0"
        new_end = f"{int(start.split('.')[0]) + last_line}.end"

        # Apply and schedule removal of highlight
        self._apply_temporary_highlight(new_start, new_end)

    def _find_content_lines(self, lines: list[str]) -> tuple[int, int]:
        """Find the first and last non-backtick lines."""
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
        return first_line, last_line

    def _apply_temporary_highlight(self, start: str, end: str) -> None:
        """Apply temporary highlight and schedule its removal."""
        self.tag_add("highlight", start, end)
        self.tag_configure("highlight", background="yellow", foreground="black")

        # Schedule the removal of the highlight
        self.after(500, lambda: self.tag_remove("highlight", start, end))
