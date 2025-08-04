import sys
from typing import NamedTuple


def is_macos() -> bool:
    return sys.platform == "darwin"


class ContentUpdate(NamedTuple):
    answer_index: int
    content_chunk: str
    is_done: bool = False
    is_error: bool = False
