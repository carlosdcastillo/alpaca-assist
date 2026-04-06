"""
A Python wrapper for ripgrep (rg) command-line tool.

This module provides a clean interface to ripgrep for searching files
with support for Windows, Linux, and macOS.
"""
import json
import subprocess
import sys
from pathlib import Path
from typing import Any
from typing import Dict
from typing import List
from typing import Optional


class RipgrepError(Exception):
    """Raised when ripgrep command fails."""

    pass


class RipgrepWrapper:
    """A wrapper around the ripgrep (rg) command-line tool."""

    def __init__(self, rg_path: str = "rg"):
        """
        Initialize the ripgrep wrapper.

        Args:
            rg_path: Path to the ripgrep executable. Defaults to "rg" (assumes it's in PATH).

        Raises:
            RipgrepError: If ripgrep is not found or not executable.
        """
        self.rg_path = rg_path
        self._verify_rg_installed()

    def _verify_rg_installed(self) -> None:
        """
        Verify that ripgrep is installed and accessible.

        Raises:
            RipgrepError: If ripgrep cannot be found or executed.
        """
        try:
            subprocess.run(
                [self.rg_path, "--version"],
                capture_output=True,
                text=True,
                check=True,
            )
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            raise RipgrepError(
                f"ripgrep not found at '{self.rg_path}'. "
                "Please ensure ripgrep is installed and in your PATH.",
            ) from e

    def search(
        self,
        directory: str,
        search_pattern: str,
        file_pattern: str | None = None,
        case_insensitive: bool = False,
        whole_word: bool = False,
        multiline: bool = False,
        max_count: int | None = None,
        context_lines: int = 0,
    ) -> list[dict[str, Any]]:
        """
        Search for a pattern in files within a directory.

        Args:
            directory: Directory to recursively search.
            search_pattern: Regular expression pattern to search for.
            file_pattern: Optional glob pattern to filter files (e.g., "*.py", "*.txt").
            case_insensitive: If True, search is case-insensitive.
            whole_word: If True, only match whole words.
            multiline: If True, enable multiline mode for regex.
            max_count: Maximum number of matches to return per file.
            context_lines: Number of context lines to show before and after match.

        Returns:
            List of match dictionaries containing file, line, column, and match text.

        Raises:
            RipgrepError: If the search command fails.
            ValueError: If directory doesn't exist.
        """
        dir_path = Path(directory)
        if not dir_path.exists():
            raise ValueError(f"Directory does not exist: {directory}")
        if not dir_path.is_dir():
            raise ValueError(f"Path is not a directory: {directory}")

        cmd = [self.rg_path, "--json"]

        # Add search pattern
        cmd.append(search_pattern)

        # Add directory to search
        cmd.append(str(dir_path))

        # Add optional flags
        if case_insensitive:
            cmd.insert(1, "-i")
        if whole_word:
            cmd.insert(1, "-w")
        if multiline:
            cmd.insert(1, "-U")
        if max_count is not None:
            cmd.extend(["--max-count", str(max_count)])
        if context_lines > 0:
            cmd.extend(["-C", str(context_lines)])
        if file_pattern:
            cmd.extend(["-g", file_pattern])

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,  # Don't raise on non-zero exit (no matches returns 1)
            )

            # ripgrep returns 1 when no matches found, which is not an error
            if result.returncode not in (0, 1):
                raise RipgrepError(
                    f"ripgrep command failed with exit code {result.returncode}: "
                    f"{result.stderr}",
                )

            # Parse JSON output
            matches = []
            for line in result.stdout.strip().split("\n"):
                if line:
                    matches.append(json.loads(line))

            return matches

        except json.JSONDecodeError as e:
            raise RipgrepError(f"Failed to parse ripgrep JSON output: {e}") from e

    def search_simple(
        self,
        directory: str,
        search_pattern: str,
        file_pattern: str | None = None,
    ) -> list[str]:
        """
        Simple text search that returns formatted strings.

        Args:
            directory: Directory to recursively search.
            search_pattern: Pattern to search for.
            file_pattern: Optional glob pattern to filter files.

        Returns:
            List of formatted match strings (file:line:column:text).
        """
        try:
            result = subprocess.run(
                [
                    self.rg_path,
                    search_pattern,
                    str(directory),
                    *(["-g", file_pattern] if file_pattern else []),
                ],
                capture_output=True,
                text=True,
                check=False,
            )

            if result.returncode not in (0, 1):
                raise RipgrepError(
                    f"ripgrep command failed with exit code {result.returncode}: "
                    f"{result.stderr}",
                )

            return [line for line in result.stdout.strip().split("\n") if line]

        except Exception as e:
            raise RipgrepError(f"Search failed: {e}") from e


