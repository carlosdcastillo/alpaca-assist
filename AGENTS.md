# AGENTS.md — Developer / Agent Notes for pywebview_demo

This file documents non-obvious gotchas, architecture decisions, and hard-won debugging lessons for this project. Read before touching streaming, folds, or the Python↔JS bridge.

---

## Reference Implementation

**`C:\Users\Carlos\Desktop\ollama_query`** is the authoritative, fully-working Tkinter version of the same chat app. It has a solved fold system, wire protocol, and streaming pipeline. When something is broken here and you are unsure why, **read that codebase first** before writing fix attempts. It will usually answer the question in one read.

---

## Tool Call Detection

### The model does NOT use native Ollama `tool_calls`

The LLM outputs tool calls as **JSON embedded in the text content field**, not in Ollama's native `message.tool_calls` array. The streamed content looks like:

```
{"tool_call": {"id": "...", "name": "server_tool", "arguments": {...}}}
```

This means:
- `message.get("tool_calls")` is **always falsy** — do not rely on it as the sole detection path.
- Detection happens in `_check_and_handle_tool_call` via `ToolCallDetector`, which scans each content chunk for the `{"tool_call"` pattern.
- The native `tool_calls` path in `_process_stream` is kept as a secondary fallback but is not what fires in practice.

Both paths must populate `pending_call_fold_jsons` so the call fold gets injected after the `done` signal. See below.

---

## Fold Injection Timing

### Inject call folds AFTER the `done` signal, not during streaming

The JS answer buffer (`answerBuffers` map in `ChatDisplay`) is created the first time `appendToAnswerBuffer` is called for a given `answer_index`. For a pure tool-call response (no text content), this happens when the `done` update is processed.

If `injectToolFold(call)` JS is queued **before** the `done` update, the buffer does not exist yet and the fold has nowhere to attach.

**Pattern that works:**
```python
# In _process_stream — collect tool JSONs, inject AFTER done
pending_call_fold_jsons: list[str] = []

# Detection (native path)
if message.get("tool_calls"):
    pending_call_fold_jsons.append(tool_json)

# Detection (text-embedded path) — pass the list in!
content_chunk = self._check_and_handle_tool_call(
    content_chunk, answer_index, pending_call_fold_jsons
)

# On done: queue done FIRST, then inject folds
if data.get("done"):
    api.on_content_update(tab_id, done_update)          # creates JS buffer
    for tool_json in pending_call_fold_jsons:
        _inject_tool_call_fold(answer_index, tool_json)  # buffer exists now
```

Result folds work naturally because they are injected by `_execute_tool` after the MCP call completes, by which time the buffer always exists.

---

## JS↔Python Bridge (UpdatePoller)

- Python queues JS strings via `_safe_evaluate_js` → `_pending_updates` queue.
- JS polls every 50 ms with `get_pending_js()`, which atomically drains the entire queue.
- All strings from one poll batch are executed **synchronously** in order before the next batch.
- **Order matters.** Items queued close together (within one Python logical operation) will land in the same batch and execute in sequence. Rely on this for the done-before-fold ordering above.

---

## ChatDisplay DOM Structure

Each answer creates a segment-based layout inside `answer-wrapper`. Folds are inserted as **siblings** of text segments, not in a dedicated container:

```
answer-wrapper
  answer-header                       ← "Assistant" label
  div.answer.answer-segment.answer-N  ← first text segment (pre-fold text)
  <tool-fold id="fold-call-N-r0">     ← call fold (sibling, not child)
  <tool-fold id="fold-result-N-r0">   ← result fold (sibling)
  div.answer.answer-segment.answer-N  ← new segment created by _createNewSegment
  ...                                 ← more fold/segment pairs for multi-tool turns
```

`_createNewSegment` is called after every result fold so continuation text lands in the right position. `_renderAnswerBuffer` only touches `answerElement.innerHTML` (the current segment); fold siblings are unaffected.

---

## Fold Re-injection on Tab Switch

