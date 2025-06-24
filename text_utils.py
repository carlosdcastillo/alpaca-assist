from typing import cast
from typing import List
from typing import Optional
from typing import Tuple
from typing import Union


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
