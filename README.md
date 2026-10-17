# Alpaca Assist

A desktop chat client for LLMs, built with [PyWebView](https://pywebview.flowrl.com/):
a Python backend drives the business logic and a web frontend (HTML/CSS/JS)
renders the UI in a native window. It was originally a Tkinter application; the
UI layer is now web-based while the streaming, tool-execution, and
state-management logic remains in Python.

## Architecture

The Python process owns all business logic and exposes it to the web UI through
PyWebView's `js_api` bridge. UI updates that originate on background threads
(e.g. streaming chunks) are delivered via a JavaScript polling loop rather than
cross-thread `evaluate_js`, which keeps updates thread-safe across platforms.

```
pywebview_demo/
├── web/                          # Web frontend
│   ├── index.html                # Application shell
│   ├── css/
│   │   ├── themes.css            # Light/dark theme variables
│   │   └── app.css               # Application styles
│   └── js/
│       ├── app.js                # Frontend bootstrap
│       ├── api.js                # Python/JS API bridge (client side)
│       ├── UpdatePoller.js       # Polls Python for queued UI updates
│       ├── components/
│       │   ├── ChatDisplay.js    # Message rendering, markdown, tool folds
│       │   ├── TabManager.js     # Multi-tab handling
│       │   ├── InputArea.js      # Input controls
│       │   ├── MarkdownInput.js  # Markdown-aware text input
│       │   └── ToolFolds.js      # Collapsible tool-call widgets
│       ├── utils/
│       │   ├── markdown.js       # Markdown rendering helpers
│       │   └── helpers.js        # Misc helpers
│       └── lib/                  # Bundled third-party assets
│           ├── marked.min.js     # Markdown parser
│           ├── highlight.min.js  # Syntax highlighting
│           ├── github.min.css    # Light highlight.js theme
│           ├── nord.min.css      # Dark highlight.js theme
│           └── purify.min.js     # HTML sanitizer
├── core/                         # UI-agnostic business logic
│   ├── app_core.py               # AppCore: tabs, session, preferences
│   ├── chat_tab*.py              # Chat tab: base, streaming, tools, summary
│   ├── pack_*.py                 # Pack (remote-session) tabs and transport
│   ├── tool_output_gate.py       # Byte-budgeting of tool results
│   └── config.py, text_parsing.py
├── webview_app.py                # PyWebView entry point / main window
├── webview_api.py                # WebViewAPI: the JS <-> Python bridge
├── anthropic_ollama_server.py    # Ollama-compatible proxy -> Anthropic / Fireworks
├── bedrock_server.py             # AWS Bedrock backend
├── conversation_graph.py         # DAG conversation model (branching)
├── mcp_manager.py                # MCP server management
├── tool_call_detector.py,
│   internal_tools.py             # Tool calling + built-in tools
├── requirements.txt
└── tests/                        # pytest suite
```

## Running

```bash
pip install -r requirements.txt
python webview_app.py
```

Set `DEBUG=1` to open developer tools and disable the WebView cache:

```bash
DEBUG=1 python webview_app.py     # PowerShell: $env:DEBUG=1; python .\webview_app.py
```

### Building a desktop executable

PyInstaller must run on the operating system being targeted. Install the build
dependencies and build from the repository root:

```bash
pip install -r requirements-build.txt
pyinstaller --clean --noconfirm AlpacaAssist.spec
```

The packaged application is written to `dist/AlpacaAssist` on Windows and
Linux, or `dist/AlpacaAssist.app` on macOS. The spec embeds the native icon and
bundles the complete `web/` frontend plus the runtime PNG icon. Windows and
macOS builds embed the appropriate `.ico` or `.icns`; Linux uses the PNG as
the pywebview window icon at runtime.

### Model backends

LLM access goes through an Ollama-compatible HTTP API.
`anthropic_ollama_server.py` provides that endpoint and routes requests to
Anthropic or Fireworks (model names prefixed `accounts/fireworks/…` route to
Fireworks); `bedrock_server.py` covers AWS Bedrock. Supply the relevant
provider's API key in the environment.

### MCP servers

External tool servers are configured in `mcp_servers.json` (see
`mcp_servers.json.example`). Server-provided tools appear in chat as collapsible
fold widgets.

### Pack projects

Remote Pack tabs can optionally bind each new conversation to a project. Local
tabs remain projectless. Project definitions live on the local machine under
`~/packs`; choose **None — raw Pack tab** to retain the original unmanaged
remote behavior.

```text
~/packs/
└── my-project/
    ├── project.toml
    ├── RUNBOOK.md
    ├── SPINUP.md
    └── hosts/
        └── build-host.md
```

```toml
repo_url = "git@github.com:example/my-project.git"
branch = "main"                         # optional
workspace_base = "~/workspaces"        # optional
workspace_naming = "{project}-{session_id}" # optional
```

The app clones the repository into an independent workspace on the selected
Pack host. `RUNBOOK.md` (plus an optional matching host overlay) is included in
every model request. `SPINUP.md` is included on the first turn so the agent
performs project-specific setup before the user's task. Relative built-in file,
search, and shell tool paths default to the managed workspace. Reopening the
conversation reuses the same workspace; closing it never deletes remote files.

### Starting Pack tasks from a model

Models can discover `list_pack_targets`, then call `start_pack_task` to open a
**new** project-bound Pack tab and submit a prompt with an explicit model:

```json
{
  "request_id": "reports-refresh-2026-10-16",
  "host": "build-host",
  "project": "my-project",
  "model": "codex/gpt-5.6-sol",
  "prompt": "Update the report using the latest project data."
}
```

The project must already be defined under `~/packs` on the invoking controller;
the host is an SSH destination available to that controller. `model` must match
a name advertised by the target host's `/api/tags` endpoint. Use
`claude-code/sonnet` (or another offered Claude Code model), `codex/…`, or an
offered regular model. There is no implicit backend/model fallback for tasks.
The target host needs the updated Pack daemon (and updated bundled API proxy
if used), its usual API service, and the selected backend's CLI/credentials.
Project setup finishes before submission;
the existing runbook and first-turn spinup instructions still apply.

Ordinary models see the `packtasks_`-prefixed tools. Claude Code and Codex get
the same tools through the automatically injected `alpaca-pack-tasks` MCP
adapter. Its authenticated loopback channel requires the CLI proxy and owning
Alpaca process to run on the same machine, as they do in a Pack session. Do not
manually copy its per-process control credentials into shared MCP config.

Creation returns a `task_id` immediately. Poll `get_pack_task` for `starting`,
`started`, or `failed`; **started means the remote daemon accepted the prompt,
not that the work finished successfully**. Results and subsequent backend
errors arrive through the new conversation. Desktop-created tasks add a Pack
tab without switching focus. A task invoked inside a remote daemon belongs to
that daemon's AppCore; it is not automatically added as a top-level desktop tab.

Reuse the same `request_id` and identical arguments when retrying a tool call:
within the controller's lifetime this returns the same launch instead of
creating a second tab. Conflicting reuse is rejected. Launch status and retry
deduplication are in memory, **not durable across controller restarts**; remote
conversations/workspaces retain normal Pack persistence. Failed launches keep
their tab/session for inspection and never retry automatically. A connection
loss during submission can leave delivery uncertain: inspect that session
before launching again with a new request ID.

This is immediate execution, not scheduling. It uses existing SSH, filesystem,
and backend permissions; neither a new sandbox nor an additional approval
system is introduced. Only request remote work you intend to authorize.

### Scheduling Pack tasks

The same ordinary-model and Claude Code/Codex MCP paths expose
`create_pack_schedule`, `list_pack_schedules`, `update_pack_schedule`, and
`control_pack_schedule`. For example, run a report every Monday at 09:00
Los Angeles time:

```json
{
  "request_id": "weekly-report-v1",
  "host": "build-host",
  "project": "my-project",
  "model": "claude-code/sonnet",
  "prompt": "Update the report using the latest project data. Do not push.",
  "when": {
    "kind": "weekly",
    "time": "09:00",
    "timezone": "America/Los_Angeles",
    "weekday": 0
  }
}
```

Other `when` forms: `{"kind":"delay","seconds":600}`,
`{"kind":"once","at":"2026-12-01T09:00:00-08:00"}`, or daily with `time`
and `timezone` but no `weekday`. Weekdays are Monday=0 through Sunday=6.
One-shot times must be in the future and include an offset; delays become a
fixed UTC timestamp at creation. Recurrence uses an explicit IANA timezone,
skips nonexistent DST times, and runs repeated local times only once (the first
occurrence). Returned `next_run`, `due_at`, and `claimed_at` are Unix UTC seconds.

Schedules and occurrence history persist in `pack_schedules.db` alongside the
controller's session files. Request-ID deduplication for schedule creation
survives restarts. The app resumes the scheduler after session restoration;
a Pack daemon owns its own schedules independently of the desktop. Only one
controller may own a given store. Keep the store on a local filesystem, not
a network share, and protect it like chat history: it contains prompts.

Each occurrence gets a fresh Pack session/workspace. A durable claim precedes
launch; overlapping occurrences of the **same schedule** are blocked until the
whole remote turn ends, including tool continuations. Different schedules may
run concurrently. Missed recurring slots coalesce into one immediate catch-up
run; overdue one-shots run once on reopening. There is no host wakeup, OS
service, autostart, or execution while the owning process is stopped.

Use `control_pack_schedule` with `schedule_id` and `action` set to `pause`,
`resume`, or `cancel`. These affect future launches, not an already claimed
task. Cancel is terminal. Updating requires the full host/project/model/prompt
and `when`, and is refused while a run is active or uncertain; paused schedules
stay paused. `list_pack_schedules` includes the latest 20 occurrences.

A restart during a claimed/running occurrence, launch failure, or failed status
query marks it `needs_inspection` and blocks further occurrences. There is **no
automatic retry**: inspect the recorded host/session/workspace, verify the task
is no longer running, then explicitly `acknowledge` its `occurrence_id` through
the control tool. Acknowledgment never reruns that occurrence. `finished` only
means the turn ended (including stop/error), not that its work was correct.

Scheduling grants future execution with the host's existing permissions and
credentials, not a new security boundary. No budget enforcement or automatic
workspace cleanup is added. Review long-lived prompts, repo/runbook changes,
disk growth, backend costs, and cancellation before authorizing recurring work.

## Features

- **Streaming responses** with live markdown rendering and syntax-highlighted
  code blocks (copy buttons, language detection)
- **Multi-tab chats**, including **pack tabs** that attach to remote sessions
- **Pack-aware file links** — Markdown paths from a Pack tab are fetched from
  that tab's worker on demand, cached temporarily, and opened by the local app
- **MCP tool execution** shown in collapsible tool-fold widgets; image tool
  results render in their folds and can be embedded in assistant Markdown
  with `![caption](alpaca://image/<tool-call-id>)`. References are scoped to
  their answer and work identically in local and Pack tabs, including replay
  after switching tabs.
- **Branching conversations** — a DAG model supporting edit, fork, and
  regenerate, with stable conversation IDs and internal conversation search
- **Tool-output gating** — large tool results are byte-budgeted so context
  stays manageable
- **Dark / light themes**
- **Session persistence** — conversations auto-save and restore on launch

### Local subagents

Ask an app-managed model to delegate independent work. It can call
`internal_spawn_agent(task, context?, title?)` to open a background child tab,
then `internal_wait_agents(task_ids, timeout_seconds?)` to collect its final
answer. Spawn returns immediately; wait returns completed results and pending
IDs after at most 60 seconds, so the model can wait again. Results include a
stable transcript link and remain readable on repeated waits.

Children are grouped under their parent, show their task status, and have an
**↑ Parent** button. Open them to inspect their complete tool transcript.
Their final answer is captured only after the entire tool loop finishes—not
after the first model invocation. Stop remains available while a parent waits.
`internal_cancel_agent(task_id)`, stopping the parent, or closing either tab
cancels outstanding delegated work. Completed results are retained.

This version supports **local tabs and app-managed models only**, not Pack
tabs or the Claude Code/Codex CLI backends. It allows four running children
across the app, with no nested delegation. Children receive only the supplied
task/context, inherit the parent's model and project workspace, and share its
filesystem; coordinate edits rather than treating tabs as isolated worktrees.
Cancellation is cooperative: it stops generation and prevents later result
delivery, but does not undo writes or forcibly terminate already executing
tools. Saved completed results survive session restore. In-flight local tasks
restore as **interrupted**, never silently rerun after a restart.

Run the deterministic HTTP integration checks with:

```bash
python -m pytest tests/test_subagents.py -q
```

## Development

- **New JS <-> Python method:** add it to `webview_api.py`, then call it from
  `web/js/api.js`. For Python-initiated UI updates, queue work that the frontend
  picks up via `get_pending_js` (see `UpdatePoller.js` and the `on*` handlers in
  `app.js`).
- **Custom elements:** the UI defines `<tool-fold>` for tool call/result display.
- **Styling:** color variables live in `themes.css`; `[data-theme="light"]`
  holds light-mode overrides.

## Testing

```bash
pytest
```

The `tests/` directory contains the suite (unit and integration tests for the
API bridge, session restore, streaming, tools, and conversation model).

### Offline LLM regression

Run the 19 non-UI agent regression cases using the default model and save a
metrics report:

```bash
python offline_regression.py --output model-baseline.json
```

Each case gets an isolated temporary workspace and uses the production
`ChatTab` streaming, text-embedded tool-call detection, internal tool dispatch,
continuation, and tool-output gating paths. The suite covers reading, searching,
writing, modifying, shell validation, gated large results, secure archive
handling, retry semantics, cross-file debugging, state-machine reasoning,
pagination boundaries, TTL caching, UTF-8 streaming, dependency ordering, and
transactional batch updates. Additional cases exercise test-first regression
repair, multi-file refactoring, mocked HTTP integration, JavaScript behavior,
and deterministic performance debugging.
The report contains correctness, tool and invocation counts, wall time, provider-reported
input/cached/output tokens, and estimated cost. Any model supported by the
configured proxy can be selected. Models listed in `MODEL_PRICING` use their
built-in verified rates; provide input, cached-input, and output rates for other
models:

```bash
python offline_regression.py \
  --model kimi-k2p7-code \
  --input-cost-per-million 0.95 \
  --cached-input-cost-per-million 0.19 \
  --output-cost-per-million 4.00 \
  --output model-baseline.json
```

The runner can start its own local proxy for Fireworks model aliases. For any
other backend, point it at an already configured Ollama-compatible proxy:

```bash
python offline_regression.py \
  --model claude-sonnet-4-6 \
  --api-url http://localhost:11434 \
  --input-cost-per-million 0 \
  --cached-input-cost-per-million 0 \
  --output-cost-per-million 0 \
  --output model-baseline.json
```

Zero rates are valid for subscription-backed or locally hosted models when the
runner should track time and tokens without assigning an API cost.

To fail when aggregate wall time, total tokens, or cost increases by more than
10% relative to a prior report:

```bash
python offline_regression.py --baseline model-baseline.json
```

The Python app's proxy prompt uses the efficient execution policy by default,
so the agent suite inherits the same policy rather than adding benchmark-only
instructions. Experiments can select alternate tool profiles, control Fireworks
generation settings, and record the configuration in the report for review:

```bash
python offline_regression.py \
  --temperature 0.7 \
  --output model-efficient.json
```

The built-in Fireworks runner limits each invocation to 8,000 output tokens.
It also stops a case at 20 invocations, 20 tool calls, three identical tool
calls, 250,000 total tokens, or $0.25 of estimated provider cost. Override the
corresponding `--max-*` options when a deliberate stress case needs a larger
budget. Failed cases retain a bounded trace of their last 20 tool calls.
Use `--case CASE_ID` to rerun only a failed or expensive case without paying
for the complete suite.

See [`benchmarks/offline_regression_report.md`](benchmarks/offline_regression_report.md)
for measured tuning results and tradeoffs.

## License

Alpaca Assist is distributed under the [MIT License](LICENSE). Third-party
components retain their respective licenses; see
[THIRD_PARTY_LICENSES](THIRD_PARTY_LICENSES/README.md).
