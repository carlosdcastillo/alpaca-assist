"""Summary generation module for ChatTab.

This module handles conversation summary generation for chat tab titles.
It is self-contained with no dependencies on other chat_tab modules.

Key Classes:
    SummaryHandler: Manages summary generation and title updates.

Thread Safety:
    This module uses threading.Lock for the generated flag.
"""
from __future__ import annotations

import json
import logging
import queue
import threading
from typing import Any
from typing import TYPE_CHECKING

import requests

import utils

if TYPE_CHECKING:
    from core.chat_tab_base import ChatTabBase

logger = logging.getLogger(__name__)


class SummaryHandler:
    """Handles summary generation for chat tabs.

    This class is responsible for generating conversation summaries
    and updating tab titles based on the first Q/A pair.
    """

    def __init__(self, chat_tab: ChatTabBase) -> None:
        """Initialize the summary handler.

        Args:
            chat_tab: The ChatTabBase instance to generate summaries for.
        """
        self._chat = chat_tab
        self._generated = False
        self._lock = threading.Lock()

    def trigger_if_needed(self) -> None:
        """Trigger summary generation if conditions are met.

        This is the single entry point that replaces get_summary(),
        _generate_summary_if_needed(), and trigger_summary_generation().

        Summary generation is triggered only once when the first answer
        is complete and content is available.
        """
        with self._lock:
            if self._generated:
                logger.debug("[SUMMARY] Already generated, skipping")
                return
            self._generated = True

        # Only require a question — answer content is checked inside _fetch()
        # with retry logic, giving continuations (tool calls) time to complete.
        questions, _, _ = self._chat.chat_state.get_safe_copy()
        if not questions or not questions[0].strip():
            logger.warning("[SUMMARY] No question available for summary")
            self._update_title("Chat Summary")
            return

        # Start the summary generation with consistent 2.0s delay
        logger.info("[SUMMARY] Starting summary generation")
        threading.Timer(2.0, self._start_generation).start()

    def _start_generation(self) -> None:
        """Start the summary generation threads."""
        summary_queue: queue.Queue[str] = queue.Queue()

        fetch_thread = threading.Thread(
            target=self._fetch,
            args=(summary_queue,),
            daemon=True,
        )
        fetch_thread.start()

        handler_thread = threading.Thread(
            target=self._handle_response,
            args=(summary_queue,),
            daemon=True,
        )
        handler_thread.start()

    def _fetch(self, summary_queue: queue.Queue[str]) -> None:
        """Fetch summary from LLM with retry logic.

        Args:
            summary_queue: Queue to put the generated summary into.
        """
        try:
            questions, answers_full, _ = self._chat.chat_state.get_safe_copy_full()

            # Retry logic for content readiness — use full content so that
            # tool-result-only answers are not mistakenly treated as empty.
            max_retries = 3
            retry_delay = 1.0
            for retry in range(max_retries):
                if (
                    questions
                    and answers_full
                    and questions[0].strip()
                    and answers_full[0].get_text_content().strip()
                ):
                    break
                if retry < max_retries - 1:
                    logger.debug(
                        f"[SUMMARY] Waiting for content (attempt {retry + 1})",
                    )
                    # Use Event.wait instead of time.sleep for interruptibility
                    event = threading.Event()
                    event.wait(retry_delay)
                    (
                        questions,
                        answers_full,
                        _,
                    ) = self._chat.chat_state.get_safe_copy_full()
                else:
                    logger.debug("[SUMMARY] No valid content after retries")
                    summary_queue.put("Chat Summary")
                    return

            first_q = questions[0]
            first_a = answers_full[0].get_text_content()[:500]  # Limit answer length

            summary_prompt = (
                f"Please provide a very brief summary (3-5 words) of this "
                f"conversation (no period):\n\nQ: {first_q}\nA: {first_a}"
            )

            messages: list[dict[str, str]] = [
                {"role": "user", "content": summary_prompt},
            ]

            # "llama3.1" was a leftover from an old local-Ollama setup and
            # isn't one of this app's actual models — fall back to the
            # server's own default instead of a name nothing here offers.
            from anthropic_ollama_server import DEFAULT_MODEL

            selected_model = self._chat.preferences.get("model", DEFAULT_MODEL)
            payload: dict[str, Any] = {
                "model": selected_model,
                "messages": messages,
                "stream": True,
            }

            logger.debug(f"[SUMMARY] Requesting with model: {selected_model}")

            api_url = self._chat.preferences.get(
                "api_url",
                "http://localhost:11434",
            )
            api_url = utils.normalize_api_url(api_url)
            full_url = f"{api_url}/api/chat"

            with requests.post(
                full_url,
                json=payload,
                stream=True,
                timeout=30,
            ) as response:
                if response.status_code != 200:
                    error_msg = f"Summary API error: Status {response.status_code}"
                    logger.error(f"{error_msg}\nResponse: {response.text}")
                    summary_queue.put("Chat Summary")
                    return

                accumulated_summary = ""
                for line in response.iter_lines(decode_unicode=True):
                    if not line:
                        continue
                    try:
                        data = json.loads(line.strip())
                        if "message" in data and "content" in data["message"]:
                            content_chunk = data["message"]["content"]
                            if content_chunk:
                                accumulated_summary += content_chunk
                        if data.get("done", False):
                            summary = self._clean(accumulated_summary)
                            logger.info(f"[SUMMARY] Generated: {summary}")
                            summary_queue.put(summary)
                            return
                    except json.JSONDecodeError as json_err:
                        logger.error(
                            f"[SUMMARY] JSON decode error: {line}. Error: {json_err}",
                        )
                        continue

                # No done flag but have content
                if accumulated_summary:
                    summary = self._clean(accumulated_summary)
                    logger.info(f"[SUMMARY] Generated (no done): {summary}")
                    summary_queue.put(summary)
                else:
                    logger.debug("[SUMMARY] No content received")
                    summary_queue.put("Chat Summary")

        except requests.exceptions.Timeout:
            logger.error("[SUMMARY] Request timed out")
            summary_queue.put("Chat Summary")
        except requests.exceptions.ConnectionError:
            logger.error("[SUMMARY] Connection error - is Ollama running?")
            summary_queue.put("Chat Summary")
        except requests.exceptions.RequestException as req_err:
            logger.error(f"[SUMMARY] Request error: {req_err}")
            summary_queue.put("Chat Summary")
        except Exception as e:
            logger.exception(f"[SUMMARY] Error fetching: {e}")
            summary_queue.put("Chat Summary")

    def _handle_response(self, summary_queue: queue.Queue[str]) -> None:
        """Handle the summary response from the queue.

        Args:
            summary_queue: Queue containing the generated summary.
        """
        try:
            # Use timeout instead of sleep for synchronization
            response = summary_queue.get(timeout=30)
            # Response from _fetch() was already cleaned before queueing
            self._update_title(response.strip(), already_cleaned=True)
        except queue.Empty:
            # Fallback "Chat Summary" needs cleaning (though already clean)
            self._update_title("Chat Summary", already_cleaned=True)

    def _update_title(self, summary: str, already_cleaned: bool = False) -> None:
        """Update the tab title with the generated summary.

        Args:
            summary: The summary text to use as the title.
            already_cleaned: If True, skip cleaning (text was cleaned by _fetch()).
                If False, clean the text (for external/raw input).
        """
        final_summary = summary if already_cleaned else self._clean(summary)
        self._chat.title = final_summary
        api = self._chat._app_core.api
        if api is not None:
            api.update_tab_title(self._chat.tab_id, final_summary)

    def _clean(self, text: str) -> str:
        """Clean and format summary text.

        Args:
            text: Raw summary text.

        Returns:
            Cleaned summary suitable for tab title.
        """
        cleaned = text.strip().replace("\n", " ")
        cleaned = cleaned.replace('"', "").replace("'", "")
        cleaned = cleaned[:50]
        if not cleaned:
            return "Chat Summary"
        cleaned = cleaned[0].upper() + cleaned[1:] if cleaned else cleaned
        return cleaned

    def get_title_from_first_question(self) -> str | None:
        """Get a title based on the first question (fallback).

        Returns:
            Truncated first question or None if no questions exist.
        """
        if self._chat.chat_state.questions:
            first_question = self._chat.chat_state.questions[0]
            title = first_question[:50] + ("..." if len(first_question) > 50 else "")
            return title
        return None

    def update_title_from_question(self) -> None:
        """Update tab title from first question (fallback when no summary)."""
        title = self.get_title_from_first_question()
        if title:
            self._chat.title = title
            api = self._chat._app_core.api
            if api is not None:
                api.update_tab_title(self._chat.tab_id, title)
