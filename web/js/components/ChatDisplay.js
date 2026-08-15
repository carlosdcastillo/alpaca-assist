/**
 * ChatDisplay - Handles message rendering, streaming content, and markdown
 *
 * Features:
 * - Streaming content with buffering and re-rendering
 * - Markdown rendering with syntax highlighting
 * - Tool fold widget injection
 * - Progress indicator display
 */
class ChatDisplay {
  constructor(containerId) {
    this.container = document.getElementById(containerId);
    if (!this.container) {
      throw new Error(`ChatDisplay: Container #${containerId} not found`);
    }

    // Map answer_index -> the current render segment.
    this.answerBuffers = new Map();

    // Store pending fold data
    // Key: `${answerIndex}-${foldId}`, Value: { content, type, answerIndex }
    this.pendingFolds = new Map();
    this.injectedFolds = new Map();

    // Map answer_index -> Map(tool_call_id -> raw tool_result content), so
    // same-answer alpaca:// image/video refs can resolve to their media.
    // Scoped per answer_index deliberately —
    // tool-call ids are model-assigned free text, only guaranteed unique
    // within the answer that generated them.
    this.answerToolResults = new Map();
    this.gatedToolResultLoads = new Map();
    this.gatedToolResultEpoch = 0;

    // Track streaming state
    this.isStreaming = false;
    this.currentAnswerIndex = -1;

    // Configure marked
    this._configureMarked();

    // Single contenteditable root: the browser handles all caret navigation
    // natively across every message — no island-crossing workarounds needed.
    this.container.contentEditable = "true";
    this.container.spellcheck = false;

    // Block text editing — we only want caret placement and text selection.
    // Allow input in real form elements (textarea, input) that live inside
    // the container (e.g. the inline edit / fork panels).
    this.container.addEventListener(
      "beforeinput",
      (e) => {
        if (e.target.tagName === "TEXTAREA" || e.target.tagName === "INPUT")
          return;
        e.preventDefault();
      },
      true,
    );

    // PageDown/PageUp overshoot in contentEditable: the browser moves the
    // caret a full page THEN scrolls to follow it, causing a double jump.
    // Intercept both, do a precise one-viewport scroll, then snap the caret
    // to the new visible edge.
    this.container.addEventListener(
      "keydown",
      (e) => {
        if (e.key !== "PageDown" && e.key !== "PageUp") return;
        e.preventDefault();
        const isDown = e.key === "PageDown";
        this.container.scrollTop += isDown
          ? this.container.clientHeight
          : -this.container.clientHeight;
        this._placeCaretInViewport(isDown);
      },
      true,
    );

    this._createImageOverlay();

    // Use delegation so images created by streaming Markdown and images inside
    // tool-fold shadow roots are covered without per-render event handlers.
    this.container.addEventListener("click", (event) => {
      const image = event
        .composedPath()
        .find((node) => node instanceof HTMLImageElement);
      if (image) this._openImageOverlay(image);

      const link = event.target.closest?.("a[href]");
      const href = link?.getAttribute("href") || "";
      if (!link || href.startsWith("alpaca://")) return;

      // Relative file links otherwise navigate the WebView away from the app.
      // Send every ordinary Markdown link through Python so local files and
      // HTTP(S) URLs consistently open in the user's default application.
      event.preventDefault();
      window.pythonAPI?.open_link(href).then((result) => {
        if (!result?.success) {
          window.app?.setStatusMessage(
            `Could not open ${href}: ${result?.error || "unknown error"}`,
          );
        }
      });
    });

    // Desktop WebViews do not provide the browser status UI that normally
    // previews a link's destination. Surface Markdown link targets in the
    // app status bar instead, while preserving the conversation statistics.
    this.container.addEventListener("mouseover", (event) => {
      const link = event.target.closest?.("a[href]");
      if (link && !link.contains(event.relatedTarget)) {
        this._showLinkDestination(link);
      }
    });
    this.container.addEventListener("mouseout", (event) => {
      const link = event.target.closest?.("a[href]");
      if (link && !link.contains(event.relatedTarget)) {
        this._restoreStatusAfterLink();
      }
    });
  }

  _showLinkDestination(link) {
    const status = document.getElementById("status-text");
    if (!status) return;

    this.linkStatusPrevious = {
      text: status.textContent,
      title: status.getAttribute("title"),
    };
    this.linkStatusPreview = `Open: ${link.getAttribute("href")}`;
    status.textContent = this.linkStatusPreview;
    status.title = link.getAttribute("href");
  }

  _restoreStatusAfterLink() {
    const status = document.getElementById("status-text");
    if (
      !status ||
      !this.linkStatusPrevious ||
      status.textContent !== this.linkStatusPreview
    )
      return;

    status.textContent = this.linkStatusPrevious.text;
    if (this.linkStatusPrevious.title === null) {
      status.removeAttribute("title");
    } else {
      status.title = this.linkStatusPrevious.title;
    }
    this.linkStatusPrevious = null;
    this.linkStatusPreview = null;
  }

