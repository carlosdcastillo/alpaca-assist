# Alpaca Assist - PyWebView Migration

This is a PyWebView-based migration of the Alpaca Assist desktop chat application, originally built with Tkinter.

## Architecture

### New Structure

```
pywebview_demo/
├── web/                          # Web frontend
│   ├── index.html               # Main application shell
│   ├── css/
│   │   ├── themes.css           # Light/dark theme variables
│   │   └── app.css              # Main application styles
│   └── js/
│       ├── lib/                 # Bundled third-party libraries
│       │   ├── marked.min.js    # Markdown parser
│       │   ├── highlight.min.js # Syntax highlighting
│       │   ├── github.min.css   # Light theme highlight.js
│       │   ├── github-dark.min.css # Dark theme highlight.js
│       │   └── purify.min.js    # HTML sanitizer
│       ├── api.js               # Python/JS API bridge
│       ├── UpdatePoller.js      # JS polling for UI updates
│       ├── app.js               # Main application bootstrap
│       ├── components/
│       │   ├── ChatDisplay.js   # Message rendering, markdown, folds
│       │   ├── TabManager.js    # Multi-tab handling
│       │   ├── InputArea.js     # Text input
│       │   ├── ToolFolds.js     # Collapsible tool widgets
│       │   └── QABar.js         # Question/answer action bars
│       └── utils/
│           ├── markdown.js      # Markdown rendering utilities
│           └── helpers.js       # Helper utilities
├── core/                        # Python business logic
│   ├── app_core.py              # UI-agnostic AppCore
│   └── chat_tab.py              # Refactored ChatTab
├── webview_app.py               # PyWebView main application
├── webview_api.py               # Python/JS API bridge
└── requirements.txt             # Updated dependencies
```

### Key Migration Changes

1. **UI Layer Replaced**: Tkinter widgets replaced with web-based UI
2. **Thread Safety**: Implemented Pattern C (JS polling) for thread-safe UI updates
3. **Business Logic Preserved**: All streaming, tool execution, and state management code remains in Python
4. **Bidirectional Bridge**: PyWebView's `js_api` enables seamless Python/JS communication

## Running the Application

### Prerequisites

```bash
pip install -r requirements.txt
```

### Development Mode

```bash
python webview_app.py
```

### Building Executable

```bash
python build.py
```

The build script handles bundling the `web/` directory into the executable.

## Key Features

- **Streaming Responses**: Real-time LLM response streaming with markdown rendering
- **MCP Tool Execution**: Tool calls and results displayed in collapsible fold widgets
- **Multi-Tab Support**: Create, switch, and close multiple chat tabs
- **Conversation Graph**: DAG-based branching conversation model (edit, fork, regenerate)
- **Syntax Highlighting**: Code blocks with language detection and copy buttons
- **Dark/Light Themes**: Toggle between themes
- **Session Persistence**: Auto-save and restore conversations

## API Bridge

The `WebViewAPI` class provides bidirectional communication:

### JavaScript → Python
- `create_tab(title)` - Create new tab
- `send_message(tab_id, message, images)` - Send chat message
- `stop_streaming(tab_id)` - Stop current streaming
- `get_preferences()` / `save_preferences(prefs)` - Settings management
- `get_models()` - Fetch available LLM models
- `export_conversation(tab_id)` - Export to HTML

### Python → JavaScript (via polling)
- `onContentUpdate(tab_id, update)` - Streaming content chunk
- `onStreamingStart/End(tab_id, index)` - Streaming lifecycle
- `onError(tab_id, error)` - Error notifications
- `injectToolFold(tab_id, fold_data)` - Tool fold widget injection

## Thread Safety

This implementation uses **Pattern C (JavaScript polling)** as recommended in the migration spec:

1. Python queues UI updates in a thread-safe queue
2. JavaScript polls every 50ms via `get_pending_js()`
3. JavaScript executes updates directly via `Function()` constructor

This avoids thread-safety concerns with `evaluate_js()` and works reliably across platforms.

## Removed Features

Per the migration spec, the following features were removed:
- File completions (`@/`) - Not used by customers
- Prompt completions (`@@`) - Not used by customers

## Migration Status

### Completed
- [x] Core architecture with PyWebView
- [x] API bridge with bidirectional communication
- [x] Thread-safe UI updates via JS polling
- [x] Tab management
- [x] Chat display with streaming markdown
- [x] Input area with model selection
- [x] Tool fold widgets (collapsible)
- [x] Q/A action bars
- [x] Theme system (dark/light)
- [x] Session persistence framework

### TODO
- [ ] Full conversation state rendering on tab switch
- [ ] Image attachment UI
- [ ] Find dialog
- [ ] MCP tools configuration dialog
- [ ] Conversation history browser
- [ ] Complete integration testing

## Development Notes

### Adding New API Methods

1. Add method to `webview_api.py` in the appropriate section
2. Add corresponding method to `web/js/api.js`
3. For Python → JS updates, use `_safe_evaluate_js()` and handle in `app.js`

### Styling

CSS variables in `themes.css` control the color scheme. The `[data-theme="light"]` selector handles light mode overrides.

### Custom Elements

The app uses Web Components for complex UI elements:
- `<tool-fold>` - Collapsible tool call/result display
- `<qa-bar>` - Question/answer action buttons

## License

Same as original Alpaca Assist project.
