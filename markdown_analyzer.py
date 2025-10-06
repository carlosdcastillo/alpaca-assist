#!/usr/bin/env python3
"""
Markdown File Analyzer

This program analyzes a Markdown file and extracts structural information about
headings, sections, code blocks, lists, tables, and other markdown elements.
"""
import os
import re
import sys
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple


def extract_headings(content: str) -> list[dict[str, any]]:
    """
    Extract all headings from markdown content.

    Args:
        content (str): Markdown content

    Returns:
        List[Dict]: List of heading information
    """
    headings = []
    lines = content.split("\n")

    for line_num, line in enumerate(lines, 1):
        # ATX-style headings (# ## ### etc.)
        atx_match = re.match(r"^(#{1,6})\s+(.+)$", line.strip())
        if atx_match:
            level = len(atx_match.group(1))
            title = atx_match.group(2).strip()
            headings.append(
                {
                    "type": "atx_heading",
                    "level": level,
                    "title": title,
                    "line_number": line_num,
                    "raw_line": line.strip(),
                },
            )
            continue

        # Setext-style headings (underlined with = or -)
        if line_num < len(lines):
            next_line = lines[line_num] if line_num < len(lines) else ""
            if re.match(r"^=+$", next_line.strip()) and line.strip():
                headings.append(
                    {
                        "type": "setext_heading",
                        "level": 1,
                        "title": line.strip(),
                        "line_number": line_num,
                        "raw_line": line.strip(),
                    },
                )
            elif re.match(r"^-+$", next_line.strip()) and line.strip():
                headings.append(
                    {
                        "type": "setext_heading",
                        "level": 2,
                        "title": line.strip(),
                        "line_number": line_num,
                        "raw_line": line.strip(),
                    },
                )

    return headings


def extract_code_blocks(content: str) -> list[dict[str, any]]:
    """
    Extract code blocks from markdown content.

    Args:
        content (str): Markdown content

    Returns:
        List[Dict]: List of code block information
    """
    code_blocks = []
    lines = content.split("\n")
    in_fenced_block = False
    current_block = None

    for line_num, line in enumerate(lines, 1):
        # Fenced code blocks (``` or ~~~)
        fenced_match = re.match(r"^(```|~~~)(.*)$", line.strip())
        if fenced_match:
            if not in_fenced_block:
                # Starting a code block
                in_fenced_block = True
                language = fenced_match.group(2).strip()
                current_block = {
                    "type": "fenced_code_block",
                    "language": language if language else "text",
                    "start_line": line_num,
                    "content_lines": [],
                    "fence_type": fenced_match.group(1),
                }
            else:
                # Ending a code block
                if (
                    current_block
                    and fenced_match.group(1) == current_block["fence_type"]
                ):
                    current_block["end_line"] = line_num
                    current_block["line_count"] = len(current_block["content_lines"])
                    code_blocks.append(current_block)
                    in_fenced_block = False
                    current_block = None
        elif in_fenced_block and current_block:
            current_block["content_lines"].append(line)

        # Indented code blocks (4+ spaces)
        elif re.match(r"^    ", line) and not in_fenced_block:
            # This is a simple detection - in practice, indented code blocks
            # have more complex rules about blank lines and context
            if (
                not code_blocks
                or code_blocks[-1]["type"] != "indented_code_block"
                or code_blocks[-1].get("end_line", 0) < line_num - 1
            ):
                code_blocks.append(
                    {
                        "type": "indented_code_block",
                        "language": "text",
                        "start_line": line_num,
                        "content_lines": [line[4:]],  # Remove 4-space indent
                        "end_line": line_num,
                    },
                )
            else:
                # Continue existing indented block
                code_blocks[-1]["content_lines"].append(line[4:])
                code_blocks[-1]["end_line"] = line_num

    # Handle unclosed fenced blocks
    if in_fenced_block and current_block:
        current_block["end_line"] = len(lines)
        current_block["line_count"] = len(current_block["content_lines"])
        current_block["unclosed"] = True
        code_blocks.append(current_block)

    # Add line counts for indented blocks
    for block in code_blocks:
        if "line_count" not in block:
            block["line_count"] = len(block["content_lines"])

    return code_blocks


