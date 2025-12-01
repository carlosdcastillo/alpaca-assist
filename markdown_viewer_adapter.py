import re
import sys
import tkinter as tk
import tkinter.font as tkFont
from tkinter import scrolledtext
from typing import Any
from typing import Optional

from pygments import lex
from pygments.lexers import get_lexer_by_name
from pygments.lexers import guess_lexer
from pygments.styles import get_all_styles
from pygments.styles import get_style_by_name
from pygments.token import Token

from text_utils import backoff

"""
MarkdownViewerAdapter - Compatibility wrapper for MarkdownViewer

Provides a SyntaxHighlightedText-compatible interface for MarkdownViewer,
allowing it to be used as a drop-in replacement for chat display.
"""

TOKEN_COLORS = {
    Token.Comment: "#6f42c1",
    Token.Comment.Single: "#6f42c1",
    Token.Comment.Multiline: "#6f42c1",
    Token.Keyword: "#d73a49",
    Token.Keyword.Namespace: "#d73a49",
    Token.Keyword.Type: "#d73a49",
    Token.String: "#032f62",
    Token.String.Double: "#032f62",
    Token.String.Single: "#032f62",
    Token.String.Doc: "#032f62",
    Token.Number: "#005cc5",
    Token.Number.Integer: "#005cc5",
    Token.Number.Float: "#005cc5",
    Token.Name: "#24292e",
    Token.Name.Function: "#6f42c1",
    Token.Name.Class: "#6f42c1",
    Token.Name.Builtin: "#005cc5",
    Token.Operator: "#d73a49",
    Token.Punctuation: "#24292e",
    Token.Whitespace: "#24292e",
    Token.Text: "#24292e",
}


