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
    this._isImage = false;
    this._imageResult = null;
    this._isVideo = false;
    this._videoResult = null;
    this._isSurface = false;
    this._surfaceResult = null;
    this._isArtifact = false;
    this._artifactResult = null;
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
    const titleEl = this.shadowRoot?.querySelector(".fold-title");
    if (titleEl) {
      titleEl.textContent = this._titleText();
    }
    this._updateDuration();
  }

  /**
   * Header label for this fold, independent of where it is being rendered.
   */
  _titleText() {
    const type = this.getAttribute("data-type") || "call";
    const toolName = this._extractToolName();
    return type === "call"
      ? toolName
        ? `Tool Call: ${toolName}`
        : "Tool Call"
      : "Tool Result";
  }

  /**
   * Show how long the tool took, on the result fold that reports it.
   *
   * Only result folds carry data-duration-ms: the duration is not known when
   * the call fold is injected (that happens the moment the model asks for the
   * tool), and back-filling it there later would mean reaching across two
   * shadow roots to do what one attribute on the right element does.
   */
  _updateDuration() {
    const durationEl = this.shadowRoot?.querySelector(".fold-duration");
    if (!durationEl) return;
    const raw = this.getAttribute("data-duration-ms");
    const ms = raw === null ? null : Number(raw);
    if (ms === null || Number.isNaN(ms)) {
      durationEl.textContent = "";
      return;
    }
    durationEl.textContent = window.TimeFormat
      ? window.TimeFormat.formatDurationMs(ms)
      : `${ms}ms`;
    // A tool that ran for minutes is worth noticing when scanning a long
    // answer; one that returned instantly is not.
    durationEl.classList.toggle("fold-duration--slow", ms >= 5000);
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
    this._isImage = false;
    this._imageResult = null;
    this._isVideo = false;
    this._videoResult = null;
    this._isSurface = false;
    this._surfaceResult = null;
    this._isArtifact = false;
    this._artifactResult = null;

    if (!this._bodyText) {
      this._highlightedBody = "";
      return;
    }

    const type = this.getAttribute("data-type") || "call";

    if (type === "result") {
      // A view_image result (see image_tool_result.py) — render the actual
      // image instead of dumping its base64 payload as a wall of text.
      const imageResult = window.ImageResultUtils?.parse(this._bodyText);
      if (imageResult) {
        this._isImage = true;
        this._imageResult = imageResult;
        return;
      }
      const videoResult = window.VideoResultUtils?.parse(this._bodyText);
      if (videoResult) {
        this._isVideo = true;
        this._videoResult = videoResult;
        return;
      }
      // A live app surface (see core/surface_protocol.py). Only a descriptor
      // is stored, so this renders a handle, never pixels — those live in
      // the dock outside the chat container.
      const surfaceResult = window.SurfaceResultUtils?.parse(this._bodyText);
      if (surfaceResult) {
        this._isSurface = true;
        this._surfaceResult = surfaceResult;
        return;
      }
      const artifactResult = window.ArtifactResultUtils?.parse(this._bodyText);
      if (artifactResult) {
        this._isArtifact = true;
        this._artifactResult = artifactResult;
        return;
      }
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

    const titleText = this._titleText();

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

                .fold-duration {
                    font-size: 11px;
                    font-variant-numeric: tabular-nums;
                    color: var(--text-secondary, #9a9a9a);
                    opacity: 0.85;
                }

                .fold-duration--slow {
                    color: var(--warning, #d7a55b);
                    opacity: 1;
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

                /* view_image result */
                .image-result img {
                    display: block;
                    max-width: 100%;
                    height: auto;
                    border-radius: 4px;
                    cursor: zoom-in;
                }

                .image-caption {
                    margin-top: 6px;
                    font-family: var(--font-mono, 'Consolas', monospace);
                    font-size: 12px;
                    color: var(--text-secondary, #9d9d9d);
                }

                .video-result video {
                    display: block;
                    width: 100%;
                    max-height: 70vh;
                    border-radius: 4px;
                    background: #000;
                }

                .video-status {
                    color: var(--text-secondary, #9d9d9d);
                    font-family: var(--font-mono, 'Consolas', monospace);
                    font-size: 12px;
                }

                /* Live surface handle */
                .surface-card {
                    display: flex;
                    align-items: center;
                    gap: 10px;
                }

                .surface-card-body {
                    flex: 1;
                    min-width: 0;
                }

                .surface-card-title {
                    font-weight: 600;
                    font-family: var(--font-ui, 'Segoe UI', sans-serif);
                }

                .surface-card-meta {
                    font-size: 11px;
                    color: var(--text-secondary, #9d9d9d);
                }

                .surface-card button {
                    background: var(--bg-tertiary, #3c3c3c);
                    color: var(--text-primary, #d4d4d4);
                    border: 1px solid var(--fold-border, #3e3e42);
                    border-radius: 4px;
                    padding: 4px 10px;
                    cursor: pointer;
                    font-family: var(--font-ui, 'Segoe UI', sans-serif);
                    font-size: 12px;
                }

                .surface-card button:hover {
                    background: var(--bg-hover, #4a4a4a);
                }

                .surface-card button[disabled] {
                    opacity: 0.6;
                    cursor: default;
                }
            </style>
            <div class="fold-header">
                <span class="fold-icon">${type === "call" ? "🔧" : "📦"}</span>
                <span class="fold-title">${titleText}</span>
                <span class="fold-duration"></span>
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

    if (this._isImage && this._imageResult) {
      const { mimeType, base64Data, description } = this._imageResult;
      // Defensive: this data is server-generated (view_image/Pillow), but
      // validate before interpolating into an HTML attribute regardless —
      // a malformed mime type or non-base64 payload falls back to plain
      // text rather than risk breaking out of the data: URI attribute.
      const safeMime = /^image\/[a-zA-Z0-9.+-]+$/.test(mimeType)
        ? mimeType
        : null;
      const safeData = /^[A-Za-z0-9+/=]*$/.test(base64Data) ? base64Data : null;
      if (safeMime && safeData) {
        const safeDesc = this._escapeHtml(description || "");
        bodyDiv.innerHTML = `
          <div class="image-result">
            <img src="data:${safeMime};base64,${safeData}" alt="${safeDesc}" />
            ${description ? `<div class="image-caption">${safeDesc}</div>` : ""}
          </div>
        `;
        return;
      }
      // Fell through validation — treat as plain text below instead.
    }

    if (this._isVideo && this._videoResult) {
      const result = this._videoResult;
      bodyDiv.innerHTML = '<div class="video-status">Loading video…</div>';
      const tabId = window.app?.currentTabId;
      if (!tabId) {
        bodyDiv.textContent = "Video is unavailable because no task is active.";
        return;
      }
      window.VideoResultUtils.load(tabId, result)
        .then((url) => {
          if (!this.isConnected) return;
          bodyDiv.innerHTML = "";
          const wrapper = document.createElement("div");
          wrapper.className = "video-result";
          const video = document.createElement("video");
          video.controls = true;
          video.preload = "metadata";
          video.src = url;
          wrapper.appendChild(video);
          if (result.description) {
            const caption = document.createElement("div");
            caption.className = "image-caption";
            caption.textContent = result.description;
            wrapper.appendChild(caption);
          }
          bodyDiv.appendChild(wrapper);
        })
        .catch((error) => {
          if (this.isConnected && error.message !== "Video load cancelled") {
            bodyDiv.textContent = `Could not load video: ${error.message}`;
          }
        });
      return;
    }

    if (this._isSurface && this._surfaceResult) {
      this._renderSurfaceCard(bodyDiv, this._surfaceResult);
      return;
    }

    if (this._isArtifact && this._artifactResult) {
      this._renderArtifactCard(bodyDiv, this._artifactResult);
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
   * Render the handle for a live app surface.
   *
   * What is stored in the transcript is a descriptor, never a session, so
   * this is the one fold type that can legitimately refer to something that
   * no longer exists. Clicking asks whether the surface is still running;
   * if it isn't, the card says so and stops. Nothing here ever recreates a
   * surface — they are ephemeral by design, and asking for the app again is
   * a fresh `surface_open`.
   */
  _renderSurfaceCard(bodyDiv, result) {
    const { surfaceId, width, height, description } = result;
    bodyDiv.innerHTML = "";

    const card = document.createElement("div");
    card.className = "surface-card";

    const body = document.createElement("div");
    body.className = "surface-card-body";
    const title = document.createElement("div");
    title.className = "surface-card-title";
    title.textContent = description || "Live app surface";
    const meta = document.createElement("div");
    meta.className = "surface-card-meta";
    meta.textContent = `${surfaceId} · ${width}×${height}`;
    body.append(title, meta);

    const button = document.createElement("button");
    button.textContent = "Show panel";
    button.addEventListener("click", async () => {
      const tabId = window.app?.currentTabId;
      if (!tabId) {
        meta.textContent = `${surfaceId} · no active task`;
        return;
      }
      button.disabled = true;
      button.textContent = "Attaching…";
      const attached = await window.pythonAPI.surface_attach(tabId, surfaceId);
      if (!attached?.success) {
        button.remove();
        meta.textContent = `${surfaceId} · session ended`;
        return;
      }
      await window.SurfaceDock?.show(tabId, attached);
      button.disabled = false;
      button.textContent = "Show panel";
    });

    card.append(body, button);
    bodyDiv.appendChild(card);
  }

  _renderArtifactCard(bodyDiv, manifest) {
    const artifactId = manifest.artifact_id;
    bodyDiv.innerHTML = "";
    const card = document.createElement("div");
    card.className = "surface-card";
    const body = document.createElement("div");
    body.className = "surface-card-body";
    const title = document.createElement("div");
    title.className = "surface-card-title";
    title.textContent = manifest.title;
    const meta = document.createElement("div");
    meta.className = "surface-card-meta";
    meta.textContent = `${artifactId} · interactive HTML · r${manifest.revision}`;
    body.append(title, meta);

    const button = document.createElement("button");
    button.textContent = "Show panel";
    button.addEventListener("click", async () => {
      const tabId = window.app?.currentTabId;
      if (!tabId) return;
      button.disabled = true;
      button.textContent = "Loading…";
      const attached = await window.pythonAPI.artifact_attach(
        tabId,
        artifactId,
      );
      if (!attached?.success) {
        meta.textContent = `${artifactId} · unavailable`;
      } else {
        await window.ArtifactDock?.show(tabId, attached);
      }
      button.disabled = false;
      button.textContent = "Show panel";
    });
    card.append(body, button);
    bodyDiv.appendChild(card);
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
