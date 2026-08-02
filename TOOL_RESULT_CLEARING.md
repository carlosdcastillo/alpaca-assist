# Spec: Coarse tool-result clearing (bound worst-case turns)

## Problem

`prepare_continuation_messages` (`core/chat_tab_tools.py:455-561`) rebuilds
the full message history from scratch on every tool-call continuation
request within a turn. Individual tool results are already capped by
`gate_tool_output` (32KB), but there's no limit on *how many times* an
already-small result gets resent — a turn with 80+ tool round-trips resends
all 80+ pairs on every single one of those round-trips. That's the
mechanism behind the 5.8M/3.3M-token turns found in the July 25 session
(see conversation history / `debug_data.json`).

This is a known, standard gap in agent harnesses — the equivalent of
Anthropic's `clear_tool_uses_20250919` context-editing primitive, applied
client-side since this app proxies through a non-Anthropic backend.

## Goal

Bound the worst case (turns with many tool round-trips), not eliminate
normal growth. Ordinary turns (a handful of tool calls) are unaffected.

## Non-goals

- **Not compaction.** No summarization, no LLM call to condense history.
  Pure truncation/clearing.
- **Not a replacement for `gate_tool_output`.** Per-result size capping
  stays as-is; this addresses repetition count, not single-result size.

## Mechanism

In `prepare_continuation_messages`, tool-call/result pairs are flattened
into one chronological list *across the whole conversation* (not reset per
turn — see "Applied across turns" below), and once that list exceeds a
threshold (`KEEP_LAST_N_TOOL_PAIRS`, currently 15):

- The most recent `KEEP_LAST_N_TOOL_PAIRS` pairs, globally, are sent with
  full `tool_result` content, as today.
- Older pairs keep their `tool_use` message (call name + arguments) but
  have their `tool_result` content replaced with a fixed stub, e.g.:
  `"[tool result cleared to reduce context size — result of this call is
  no longer shown; re-run the tool if you need this again]"`
- A second `cache_control` breakpoint is placed on the message
  immediately after the clear boundary (in addition to the existing
  breakpoint at the end of the previous completed turn), so subsequent
  calls resume accumulating cache hits against the new, shorter prefix
  instead of paying full/write price on every call.

Threshold is coarse and count-based (not time-based) — deliberately simple
to avoid interacting with the ~5-minute cache TTL in complicated ways.

### Applied across turns

Originally scoped to only the turn currently being continued, deliberately
(see git history) — a conversation made of many turns that each individually
stay under the threshold got no protection at all, since the pair count
reset to zero at every turn boundary. Extended to a conversation-wide flat
list instead: pairs are identified by their stable `tc.id` (already unique —
either server-assigned or a uuid4 fallback), so "kept full" / "cleared"
membership is computed once globally, then applied per turn when messages
are rebuilt. A tool call's own arguments (`tool_use` block) are still never
cleared regardless of which turn or how old — see
`core/tool_output_gate.py`'s `gate_tool_call_arguments` for the separate
mechanism that bounds *that* side.

## What's visible to the customer

**Nothing changes in the UI, history, or persisted data.** The
full-fidelity tool results remain exactly as they are today in:
- the chat fold/UI display,
- `chat_state` / `conversations.db` / exported conversations,
- `debug_data.json`-style dumps.

Clearing only affects the *outbound request payload* built at send time —
it's a resend-cost optimization, not a data-retention change.

The one behavior a user could notice, only in pathological long-tool-loop
turns: if the model needs information from a tool result old enough to
have been cleared, it re-issues the same tool call rather than already
"remembering" the answer. This shows up as a duplicate/repeated tool call
in the transcript. This is the expected, accepted tradeoff — strictly
better than the current behavior (unbounded resend growth), and the model
already has the tool available to just ask again.

No new UI, no new settings surface, no user-facing copy changes required
for v1.

## Where implemented

- `core/chat_tab_tools.py` — `prepare_continuation_messages` (clearing
  logic + second cache_control breakpoint).
- No changes to `core/tool_output_gate.py`, `chat_state.py`,
  `anthropic_ollama_server.py`, or the web UI.

## Risk / difficulty (recap)

- **Risk: low.** Touches only outbound request shaping; no effect on
  persistence or display; worst-case failure is a redundant tool call, not
  a crash or data loss; trivially revertible.
- **Difficulty: low-moderate.** Clearing loop is straightforward given
  `tool_pairs` is already assembled in the function. The one fiddly part
  is placing the second `cache_control` breakpoint correctly at the clear
  boundary (well under the 4-breakpoint-per-request cap).

## Suggested test coverage

- Turn with pair count under threshold: no clearing, output unchanged
  (regression guard).
- Turn with pair count over threshold: older pairs' `tool_result` content
  replaced with stub, `tool_use` blocks unchanged, most recent
  `KEEP_LAST_N_PAIRS` pairs unchanged.
- Cache-control breakpoint present exactly once at previous-turn boundary
  (existing behavior) and once at the new clear boundary when clearing
  occurred; absent when it didn't.
- Multi-turn conversation: an already-completed turn's pairs get cleared
  once the *global*, conversation-wide pair count crosses the threshold,
  even if that turn's own pair count never would have on its own.
- Many small turns (each under threshold individually) still get bounded
  once their combined count crosses it globally.