def main(
    directory: str,
    search_pattern: str,
    file_pattern: str | None = None,
    use_json: bool = True,
    **kwargs,
) -> list[dict[str, Any]] | list[str]:
    r"""
    Main entry point for ripgrep wrapper.

    Args:
        directory: Directory to recursively search.
        search_pattern: Regular expression pattern to search for.
        file_pattern: Optional glob pattern to filter files (e.g., "*.py").
        use_json: If True, return structured JSON results; if False, return formatted strings.
        **kwargs: Additional arguments passed to RipgrepWrapper.search() or .search_simple().

    Returns:
        List of matches (dict format if use_json=True, string format otherwise).

    Raises:
        RipgrepError: If ripgrep is not found or search fails.
        ValueError: If directory doesn't exist.

    Example:
        >>> results = main(
        ...     directory="/path/to/search",
        ...     search_pattern=r"def \w+\(",
        ...     file_pattern="*.py"
        ... )
    """
    wrapper = RipgrepWrapper()

    if use_json:
        return wrapper.search(
            directory=directory,
            search_pattern=search_pattern,
            file_pattern=file_pattern,
            **kwargs,
        )
    else:
        return wrapper.search_simple(
            directory=directory,
            search_pattern=search_pattern,
            file_pattern=file_pattern,
        )


if __name__ == "__main__":
    # Example usage
    import argparse

    parser = argparse.ArgumentParser(
        description="Python wrapper for ripgrep (rg)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Search for Python function definitions in current directory
  python rg_wrapper.py . "def \\w+\\(" --file-pattern "*.py"

  # Case-insensitive search for TODO comments
  python rg_wrapper.py /path/to/dir "TODO" -i

  # Search with context lines
  python rg_wrapper.py . "error" -C 2
        """,
    )

    parser.add_argument("directory", help="Directory to search")
    parser.add_argument("pattern", help="Search pattern (regex)")
    parser.add_argument(
        "-g",
        "--file-pattern",
        help="File glob pattern (e.g., '*.py', '*.txt')",
    )
    parser.add_argument(
        "-i",
        "--case-insensitive",
        action="store_true",
        help="Case-insensitive search",
    )
    parser.add_argument(
        "-w",
        "--whole-word",
        action="store_true",
        help="Match whole words only",
    )
    parser.add_argument(
        "-U",
        "--multiline",
        action="store_true",
        help="Enable multiline mode",
    )
    parser.add_argument(
        "-m",
        "--max-count",
        type=int,
        help="Maximum matches per file",
    )
    parser.add_argument(
        "-C",
        "--context",
        type=int,
        default=0,
        help="Context lines before and after match",
    )
    parser.add_argument(
        "--simple",
        action="store_true",
        help="Use simple text output instead of JSON",
    )

    args = parser.parse_args()

    try:
        results = main(
            directory=args.directory,
            search_pattern=args.pattern,
            file_pattern=args.file_pattern,
            case_insensitive=args.case_insensitive,
            whole_word=args.whole_word,
            multiline=args.multiline,
            max_count=args.max_count,
            context_lines=args.context,
            use_json=not args.simple,
        )

        if results:
            if args.simple:
                for match in results:
                    print(match)
            else:
                import json

                for match in results:
                    print(json.dumps(match))
        else:
            print("No matches found.", file=sys.stderr)
            sys.exit(1)

    except (RipgrepError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