  _createImageOverlay() {
    const overlay = document.createElement("div");
    overlay.className = "conversation-image-overlay";
    overlay.setAttribute("role", "dialog");
    overlay.setAttribute("aria-modal", "true");
    overlay.setAttribute("aria-label", "Full-size conversation image");
    overlay.setAttribute("aria-hidden", "true");
    overlay.innerHTML = `
      <button class="conversation-image-overlay-close" type="button" aria-label="Close full-size image">&times;</button>
      <img alt="" />
    `;
    document.body.appendChild(overlay);

    this.imageOverlay = overlay;
    this.imageOverlayImage = overlay.querySelector("img");
    this.imageOverlayClose = overlay.querySelector("button");
    this.imageOverlayClose.addEventListener("click", () =>
      this._closeImageOverlay(),
    );
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay || event.target === this.imageOverlayImage) {
        this._closeImageOverlay();
      }
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && overlay.classList.contains("active")) {
        this._closeImageOverlay();
      }
    });
  }

  _openImageOverlay(image) {
    this.imageOverlayPreviousFocus = document.activeElement;
    this.imageOverlayImage.src = image.currentSrc || image.src;
    this.imageOverlayImage.alt = image.alt || "Full-size conversation image";
    this.imageOverlay.classList.add("active");
    this.imageOverlay.setAttribute("aria-hidden", "false");
    this.imageOverlayClose.focus();
  }

  _closeImageOverlay() {
    if (!this.imageOverlay.classList.contains("active")) return;
    this.imageOverlay.classList.remove("active");
    this.imageOverlay.setAttribute("aria-hidden", "true");
    this.imageOverlayImage.removeAttribute("src");
    this.imageOverlayPreviousFocus?.focus?.();
    this.imageOverlayPreviousFocus = null;
  }

  /**
   * Configure marked.js with custom renderer
   */
  _configureMarked() {
    const renderer = new marked.Renderer();

    // Custom code block rendering with header and copy button.
    // marked v5+ calls renderer.code with a single token object
    // ({ text, lang, ... }) instead of positional (code, language) args.
    renderer.code = ({ text: code, lang: language }) => {
      const escapedCode = this._escapeHtml(code);
      // Guard against unknown language identifiers (e.g. "bash configuration")
      // that hljs.highlight() throws on, which would abort the entire render loop.
      const highlighted =
        language && hljs.getLanguage(language)
          ? hljs.highlight(code, { language }).value
          : escapedCode;

      return `
                <div class="code-block">
                    <div class="code-header">
                        <span class="lang">${language || "text"}</span>
                        <button class="copy-btn" onclick="app.copyCode(this)">Copy</button>
                    </div>
                    <pre><code class="language-${language}">${highlighted}</code></pre>
                </div>
            `;
    };

    marked.setOptions({
      renderer: renderer,
      highlight: (code, lang) => {
        if (lang && hljs.getLanguage(lang)) {
          return hljs.highlight(code, { language: lang }).value;
        }
        return this._escapeHtml(code);
      },
      breaks: true,
      gfm: true,
    });

    // Allow app-internal and local file links to survive DOMPurify sanitization.
    // DOMPurify strips unrecognized URL schemes from href by default.
    // This global hook runs for every DOMPurify.sanitize() call in the app.
    DOMPurify.addHook("uponSanitizeAttribute", (node, data) => {
      if (
        data.attrName === "href" &&
        typeof data.attrValue === "string" &&
        (data.attrValue.startsWith("alpaca://conv/") ||
          data.attrValue.startsWith("alpaca://video/") ||
          data.attrValue.startsWith("file://"))
      ) {
        data.forceKeepAttr = true;
      }
    });
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
   * Render TeX delimiters after the Markdown HTML has been sanitized.
   * KaTeX ignores code/pre elements, so examples containing literal TeX stay
   * untouched. Invalid or temporarily incomplete streaming expressions remain
   * readable instead of aborting the rest of the answer render.
   */
  _renderMath(element) {
    if (typeof renderMathInElement !== "function") return;

    renderMathInElement(element, {
      delimiters: [
        { left: "$$", right: "$$", display: true },
        { left: "\\[", right: "\\]", display: true },
        { left: "\\(", right: "\\)", display: false },
      ],
      throwOnError: false,
      strict: false,
    });
  }

  /**
   * Return a stable key only for the outer span created by KaTeX auto-render.
   */
  _renderedMathKey(element) {
    if (!(element instanceof HTMLElement) || element.tagName !== "SPAN")
      return null;
    const rendered = element.firstElementChild;
    if (
      element.childElementCount !== 1 ||
      !rendered?.matches(".katex, .katex-display")
    )
      return null;
    const annotation = rendered.querySelector(
      'annotation[encoding="application/x-tex"]',
    );
    if (!annotation) return null;
    return `${rendered.matches(".katex-display") ? "display" : "inline"}:$${
      annotation.textContent
    }`;
  }

  /**
   * Reconcile a detached render into the live DOM without replacing unchanged
   * nodes. In particular, a stable KaTeX wrapper is never removed or moved,
   * which prevents WebKit from painting blank/raw formula frames.
   */
  _updateRenderedContent(current, next) {
    if (current.nodeType !== next.nodeType) {
      current.replaceWith(next);
      return;
    }

    if (
      current.nodeType === Node.TEXT_NODE ||
      current.nodeType === Node.COMMENT_NODE
    ) {
      if (current.nodeValue !== next.nodeValue)
        current.nodeValue = next.nodeValue;
      return;
    }

    if (!(current instanceof HTMLElement) || !(next instanceof HTMLElement))
      return;
    if (current.tagName !== next.tagName) {
      current.replaceWith(next);
      return;
    }

    const currentMathKey = this._renderedMathKey(current);
    if (currentMathKey && currentMathKey === this._renderedMathKey(next))
      return;
    if (currentMathKey) {
      current.replaceWith(next);
      return;
    }

    for (const { name } of Array.from(current.attributes)) {
      if (!next.hasAttribute(name)) current.removeAttribute(name);
    }
    for (const { name, value } of Array.from(next.attributes)) {
      if (current.getAttribute(name) !== value)
        current.setAttribute(name, value);
    }

    this._updateRenderedChildren(current, next);
  }

  _updateRenderedChildren(current, next) {
    const nextChildren = Array.from(next.childNodes);
    for (let index = 0; index < nextChildren.length; index++) {
      const currentChild = current.childNodes[index];
      const nextChild = nextChildren[index];
      if (currentChild) this._updateRenderedContent(currentChild, nextChild);
      else current.appendChild(nextChild);
    }
    while (current.childNodes.length > nextChildren.length) {
      current.lastChild.remove();
    }
  }

  /**
   * Replace complete math expressions with inert text tokens before Marked can
   * insert <br> elements into multiline $$...$$ blocks or consume backslashes.
   * Code spans and fenced code blocks are deliberately left untouched. The
   * ambiguous $...$ form is normalized to \(...\) only when it looks like a
   * complete formula, preventing currency prose such as "$50 to $99" from
   * being consumed by KaTeX.
   */
  _protectMath(text) {
    const formulas = new Map();
    let index = 0;
    const pattern =
      /(```[\s\S]*?```|~~~[\s\S]*?~~~|`+[^`]*`+)|(\$\$[\s\S]*?\$\$|\\\[[\s\S]*?\\\]|\\\([\s\S]*?\\\)|\$[^$\n]+?\$)/g;
    const protectedText = text.replace(pattern, (match, code, math) => {
      if (code || !math) return match;
      if (math.startsWith("$") && !math.startsWith("$$")) {
        const body = math.slice(1, -1);
        if (!this._isInlineDollarMath(body)) return match;
        math = `\\(${body}\\)`;
      }
      const token = `ALPACA_MATH_TOKEN_${index++}_END`;
      formulas.set(token, math);
      return token;
    });
    return { protectedText, formulas };
  }

  /**
   * Single-dollar math is common in model output but collides with currency.
   * Require tight delimiters and reject the numeric/range shapes that occur in
   * prices. Explicit \(...\) remains available for intentionally ambiguous
   * formulas such as a lone number.
   */
  _isInlineDollarMath(body) {
    if (!body || body !== body.trim()) return false;
    if (/^\d[\d,]*(?:\.\d+)?$/.test(body)) return false;
    if (/^\d[\d,.]*\s*(?:-|–|—|to)\s*$/i.test(body)) return false;

    // Whitespace-only prose is more likely to be a pair of currency markers.
    // Real spaced formulas normally contain an operator or a TeX command.
    if (/\s/.test(body) && !/[\\^_{}=+*/<>≤≥≈]/.test(body)) return false;
    return true;
  }

  _restoreMathTokens(element, formulas) {
    if (formulas.size === 0) return;
    const visit = (node) => {
      if (node.nodeType === Node.TEXT_NODE) {
        let value = node.nodeValue;
        if (!value.includes("ALPACA_MATH_TOKEN_")) return;
        for (const [token, math] of formulas) {
          value = value.split(token).join(math);
        }
        node.nodeValue = value;
        return;
      }
      if (!(node instanceof HTMLElement) || node.matches("code, pre")) return;
      for (const child of Array.from(node.childNodes)) visit(child);
    };
    visit(element);
  }

  /**
   * Render Markdown and math off-DOM, then swap it into place in one operation.
   */
  _renderMarkdown(element, text) {
    const next = document.createElement("div");
    const { protectedText, formulas } = this._protectMath(text);
    next.innerHTML = DOMPurify.sanitize(marked.parse(protectedText));
    this._restoreMathTokens(next, formulas);
    this._renderMath(next);
    this._updateRenderedChildren(element, next);
  }

  /**
   * Handle content update from streaming or re-rendering
   */
  appendContent(update) {
    const {
      type,
      content,
      answer_index,
      is_tool_call,
      is_tool_result,
      is_done,
      is_error,
      tool_id,
      metrics,
    } = update;

    console.log(
      `[CHAT DEBUG] appendContent called: type=${type}, answer_index=${answer_index}, content_length=${
        content?.length || 0
      }`,
    );

    // Snapshot scroll position before the DOM update — scrollHeight grows
    // after content is added, so checking afterwards always looks "far".
    const nearBottom = this._isNearBottom();

    // Update current answer index
    this.currentAnswerIndex = answer_index;

    if (type === "tool_call" || is_tool_call) {
      // Fold injection is handled exclusively by injectFoldWithId (via app.injectToolFold).
      // Doing it here too would create a duplicate fold with a different ID.
      console.log(
        `[CHAT DEBUG] tool_call update — fold handled by injectToolFold, skipping`,
      );
      return;
    } else if (type === "tool_result" || is_tool_result) {
      // Same: fold injection handled by injectFoldWithId.
      console.log(
        `[CHAT DEBUG] tool_result update — fold handled by injectToolFold, skipping`,
      );
      return;
    } else if (type === "progress" || (content && content.startsWith("🔧"))) {
      this.replaceProgressLine(content, answer_index);
    } else if (type === "error" || is_error) {
      this.showError(content, answer_index);
    } else {
      // Regular content
      console.log(`[CHAT DEBUG] Appending regular content to buffer`);
      this.appendToAnswerBuffer(
        answer_index,
        content,
        is_done || type === "done",
      );
    }

    if (nearBottom) {
      this.scrollToBottom();
    }
  }

  /**
   * Store fold data and create fold during streaming or re-rendering
   * This is called from tool_call/tool_result content updates.
   * Folds are injected inline into the answer content.
   */
  storeFoldData(answerIndex, content, foldType, toolId) {
    // Generate a deterministic ID based on content to avoid duplicates when re-rendering
    const contentHash = this._simpleHash(content);
    const foldId = `fold-${foldType}-${answerIndex}-${contentHash}`;

    // Check if this fold already exists (avoid duplicates when re-rendering)
    const existingKey = `${answerIndex}-${foldId}`;
    if (
      this.injectedFolds.has(existingKey) ||
      this.pendingFolds.has(existingKey)
    ) {
      console.log(
        `[CHAT DEBUG] storeFoldData: fold ${foldId} already exists, skipping`,
      );
      return;
    }

    const foldData = {
      id: foldId,
      content: content,
      type: foldType,
      answerIndex: answerIndex,
      toolId: toolId,
    };

    this.pendingFolds.set(existingKey, foldData);

    // Add placeholder to buffer so fold appears at correct position in text flow
    const bufferData = this.answerBuffers.get(answerIndex);
    if (bufferData) {
      console.log(
        `[CHAT DEBUG] Adding fold placeholder to buffer for answer ${answerIndex}`,
      );
      bufferData.buffer += `\n<!--FOLD_PLACEHOLDER:${foldId}-->\n`;
    } else {
      // No buffer yet - fold stays in pendingFolds until answer content creates the buffer
      console.log(
        `[CHAT DEBUG] No buffer for answer ${answerIndex}, ${foldType} fold queued for later injection`,
      );
    }
  }

  /**
   * Simple hash function for fold deduplication
   */
  _simpleHash(str) {
    let hash = 0;
    for (let i = 0; i < str.length; i++) {
      const char = str.charCodeAt(i);
      hash = (hash << 5) - hash + char;
      hash = hash & hash; // Convert to 32bit integer
    }
    return Math.abs(hash).toString(36).substr(0, 9);
  }

  /**
   * Append content to answer buffer with re-rendering
   */
  appendToAnswerBuffer(answerIndex, content, isFinal) {
    let bufferData = this.answerBuffers.get(answerIndex);
    let isNewBuffer = false;

    if (!bufferData) {
      // Create new answer wrapper
      const answerWrapper = document.createElement("div");
      answerWrapper.className = "answer-wrapper";
      answerWrapper.dataset.answerIndex = answerIndex;

      // Answer header with ASSISTANT indicator
      const answerHeader = document.createElement("div");
      answerHeader.className = "answer-header";
      answerHeader.contentEditable = "false";

      const answerRole = document.createElement("span");
      answerRole.className = "answer-role";
      answerRole.textContent = "Assistant";

      answerHeader.appendChild(answerRole);

      // First text segment — more segments are created dynamically after each
      // result fold so folds and text interleave in the natural narrative order.
      const segmentDiv = document.createElement("div");
      segmentDiv.className = `answer answer-segment answer-${answerIndex}`;
      segmentDiv.dataset.answerIndex = answerIndex;

      answerWrapper.appendChild(answerHeader);
      answerWrapper.appendChild(segmentDiv);
      this.container.appendChild(answerWrapper);

      bufferData = {
        buffer: "",
        answerElement: segmentDiv,
        answerWrapper: answerWrapper,
        lastRenderLength: 0,
      };
      this.answerBuffers.set(answerIndex, bufferData);
      isNewBuffer = true;
    }

    // Flush any pending folds into the wrapper whenever a buffer exists.
    // We do this on every call (not just isNewBuffer) so that folds queued
    // in pendingFolds before the buffer was created get injected as soon as
    // the first content update arrives.
    for (const [key, foldData] of this.pendingFolds.entries()) {
      if (
        foldData.answerIndex === answerIndex &&
        !this.injectedFolds.has(key)
      ) {
        console.log(
          `[CHAT DEBUG] Flushing queued fold: ${foldData.id} (isNewBuffer=${isNewBuffer})`,
        );
        try {
          this._appendFold(key, foldData);
        } catch (err) {
          console.error(
            `[CHAT DEBUG] _appendFold failed for ${foldData.id}:`,
            err,
          );
        }
      }
    }

    bufferData.buffer += content;

    // Decide whether to render
    const shouldRender =
      isFinal ||
      bufferData.buffer.length - bufferData.lastRenderLength > 100 ||
      this._isNaturalBreakPoint(content);

    if (shouldRender) {
      this._renderAnswerBuffer(answerIndex, bufferData, isFinal);
    }
  }

  /**
   * Copy only the selection within the chat, converting its rendered HTML back
   * to Markdown so links, emphasis, lists, and code remain portable.
   */
  async copySelectionAsMarkdown() {
    const selection = window.getSelection();
    if (!selection || selection.rangeCount === 0 || selection.isCollapsed)
      return false;

    const range = selection.getRangeAt(0);
    const start =
      range.startContainer.nodeType === Node.ELEMENT_NODE
        ? range.startContainer
        : range.startContainer.parentElement;
    const end =
      range.endContainer.nodeType === Node.ELEMENT_NODE
        ? range.endContainer
        : range.endContainer.parentElement;
    if (!this.container.contains(start) || !this.container.contains(end))
      return false;

    const fragment = range.cloneContents();
    let markdown = Array.from(fragment.childNodes)
      .map((node) => this._selectionNodeToMarkdown(node))
      .join("")
      .trim();

    // A range wholly inside one text node does not clone its inline ancestors.
    // Restore those wrappers up to the answer segment.
    if (range.commonAncestorContainer.nodeType === Node.TEXT_NODE) {
      let ancestor = range.commonAncestorContainer.parentElement;
      while (ancestor && ancestor !== this.container) {
        if (ancestor.matches("strong, b")) markdown = `**${markdown}**`;
        else if (ancestor.matches("em, i")) markdown = `*${markdown}*`;
        else if (ancestor.matches("del, s")) markdown = `~~${markdown}~~`;
        else if (ancestor.matches("code") && !ancestor.closest("pre"))
          markdown = `\`${markdown}\``;
        else if (ancestor.matches("a"))
          markdown = `[${markdown}](${ancestor.getAttribute("href") || ""})`;
        ancestor = ancestor.parentElement;
      }
    }

    if (!markdown) return false;

    return window.Helpers.copyToClipboard(markdown);
  }

  _selectionNodeToMarkdown(node) {
    if (node.nodeType === Node.TEXT_NODE) return node.nodeValue;
    if (!(node instanceof HTMLElement)) return "";

    const children = () =>
      Array.from(node.childNodes)
        .map((child) => this._selectionNodeToMarkdown(child))
        .join("");
    const tag = node.tagName.toLowerCase();

    if (node.matches(".answer-header, .message-header, button, tool-fold"))
      return "";
    if (node.classList.contains("code-block")) {
      const code = node.querySelector("code")?.textContent || "";
      const language = node.querySelector(".lang")?.textContent || "";
      return `\n\n\`\`\`${
        language === "text" ? "" : language
      }\n${code}\n\`\`\`\n\n`;
    }
    if (tag === "strong" || tag === "b") return `**${children()}**`;
    if (tag === "em" || tag === "i") return `*${children()}*`;
    if (tag === "del" || tag === "s") return `~~${children()}~~`;
    if (tag === "code" && node.parentElement?.tagName !== "PRE")
      return `\`${node.textContent}\``;
    if (tag === "a")
      return `[${children()}](${node.getAttribute("href") || ""})`;
    if (tag === "img")
      return `![${node.getAttribute("alt") || ""}](${
        node.getAttribute("src") || ""
      })`;
    if (tag === "br") return "\n";
    if (tag === "hr") return "\n\n---\n\n";
    if (/^h[1-6]$/.test(tag))
      return `${"#".repeat(Number(tag[1]))} ${children().trim()}\n\n`;
    if (tag === "p") return `${children().trim()}\n\n`;
    if (tag === "blockquote")
      return `${children()
        .trim()
        .split("\n")
        .map((line) => `> ${line}`)
        .join("\n")}\n\n`;
    if (tag === "ul" || tag === "ol") {
      return `${Array.from(node.children)
        .filter((child) => child.tagName === "LI")
        .map((child, index) => {
          const marker = tag === "ol" ? `${index + 1}. ` : "- ";
          return marker + this._selectionNodeToMarkdown(child).trim();
        })
        .join("\n")}\n\n`;
    }
    if (tag === "li") return children();
    if (tag === "pre") {
      const code = node.querySelector("code") || node;
      const language = Array.from(code.classList || [])
        .find((name) => name.startsWith("language-"))
        ?.slice("language-".length);
      return `\n\n\`\`\`${language || ""}\n${code.textContent}\n\`\`\`\n\n`;
    }
    return children();
  }

  /**
   * Check if content ends at a natural break point
   */
  _isNaturalBreakPoint(content) {
    const breakChars = ["\n\n", "\n```", ". ", "? ", "! "];
    return breakChars.some((bc) => content.endsWith(bc));
  }

  /**
   * Render answer buffer content
   */
  _renderAnswerBuffer(answerIndex, bufferData, isFinal) {
    const { buffer, answerElement } = bufferData;

    // Strip any legacy fold-placeholder comments before markdown parsing.
    const { textToRender } = this._extractFoldPlaceholders(buffer);
    const withResolvedImages = this._resolveInlineImageRefs(
      textToRender,
      answerIndex,
    );
    const processedText = isFinal
      ? withResolvedImages
      : this._closeOpenFences(withResolvedImages);

    this._renderMarkdown(answerElement, processedText);
    this._hydrateInlineVideoRefs(answerElement, answerIndex);

    bufferData.lastRenderLength = buffer.length;
    // Folds are siblings of the text segments in answerWrapper and are unaffected.
  }

  /**
   * Record a tool_result's raw content against its real tool-call id, so
   * `alpaca://image/<id>` and `alpaca://video/<id>` refs written in that
   * same answer's text can be resolved later. Called from both the live
   * streaming path (app.js's injectToolFold, deriving the id from fold_id)
   * and the historical re-render path (the component's own `.id` field is
   * used directly).
   */
  registerToolResult(answerIndex, toolCallId, content) {
    if (!toolCallId) return;
    let map = this.answerToolResults.get(answerIndex);
    if (!map) {
      map = new Map();
      this.answerToolResults.set(answerIndex, map);
    }
    map.set(toolCallId, content);

    // Pack notifications and local UI updates preserve order, but rendering
    // is deliberately tolerant of a late result. If markdown containing the
    // reference was already painted, resolve it as soon as its image/video
    // arrives. CLI-backed models (Claude Code CLI, Codex CLI) register
    // every tool result only after the whole answer — including its
    // alpaca://video/<id> refs — has already streamed and rendered, so the
    // video branch here isn't optional the way it might look.
    const bufferData = this.answerBuffers.get(answerIndex);
    const isReferenced =
      bufferData?.buffer.includes(`alpaca://image/${toolCallId}`) ||
      bufferData?.buffer.includes(`alpaca://video/${toolCallId}`);
    if (
      isReferenced &&
      (window.ImageResultUtils?.parse(content) ||
        window.VideoResultUtils?.parse(content) ||
        this._isGatedToolResult(content))
    ) {
      this._renderAnswerBuffer(answerIndex, bufferData, false);
    }
  }

  _isGatedToolResult(content) {
    return (
      typeof content === "string" &&
      content.includes("[Output truncated:") &&
      /^Full output saved to: .+$/m.test(content)
    );
  }

  /** Replace a stored gate placeholder with its full temp-file content. */
  _loadGatedToolResult(answerIndex, toolCallId, gatedContent) {
    if (!this._isGatedToolResult(gatedContent)) return null;
    const tabId = window.app?.currentTabId;
    if (!tabId || !window.pythonAPI?.get_gated_tool_output) return null;

    const key = `${answerIndex}:${toolCallId}`;
    const existing = this.gatedToolResultLoads.get(key);
    if (existing) return existing;
    const epoch = this.gatedToolResultEpoch;
    const promise = window.pythonAPI
      .get_gated_tool_output(tabId, gatedContent)
      .then((response) => {
        if (!response?.success) {
          throw new Error(response?.error || "Could not load tool output");
        }
        const results = this.answerToolResults.get(answerIndex);
        if (
          epoch !== this.gatedToolResultEpoch ||
          results?.get(toolCallId) !== gatedContent
        ) {
          return null;
        }
        results.set(toolCallId, response.content);
        const bufferData = this.answerBuffers.get(answerIndex);
        if (bufferData)
          this._renderAnswerBuffer(answerIndex, bufferData, false);
        return response.content;
      })
      .catch((error) => {
        console.warn(`Could not load gated tool result: ${error.message}`);
        return null;
      })
      .finally(() => {
        if (this.gatedToolResultLoads.get(key) === promise) {
          this.gatedToolResultLoads.delete(key);
        }
      });
    this.gatedToolResultLoads.set(key, promise);
    return promise;
  }

  /**
   * Resolve `alpaca://image/<tool_call_id>` markdown image refs to the
   * matching view_image result's data: URI, using only this same answer's
   * own tool results (see registerToolResult's docstring for why this is
   * scoped per-answer rather than a global lookup). Unresolved refs are
   * left as-is — marked/DOMPurify renders them as a normal broken image
   * rather than crashing.
   */
  _resolveInlineImageRefs(text, answerIndex) {
    if (!text.includes("alpaca://image/")) return text;
    const results = this.answerToolResults.get(answerIndex);
    // Fallback for when the model writes a short placeholder id ("1",
    // "2") instead of copying the real tool-call id as instructed — the
    // system prompt says not to, but this is cheap insurance against it
    // happening anyway. Only used when the referenced id doesn't match
    // anything at all (not even a gated placeholder); assumes refs and
    // their matching image results appear in the same relative order
    // within the answer, which held in the observed failure case.
    const imageResults = results
      ? [...results.entries()].filter(
          ([, c]) =>
            window.ImageResultUtils?.parse(c) || this._isGatedToolResult(c),
        )
      : [];
    let positionalIdx = 0;
    return text.replace(/alpaca:\/\/image\/([^)\s"'>]+)/g, (match, id) => {
      let realId = id;
      let content = results?.get(id);
      if (content === undefined && imageResults[positionalIdx]) {
        [realId, content] = imageResults[positionalIdx];
      }
      positionalIdx++;
      const parsed = content && window.ImageResultUtils?.parse(content);
      if (!parsed) {
        this._loadGatedToolResult(answerIndex, realId, content);
        return match;
      }
      return `data:${parsed.mimeType};base64,${parsed.base64Data}`;
    });
  }

  /** Replace same-answer alpaca://video links with an asynchronously loaded player. */
  _hydrateInlineVideoRefs(element, answerIndex) {
    const results = this.answerToolResults.get(answerIndex);
    // Same positional fallback as _resolveInlineImageRefs, for the same
    // reason — see its comment.
    const videoResults = results
      ? [...results.entries()].filter(
          ([, c]) =>
            window.VideoResultUtils?.parse(c) || this._isGatedToolResult(c),
        )
      : [];
    let positionalIdx = 0;
    for (const link of element.querySelectorAll("a")) {
      const href = link.getAttribute("href") || "";
      if (!href.startsWith("alpaca://video/")) continue;
      const id = href.slice("alpaca://video/".length);
      let realId = id;
      let content = results?.get(id);
      if (content === undefined && videoResults[positionalIdx]) {
        [realId, content] = videoResults[positionalIdx];
      }
      positionalIdx++;
      const parsed = window.VideoResultUtils?.parse(content);
      if (!parsed) {
        this._loadGatedToolResult(answerIndex, realId, content);
        continue;
      }

      const wrapper = document.createElement("div");
      wrapper.className = "inline-video-result";
      wrapper.contentEditable = "false";
      wrapper.textContent = "Loading video…";
      link.replaceWith(wrapper);
      const tabId = window.app?.currentTabId;
      if (!tabId) continue;
      window.VideoResultUtils.load(tabId, parsed)
        .then((url) => {
          if (!wrapper.isConnected) return;
          wrapper.textContent = "";
          const video = document.createElement("video");
          video.controls = true;
          video.preload = "metadata";
          video.src = url;
          video.setAttribute(
            "aria-label",
            link.textContent || "Generated video",
          );
          wrapper.appendChild(video);
          if (link.textContent) {
            const caption = document.createElement("div");
            caption.className = "inline-video-caption";
            caption.textContent = link.textContent;
            wrapper.appendChild(caption);
          }
        })
        .catch((error) => {
          if (wrapper.isConnected && error.message !== "Video load cancelled") {
            wrapper.textContent = `Could not load video: ${error.message}`;
          }
        });
    }
  }

  /**
   * Inject a fold into the answer wrapper and record it as injected.
   * Single choke-point for all fold injection so state stays consistent.
   *
   * Folds are inserted as siblings of the text segments inside answerWrapper,
   * producing a naturally interleaved layout:
   *   [segment0] [call-fold] [result-fold] [segment1] [call-fold] ...
   *
   * When a result fold is injected a new text segment is appended so that
   * any continuation text flows into the correct position.
   */
  _appendFold(key, foldData) {
    const bd = this.answerBuffers.get(foldData.answerIndex);
    if (!bd) {
      console.warn(
        `[CHAT DEBUG] _appendFold: no buffer for answer ${foldData.answerIndex}`,
      );
      return;
    }

    const fold = this._createFoldElement(foldData);
    const wrapper = bd.answerWrapper;

    // Paired positioning: call and result folds share a base ID derived from
    // tc_store_id.  Fold IDs follow the pattern:
    //   fold-call-{answerIndex}-{tc_store_id}
    //   fold-result-{answerIndex}-{tc_store_id}
    if (foldData.type === "call") {
      // If the result fold arrived first (fast tool), insert call before it.
      const resultId = foldData.id.replace("fold-call-", "fold-result-");
      const matchingResult = wrapper.querySelector("#" + CSS.escape(resultId));
      if (matchingResult) {
        wrapper.insertBefore(fold, matchingResult);
      } else {
        wrapper.appendChild(fold);
      }
    } else {
      // Insert result fold immediately after its matching call fold.
      const callId = foldData.id.replace("fold-result-", "fold-call-");
      const matchingCall = wrapper.querySelector("#" + CSS.escape(callId));
      if (matchingCall) {
        matchingCall.insertAdjacentElement("afterend", fold);
      } else {
        wrapper.appendChild(fold);
      }
      // A result fold closes a tool pair — create a fresh text segment so
      // continuation text flows naturally after the fold pair.
      this._createNewSegment(foldData.answerIndex, bd);
    }

    this.injectedFolds.set(key, foldData);
    this.pendingFolds.delete(key);
    console.log(
      `[CHAT DEBUG] Injected fold ${foldData.id} (type=${foldData.type})`,
    );

    // Signal Python that the result fold is rendered — do this AFTER DOM insertion
    // so connectedCallback has already fired and the element is truly in the DOM.
    if (foldData.type === "result" && window.pythonAPI && foldData.id) {
      const tabId = window.app ? window.app.currentTabId : null;
      if (tabId) {
        console.log(
          `[CHAT DEBUG] SIGNALING FOLD RENDERED: tabId=${tabId}, foldId=${foldData.id}`,
        );
        window.pythonAPI
          .on_fold_rendered(tabId, foldData.id)
          .then(() => {
            console.log(
              `[CHAT DEBUG] on_fold_rendered succeeded for ${foldData.id}`,
            );
          })
          .catch((err) => {
            console.error(`[CHAT DEBUG] on_fold_rendered failed: ${err}`);
          });
      }
    }
  }

  /**
   * Flush the current segment buffer and create a new empty text segment
   * appended at the end of answerWrapper.  Called after each result fold
   * so continuation text lands in the right position.
   */
  _createNewSegment(answerIndex, bd) {
    // Flush any unrendered buffer content to the current segment first.
    if (bd.buffer.length > bd.lastRenderLength) {
      this._renderAnswerBuffer(answerIndex, bd, false);
    }

    const newSegment = document.createElement("div");
    newSegment.className = `answer answer-segment answer-${answerIndex}`;
    newSegment.dataset.answerIndex = answerIndex;

    bd.answerWrapper.appendChild(newSegment);
    bd.answerElement = newSegment;
    bd.buffer = "";
    bd.lastRenderLength = 0;
    console.log(
      `[CHAT DEBUG] New text segment created for answer ${answerIndex}`,
    );
  }

  /**
   * Create a fold element (without appending to container)
   */
  _createFoldElement(foldData) {
    const fold = document.createElement("tool-fold");
    fold.id = foldData.id;
    fold.setAttribute("data-type", foldData.type);
    fold.setAttribute("data-answer-index", foldData.answerIndex);
    fold.contentEditable = "false"; // skip as atomic unit on arrow-key navigation
    fold.setBody(foldData.content);
    return fold;
  }

  /**
   * Inject pending fold widgets inline into the answer content
   * This ensures folds appear at the correct position within the text flow
   */
  _injectPendingFoldsIntoAnswer(
    answerIndex,
    answerElement,
    placeholderIds = [],
  ) {
    // Find all fold placeholders in the answer element
    const placeholderRegex = /<!--FOLD_PLACEHOLDER:([^>]+)-->/g;
    let html = answerElement.innerHTML;
    let modified = false;

    for (const foldId of placeholderIds) {
      const key = `${answerIndex}-${foldId}`;
      const foldData = this.pendingFolds.get(key);

      if (foldData && !this.injectedFolds.has(key)) {
        // Create a temporary container for the fold
        const tempDiv = document.createElement("div");
        this._createFoldWidget(foldData, tempDiv);
        const foldHtml = tempDiv.innerHTML;

        // Replace the placeholder with the fold HTML
        const placeholder = `<!--FOLD_PLACEHOLDER:${foldId}-->`;
        if (html.includes(placeholder)) {
          html = html.replace(placeholder, foldHtml);
          this.injectedFolds.set(key, foldData);
          this.pendingFolds.delete(key);
          modified = true;
        }
      }
    }

    if (modified) {
      answerElement.innerHTML = html;
    }

    // Also check for any other pending folds for this answer that weren't in placeholders
    for (const [key, foldData] of this.pendingFolds.entries()) {
      if (
        foldData.answerIndex === answerIndex &&
        !this.injectedFolds.has(key)
      ) {
        // Append to the end of the answer element
        this._createFoldWidget(foldData, answerElement);
        this.injectedFolds.set(key, foldData);
        this.pendingFolds.delete(key);
      }
    }
  }

  /**
   * Extract fold placeholders from buffer text
   */
  _extractFoldPlaceholders(buffer) {
    const placeholderRegex = /<!--FOLD_PLACEHOLDER:([^>]+)-->/g;
    const foldPlaceholders = [];
    let match;

    while ((match = placeholderRegex.exec(buffer)) !== null) {
      foldPlaceholders.push(match[1]);
    }

    // Remove placeholders from text for markdown parsing
    const textToRender = buffer.replace(/<!--FOLD_PLACEHOLDER:[^>]+-->/g, "");

    return { textToRender, foldPlaceholders };
  }

  /**
   * Close open code fences for partial rendering
   */
  _closeOpenFences(text) {
    const lines = text.split("\n");
    const openFences = [];

    for (const line of lines) {
      // Match fence lines (``` or ~~~ with optional language)
      const fenceMatch = line.match(/^(```+|~~~+)([^\s]*)$/);
      if (fenceMatch) {
        const fence = fenceMatch[1];
        if (
          openFences.length > 0 &&
          openFences[openFences.length - 1] === fence
        ) {
          openFences.pop();
        } else {
          openFences.push(fence);
        }
      }
    }

    let result = text;
    while (openFences.length > 0) {
      result += "\n" + openFences.pop();
    }

    return result;
  }

  /**
   * Inject pending fold widgets
   */
  _injectPendingFolds(answerIndex, foldContainer, placeholderIds) {
    for (const foldId of placeholderIds) {
      const key = `${answerIndex}-${foldId}`;
      const foldData = this.pendingFolds.get(key);

      if (foldData && !this.injectedFolds.has(key)) {
        this._createFoldWidget(foldData, foldContainer);
        this.injectedFolds.set(key, foldData);
        this.pendingFolds.delete(key);
      }
    }

    // Also check for any other pending folds for this answer
    for (const [key, foldData] of this.pendingFolds.entries()) {
      if (
        foldData.answerIndex === answerIndex &&
        !this.injectedFolds.has(key)
      ) {
        this._createFoldWidget(foldData, foldContainer);
        this.injectedFolds.set(key, foldData);
        this.pendingFolds.delete(key);
      }
    }
  }

  /**
   * Create a fold widget element
   */
  _createFoldWidget(foldData, container) {
    console.log(
      `[CHAT DEBUG] _createFoldWidget called: type=${foldData.type}, id=${
        foldData.id
      }, container_exists=${!!container}`,
    );

    if (!container) {
      console.error(`[CHAT DEBUG] No container provided for fold widget!`);
      return;
    }

    // Check if fold already exists (avoid duplicates)
    const existingFold = container.querySelector(
      `tool-fold#${CSS.escape(foldData.id)}`,
    );
    if (existingFold) {
      console.log(`[CHAT DEBUG] Fold ${foldData.id} already exists, skipping`);
      return;
    }

    const fold = document.createElement("tool-fold");
    fold.id = foldData.id;
    fold.setAttribute("data-type", foldData.type);
    fold.setAttribute("data-answer-index", foldData.answerIndex);
    fold.contentEditable = "false"; // skip as atomic unit on arrow-key navigation
    fold.setBody(foldData.content);

    container.appendChild(fold);
    console.log(
      `[CHAT DEBUG] Fold widget appended to container, children count=${container.children.length}`,
    );

    // Signal Python that the fold is rendered - ONLY for result folds (not call folds)
    // This is the critical synchronization point
    if (foldData.type === "result" && window.pythonAPI && foldData.id) {
      const tabId = window.app ? window.app.currentTabId : null;
      if (tabId) {
        console.log(
          `[CHAT DEBUG] SIGNALING FOLD RENDERED: tabId=${tabId}, foldId=${foldData.id}`,
        );
        window.pythonAPI
          .on_fold_rendered(tabId, foldData.id)
          .then(() => {
            console.log(
              `[CHAT DEBUG] on_fold_rendered succeeded for ${foldData.id}`,
            );
          })
          .catch((err) => {
            console.error(`[CHAT DEBUG] on_fold_rendered failed: ${err}`);
          });
      }
    }
  }

  /**
   * Replace or create progress indicator
   */
  replaceProgressLine(content, answerIndex) {
    let progressEl = this.container.querySelector(
      `.progress-indicator[data-answer-index="${answerIndex}"]`,
    );

    if (!progressEl) {
      progressEl = document.createElement("div");
      progressEl.className = "progress-indicator";
      progressEl.dataset.answerIndex = answerIndex;
      progressEl.contentEditable = "false";
      this.container.appendChild(progressEl);
    }

    progressEl.textContent = content;

    if (!content) {
      progressEl.remove();
    }
  }

  /**
   * Show error message
   */
  showError(message, answerIndex) {
    const errorEl = document.createElement("div");
    errorEl.className = "error-message";
    errorEl.contentEditable = "false";
    errorEl.style.cssText =
      "color: var(--error-color); padding: 8px; margin: 8px 0;";
    errorEl.textContent = message;
    this.container.appendChild(errorEl);
  }

  /**
   * Inject a fold with a specific ID (called from injectToolFold).
   *
   * If the answer buffer already exists the fold is appended immediately.
   * If not, a requestAnimationFrame loop polls every frame until the buffer
   * is ready, then injects.  This is more reliable than depending on
   * appendToAnswerBuffer to notice pendingFolds entries.
   */
  injectFoldWithId(answerIndex, content, foldType, foldId) {
    console.log(
      `[CHAT DEBUG] injectFoldWithId: answerIndex=${answerIndex}, type=${foldType}, foldId=${foldId}`,
    );

    const existingKey = `${answerIndex}-${foldId}`;

    // Guard: already injected
    if (this.injectedFolds.has(existingKey)) {
      console.log(`[CHAT DEBUG] already injected, skipping`);
      return;
    }

    const foldData = { id: foldId, content, type: foldType, answerIndex };

    // Store in pendingFolds so the flush-on-appendToAnswerBuffer path also
    // works as a secondary safety net.
    this.pendingFolds.set(existingKey, foldData);

    // Primary injection path: rAF loop that fires every frame until the
    // buffer (and its answerWrapper) exists, then injects directly.
    const tryInject = () => {
      // Stop if already injected by another path (e.g. the flush).
      if (this.injectedFolds.has(existingKey)) return;
      // Stop if cleared (tab switch / clear()) — key removed from pendingFolds.
      if (!this.pendingFolds.has(existingKey)) return;

      const bd = this.answerBuffers.get(answerIndex);
      if (bd && bd.answerWrapper) {
        // Buffer ready — inject now.
        console.log(
          `[CHAT DEBUG] rAF: injecting ${foldType} fold for answer ${answerIndex}`,
        );
        try {
          this._appendFold(existingKey, foldData);
        } catch (err) {
          console.error(`[CHAT DEBUG] rAF inject failed:`, err);
        }
      } else {
        // Buffer not ready yet — retry next frame.
        requestAnimationFrame(tryInject);
      }
    };

    requestAnimationFrame(tryInject);
  }

  /**
   * Render text that appeared BEFORE the first tool call.
   * With the segment-based layout this is just the first segment, so we
   * delegate to appendToAnswerBuffer which creates it on demand.
   */
  setPreFoldsText(answerIndex, text) {
    if (text) {
      this.appendToAnswerBuffer(answerIndex, text, false);
    }
  }

  // Place the caret at the top (atTop=true) or bottom (atTop=false) of the
  // visible container area.  Tries a few y offsets to land on actual text
  // Try to place the caret at exactly (x, y).  Returns true on success.
  _tryPlaceCaretAt(x, y) {
    const range = document.caretRangeFromPoint(x, y);
    if (!range) return false;
    const node = range.startContainer;
    const el = node.nodeType === Node.TEXT_NODE ? node.parentElement : node;
    if (!el || !this.container.contains(el)) return false;
    const sel = window.getSelection();
    if (sel) {
      sel.removeAllRanges();
      sel.addRange(range);
    }
    return true;
  }

  // Place the caret at the top (atTop=true) or bottom of the visible area.
  _placeCaretInViewport(atTop) {
    const rect = this.container.getBoundingClientRect();
    const x = rect.left + rect.width / 2;
    const yOffsets = atTop ? [8, 24, 48, 72] : [-8, -24, -48, -72];
    for (const dy of yOffsets) {
      const y = atTop ? rect.top + dy : rect.bottom + dy;
      if (this._tryPlaceCaretAt(x, y)) return;
    }
  }

  // True when the user is within 100px of the bottom — close enough that
  // they are considered to be "tracking" the stream.
  _isNearBottom() {
    const { scrollTop, scrollHeight, clientHeight } = this.container;
    return scrollHeight - scrollTop - clientHeight <= 100;
  }

  /**
   * Scroll to bottom of container
   */
  scrollToBottom() {
    this.container.scrollTop = this.container.scrollHeight;
  }

  /**
   * Clear all content
   */
  clear() {
    this._closeImageOverlay();
    window.VideoResultUtils?.clear();
    this.gatedToolResultEpoch += 1;
    this.container.innerHTML = "";
    this.answerBuffers.clear();
    this.pendingFolds.clear();
    this.injectedFolds.clear();
    this.answerToolResults.clear();
    this.gatedToolResultLoads.clear();
    this.currentAnswerIndex = -1;
  }

  /**
   * Add a question message.
   * @param {string} text - The question text.
   * @param {string[]} images - Array of data URI strings for image previews.
   */
  addQuestion(text, images = []) {
    const messageEl = document.createElement("div");
    messageEl.className = "message question";
    messageEl.dataset.rawText = text;

    const headerEl = document.createElement("div");
    headerEl.className = "message-header";
    headerEl.contentEditable = "false";
    headerEl.innerHTML = '<span class="message-role">User</span>';

    const contentEl = document.createElement("div");
    contentEl.className = "message-content";
    this._renderMarkdown(contentEl, text);

    // Add image previews if any
    if (images && images.length > 0) {
      const imagesEl = document.createElement("div");
      imagesEl.className = "message-images";
      imagesEl.contentEditable = "false";

      for (const img of images) {
        const imgEl = document.createElement("img");
        imgEl.src = img;
        imagesEl.appendChild(imgEl);
      }
      contentEl.appendChild(imagesEl);
    }

    messageEl.appendChild(headerEl);
    messageEl.appendChild(contentEl);

    this.container.appendChild(messageEl);

    this.scrollToBottom();
  }

  /**
   * Set streaming state
   */
  setStreaming(isStreaming) {
    this.isStreaming = isStreaming;
  }
}

// Export for use in app.js
window.ChatDisplay = ChatDisplay;
