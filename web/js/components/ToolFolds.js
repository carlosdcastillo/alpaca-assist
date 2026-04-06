/**
 * ToolFold - Custom element for collapsible tool call/result display
 *
 * Features:
 * - Collapsible header with icon
 * - Syntax-highlighted content
 * - Shadow DOM for style isolation
 */
class ToolFold extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this.expanded = false;
    this._bodyText = "";
    this._highlightedBody = "";
    this._isPlainText = false;
  }

  /**
   * Set the body content
   */
  setBody(text) {
    this._bodyText = text;
    this._highlightBody();
    // If shadow DOM is already set up, update the display immediately
    if (this.shadowRoot && this.shadowRoot.querySelector(".fold-body")) {
      this._updateTitle();
      this._renderBody();
    }
    // Otherwise, _updateTitle and _renderBody will be called in connectedCallback
  }

  /**
   * Update the fold title based on body content
   */
  _updateTitle() {
    const type = this.getAttribute("data-type") || "call";
    const toolName = this._extractToolName();
    const titleText =
      type === "call"
        ? toolName
          ? `Tool Call: ${toolName}`
          : "Tool Call"
        : "Tool Result";

    const titleEl = this.shadowRoot?.querySelector(".fold-title");
    if (titleEl) {
      titleEl.textContent = titleText;
    }
  }

  /**
   * Check if content is an error message
   */
  _isErrorContent(text) {
    if (!text) return false;
    const lower = text.toLowerCase();
    return (
      lower.includes("error") ||
      lower.includes("exception") ||
      lower.includes("failed") ||
      lower.includes("traceback") ||
      lower.includes("syntaxerror") ||
      lower.includes("runtimeerror")
    );
  }

  /**
   * Check if content is valid JSON
   */
  _isJsonContent(text) {
    if (!text) return false;
    try {
      JSON.parse(text);
      return true;
    } catch (e) {
      return false;
    }
  }

  /**
   * Try to extract readable text from a tool result.
   * Mirrors Python's _format_tool_result_for_display logic.
   * Returns the extracted string, or null if the content should be shown as JSON.
   */
  _extractResultText(text) {
    if (!text) return null;
    let parsed;
    try {
      parsed = JSON.parse(text);
    } catch (e) {
      return null;
    }
    if (typeof parsed !== "object" || parsed === null) return null;

    // MCP-style: {"content": [{"type": "text", "text": "..."}]}
    if (Array.isArray(parsed.content)) {
      const texts = parsed.content
        .filter(
          (item) =>
            item && item.type === "text" && typeof item.text === "string",
        )
        .map((item) => item.text);
      if (texts.length > 0 && !this._isErrorContent(texts.join("\n"))) {
        return texts.join("\n");
      }
    }

    // Simple single-key wrapper: {"result": "..."} or {"content": "..."}
    const keys = Object.keys(parsed);
    if (keys.length === 1) {
      const val = parsed[keys[0]];
      if (typeof val === "string" && !this._isErrorContent(val)) {
        return val;
      }
    }

    return null; // show as JSON
  }

  /**
   * Highlight body content with hljs
   */
  _highlightBody() {
    if (!this._bodyText) {
      this._highlightedBody = "";
      return;
    }

    const type = this.getAttribute("data-type") || "call";

    if (type === "result") {
      // Try to extract plain text from MCP-style JSON before deciding how to display.
      const extracted = this._extractResultText(this._bodyText);
      if (extracted !== null) {
        this._highlightedBody = this._escapeHtml(extracted);
        this._isPlainText = true;
        return;
      }
      // Non-JSON plain text
      if (!this._isJsonContent(this._bodyText)) {
        this._highlightedBody = this._escapeHtml(this._bodyText);
        this._isPlainText = true;
        return;
      }
    }

    this._isPlainText = false;

    try {
      // Try to parse as JSON for better formatting
      let content = this._bodyText;
      try {
        const parsed = JSON.parse(content);
        content = JSON.stringify(parsed, null, 2);
      } catch (e) {
        // Not valid JSON, use as-is
      }

      // Use hljs.highlight for shadow DOM compatibility
      const result = hljs.highlight(content, { language: "json" });
      this._highlightedBody = result.value;
    } catch (e) {
      // Fallback to escaped text
      this._highlightedBody = this._escapeHtml(this._bodyText);
    }
  }

  /**
   * Escape HTML special characters
   */
  _escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
  }

  /**
   * Extract tool name from JSON content
   */
  _extractToolName() {
    if (!this._bodyText) return null;
    try {
      const parsed = JSON.parse(this._bodyText);
      // Handle {"tool_call": {"name": "...", ...}} format
      if (parsed.tool_call && parsed.tool_call.name) {
        return parsed.tool_call.name;
      }
      // Handle {"name": "...", ...} format (direct)
      if (parsed.name) {
        return parsed.name;
      }
    } catch (e) {
      // Not valid JSON or no name field
    }
    return null;
  }

  /**
   * Called when element is added to DOM
   */
  connectedCallback() {
    const type = this.getAttribute("data-type") || "call";
    const answerIndex = this.getAttribute("data-answer-index") || "0";

    // Extract tool name for display
    const toolName = this._extractToolName();
    const titleText =
      type === "call"
        ? toolName
          ? `Tool Call: ${toolName}`
          : "Tool Call"
        : "Tool Result";

    this.shadowRoot.innerHTML = `
            <style>
                :host {
                    display: block;
                    margin: 4px 0;
                    font-family: var(--font-mono, 'Consolas', monospace);
                    font-size: 13px;
                }

                .fold-header {
                    background: var(--fold-bg, #2d2d30);
                    border: 1px solid var(--fold-border, #3e3e42);
                    border-radius: 6px;
                    padding: 6px 12px;
                    cursor: pointer;
                    display: flex;
                    align-items: center;
                    gap: 8px;
                    user-select: none;
                    transition: background 0.15s;
                }

                .fold-header:hover {
                    background: var(--bg-hover, #3c3c3c);
                }

                .fold-icon {
                    font-size: 14px;
                    width: 20px;
                    text-align: center;
                }

                .fold-title {
                    flex: 1;
                    font-weight: 500;
                }

                .fold-arrow {
                    font-size: 10px;
                    transition: transform 0.2s;
                }

                .fold-arrow.expanded {
                    transform: rotate(90deg);
                }

                .fold-body {
                    display: none;
                    background: var(--fold-body-bg, #1e1e1e);
                    border: 1px solid var(--fold-border, #3e3e42);
                    border-top: none;
                    border-radius: 0 0 6px 6px;
                    padding: 12px;
                    overflow-x: auto;
                }

                .fold-body.expanded {
                    display: block;
                }

                pre {
                    margin: 0;
                    white-space: pre-wrap;
                    word-break: break-word;
                }

                code {
                    font-family: var(--font-mono, 'Consolas', monospace);
                    font-size: 12px;
                    line-height: 1.4;
                }

                /* Syntax highlighting colors */
                .hljs {
                    color: var(--text-primary, #d4d4d4);
                }
                .hljs-key {
                    color: #9cdcfe;
                }
                .hljs-string {
                    color: #ce9178;
                }
                .hljs-number {
                    color: #b5cea8;
                }
                .hljs-boolean {
                    color: #569cd6;
                }
                .hljs-null {
                    color: #569cd6;
                }

                /* Plain text content styling */
                .plain-text-content {
                    font-family: var(--font-mono, 'Consolas', monospace);
                    font-size: 12px;
                    line-height: 1.5;
                    color: var(--text-primary, #d4d4d4);
                    white-space: pre-wrap;
                    word-break: break-word;
                }
            </style>
            <div class="fold-header">
                <span class="fold-icon">${type === "call" ? "🔧" : "📦"}</span>
                <span class="fold-title">${titleText}</span>
                <span class="fold-arrow ${
                  this.expanded ? "expanded" : ""
                }">▶</span>
            </div>
            <div class="fold-body ${this.expanded ? "expanded" : ""}">
                <pre><code class="language-json"></code></pre>
            </div>
        `;

    // Update title and render body content
    this._updateTitle();
    this._renderBody();

    // Bind click handler
    const header = this.shadowRoot.querySelector(".fold-header");
    header.addEventListener("click", () => this.toggle());
  }

  /**
   * Render body content into the DOM
   */
  _renderBody() {
    const bodyDiv = this.shadowRoot?.querySelector(".fold-body");
    if (!bodyDiv) {
      console.log(`[TOOLFOLD DEBUG] _renderBody: no bodyDiv found`);
      return;
    }
    if (!this._highlightedBody) {
      console.log(
        `[TOOLFOLD DEBUG] _renderBody: no highlightedBody, bodyText="${this._bodyText?.substring(
          0,
          50,
        )}"`,
      );
      return;
    }

    console.log(
      `[TOOLFOLD DEBUG] _renderBody: isPlainText=${this._isPlainText}, bodyLength=${this._highlightedBody.length}`,
    );

    // For plain text results, use a simple div instead of pre/code block
    if (this._isPlainText) {
      bodyDiv.innerHTML = `<div class="plain-text-content">${this._highlightedBody}</div>`;
    } else {
      // For JSON/code content, use the pre/code structure
      const codeBlock = bodyDiv.querySelector("code");
      if (codeBlock) {
        codeBlock.innerHTML = this._highlightedBody;
      } else {
        // If the structure doesn't exist yet (e.g., after setBody is called after connectedCallback)
        bodyDiv.innerHTML = `<pre><code class="language-json">${this._highlightedBody}</code></pre>`;
      }
    }
  }

  /**
   * Toggle fold state
   */
  toggle() {
    this.expanded = !this.expanded;

    const arrow = this.shadowRoot?.querySelector(".fold-arrow");
    const body = this.shadowRoot?.querySelector(".fold-body");

    if (arrow) {
      arrow.classList.toggle("expanded", this.expanded);
    }
    if (body) {
      body.classList.toggle("expanded", this.expanded);
    }
  }

  /**
   * Expand the fold
   */
  expand() {
    if (!this.expanded) {
      this.toggle();
    }
  }

  /**
   * Collapse the fold
   */
  collapse() {
    if (this.expanded) {
      this.toggle();
    }
  }

  /**
   * Get body text
   */
  getBody() {
    return this._bodyText;
  }

  /**
   * Check if fold is expanded
   */
  isExpanded() {
    return this.expanded;
  }
}

// Register the custom element
try {
  customElements.define("tool-fold", ToolFold);
  console.log("[TOOLFOLD DEBUG] Custom element registered successfully");
} catch (e) {
  console.error("[TOOLFOLD DEBUG] Failed to register custom element:", e);
}

// Export for use in other components
window.ToolFold = ToolFold;
