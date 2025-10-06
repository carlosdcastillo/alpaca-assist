import re
import sys
from typing import List
from typing import Optional
from typing import Tuple


class MarkdownSectionEditor:
    def __init__(self, content: str):
        self.lines = content.split("\n")
        self.sections = self._parse_sections()

    def _parse_sections(self) -> list[dict]:
        """Parse the markdown content and extract section information."""
        sections = []
        current_section = None
        for i, line in enumerate(self.lines):
            header_match = re.match("^(#{1,6})\\s+(.+)$", line.strip())
            if header_match:
                level = len(header_match.group(1))
                title = header_match.group(2).strip()
                section = {
                    "level": level,
                    "title": title,
                    "line_start": i,
                    "line_end": None,
                    "parent": None,
                }
                for prev_section in reversed(sections):
                    if prev_section["level"] < level:
                        section["parent"] = prev_section["title"]
                        break
                if sections:
                    sections[-1]["line_end"] = i - 1
                sections.append(section)
        if sections:
            sections[-1]["line_end"] = len(self.lines) - 1
        return sections

    def _find_sections_by_name(self, section_name: str) -> list[dict]:
        """Find all sections with the given name."""
        return [
            section for section in self.sections if section["title"] == section_name
        ]

    def _find_section(self, section_name: str, parent_name: str) -> dict | None:
        """Find a section by name and parent, with forgiveness for unambiguous cases."""
        matching_sections = self._find_sections_by_name(section_name)
        if not matching_sections:
            return None
        if len(matching_sections) == 1:
            return matching_sections[0]
        for section in matching_sections:
            if (
                parent_name == "toplevel"
                and section["parent"] is None
                or section["parent"] == parent_name
            ):
                return section
        return None

    def _find_insertion_point(
        self,
        section_name: str,
        parent_name: str,
        section_level: int,
    ) -> int:
        """Find where to insert a new section."""
        if parent_name == "toplevel":
            for i, section in enumerate(self.sections):
                if section["level"] <= section_level and section["parent"] is None:
                    return section["line_start"]
            return len(self.lines)
        else:
            parent_section = None
            for section in self.sections:
                if section["title"] == parent_name:
                    parent_section = section
                    break
            if not parent_section:
                raise ValueError(f"Parent section '{parent_name}' not found")
            parent_end = (
                parent_section["line_end"]
                if parent_section["line_end"] is not None
                else len(self.lines) - 1
            )
            for section in self.sections:
                if (
                    section["parent"] == parent_name
                    and section["level"] >= section_level
                    and (section["line_start"] > parent_section["line_start"])
                ):
                    return section["line_start"]
            return parent_end + 1

    def _find_section_by_name(self, name: str) -> dict | None:
        """Find any section by name (regardless of parent)."""
        matching = self._find_sections_by_name(name)
        if len(matching) == 1:
            return matching[0]
        elif len(matching) > 1:
            raise ValueError(
                f"Multiple sections named '{name}' found. Please be more specific.",
            )
        return None

    def _remove_duplicate_consecutive_sections(self):
        """Remove duplicate consecutive sections with same depth and title.
        When duplicates are found, merge their content into the first occurrence."""
        if len(self.sections) < 2:
            return
        i = 0
        while i < len(self.sections) - 1:
            current = self.sections[i]
            next_section = self.sections[i + 1]
            if (
                current["level"] == next_section["level"]
                and current["title"] == next_section["title"]
            ):
                current_start = current["line_start"]
                current_end = current["line_end"]
                next_start = next_section["line_start"]
                next_end = next_section["line_end"]
                next_content_lines = self.lines[next_start + 1 : next_end + 1]
                while next_content_lines and (not next_content_lines[0].strip()):
                    next_content_lines.pop(0)
                if next_content_lines:
                    insert_pos = current_end + 1
                    self.lines[insert_pos:insert_pos] = next_content_lines
                    current["line_end"] = current_end + len(next_content_lines)
                del self.lines[next_start : next_end + 1]
                self.sections.pop(i + 1)
                lines_removed = next_end - next_start + 1
                for j in range(i + 1, len(self.sections)):
                    self.sections[j]["line_start"] -= lines_removed
                    if self.sections[j]["line_end"] is not None:
                        self.sections[j]["line_end"] -= lines_removed
                print(
                    f"Removed duplicate consecutive section: '{current['title']}' at level {current['level']}",
                )
            else:
                i += 1

    def _remove_duplicate_subsections(self):
        """Remove duplicate subsections under the same parent (non-consecutive).
        When duplicates are found, merge their content into the first occurrence."""
        if len(self.sections) < 2:
            return
        parent_groups = {}
        for section in self.sections:
            parent = section["parent"] if section["parent"] else "__toplevel__"
            if parent not in parent_groups:
                parent_groups[parent] = []
            parent_groups[parent].append(section)
        sections_to_remove = []
        for parent, children in parent_groups.items():
            seen_titles = {}
            for section in children:
                title = section["title"]
                if title in seen_titles:
                    first_section = seen_titles[title]
                    dup_start = section["line_start"]
                    dup_end = section["line_end"]
                    dup_content_lines = self.lines[dup_start + 1 : dup_end + 1]
                    while dup_content_lines and (not dup_content_lines[0].strip()):
                        dup_content_lines.pop(0)
                    if dup_content_lines:
                        first_end = first_section["line_end"]
                        insert_pos = first_end + 1
                        self.lines[insert_pos:insert_pos] = dup_content_lines
                        for s in self.sections:
                            if s["line_start"] > first_end:
                                s["line_start"] += len(dup_content_lines)
                                if s["line_end"] is not None:
                                    s["line_end"] += len(dup_content_lines)
                        first_section["line_end"] += len(dup_content_lines)
                        section["line_start"] += len(dup_content_lines)
                        section["line_end"] += len(dup_content_lines)
                    sections_to_remove.append(section)
                    print(
                        f"Found duplicate subsection: '{title}' under parent '{parent.replace('__toplevel__', 'toplevel')}', merging content",
                    )
                else:
                    seen_titles[title] = section
        for section in sections_to_remove:
            start_line = section["line_start"]
            end_line = section["line_end"]
            del self.lines[start_line : end_line + 1]
            lines_removed = end_line - start_line + 1
            self.sections.remove(section)
            for s in self.sections:
                if s["line_start"] > start_line:
                    s["line_start"] -= lines_removed
                    if s["line_end"] is not None:
                        s["line_end"] -= lines_removed

    def update_section(
        self,
        section_name: str,
        parent_name: str,
        new_content: str,
    ) -> str:
        """Update or create a section with new content."""
        matching_sections = self._find_sections_by_name(section_name)
        existing_section = None
        if matching_sections:
            if len(matching_sections) == 1:
                existing_section = matching_sections[0]
                print(
                    f"Found unique section '{section_name}', ignoring parent specification",
                )
            else:
                existing_section = self._find_section(section_name, parent_name)
                if not existing_section:
                    parents = [s["parent"] or "toplevel" for s in matching_sections]
                    raise ValueError(
                        f"Multiple sections named '{section_name}' found. Available parents: {parents}. Please specify the correct parent.",
                    )
        if existing_section:
            start_line = existing_section["line_start"]
            end_line = existing_section["line_end"]
            section_level = existing_section["level"]
            header = "#" * section_level + " " + section_name
            new_section_lines = [header] + new_content.strip().split("\n")
            actual_end = end_line
            for i in range(start_line + 1, len(self.lines)):
                line = self.lines[i].strip()
                if re.match("^#{1," + str(section_level) + "}\\s+", line):
                    actual_end = i - 1
                    break
            self.lines[start_line : actual_end + 1] = new_section_lines
        else:
            if parent_name == "toplevel":
                section_level = 1
            else:
                parent_section = self._find_section_by_name(parent_name)
                if not parent_section:
                    raise ValueError(f"Parent section '{parent_name}' not found")
                section_level = parent_section["level"] + 1
            header = "#" * section_level + " " + section_name
            new_section_lines = [header] + new_content.strip().split("\n")
            insertion_point = self._find_insertion_point(
                section_name,
                parent_name,
                section_level,
            )
            self.lines[insertion_point:insertion_point] = new_section_lines + [""]
        self.sections = self._parse_sections()
        self._remove_duplicate_consecutive_sections()
        self._remove_duplicate_subsections()
        return "\n".join(self.lines)

    def remove_section(self, section_name: str, parent_name: str = None) -> str:
        """Remove a section and all its content.

        Args:
            section_name: Name of the section to remove
            parent_name: Optional parent section name for disambiguation

        Returns:
            The updated markdown content with the section removed

        Raises:
            ValueError: If section not found or multiple sections found without parent specification
        """
        matching_sections = self._find_sections_by_name(section_name)
        if not matching_sections:
            raise ValueError(f"Section '{section_name}' not found")
        section_to_remove = None
        if len(matching_sections) == 1:
            section_to_remove = matching_sections[0]
            print(f"Found unique section '{section_name}', removing it")
        else:
            if parent_name is None:
                parents = [s["parent"] or "toplevel" for s in matching_sections]
                raise ValueError(
                    f"Multiple sections named '{section_name}' found. Available parents: {parents}. Please specify the parent.",
                )
            section_to_remove = self._find_section(section_name, parent_name)
            if not section_to_remove:
                raise ValueError(
                    f"Section '{section_name}' with parent '{parent_name}' not found",
                )
        start_line = section_to_remove["line_start"]
        end_line = section_to_remove["line_end"]
        section_level = section_to_remove["level"]
        actual_end = end_line
        for i in range(start_line + 1, len(self.lines)):
            line = self.lines[i].strip()
            header_match = re.match("^(#{1,6})\\s+(.+)$", line)
            if header_match:
                level = len(header_match.group(1))
                if level <= section_level:
                    actual_end = i - 1
                    break
            if i == len(self.lines) - 1:
                actual_end = i
        del self.lines[start_line : actual_end + 1]
        if start_line > 0 and start_line < len(self.lines):
            if (
                start_line < len(self.lines)
                and self.lines[start_line - 1].strip()
                and self.lines[start_line].strip()
            ):
                self.lines.insert(start_line, "")
        self.sections = self._parse_sections()
        self._remove_duplicate_consecutive_sections()
        self._remove_duplicate_subsections()
        return "\n".join(self.lines)


def main():
    if len(sys.argv) != 5:
        print(
            "Usage: python markdown_editor.py <markdown_file> <section_name> <parent_section> <new_content_file>",
        )
        print("Use 'toplevel' as parent_section if the section has no parent")
        print(
            "Note: If section name is unique in the document, parent_section will be ignored",
        )
        sys.exit(1)
    markdown_file = sys.argv[1]
    section_name = sys.argv[2]
    parent_section = sys.argv[3]
    content_file = sys.argv[4]
    try:
        with open(markdown_file, encoding="utf-8") as f:
            markdown_content = f.read()
        with open(content_file, encoding="utf-8") as f:
            new_content = f.read()
        editor = MarkdownSectionEditor(markdown_content)
        updated_content = editor.update_section(
            section_name,
            parent_section,
            new_content,
        )
        with open(markdown_file, "w", encoding="utf-8") as f:
            f.write(updated_content)
        print(f"Successfully updated section '{section_name}' in {markdown_file}")
    except FileNotFoundError as e:
        print(f"Error: File not found - {e}")
        sys.exit(1)
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Unexpected error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
