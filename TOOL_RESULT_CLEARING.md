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
turn — see "Applied across turns" below). The list is walked backward from
the most recent pair, accumulating actual byte size (call args + result)
until a budget (`KEEP_TOOL_CONTEXT_BUDGET_BYTES`, currently 24KB) is
exceeded:

- Pairs within the budget, walking backward from most recent, are sent
  with full `tool_result` content, as today. The single most recent pair
  always stays full regardless of its own size, so the model never loses
  direct visibility into what it just did.
- Older pairs (past the budget) keep their `tool_use` message (call name +
  arguments) but have their `tool_result` content replaced with a fixed
  stub, e.g.: `"[tool result cleared to reduce context size — result of
  this call is no longer shown; re-run the tool if you need this again]"`
- A second `cache_control` breakpoint is placed on the message
  immediately after the clear boundary (in addition to the existing
  breakpoint at the end of the previous completed turn), so subsequent
  calls resume accumulating cache hits against the new, shorter prefix
  instead of paying full/write price on every call.

**Bytes, not pair count.** The original version counted pairs
(`KEEP_LAST_N_TOOL_PAIRS`, a fixed number regardless of content size).
Real pair sizes vary by 100x+ — a few dozen bytes for something like
`read_file_range` vs. tens of KB for a gated `write_file` result — so a
fixed count gave wildly inconsistent actual resend cost depending on which
tools happened to be recent. A byte budget bounds the thing that actually
matters, and self-adjusts: a run of small pairs is barely touched, a run
of large ones gets clamped down hard. 24KB sits between the two per-item
gate thresholds — smaller than `GATE_THRESHOLD_BYTES` (32KB, so one
maximally-sized result can't dominate the window alone), larger than
`CALL_ARG_GATE_THRESHOLD_BYTES` (16KB, so a couple of sizeable recent
calls can still coexist in it).

Budget is size-based (not time-based) — deliberately simple to avoid
interacting with the ~5-minute cache TTL in complicated ways.

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

- `core/chat_tab_tools.py` — `prepare_continuation_messages` (byte-budget
  clearing logic + second cache_control breakpoint) and `handle_tool_call`
  (wires `gate_tool_call_arguments` in at storage time).
- `core/tool_output_gate.py` — `gate_tool_call_arguments`, the equivalent
  cap for the call-argument side, which this mechanism never touches
  regardless of age (see that module's docstring).
- No changes to `chat_state.py`, `anthropic_ollama_server.py`, or the web UI.

## Risk / difficulty (recap)

- **Risk: low.** Touches only outbound request shaping; no effect on
  persistence or display; worst-case failure is a redundant tool call, not
  a crash or data loss; trivially revertible.
- **Difficulty: low-moderate.** Clearing loop is straightforward given
  `tool_pairs` is already assembled in the function. The one fiddly part
  is placing the second `cache_control` breakpoint correctly at the clear
  boundary (well under the 4-breakpoint-per-request cap).

## Suggested test coverage

- Turn with total pair bytes under the budget: no clearing, output
  unchanged (regression guard).
- Turn with total pair bytes over the budget: older pairs' `tool_result`
  content replaced with stub, `tool_use` blocks unchanged, the most recent
  pairs that fit the budget stay unchanged.
- A budget smaller than even a single pair: the single most recent pair
  still stays full, everything else clears.
- Cache-control breakpoint present exactly once at previous-turn boundary
  (existing behavior) and once at the new clear boundary when clearing
  occurred; absent when it didn't.
- Multi-turn conversation: an already-completed turn's pairs get cleared
  once the *global*, conversation-wide byte budget is exceeded, even if
  that turn's own pairs never would have crossed it alone.
- Many small turns (each under budget individually) still get bounded
  once their combined size crosses it globally.