def extract_lists(content: str) -> list[dict[str, any]]:
    """
    Extract lists from markdown content.

    Args:
        content (str): Markdown content

    Returns:
        List[Dict]: List of list information
    """
    lists = []
    lines = content.split("\n")
    current_list = None

    for line_num, line in enumerate(lines, 1):
        # Unordered lists (-, *, +)
        unordered_match = re.match(r"^(\s*)([-*+])\s+(.+)$", line)
        if unordered_match:
            indent_level = (
                len(unordered_match.group(1)) // 2
            )  # Approximate nesting level
            marker = unordered_match.group(2)
            content_text = unordered_match.group(3)

            if (
                not current_list
                or current_list["type"] != "unordered"
                or current_list.get("end_line", 0) < line_num - 2
            ):
                # Start new list
                current_list = {
                    "type": "unordered",
                    "marker": marker,
                    "start_line": line_num,
                    "items": [],
                    "max_nesting_level": indent_level,
                }
                lists.append(current_list)

            current_list["items"].append(
                {
                    "line_number": line_num,
                    "indent_level": indent_level,
                    "content": content_text,
                },
            )
            current_list["end_line"] = line_num
            current_list["max_nesting_level"] = max(
                current_list["max_nesting_level"],
                indent_level,
            )
            continue

        # Ordered lists (1. 2. etc.)
        ordered_match = re.match(r"^(\s*)(\d+)\. (.+)$", line)
        if ordered_match:
            indent_level = len(ordered_match.group(1)) // 2
            number = int(ordered_match.group(2))
            content_text = ordered_match.group(3)

            if (
                not current_list
                or current_list["type"] != "ordered"
                or current_list.get("end_line", 0) < line_num - 2
            ):
                # Start new list
                current_list = {
                    "type": "ordered",
                    "start_line": line_num,
                    "items": [],
                    "max_nesting_level": indent_level,
                }
                lists.append(current_list)

            current_list["items"].append(
                {
                    "line_number": line_num,
                    "indent_level": indent_level,
                    "number": number,
                    "content": content_text,
                },
            )
            current_list["end_line"] = line_num
            current_list["max_nesting_level"] = max(
                current_list["max_nesting_level"],
                indent_level,
            )
            continue

        # If we hit a non-list line, close current list if it exists
        if current_list and line.strip() == "":
            continue  # Blank lines are okay in lists
        elif current_list and not re.match(r"^\s*([-*+]|\d+\.)\s", line):
            current_list = None

    # Add item counts
    for list_item in lists:
        list_item["item_count"] = len(list_item["items"])

    return lists


def extract_tables(content: str) -> list[dict[str, any]]:
    """
    Extract tables from markdown content.

    Args:
        content (str): Markdown content

    Returns:
        List[Dict]: List of table information
    """
    tables = []
    lines = content.split("\n")

    for line_num, line in enumerate(lines, 1):
        # Look for table separator lines (|---|---|)
        if re.match(r"^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$", line.strip()):
            # This might be a table separator
            if line_num > 1:
                header_line = lines[line_num - 2].strip()
                if "|" in header_line:
                    # Count columns
                    separator_cols = len(
                        [
                            col
                            for col in line.split("|")
                            if col.strip() and re.match(r"^\s*:?-+:?\s*$", col.strip())
                        ],
                    )
                    header_cols = len(
                        [col for col in header_line.split("|") if col.strip()],
                    )

                    if separator_cols > 0:
                        # Find all rows of this table
                        table_rows = [header_line]
                        row_count = 1

                        # Look for data rows after the separator
                        for next_line_idx in range(line_num, len(lines)):
                            next_line = lines[next_line_idx].strip()
                            if "|" in next_line and not re.match(
                                r"^\s*\|?\s*:?-+:?",
                                next_line,
                            ):
                                table_rows.append(next_line)
                                row_count += 1
                            elif next_line == "":
                                continue  # Skip blank lines
                            else:
                                break  # End of table

                        tables.append(
                            {
                                "type": "table",
                                "start_line": line_num - 1,  # Header line
                                "separator_line": line_num,
                                "end_line": line_num - 1 + row_count,
                                "column_count": separator_cols,
                                "row_count": row_count,
                                "header": header_line,
                                "rows": table_rows[1:] if len(table_rows) > 1 else [],
                            },
                        )

    return tables


def extract_links_and_images(content: str) -> dict[str, list[dict[str, any]]]:
    """
    Extract links and images from markdown content.

    Args:
        content (str): Markdown content

    Returns:
        Dict: Dictionary with 'links' and 'images' keys
    """
    links = []
    images = []

    # Regular links [text](url)
    link_pattern = r"(?<!!)\[([^\]]+)\]\(([^)]+)\)"
    for match in re.finditer(link_pattern, content):
        links.append(
            {
                "type": "inline_link",
                "text": match.group(1),
                "url": match.group(2),
                "position": match.start(),
            },
        )

    # Images ![alt](url)
    image_pattern = r"!\[([^\]]*)\]\(([^)]+)\)"
    for match in re.finditer(image_pattern, content):
        images.append(
            {
                "type": "inline_image",
                "alt_text": match.group(1),
                "url": match.group(2),
                "position": match.start(),
            },
        )

    # Reference links [text][ref]
    ref_link_pattern = r"\[([^\]]+)\]\[([^\]]+)\]"
    for match in re.finditer(ref_link_pattern, content):
        links.append(
            {
                "type": "reference_link",
                "text": match.group(1),
                "reference": match.group(2),
                "position": match.start(),
            },
        )

    # Reference definitions [ref]: url
    ref_def_pattern = r"^\s*\[([^\]]+)\]:\s*(.+)$"
    for line_num, line in enumerate(content.split("\n"), 1):
        match = re.match(ref_def_pattern, line)
        if match:
            links.append(
                {
                    "type": "reference_definition",
                    "reference": match.group(1),
                    "url": match.group(2).strip(),
                    "line_number": line_num,
                },
            )

    return {"links": links, "images": images}


