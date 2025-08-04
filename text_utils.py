import html
import os
import tempfile
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

# Add these imports for syntax highlighting


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


def parse_code_blocks(text: str) -> List[Tuple[int, str, int, int]]:
    """
    Parse code blocks from text, handling nested blocks correctly.

    Args:
        text (str): Input text containing code blocks

    Returns:
        List[Tuple[int, str, int, int]]: List of tuples (indentation_level, language, start_line, end_line)
    """
    lines: List[str] = text.split("\n")
    blocks: List[Tuple[int, str, int, int]] = []
    open_blocks: List[dict[str, Union[int, str]]] = []  # Stack of open blocks

    for line_idx, line in enumerate(lines):
        line_num: int = line_idx + 1
        leading_spaces: int = len(line) - len(line.lstrip())
        stripped: str = line.strip()

        if stripped.startswith("```"):
            if stripped == "```":
                # This is a plain ``` which could be either opening or closing

                # Check if it's closing an existing block with the same indentation
                # Search from most recent to earliest (reverse order)
                matching_block_idx: Optional[int] = None
                for idx in range(len(open_blocks) - 1, -1, -1):
                    if open_blocks[idx]["indent"] == leading_spaces:
                        matching_block_idx = idx
                        break

                if matching_block_idx is not None:
                    block = open_blocks[matching_block_idx]
                    blocks.append(
                        (
                            cast(int, block["indent"]),
                            cast(str, block["language"]),
                            cast(int, block["start_line"]),
                            line_num,
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
) -> Optional[str]:
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
