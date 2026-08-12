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
├── tool_executor.py,
│   tool_call_detector.py,
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

## Features

- **Streaming responses** with live markdown rendering and syntax-highlighted
  code blocks (copy buttons, language detection)
- **Multi-tab chats**, including **pack tabs** that attach to remote sessions
- **MCP tool execution** shown in collapsible tool-fold widgets; image tool
  results render inline
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

Same as the original Alpaca Assist project.
