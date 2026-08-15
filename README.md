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
bundles the complete `web/` frontend plus the runtime PNG icon; Windows, macOS,
and Linux builds select the appropriate `.ico`, `.icns`, or `.png` source.

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

## License

Alpaca Assist is distributed under the [MIT License](LICENSE). Third-party
components retain their respective licenses; see
[THIRD_PARTY_LICENSES](THIRD_PARTY_LICENSES/README.md).
