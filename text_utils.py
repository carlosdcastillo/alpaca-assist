import html
import os
import tempfile
import tkinter as tk
import webbrowser
from typing import cast
from typing import List
from typing import Optional
from typing import Tuple
from typing import Union

import markdown
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name
from pygments.lexers import MarkdownLexer
from pygments.styles import get_style_by_name


def backoff(x: str) -> str:
    parts = x.split(".")
    if len(parts) > 1:
        return ".".join(parts[:-1])
    else:
        return x


def count_leading_chars(text, char):
    count = 0
    for c in text:
        if c == char:
            count += 1
        else:
            break
    return count


def parse_code_blocks(text: str) -> list[tuple[int, int, str, str]]:
    """
    Parse code blocks from text, handling nested blocks correctly.

    Args:
        text (str): Input text containing code blocks

    Returns:
        List[Tuple[int, int, str, str]]: List of tuples (start_line, end_line, language, code)
        where start_line and end_line are 1-indexed line numbers
    """
    lines: list[str] = text.split("\n")
    blocks: list[tuple[int, int, str, str]] = []
    open_blocks: list[dict[str, int | str]] = []  # Stack of open blocks

    for line_idx, line in enumerate(lines):
        line_num: int = line_idx + 1
        leading_spaces: int = len(line) - len(line.lstrip())
        stripped: str = line.strip()

        if stripped.startswith("```"):
            if stripped == "```":
                # This is a plain ``` which could be either opening or closing

                # Check if it's closing an existing block with the same indentation
                # Search from most recent to earliest (reverse order)
                matching_block_idx: int | None = None
                for idx in range(len(open_blocks) - 1, -1, -1):
                    if open_blocks[idx]["indent"] == leading_spaces:
                        matching_block_idx = idx
                        break

                if matching_block_idx is not None:
                    block = open_blocks[matching_block_idx]
                    start_line = cast(int, block["start_line"])
                    language = cast(str, block["language"])

                    # Extract code between start and end
                    code_lines = lines[start_line : line_num - 1]
                    code = "\n".join(code_lines)

                    blocks.append(
                        (
                            start_line,
                            line_num,
                            language,
                            code,
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


def export_to_html(
    text_content: str,
    title: str = "Exported Content",
    theme_name: str = "default",
    background_color: str = "white",
    font_family: str = "Cascadia Code",
    font_size: int = 12,
) -> str | None:
    """
    Export text content to HTML using markdown conversion with app-consistent syntax highlighting.

    Args:
        text_content: The text content to export
        title: Title for the HTML document
        theme_name: Pygments theme name (matches app's syntax highlighting theme)
        background_color: Background color preference ("black" or "white")
        font_family: Font family to use
        font_size: Font size to use

    Returns:
        Path to the generated HTML file, or None if export failed
    """
    try:
        # Get the pygments style that matches the app's theme
        try:
            pygments_style = get_style_by_name(theme_name)
        except:
            try:
                pygments_style = get_style_by_name("default")
            except:
                # Fallback to a basic style
                pygments_style = None

        # Create HTML formatter with the same style
        if pygments_style:
            formatter = HtmlFormatter(
                style=theme_name,
                cssclass="highlight",
                noclasses=True,  # Inline styles for portability
                linenos=False,
                prestyles=f"background-color: transparent; font-family: {font_family}, monospace; font-size: {font_size}px;",
            )

            # Generate CSS for the syntax highlighting
            syntax_css = formatter.get_style_defs(".highlight")
        else:
            syntax_css = ""

        # Configure markdown with enhanced code highlighting
        md = markdown.Markdown(
            extensions=[
                "codehilite",
                "fenced_code",
                "tables",
                "toc",
                "nl2br",
            ],
            extension_configs={
                "codehilite": {
                    "css_class": "highlight",
                    "use_pygments": True,
                    "pygments_style": theme_name,
                    "noclasses": True,  # Use inline styles
                    "linenos": False,
                },
            },
        )

        # Convert the content
        html_content = md.convert(text_content)

        # Determine colors based on background preference
        if background_color == "black":
            bg_color = "#000000"
            text_color = "#f8f8f2"
            code_bg = "#1e1e1e"
            border_color = "#444444"
            header_color = "#4a9eff"
        else:  # white
            bg_color = "#ffffff"
            text_color = "#333333"
            code_bg = "#f8f8f8"
            border_color = "#e1e1e8"
            header_color = "#2c3e50"

        # Create a complete HTML document with app-consistent styling
        full_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{html.escape(title)}</title>
    <style>
        body {{
            font-family: '{font_family}', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            line-height: 1.6;
            max-width: 900px;
            margin: 0 auto;
            padding: 20px;
            background-color: {bg_color};
            color: {text_color};
            font-size: {font_size}px;
        }}

        /* Enhanced code block styling that matches app */
        .highlight {{
            background-color: {code_bg};
            border: 1px solid {border_color};
            border-radius: 6px;
            padding: 16px;
            overflow-x: auto;
            margin: 16px 0;
            font-family: '{font_family}', 'Fira Code', 'SF Mono', 'Monaco', 'Consolas', 'Courier New', monospace;
            font-size: {max(font_size - 2, 10)}px;
            line-height: 1.4;
        }}

        .highlight pre {{
            margin: 0;
            padding: 0;
            background: transparent;
            border: none;
            font-family: inherit;
            color: inherit;
        }}

        /* Inline code */
        code {{
            background-color: {code_bg};
            padding: 2px 6px;
            border-radius: 4px;
            font-family: '{font_family}', 'Fira Code', 'SF Mono', 'Monaco', 'Consolas', 'Courier New', monospace;
            font-size: {max(font_size - 2, 10)}px;
            border: 1px solid {border_color};
        }}

        /* Don't style code inside pre blocks */
        .highlight code {{
            background: transparent;
            padding: 0;
            border-radius: 0;
            border: none;
            color: inherit;
        }}

        /* Regular pre blocks (fallback) */
        pre {{
            background-color: {code_bg};
            border: 1px solid {border_color};
            border-radius: 6px;
            padding: 16px;
            overflow-x: auto;
            font-family: '{font_family}', 'Fira Code', 'SF Mono', 'Monaco', 'Consolas', 'Courier New', monospace;
            font-size: {max(font_size - 2, 10)}px;
            line-height: 1.4;
            color: {text_color};
        }}

        blockquote {{
            border-left: 4px solid {border_color};
            margin: 0;
            padding-left: 16px;
            color: {text_color};
            opacity: 0.8;
            font-style: italic;
        }}

        table {{
            border-collapse: collapse;
            width: 100%;
            margin: 20px 0;
        }}

        th, td {{
            border: 1px solid {border_color};
            padding: 8px 12px;
            text-align: left;
        }}

        th {{
            background-color: {code_bg};
            font-weight: 600;
        }}

        h1, h2, h3, h4, h5, h6 {{
            color: {header_color};
            margin-top: 24px;
            margin-bottom: 16px;
            font-weight: 600;
            line-height: 1.25;
        }}

        h1 {{
            border-bottom: 1px solid {border_color};
            padding-bottom: 10px;
            font-size: {font_size + 8}px;
        }}

        h2 {{
            border-bottom: 1px solid {border_color};
            padding-bottom: 8px;
            font-size: {font_size + 4}px;
        }}

        .toc {{
            background-color: {code_bg};
            border: 1px solid {border_color};
            border-radius: 6px;
            padding: 16px;
            margin: 20px 0;
        }}

        .toc ul {{
            margin: 0;
            padding-left: 20px;
        }}

        /* Custom syntax highlighting CSS */
        {syntax_css}

        /* Ensure proper text selection */
        ::selection {{
            background-color: {"#4A90E2" if background_color == "black" else "#316AC5"};
            color: #ffffff;
        }}

        /* Print styles */
        @media print {{
            body {{
                background-color: white;
                color: black;
            }}

            .highlight, pre, code {{
                background-color: #f8f8f8;
                border-color: #ddd;
            }}
        }}
    </style>
</head>
<body>
    <h1>{html.escape(title)}</h1>
    {html_content}
</body>
</html>"""

        # Create a temporary HTML file
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".html",
            delete=False,
            encoding="utf-8",
        ) as temp_file:
            temp_file.write(full_html)
            temp_file_path = temp_file.name

        return temp_file_path

    except Exception as e:
        print(f"Error exporting to HTML: {e}")
        import traceback

        traceback.print_exc()
        return None


def open_in_browser(file_path: str) -> bool:
    """
    Open the HTML file in the default browser.

    Args:
        file_path: Path to the HTML file

    Returns:
        True if successful, False otherwise
    """
    try:
        webbrowser.open(f"file://{os.path.abspath(file_path)}")
        return True
    except Exception as e:
        print(f"Error opening file in browser: {e}")
        return False


def export_and_open(
    text_content: str,
    title: str = "Exported Content",
    theme_name: str = "default",
    background_color: str = "white",
    font_family: str = "Cascadia Code",
    font_size: int = 12,
) -> bool:
    """
    Export text content to HTML and open it in the browser with app-consistent styling.

    Args:
        text_content: The text content to export
        title: Title for the HTML document
        theme_name: Pygments theme name (matches app's syntax highlighting theme)
        background_color: Background color preference ("black" or "white")
        font_family: Font family to use
        font_size: Font size to use

    Returns:
        True if successful, False otherwise
    """
    html_file = export_to_html(
        text_content,
        title,
        theme_name,
        background_color,
        font_family,
        font_size,
    )
    if html_file:
        success = open_in_browser(html_file)
        if success:
            print(f"Exported to: {html_file}")
            return True
    return False


def copy_to_clipboard(text: str, preferences: dict) -> None:
    """
    Copy text to clipboard using pyperclip.

    Args:
        text: The text to copy
        preferences: User preferences dict (for future extensions)
    """
    try:
        import pyperclip

        pyperclip.copy(text)
    except ImportError:
        print("Warning: pyperclip not installed, cannot copy to clipboard")
    except Exception as e:
        print(f"Error copying to clipboard: {e}")


def copy_code_block_from_widget(target_widget, preferences):
    """
    Copy a code block from a widget to clipboard.

    Works with both MarkdownViewerAdapter and SyntaxHighlightedText widgets.
    For MarkdownViewerAdapter, maps rendered line to content line first.

    Args:
        target_widget: The widget containing the code
        preferences: User preferences dict with clipboard settings
    """
    try:
        # Get cursor position from widget
        cursor_index = target_widget.index(tk.INSERT)
        # Convert "line.column" to line number (0-indexed)
        rendered_line_num = int(cursor_index.split(".")[0]) - 1

        # Check if this is a MarkdownViewerAdapter
        if hasattr(target_widget, "_char_to_content_line_map") and hasattr(
            target_widget,
            "_content_buffer",
        ):
            # This is a MarkdownViewerAdapter - need to map rendered line to content line
            content_line_num = target_widget.get_content_line_from_rendered_line(
                rendered_line_num,
            )

            # Debug: Print mapping info
            print(
                f"[DEBUG] Rendered line: {rendered_line_num}, Content line: {content_line_num}",
            )
            print(
                f"[DEBUG] Total rendered lines in map: {len(target_widget._char_to_content_line_map)}",
            )
            print(f"[DEBUG] First 20 mappings:")
            for i in range(min(20, len(target_widget._char_to_content_line_map))):
                print(
                    f"  Rendered line {i} -> Content line {target_widget._char_to_content_line_map[i]}",
                )

            if content_line_num == -1:
                print(
                    f"Error: Could not map rendered line {rendered_line_num} to content line",
                )
                return

            # Parse code blocks from raw content buffer
            content = target_widget._content_buffer
            code_blocks = parse_code_blocks(content)
        else:
            # This is a SyntaxHighlightedText - use rendered text directly
            content = target_widget.get("1.0", tk.END)
            code_blocks = parse_code_blocks(content)
            content_line_num = rendered_line_num

        # Find code block at the current line
        # parse_code_blocks returns 1-indexed line numbers, so convert
        content_line_num_1indexed = content_line_num + 1
        print([(a, b, c) for (a, b, c, d) in code_blocks])

        for block in code_blocks:
            block_start_line, block_end_line, language, code = block
            if block_start_line <= content_line_num_1indexed <= block_end_line:
                # Extract the code and copy
                code = code.strip()
                if code:
                    copy_to_clipboard(code, preferences)
                    print(
                        f"Copied {language or 'plain'} code block ({len(code)} chars) to clipboard",
                    )
                    return

        print(f"Error: No code block found at line {content_line_num_1indexed}")

    except Exception as e:
        print(f"Error copying code block: {e}")
        import traceback

        traceback.print_exc()


def _map_rendered_line_to_content_line(widget, rendered_line: int) -> int | None:
    """
    Map a line number from the rendered display to the content buffer.

    Uses the line mapping stored in MarkdownViewerAdapter to find which
    content line corresponds to the given rendered line.

    Args:
        widget: MarkdownViewerAdapter instance
        rendered_line: Line number in the rendered display (1-indexed)

    Returns:
        Line number in the content buffer (1-indexed), or None if not found
    """
    if not hasattr(widget, "_line_mapping"):
        return None

    # The rendered_line is 1-indexed, but we need to convert to character position
    # Get the text up to this line in the rendered buffer
    rendered_text = widget._rendered_display_buffer
    rendered_lines = rendered_text.split("\n")

    if rendered_line < 1 or rendered_line > len(rendered_lines):
        return None

    # Calculate character position at the start of this line
    char_pos = sum(len(line) + 1 for line in rendered_lines[: rendered_line - 1])

    # Find the mapping for this position
    for content_line_num, render_start, render_end in widget._line_mapping:
        if render_start <= char_pos <= render_end:
            return content_line_num

    return None