def summarize_file(filename: str) -> dict[str, any]:
    """
    Analyze a Markdown file and extract structural information.

    Args:
        filename (str): Path to the Markdown file to analyze

    Returns:
        Dict: Dictionary containing markdown structure information
    """
    try:
        with open(filename, encoding="utf-8") as f:
            content = f.read()

        # Extract all structural elements
        headings = extract_headings(content)
        code_blocks = extract_code_blocks(content)
        lists = extract_lists(content)
        tables = extract_tables(content)
        links_and_images = extract_links_and_images(content)

        # Calculate some statistics
        lines = content.split("\n")
        word_count = len(content.split())
        char_count = len(content)

        return {
            "headings": headings,
            "code_blocks": code_blocks,
            "lists": lists,
            "tables": tables,
            "links": links_and_images["links"],
            "images": links_and_images["images"],
            "statistics": {
                "line_count": len(lines),
                "word_count": word_count,
                "character_count": char_count,
                "non_empty_lines": len([line for line in lines if line.strip()]),
            },
        }

    except FileNotFoundError:
        return {"error": f"File '{filename}' not found."}
    except UnicodeDecodeError:
        return {
            "error": f"File '{filename}' is not a valid text file or uses unsupported encoding.",
        }
    except Exception as e:
        return {"error": f"Error analyzing '{filename}': {e}"}


