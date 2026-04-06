from typing import Any
from typing import List
from typing import Tuple


class TokenCache:
    def __init__(self, max_size: int = 100):
        self.cache: dict[str, list[tuple[Any, str]]] = {}
        self.access_order: list[str] = []
        self.max_size = max_size

    def get_tokens(self, text: str, lexer) -> list[tuple[Any, str]]:
        # Create a hash of the text for cache key
        text_hash = hash(text)
        cache_key = f"{text_hash}_{type(lexer).__name__}"

        if cache_key in self.cache:
            # Move to end of access order (LRU)
            self.access_order.remove(cache_key)
            self.access_order.append(cache_key)
            return self.cache[cache_key]

        # Not in cache, tokenize and store
        tokens = list(lexer.get_tokens(text))

        # Manage cache size (LRU eviction)
        if len(self.cache) >= self.max_size:
            oldest_key = self.access_order.pop(0)
            del self.cache[oldest_key]

        self.cache[cache_key] = tokens
        self.access_order.append(cache_key)
        return tokens