class MarkdownViewerAdapter:
    """
    Adapter that provides a SyntaxHighlightedText-compatible interface using
    markdown rendering capabilities.

    This adapter maintains an internal plain-text buffer and provides methods
    that map the SyntaxHighlightedText interface to markdown rendering.
    """

    def _configure_syntax_tags(self) -> None:
        """Configure syntax highlighting tags using Pygments theme system."""
        try:
            self._clear_syntax_tags()

            if not self._validate_style():
                return

            styles_dict = self.style.styles
            print(f"Configuring syntax tags for {len(styles_dict)} style entries")

            cache: dict[str, str] = {}
            normal_text_color = self._find_normal_text_color(styles_dict, cache)
            successful_configs, failed_configs = self._configure_all_tokens(
                styles_dict,
                cache,
                normal_text_color,
            )

            print(
                f"Syntax theme configuration complete: {successful_configs} successful, {failed_configs} failed",
            )

        except Exception as e:
            print(f"Error configuring syntax theme tags: {e}")
            import traceback

            traceback.print_exc()

    def _get_token_tag(self, token_type: Token) -> str:
        """Get the tag name for a token type."""
        # Use the token type string directly as the tag name
        return str(token_type)

    def set_server_mode(self, enabled: bool) -> None:
        """Set server mode (no-op for compatibility)."""
        self._server_mode = enabled

    def highlight_text(self) -> None:
        """Highlight text (no-op for markdown viewer - rendering is automatic)."""
        pass

    def ensure_caret_enabled(self) -> None:
        """Ensure caret is enabled (no-op for markdown viewer)."""
        pass

    def config(self, **kwargs: Any) -> None:
        """Configure widget properties."""
        if "state" in kwargs:
            state = kwargs["state"]
            self.text_widget.config(state=state)

    def insert(self, index: str, text: str) -> None:
        """Insert text at the specified index."""
        self._content_buffer += text
        # Just append text directly without re-rendering
        # This is fast and responsive for streaming
        self.text_widget.insert(tk.END, text, "paragraph")

    def delete(self, index1: str, index2: str) -> None:
        """Delete text between two indices."""
        self._content_buffer = ""
        self._rendered_display_buffer = ""
        self.text_widget.config(state=tk.NORMAL)
        self.text_widget.delete("1.0", tk.END)

    def get(self, index1: str, index2: str) -> str:
        """Get text between two indices - returns the rendered display text, not markdown."""
        return self._rendered_display_buffer

    def bind(self, sequence: str, func, add: str | None = None) -> None:
        """Bind an event to a function."""
        self.text_widget.bind(sequence, func, add=add)

    def pack(self, **kwargs: Any) -> None:
        """Pack the text widget."""
        self.text_widget.pack(**kwargs)

    def pack_forget(self) -> None:
        """Forget the pack geometry."""
        self.text_widget.pack_forget()

    def focus_set(self) -> None:
        """Set focus to the text widget."""
        self.text_widget.focus_set()

    def winfo_rootx(self) -> int:
        """Get the root X coordinate."""
        return self.text_widget.winfo_rootx()

    def winfo_rooty(self) -> int:
        """Get the root Y coordinate."""
        return self.text_widget.winfo_rooty()

    def bbox(self, index: str) -> tuple | None:
        """Get the bounding box of a character."""
        return self.text_widget.bbox(index)

    def index(self, position: str) -> str:
        """Get the index for a position."""
        return self.text_widget.index(position)

    def mark_set(self, mark_name: str, index: str) -> None:
        """Set a mark at an index."""
        self.text_widget.mark_set(mark_name, index)

    def see(self, index: str) -> None:
        """Scroll to make the index visible."""
        self.text_widget.see(index)

    def tag_configure(self, tag_name: str, **kwargs: Any) -> None:
        """Configure a text tag."""
        self.text_widget.tag_configure(tag_name, **kwargs)

    def yview(self) -> tuple:
        """Get the vertical view position."""
        return self.text_widget.yview()

    def xview(self) -> tuple:
        """Get the horizontal view position."""
        return self.text_widget.xview()

    def yview_moveto(self, fraction: float) -> None:
        """Move the vertical view to a specific fraction."""
        self.text_widget.yview_moveto(fraction)

    def xview_moveto(self, fraction: float) -> None:
        """Move the horizontal view to a specific fraction."""
        self.text_widget.xview_moveto(fraction)

    def cget(self, option: str) -> Any:
        """Get widget configuration option. Delegates to text widget."""
        try:
            return self.text_widget.cget(option)
        except tk.TclError:
            if option == "state":
                return tk.NORMAL
            return ""

    def cleanup(self) -> None:
        """Clean up resources."""
        pass

    def _render_inline_formatting(self, text: str, base_tag: str = "paragraph") -> None:
        """Render text with inline markdown formatting."""
        i = 0
        while i < len(text):
            if i < len(text) - 1 and text[i : i + 2] == "**":
                end = text.find("**", i + 2)
                if end != -1:
                    self._insert_text(text[i + 2 : end], "bold")
                    i = end + 2
                    continue

            if i < len(text) - 1 and text[i : i + 2] == "__":
                end = text.find("__", i + 2)
                if end != -1:
                    self._insert_text(text[i + 2 : end], "bold")
                    i = end + 2
                    continue

            if text[i] == "*" and i < len(text) - 1 and text[i + 1] != "*":
                end = text.find("*", i + 1)
                if end != -1 and (end == len(text) - 1 or text[end + 1] != "*"):
                    self._insert_text(text[i + 1 : end], "italic")
                    i = end + 1
                    continue

            if text[i] == "_" and i < len(text) - 1 and text[i + 1] != "_":
                end = text.find("_", i + 1)
                if end != -1 and (end == len(text) - 1 or text[end + 1] != "_"):
                    self._insert_text(text[i + 1 : end], "italic")
                    i = end + 1
                    continue

            if i < len(text) - 1 and text[i : i + 2] == "~~":
                end = text.find("~~", i + 2)
                if end != -1:
                    self._insert_text(text[i + 2 : end], "strikethrough")
                    i = end + 2
                    continue

            if text[i] == "`":
                end = text.find("`", i + 1)
                if end != -1:
                    self._insert_text(text[i + 1 : end], "code")
                    i = end + 1
                    continue

            if text[i] == "[":
                close_bracket = text.find("]", i)
                if (
                    close_bracket != -1
                    and close_bracket + 1 < len(text)
                    and text[close_bracket + 1] == "("
                ):
                    close_paren = text.find(")", close_bracket)
                    if close_paren != -1:
                        link_text = text[i + 1 : close_bracket]
                        self._insert_text(link_text, "link")
                        i = close_paren + 1
                        continue

            self._insert_text(text[i], base_tag)
            i += 1

    def _render_code_block(self, code_info: tuple) -> None:
        """Render a code block with syntax highlighting using Pygments theme."""
        language, code = code_info

        try:
            if language:
                lexer = get_lexer_by_name(language)
            else:
                lexer = guess_lexer(code)
        except:
            lexer = None

        if lexer:
            tokens = lex(code, lexer)
            for token_type, value in tokens:
                tag = self._get_token_tag(token_type)
                self._insert_text(value, tag)
        else:
            self._insert_text(code, "code_block")

        self._insert_text("\n", "code_block")

    def _render_blockquote(self, lines: list) -> None:
        """Render a blockquote."""
        for line in lines:
            self._insert_text(line + "\n", "blockquote")
        self._insert_text("\n", "paragraph")

    def _render_list(self, items: list, ordered: bool = False) -> None:
        """Render a list (ordered or unordered)."""
        for idx, item in enumerate(items):
            if ordered:
                bullet = f"{idx + 1}. "
            else:
                bullet = "• "

            self._insert_text(bullet, "list_item")
            self._render_inline_formatting(item, "list_item")
            self._insert_text("\n", "list_item")

        self._insert_text("\n", "paragraph")

    def _render_paragraph(self, lines: list) -> None:
        """Render a paragraph."""
        text = " ".join(line.strip() for line in lines if line.strip())
        if text:
            self._render_inline_formatting(text)
            self._insert_text("\n", "paragraph")

    def _render_table(self, table_data: list) -> None:
        """Render a markdown table."""
        if not table_data:
            return

        widths = self._get_column_widths(table_data)

        header = table_data[0]
        for idx, cell in enumerate(header):
            clean_cell = self._remove_inline_formatting_markers(cell)
            padding_len = widths[idx] - len(clean_cell)
            self._render_inline_formatting(cell, "table_header")
            if padding_len > 0:
                self._insert_text(" " * padding_len, "table_header")
            if idx < len(header) - 1:
                self._insert_text(" │ ", "table_header")
        self._insert_text("\n", "table_header")

        sep = self._build_separator_line(widths)
        self._insert_text(sep, "table_separator")
        self._insert_text("\n", "table_separator")

        for row in table_data[1:]:
            for idx, cell in enumerate(row):
                clean_cell = self._remove_inline_formatting_markers(cell)
                padding_len = widths[idx] - len(clean_cell)
                self._render_inline_formatting(cell, "table_cell")
                if padding_len > 0:
                    self._insert_text(" " * padding_len, "table_cell")
                if idx < len(row) - 1:
                    self._insert_text(" │ ", "table_cell")
            self._insert_text("\n", "table_cell")

        self._insert_text("\n", "paragraph")

    def _extract_code_block(self, lines: list, start_idx: int) -> tuple:
        """Extract a code block from lines."""
        fence = lines[start_idx].strip()
        language = fence[3:].strip() if len(fence) > 3 else ""

        code_lines = []
        i = start_idx + 1

        while i < len(lines):
            if lines[i].strip().startswith("```"):
                return i + 1, (language, "\n".join(code_lines))
            code_lines.append(lines[i])
            i += 1

        return i, (language, "\n".join(code_lines))

    def _extract_blockquote(self, lines: list, start_idx: int) -> tuple:
        """Extract a blockquote from lines."""
        quote_lines = []
        i = start_idx

        while i < len(lines) and (lines[i].startswith("> ") or lines[i].strip() == ""):
            if lines[i].startswith("> "):
                quote_lines.append(lines[i][2:])
            elif (
                lines[i].strip() == ""
                and i + 1 < len(lines)
                and lines[i + 1].startswith("> ")
            ):
                quote_lines.append("")
            else:
                break
            i += 1

        return i, quote_lines

    def _extract_list(
        self,
        lines: list,
        start_idx: int,
        ordered: bool = False,
    ) -> tuple:
        """Extract a list from lines."""
        items = []
        i = start_idx

        while i < len(lines):
            line = lines[i].strip()

            if ordered:
                match = re.match(r"^(\d+)\. (.+)$", line)
                if match:
                    items.append(match.group(2))
                    i += 1
                    continue
            else:
                if line.startswith(("- ", "* ", "+ ")):
                    items.append(line[2:])
                    i += 1
                    continue

            if line == "":
                i += 1
                continue

            break

        return i, items

    def _extract_paragraph(self, lines: list, start_idx: int) -> tuple:
        """Extract a paragraph from lines."""
        para_lines = []
        i = start_idx

        while i < len(lines):
            line = lines[i]

            if (
                line.strip() == ""
                or line.startswith(("#", "```", "> ", "- ", "* ", "+ "))
                or self._is_table_row(line)
                or re.match(r"^\d+\. ", line.strip())
            ):
                break

            para_lines.append(line)
            i += 1

        return i, para_lines

    def _is_table_row(self, line: str) -> bool:
        """Check if a line is a table row."""
        line = line.strip()
        return line.startswith("|") and line.endswith("|") and "|" in line[1:-1]

    def _is_table_separator(self, line: str) -> bool:
        """Check if a line is a table separator."""
        line = line.strip()
        if not line.startswith("|") or not line.endswith("|"):
            return False
        cells = line.split("|")[1:-1]
        return all(re.match(r"^\s*:?-+:?\s*$", cell) for cell in cells)

    def _extract_table(self, lines: list, start_idx: int) -> tuple:
        """Extract a table from lines."""
        table_data = []
        i = start_idx

        if i < len(lines) and self._is_table_row(lines[i]):
            header_cells = [cell.strip() for cell in lines[i].split("|")[1:-1]]
            table_data.append(header_cells)
            i += 1
        else:
            return i, []

        if i < len(lines) and self._is_table_separator(lines[i]):
            i += 1
        else:
            return i, []

        while i < len(lines) and self._is_table_row(lines[i]):
            cells = [cell.strip() for cell in lines[i].split("|")[1:-1]]
            table_data.append(cells)
            i += 1

        return i, table_data

    def _remove_inline_formatting_markers(self, text: str) -> str:
        """Remove markdown formatting markers from text."""
        result = ""
        i = 0
        while i < len(text):
            if i < len(text) - 1 and text[i : i + 2] == "**":
                end = text.find("**", i + 2)
                if end != -1:
                    result += text[i + 2 : end]
                    i = end + 2
                    continue
            if i < len(text) - 1 and text[i : i + 2] == "__":
                end = text.find("__", i + 2)
                if end != -1:
                    result += text[i + 2 : end]
                    i = end + 2
                    continue
            if text[i] == "*" and i < len(text) - 1 and text[i + 1] != "*":
                end = text.find("*", i + 1)
                if end != -1 and (end == len(text) - 1 or text[end + 1] != "_"):
                    result += text[i + 1 : end]
                    i = end + 1
                    continue
            if text[i] == "_" and i < len(text) - 1 and text[i + 1] != "_":
                end = text.find("_", i + 1)
                if end != -1 and (end == len(text) - 1 or text[end + 1] != "_"):
                    result += text[i + 1 : end]
                    i = end + 1
                    continue
            if i < len(text) - 1 and text[i : i + 2] == "~~":
                end = text.find("~~", i + 2)
                if end != -1:
                    result += text[i + 2 : end]
                    i = end + 2
                    continue
            if text[i] == "`":
                end = text.find("`", i + 1)
                if end != -1:
                    result += text[i + 1 : end]
                    i = end + 1
                    continue
            if text[i] == "[":
                close_bracket = text.find("]", i)
                if (
                    close_bracket != -1
                    and close_bracket + 1 < len(text)
                    and text[close_bracket + 1] == "("
                ):
                    close_paren = text.find(")", close_bracket)
                    if close_paren != -1:
                        result += text[i + 1 : close_bracket]
                        i = close_paren + 1
                        continue
            result += text[i]
            i += 1
        return result

    def _get_column_widths(self, table_data: list) -> list:
        """Get column widths for table rendering."""
        if not table_data:
            return []

        num_cols = len(table_data[0])
        widths = [0] * num_cols

        for row in table_data:
            for col_idx, cell in enumerate(row):
                if col_idx < len(widths):
                    clean_cell = self._remove_inline_formatting_markers(cell)
                    widths[col_idx] = max(widths[col_idx], len(clean_cell))

        return widths

    def _build_separator_line(self, widths: list) -> str:
        """Build a table separator line."""
        parts = [" "]
        for idx, width in enumerate(widths):
            parts.append("─" * width)
            if idx < len(widths) - 1:
                parts.append(" │ ")
        return "".join(parts)

    def _copy_selection(self, event):
        """Handle copy selection."""
        try:
            selection = self.text_widget.get(tk.SEL_FIRST, tk.SEL_LAST)
            self.text_widget.master.clipboard_clear()
            self.text_widget.master.clipboard_append(selection)
        except tk.TclError:
            pass
        return "break"

    def finalize_rendering(self) -> None:
        """Finalize the rendering and apply final formatting."""
        # Save current scroll position
        current_scroll = self.text_widget.yview()[0]

        # Re-render with final content
        self._re_render()

        # Restore scroll position
        self.text_widget.yview_moveto(current_scroll)

    def update_font(self, font_family: str, font_size: int) -> None:
        """Update the font family and size."""
        try:
            import tkinter.font as tkfont

            test_font = tkfont.Font(family=font_family, size=font_size)
            test_font.metrics()

            self.font_family = font_family
            self.font_size = font_size
            self.text_widget.config(font=(font_family, font_size))
            # Reconfigure tags with new font
            self._configure_tags()
        except Exception as e:
            print(f"Error applying font {font_family} at size {font_size}: {e}")
            self.text_widget.config(font=("Courier", font_size))

    def _get_header_colors(self) -> list[str]:
        """Get header colors based on current theme."""
        if self.bg_color == "#000000":  # Dark theme
            return ["#ffffff", "#e8e8e8", "#d0d0d0", "#b8b8b8", "#a0a0a0", "#888888"]
        else:  # Light theme
            return ["#1a1a1a", "#2c3e50", "#34495e", "#7f8c8d", "#95a5a6", "#bdc3c7"]

    def _get_text_colors(self) -> dict[str, str]:
        """Get text colors based on current theme."""
        if self.bg_color == "#000000":  # Dark theme
            return {
                "bold": self._make_color_more_blue(self.fg_color),
                "italic": "#b19cd9",
                "strikethrough": "#888888",
                "code_fg": "#f8f8f2",
                "code_bg": "#2d2d2d",
                "code_block_fg": "#f8f8f2",
                "blockquote": "#888888",
                "link": "#6495ed",
                "select_bg": "#4A90E2",
                "select_fg": "#ffffff",
            }
        else:  # Light theme
            return {
                "bold": self._make_color_more_blue(self.fg_color),
                "italic": "#6f42c1",
                "strikethrough": "#888888",
                "code_fg": "#c7254e",
                "code_bg": "#f6f8fa",
                "code_block_fg": "#24292e",
                "blockquote": "#7f8c8d",
                "link": "#3498db",
                "select_bg": "#e8f0fe",
                "select_fg": "#24292e",
            }

    def _get_table_colors(self) -> dict[str, str]:
        """Get table colors based on current theme."""
        if self.bg_color == "#000000":  # Dark theme
            return {
                "bg": "#1a1a1a",
                "header_fg": "#ffffff",
                "cell_fg": "#e8e8e8",
                "separator_fg": "#666666",
            }
        else:  # Light theme
            return {
                "bg": "white",
                "header_fg": "#24292e",
                "cell_fg": "#24292e",
                "separator_fg": "#24292e",
            }

    def _clear_syntax_tags(self) -> None:
        """Clear existing syntax tags but preserve important ones."""
        for tag in self.text_widget.tag_names():
            if tag.startswith("Token.") or tag.startswith("token_"):
                self.text_widget.tag_delete(tag)

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

    def configure_selection_colors(self) -> None:
        """Configure selection colors for better readability."""
        if self.bg_color == "#000000":
            selection_bg = "#4A90E2"
            selection_fg = "#FFFFFF"
        else:
            selection_bg = "#316AC5"
            selection_fg = "#FFFFFF"

        self.text_widget.configure(
            selectbackground=selection_bg,
            selectforeground=selection_fg,
            inactiveselectbackground=selection_bg,
        )

    def tag_names(self):
        """Get all tag names."""
        return self.text_widget.tag_names()

    def update_theme(self, theme_name: str) -> None:
        """Update the syntax highlighting theme."""
        try:
            try:
                style = get_style_by_name(theme_name)
            except:
                try:
                    style = get_style_by_name("default")
                except:
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
            self.theme_name = theme_name

            # Reconfigure all tags with new theme - this updates both syntax and markdown tags
            self._configure_tags()

            # Force complete re-render of existing content to apply new theme
            self._re_render()

            print(
                f"Successfully updated theme to '{theme_name}' and re-rendered content",
            )
        except Exception as e:
            print(f"Error updating theme '{theme_name}': {e}")

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

        self.background_color = background_color
        self.text_widget.config(
            bg=self.bg_color,
            fg=self.fg_color,
            insertbackground=self.cursor_color,
        )
        self.configure_selection_colors()
        # Reconfigure all tags with new colors
        self._configure_tags()

    def tag_add(self, tag_name: str, index1: str, index2: str) -> None:
        """Add a tag to a range."""
        self.text_widget.tag_add(tag_name, index1, index2)

    def get_content_line_from_rendered_line(self, rendered_line_num: int) -> int:
        """
        Get the content line number for a given rendered line number.

        Time Complexity: O(n) where n = number of rendered lines
        Space Complexity: O(1)

        Args:
            rendered_line_num: Line number in the rendered display (0-indexed)

        Returns:
            The content line number (0-indexed), or -1 if not found
        """
        # Count newlines to find which line the rendered_line_num is on
        newline_count = 0
        char_count = 0

        for char_pos, content_line_num in enumerate(self._char_to_content_line_map):
            if self._rendered_display_buffer[char_pos] == "\n":
                if newline_count == rendered_line_num:
                    return content_line_num
                newline_count += 1

        # If we're on the last line, return the content line of the last character
        if newline_count == rendered_line_num and self._char_to_content_line_map:
            return self._char_to_content_line_map[-1]

        return -1

    def __init__(
        self,
        parent: tk.Widget,
        wrap: str = tk.WORD,
        height: int = 20,
        theme_name: str = "default",
        background_color: str = "white",
        font_family: str = "Menlo",
        font_size: int = 13,
        enable_streaming: bool = False,
        stream_delay: float = 0.05,
    ) -> None:
        """
        Initialize the MarkdownViewerAdapter.

        Args:
            parent: Parent widget (frame)
            wrap: Text wrapping mode (default: tk.WORD)
            height: Height of the text widget
            theme_name: Theme name for syntax highlighting
            background_color: Background color
            font_family: Font family name
            font_size: Font size
            enable_streaming: Enable streaming mode (not used currently)
            stream_delay: Delay between chunks in streaming mode (not used currently)
        """
        # Initialize theme and style first
        try:
            self.style = get_style_by_name(theme_name)
        except:
            try:
                self.style = get_style_by_name("default")
            except:
                available_themes = list(get_all_styles())
                self.style = (
                    get_style_by_name(available_themes[0]) if available_themes else None
                )

        # Set up color scheme based on background_color parameter
        if background_color == "black":
            self.bg_color = "#000000"
            self.fg_color = "#f8f8f2"
            self.cursor_color = "white"
        else:
            self.bg_color = "#ffffff"
            self.fg_color = "#000000"
            self.cursor_color = "black"

        # Create the text widget directly in the parent frame
        self.text_widget = scrolledtext.ScrolledText(
            parent,
            wrap=wrap,
            font=(font_family, font_size),
            bg=self.bg_color,
            fg=self.fg_color,
            insertbackground=self.cursor_color,
            height=height,
        )

        # Store configuration
        self.parent_frame = parent
        self.theme_name = theme_name
        self.background_color = background_color
        self.font_family = font_family
        self.font_size = font_size
        self.enable_streaming = enable_streaming
        self.stream_delay = stream_delay

        # Internal buffer for plain text content (raw markdown)
        self._content_buffer = ""

        # Internal buffer for rendered display text (what's actually shown without markdown markers)
        self._rendered_display_buffer = ""

        # Mapping: character position in rendered buffer -> content line number
        self._char_to_content_line_map: list[int] = []

        # Current content line being rendered (set during _render_markdown)
        self._current_content_line_for_rendering: int | None = None

        # For backwards compatibility with SyntaxHighlightedText
        self._server_mode = False

        # For tracking highlighted content
        self.last_highlighted_content = ""
        self.last_highlighted_length = 0

        # Configure selection colors
        self.configure_selection_colors()

        # Configure tags for markdown rendering
        self._configure_tags()

        # Set initial state to NORMAL (not disabled)
        self.text_widget.config(state=tk.NORMAL)

        # Bind key press handler to prevent user editing
        self.text_widget.bind("<Key>", self._on_key_press)
        self.text_widget.bind("<Control-c>", self._copy_selection)

    def _re_render(self) -> None:
        """Re-render the display from the content buffer using markdown rendering."""
        self.text_widget.config(state=tk.NORMAL)
        self.text_widget.delete("1.0", tk.END)
        self._rendered_display_buffer = ""
        self._char_to_content_line_map = []

        if self._content_buffer:
            self._render_markdown(self._content_buffer)

    def tag_remove(self, tag_name: str, index1: str, index2: str) -> None:
        """Remove a tag from a range."""
        self.text_widget.tag_remove(tag_name, index1, index2)

    def _render_markdown(self, content: str) -> None:
        """Render markdown content with proper formatting and tags."""
        lines = content.split("\n")
        i = 0

        while i < len(lines):
            line = lines[i]
            self._current_content_line_for_rendering = i

            if line.startswith("# "):
                self._render_inline_formatting(line[2:].strip(), "h1")
                self._insert_text("\n", "h1")
                i += 1
            elif line.startswith("## "):
                self._render_inline_formatting(line[3:].strip(), "h2")
                self._insert_text("\n", "h2")
                i += 1
            elif line.startswith("### "):
                self._render_inline_formatting(line[4:].strip(), "h3")
                self._insert_text("\n", "h3")
                i += 1
            elif line.startswith("#### "):
                self._render_inline_formatting(line[5:].strip(), "h4")
                self._insert_text("\n", "h4")
                i += 1
            elif line.startswith("##### "):
                self._render_inline_formatting(line[6:].strip(), "h5")
                self._insert_text("\n", "h5")
                i += 1
            elif line.startswith("###### "):
                self._render_inline_formatting(line[7:].strip(), "h6")
                self._insert_text("\n", "h6")
                i += 1
            elif line.strip().startswith("```"):
                start_line = i
                i, code_block = self._extract_code_block(lines, i)
                # Map all lines in the code block to the start line
                for code_line_idx in range(start_line, i):
                    self._current_content_line_for_rendering = code_line_idx
                self._render_code_block(code_block)
            elif line.startswith("> "):
                start_line = i
                i, quote_lines = self._extract_blockquote(lines, i)
                # Map all blockquote lines to the start line
                for quote_line_idx in range(start_line, i):
                    self._current_content_line_for_rendering = quote_line_idx
                self._render_blockquote(quote_lines)
            elif self._is_table_row(line):
                start_line = i
                i, table_data = self._extract_table(lines, i)
                # Map all table lines to the start line
                for table_line_idx in range(start_line, i):
                    self._current_content_line_for_rendering = table_line_idx
                self._render_table(table_data)
            elif line.strip().startswith(("- ", "* ", "+ ")):
                start_line = i
                i, list_items = self._extract_list(lines, i, ordered=False)
                # Map all list lines to the start line
                for list_line_idx in range(start_line, i):
                    self._current_content_line_for_rendering = list_line_idx
                self._render_list(list_items, ordered=False)
            elif re.match(r"^\d+\. ", line.strip()):
                start_line = i
                i, list_items = self._extract_list(lines, i, ordered=True)
                # Map all list lines to the start line
                for list_line_idx in range(start_line, i):
                    self._current_content_line_for_rendering = list_line_idx
                self._render_list(list_items, ordered=True)
            elif line.strip() == "":
                self._insert_text("\n", "paragraph")
                i += 1
            else:
                start_line = i
                i, para_lines = self._extract_paragraph(lines, i)
                # Map all paragraph lines to the start line
                for para_line_idx in range(start_line, i):
                    self._current_content_line_for_rendering = para_line_idx
                self._render_paragraph(para_lines)

        content_end = self.text_widget.get("end-2c")
        if content_end == "\n":
            self.text_widget.delete("end-2c", tk.END)
            if self._rendered_display_buffer.endswith("\n"):
                self._rendered_display_buffer = self._rendered_display_buffer[:-1]
                # Also remove last entries from mapping
                if self._char_to_content_line_map:
                    self._char_to_content_line_map.pop()

    def _insert_text(self, text: str, tag: str) -> None:
        """Insert text with a tag. Also tracks which content line this came from."""
        self.text_widget.insert(tk.END, text, tag)
        # Track rendered display text (without markdown markers)
        self._rendered_display_buffer += text

        # Track which content line each rendered character came from
        if self._current_content_line_for_rendering is not None:
            for _ in text:
                self._char_to_content_line_map.append(
                    self._current_content_line_for_rendering,
                )

    def _on_key_press(self, event):
        """Handle key press events - only allow navigation and selection keys."""
        # Block everything by default - this is a read-only widget

        # Allow navigation keys (without modifiers)
        navigation_keys = [
            "Up",
            "Down",
            "Left",
            "Right",
            "Home",
            "End",
            "Prior",
            "Next",
            "Page_Up",
            "Page_Down",
        ]
        if event.keysym in navigation_keys and not (
            event.state & (0x1 | 0x2 | 0x4 | 0x8)
        ):
            # Navigation without modifiers
            return None

        # Allow Copy (Ctrl+C / Cmd+C) - but return None so it propagates
        if event.state & 0x4 and event.keysym == "c":  # Control+C
            return None

        # Handle Cut (Ctrl+X / Cmd+X) - copy to clipboard but don't delete (read-only)
        if event.state & 0x4 and event.keysym == "x":  # Control+X
            self._copy_selection(event)
            return "break"  # Prevent default cut behavior

        if event.state & 0x8 and event.keysym == "c":  # Alt+C (on some systems)
            return None

        # Allow Ctrl+A (start of line)
        if event.state & 0x4 and event.keysym == "a":  # Control+A
            return None

        # Allow Ctrl+E (end of line)
        if event.state & 0x4 and event.keysym == "e":  # Control+E
            return None

        # Block Ctrl+D (delete character)
        if event.state & 0x4 and event.keysym == "d":  # Control+D
            return "break"

        # For any modifier+key combination, return None to let it propagate to global handlers
        # This allows other Ctrl+X, etc. to work
        if event.state & (0x1 | 0x2 | 0x4 | 0x8):  # Any modifier pressed
            return None

        # Block ALL other key presses (regular text input only)
        return "break"

    def _configure_tags(self) -> None:
        """Configure text tags for markdown rendering."""
        default_font = tkFont.Font(font=self.text_widget.cget("font"))
        bold_font = tkFont.Font(font=default_font)
        bold_font.configure(weight="bold")

        # Headers - colors now adapt to theme
        header_colors = self._get_header_colors()

        self.text_widget.tag_configure(
            "h1",
            font=(self.font_family, self.font_size + 5, "bold"),
            foreground=header_colors[0],
            spacing3=10,
        )
        self.text_widget.tag_configure(
            "h2",
            font=(self.font_family, self.font_size + 4, "bold"),
            foreground=header_colors[1],
            spacing3=8,
        )
        self.text_widget.tag_configure(
            "h3",
            font=(self.font_family, self.font_size + 3, "bold"),
            foreground=header_colors[2],
            spacing3=6,
        )
        self.text_widget.tag_configure(
            "h4",
            font=(self.font_family, self.font_size + 2, "bold"),
            foreground=header_colors[3],
            spacing3=4,
        )
        self.text_widget.tag_configure(
            "h5",
            font=(self.font_family, self.font_size + 1, "bold"),
            foreground=header_colors[4],
            spacing3=2,
        )
        self.text_widget.tag_configure(
            "h6",
            font=(self.font_family, self.font_size, "bold"),
            foreground=header_colors[5],
            spacing3=2,
        )

        # Text styles - colors adapt to theme
        text_colors = self._get_text_colors()

        self.text_widget.tag_configure(
            "bold",
            font=bold_font,
            foreground=text_colors["bold"],
        )
        self.text_widget.tag_configure(
            "italic",
            font=(self.font_family, self.font_size, "italic"),
            foreground=text_colors["italic"],
        )
        self.text_widget.tag_configure(
            "strikethrough",
            overstrike=True,
            foreground=text_colors["strikethrough"],
        )
        self.text_widget.tag_configure(
            "code",
            font=(self.font_family, self.font_size - 1),
            background=text_colors["code_bg"],
            foreground=text_colors["code_fg"],
            selectbackground=text_colors["select_bg"],
            selectforeground=text_colors["select_fg"],
        )

        # Block styles - colors adapt to theme
        self.text_widget.tag_configure(
            "blockquote",
            foreground=text_colors["blockquote"],
            lmargin1=20,
            lmargin2=20,
            spacing1=6,
            spacing3=6,
        )
        self.text_widget.tag_configure(
            "code_block",
            font=(self.font_family, self.font_size - 1),
            background=text_colors["code_bg"],
            foreground=text_colors["code_block_fg"],
            lmargin1=10,
            lmargin2=10,
            rmargin=10,
            spacing1=6,
            spacing3=6,
            selectbackground=text_colors["select_bg"],
            selectforeground=text_colors["select_fg"],
        )
        self.text_widget.tag_configure(
            "list_item",
            lmargin1=20,
            lmargin2=40,
            spacing1=2,
            spacing3=2,
            foreground=self.fg_color,
        )
        self.text_widget.tag_configure(
            "paragraph",
            spacing1=0,
            spacing3=8,
            foreground=self.fg_color,
        )

        # Links - color adapts to theme
        self.text_widget.tag_configure(
            "link",
            foreground=text_colors["link"],
            underline=True,
        )

        # Table tags - colors adapt to theme
        table_colors = self._get_table_colors()

        self.text_widget.tag_configure(
            "table_header",
            font=(self.font_family, self.font_size),
            background=table_colors["bg"],
            foreground=table_colors["header_fg"],
            lmargin1=10,
            lmargin2=10,
            spacing1=4,
            spacing3=4,
        )
        self.text_widget.tag_configure(
            "table_cell",
            font=(self.font_family, self.font_size),
            background=table_colors["bg"],
            foreground=table_colors["cell_fg"],
            lmargin1=10,
            lmargin2=10,
            spacing1=2,
            spacing3=2,
        )
        self.text_widget.tag_configure(
            "table_separator",
            font=(self.font_family, self.font_size),
            background=table_colors["bg"],
            foreground=table_colors["separator_fg"],
            lmargin1=10,
            lmargin2=10,
            spacing1=2,
            spacing3=2,
        )

        # Configure syntax highlighting tags using Pygments theme
        self._configure_syntax_tags()

        # Configure selection colors AFTER all tags are set up
        # This ensures widget-level selection settings take precedence
        self.configure_selection_colors()

    def _configure_token_tag(self, token, fg: str | None, bg: str | None) -> bool:
        """Configure a single token tag with the given colors.

        Includes selection colors to ensure code blocks are properly highlighted
        when text is selected, even with overlapping token tags.
        """
        if not fg or not fg.startswith("#"):
            return False

        try:
            # Use code block background as default background for syntax highlighting
            text_colors = self._get_text_colors()
            default_bg = text_colors["code_bg"]
            select_bg = text_colors["select_bg"]
            select_fg = text_colors["select_fg"]

            if bg and bg.startswith("#"):
                self.text_widget.tag_configure(
                    str(token),
                    foreground=fg,
                    background=bg,
                    selectbackground=select_bg,
                    selectforeground=select_fg,
                )
            else:
                self.text_widget.tag_configure(
                    str(token),
                    foreground=fg,
                    background=default_bg,
                    selectbackground=select_bg,
                    selectforeground=select_fg,
                )
            return True
        except tk.TclError as tcl_error:
            print(
                f"Tkinter error configuring token {token} with fg={fg}, bg={bg}: {tcl_error}",
            )
            return False


def is_macos() -> bool:
    return sys.platform == "darwin"