def format_results(results: dict[str, any], filename: str) -> str:
    """
    Format the analysis results into a string.

    Args:
        results (Dict): Results from summarize_file
        filename (str): Name of the analyzed file

    Returns:
        str: Formatted analysis results
    """
    if results is None:
        return "No results to display."

    if "error" in results:
        return f"Error: {results['error']}"

    output_lines = []
    output_lines.append(f"=== Markdown Analysis Results for '{filename}' ===")

    # Format headings
    headings = results["headings"]
    output_lines.append(f"\n📋 Headings ({len(headings)} total):")

    if headings:
        # Group by level
        by_level = {}
        for heading in headings:
            level = heading["level"]
            if level not in by_level:
                by_level[level] = []
            by_level[level].append(heading)

        for level in sorted(by_level.keys()):
            level_headings = by_level[level]
            output_lines.append(
                f"   Level {level} ({'#' * level}) - {len(level_headings)} headings:",
            )
            for heading in level_headings:
                output_lines.append(
                    f"     - {heading['title']} (line {heading['line_number']})",
                )
    else:
        output_lines.append("   (No headings found)")

    # Format code blocks
    code_blocks = results["code_blocks"]
    output_lines.append(f"\n💻 Code Blocks ({len(code_blocks)} total):")

    if code_blocks:
        fenced_blocks = [cb for cb in code_blocks if cb["type"] == "fenced_code_block"]
        indented_blocks = [
            cb for cb in code_blocks if cb["type"] == "indented_code_block"
        ]

        if fenced_blocks:
            output_lines.append(f"   Fenced Code Blocks ({len(fenced_blocks)}):")
            lang_counts = {}
            for block in fenced_blocks:
                lang = block["language"]
                lang_counts[lang] = lang_counts.get(lang, 0) + 1
                unclosed_marker = " (unclosed)" if block.get("unclosed") else ""
                output_lines.append(
                    f"     - {lang}: {block['line_count']} lines (lines {block['start_line']}-{block['end_line']}){unclosed_marker}",
                )

            output_lines.append(
                f"   Languages used: {', '.join(f'{lang} ({count})' for lang, count in sorted(lang_counts.items()))}",
            )

        if indented_blocks:
            output_lines.append(f"   Indented Code Blocks ({len(indented_blocks)}):")
            for block in indented_blocks:
                output_lines.append(
                    f"     - {block['line_count']} lines (lines {block['start_line']}-{block['end_line']})",
                )
    else:
        output_lines.append("   (No code blocks found)")

    # Format lists
    lists = results["lists"]
    output_lines.append(f"\n📝 Lists ({len(lists)} total):")

    if lists:
        ordered_lists = [l for l in lists if l["type"] == "ordered"]
        unordered_lists = [l for l in lists if l["type"] == "unordered"]

        if ordered_lists:
            output_lines.append(f"   Ordered Lists ({len(ordered_lists)}):")
            for lst in ordered_lists:
                output_lines.append(
                    f"     - {lst['item_count']} items, max nesting level {lst['max_nesting_level']} (lines {lst['start_line']}-{lst['end_line']})",
                )

        if unordered_lists:
            output_lines.append(f"   Unordered Lists ({len(unordered_lists)}):")
            for lst in unordered_lists:
                output_lines.append(
                    f"     - {lst['item_count']} items, marker '{lst['marker']}', max nesting level {lst['max_nesting_level']} (lines {lst['start_line']}-{lst['end_line']})",
                )
    else:
        output_lines.append("   (No lists found)")

    # Format tables
    tables = results["tables"]
    output_lines.append(f"\n📊 Tables ({len(tables)} total):")

    if tables:
        for i, table in enumerate(tables, 1):
            output_lines.append(
                f"   Table {i}: {table['column_count']} columns × {table['row_count']} rows (lines {table['start_line']}-{table['end_line']})",
            )
    else:
        output_lines.append("   (No tables found)")

    # Format links
    links = results["links"]
    output_lines.append(f"\n🔗 Links ({len(links)} total):")

    if links:
        link_types = {}
        for link in links:
            link_type = link["type"]
            link_types[link_type] = link_types.get(link_type, 0) + 1

        for link_type, count in sorted(link_types.items()):
            output_lines.append(f"   {link_type.replace('_', ' ').title()}: {count}")
    else:
        output_lines.append("   (No links found)")

    # Format images
    images = results["images"]
    output_lines.append(f"\n🖼️  Images ({len(images)} total):")

    if images:
        for i, image in enumerate(images, 1):
            alt_text = (
                f" (alt: '{image['alt_text']}')"
                if image["alt_text"]
                else " (no alt text)"
            )
            output_lines.append(f"   Image {i}: {image['url']}{alt_text}")
    else:
        output_lines.append("   (No images found)")

    # Format statistics
    stats = results["statistics"]
    output_lines.append(f"\n📊 Document Statistics:")
    output_lines.append(f"   - Total lines: {stats['line_count']}")
    output_lines.append(f"   - Non-empty lines: {stats['non_empty_lines']}")
    output_lines.append(f"   - Word count: {stats['word_count']}")
    output_lines.append(f"   - Character count: {stats['character_count']}")

    # Summary
    total_structural_elements = (
        len(headings)
        + len(code_blocks)
        + len(lists)
        + len(tables)
        + len(links)
        + len(images)
    )
    output_lines.append(f"\n📋 Summary:")
    output_lines.append(f"   - Headings: {len(headings)}")
    output_lines.append(f"   - Code blocks: {len(code_blocks)}")
    output_lines.append(f"   - Lists: {len(lists)}")
    output_lines.append(f"   - Tables: {len(tables)}")
    output_lines.append(f"   - Links: {len(links)}")
    output_lines.append(f"   - Images: {len(images)}")
    output_lines.append(f"   - Total structural elements: {total_structural_elements}")

    output_lines.append("\n" + "=" * 80)

    return "\n".join(output_lines)


def analyze_markdown_file(filename: str) -> str:
    """
    Main analysis function that returns formatted results as a string.

    Args:
        filename (str): Path to the Markdown file to analyze

    Returns:
        str: Formatted analysis results
    """
    results = summarize_file(filename)
    return format_results(results, filename)


def main():
    """
    Main function to handle command line arguments and run the analysis.
    """
    if len(sys.argv) != 2:
        print("Usage: python markdown_analyzer.py <markdown_file>")
        print("Example: python markdown_analyzer.py README.md")
        print("\nThis tool will show:")
        print("  - Headings structure and hierarchy")
        print("  - Code blocks (fenced and indented) with languages")
        print("  - Lists (ordered and unordered) with nesting levels")
        print("  - Tables with dimensions")
        print("  - Links and images")
        print("  - Document statistics")
        sys.exit(1)

    filename = sys.argv[1]

    # Check if file exists
    if not os.path.exists(filename):
        print(f"Error: File '{filename}' does not exist.")
        sys.exit(1)

    if not filename.lower().endswith((".md", ".markdown")):
        print(
            f"Warning: '{filename}' doesn't have a .md or .markdown extension. Proceeding anyway...",
        )

    # Analyze the file and print results
    result_string = analyze_markdown_file(filename)
    print(result_string)


if __name__ == "__main__":
    main()