When the user switches tabs, `chatDisplay.clear()` wipes all state and `_renderFullAnswer` re-renders the saved conversation. The `appendContent` method returns early for `type === 'tool_call'` and `type === 'tool_result'` — folds are **never** injected through that path.

Re-render must call `chatDisplay.injectFoldWithId(answerIndex, content, type, foldId)` directly for these components. Use a deterministic ID like `fold-call-{answerIndex}-{component.id}`.

The buffer may not exist yet when `injectFoldWithId` is called (tool components typically precede text). The `pendingFolds` flush inside `appendToAnswerBuffer` catches them: the `done` signal processed at the end of `_renderFullAnswer` always creates the buffer and flushes all pending folds.

---

## Syntax Highlighting — Guard Against Unknown Languages

`renderer.code` in `ChatDisplay._configureMarked` **must** check `hljs.getLanguage(language)` before calling `hljs.highlight()`. An unrecognized language identifier (e.g. ` ```bash configuration `) makes `hljs.highlight()` throw synchronously. That exception propagates up through `appendToAnswerBuffer` → `_renderFullAnswer` → the `for` loop in `_renderLegacyChatState`, aborting the loop mid-way. Every Q&A pair after the bad code block silently disappears from the render.

```javascript
// WRONG — throws on unknown languages like "bash configuration"
const highlighted = language ? hljs.highlight(code, { language }).value : escapedCode;

// CORRECT
const highlighted =
  language && hljs.getLanguage(language)
    ? hljs.highlight(code, { language }).value
    : escapedCode;
```

The `highlight` option in `marked.setOptions` already does this check; the custom `renderer.code` must too.

---

## Tab Switch Race Guards in `_onTabSwitched`

`_onTabSwitched` is `async` and calls `await api.get_conversation_state(tabId)`. Two separate races can make a stale result overwrite the correct render:

**1. Different-tab race** (original fix): user switches A→B→A, and the `get_conversation_state(B)` call resolves *after* the return to A has already rendered.
Guard: `if (tabId !== this.currentTabId) return;`

**2. Same-tab sequence race**: `_onTabSwitched(X)` fires twice for the same tab (e.g., revival auto-switch at seq=1, then user switch-back at seq=3). The seq=1 result arriving after seq=3 would overwrite the correct render even though `tabId` matches.
Guard: increment `_tabSwitchSeq` on entry, capture it as `mySeq`; add `mySeq !== this._tabSwitchSeq` to the discard check.

Both guards must be present:
```javascript
if (tabId !== this.currentTabId || mySeq !== this._tabSwitchSeq) return;
```

---

## `revive_conversation` Timing

`revive_conversation` in Python:
1. Creates a new tab with **empty** `ChatState` and queues `createTabUI(tabId, title, true)` via `_safe_evaluate_js`.
2. Calls `load_from_data(tab_data)` to populate the tab from DB — **before returning**.
3. Returns `{"success": True}` to JS.

By the time UpdatePoller fires the queued `createTabUI` (≥50 ms later), `load_from_data` has already run. So `get_conversation_state` called from the resulting `_onTabSwitched` always returns the full DB state — there is no window where it could return the empty initial state.

---

## `wait_for_fold_rendered` — Python Blocks on Fold Render

After injecting a **result** fold, the tool thread calls `api.wait_for_fold_rendered(tab_id, fold_id, timeout=2.0)`, which blocks the streaming thread for up to 2 seconds waiting for JS to call `on_fold_rendered`.

JS calls `on_fold_rendered` from inside `_appendFold` — but only if `tabId === this.currentTabId` (the `injectToolFold` guard). If the user has switched away from the tab, the fold is never injected, `on_fold_rendered` is never called, and Python times out. Streaming then continues regardless (the timeout is a best-effort sync, not a hard requirement).

---

## `tool-fold` Custom Element

- Defined in `web/js/components/ToolFolds.js` using Shadow DOM.
- `setBody(text)` can be called before the element is in the DOM — it stores the highlighted text.
- `connectedCallback` fires on `appendChild` and renders the body into the shadow root.
- The element starts collapsed; click the header to expand.
