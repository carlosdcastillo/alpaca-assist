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

    // Map answer_index -> { buffer: string, element: HTMLElement, foldContainer: HTMLElement }
    this.answerBuffers = new Map();

    // Store pending fold data
    // Key: `${answerIndex}-${foldId}`, Value: { content, type, answerIndex }
    this.pendingFolds = new Map();
    this.injectedFolds = new Map();

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

    // Allow internal alpaca:// links to survive DOMPurify sanitization.
    // DOMPurify strips unrecognized URL schemes from href by default.
    // This global hook runs for every DOMPurify.sanitize() call in the app.
    DOMPurify.addHook("uponSanitizeAttribute", (node, data) => {
      if (
        data.attrName === "href" &&
        typeof data.attrValue === "string" &&
        data.attrValue.startsWith("alpaca://conv/")
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
      answerHeader.innerHTML = '<span class="answer-role">Assistant</span>';

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
    const processedText = isFinal
      ? textToRender
      : this._closeOpenFences(textToRender);

    answerElement.innerHTML = DOMPurify.sanitize(marked.parse(processedText));

    bufferData.lastRenderLength = buffer.length;
    // Folds are siblings of the text segments in answerWrapper and are unaffected.
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
    this.container.innerHTML = "";
    this.answerBuffers.clear();
    this.pendingFolds.clear();
    this.injectedFolds.clear();
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
    contentEl.innerHTML = DOMPurify.sanitize(marked.parse(text));

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
