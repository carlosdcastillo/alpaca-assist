/**
 * MarkdownInput - textarea with markdown syntax highlighting overlay
 *
 * Architecture: a plain <textarea> handles all native input (Enter, caret,
 * selection, undo). An absolutely-positioned overlay div, rendered in sync,
 * provides the coloured syntax highlighting.  The textarea text is transparent;
 * the overlay supplies all visible colour.
 *
 * Features:
 * - Real-time markdown syntax highlighting using existing .md-* CSS classes
 * - Zero caret issues (textarea owns caret natively)
 * - Support for bold, italic, inline code, fenced code blocks, headers,
 *   blockquotes, lists, and links
 * - Ctrl+Enter to send, Tab inserts spaces, Ctrl+B/I/U wrap selection
 */
class MarkdownInput {
  constructor(containerId, options = {}) {
    this.container = document.getElementById(containerId);
    if (!this.container) {
      throw new Error(`MarkdownInput: Container #${containerId} not found`);
    }

    this.options = {
      placeholder: options.placeholder || "Type your message...",
      onSend: options.onSend || null,
      ...options,
    };

    this._isComposing = false;
    this._init();
  }

  // -----------------------------------------------------------------------
  // Initialisation
  // -----------------------------------------------------------------------

  _init() {
    this.container.innerHTML = `
            <div class="markdown-input-wrapper">
                <div class="markdown-highlight-overlay" aria-hidden="true"></div>
                <textarea class="markdown-input"
                          placeholder="${this.options.placeholder}"
                          spellcheck="false"></textarea>
            </div>
        `;
    this.editor = this.container.querySelector(".markdown-input");
    this.overlay = this.container.querySelector(".markdown-highlight-overlay");
    this._bindEvents();
  }

  // -----------------------------------------------------------------------
  // Event binding
  // -----------------------------------------------------------------------

  _bindEvents() {
    this.editor.addEventListener("compositionstart", () => {
      this._isComposing = true;
    });

    this.editor.addEventListener("compositionend", () => {
      this._isComposing = false;
      this._applyHighlight();
    });

    this.editor.addEventListener("input", () => {
      if (!this._isComposing) this._applyHighlight();
    });

    this.editor.addEventListener("keydown", (e) => {
      if (e.ctrlKey && e.key === "Enter") {
        e.preventDefault();
        if (this.options.onSend) this.options.onSend(this.getValue());
        return;
      }

      if (e.key === "Tab") {
        e.preventDefault();
        const start = this.editor.selectionStart;
        const end = this.editor.selectionEnd;
        this.editor.value =
          this.editor.value.slice(0, start) +
          "    " +
          this.editor.value.slice(end);
        this.editor.selectionStart = this.editor.selectionEnd = start + 4;
        this._applyHighlight();
        return;
      }

      if (e.ctrlKey && (e.key === "b" || e.key === "i" || e.key === "u")) {
        e.preventDefault();
        const marker = e.key === "b" ? "**" : e.key === "i" ? "*" : "_";
        this._wrapSelection(marker);
      }
    });

    // Keep overlay scroll in sync with textarea scroll
    this.editor.addEventListener("scroll", () => {
      this.overlay.scrollTop = this.editor.scrollTop;
      this.overlay.scrollLeft = this.editor.scrollLeft;
    });

    this.editor.addEventListener("focus", () => {
      this.container.classList.add("focused");
    });

    this.editor.addEventListener("blur", () => {
      this.container.classList.remove("focused");
    });
  }

  // -----------------------------------------------------------------------
  // Highlighting
  // -----------------------------------------------------------------------

  /**
   * Re-render the overlay with syntax highlighting.
   * The textarea is untouched — no caret disruption ever.
   */
  _applyHighlight() {
    const text = this.getValue();
    if (!text) {
      this.overlay.innerHTML = "";
      return;
    }
    // Trailing newline ensures the last line occupies its full height in the overlay
    this.overlay.innerHTML = this._tokenizeMarkdown(text) + "\n";
  }

  /**
   * Tokenise block-level markdown and delegate inline content.
   */
  _tokenizeMarkdown(text) {
    const lines = text.split("\n");
    const out = [];
    let inCodeBlock = false;
    let codeLines = [];

    for (const line of lines) {
      // Fenced code block open/close
      if (/^(`{3,}|~{3,})/.test(line)) {
        if (!inCodeBlock) {
          inCodeBlock = true;
          codeLines = [this._esc(line)];
        } else {
          codeLines.push(this._esc(line));
          out.push(
            `<span class="md-code-block">${codeLines.join("\n")}</span>`,
          );
          codeLines = [];
          inCodeBlock = false;
        }
        continue;
      }
      if (inCodeBlock) {
        codeLines.push(this._esc(line));
        continue;
      }

      // ATX heading: # Heading
      const hm = line.match(/^(#{1,6}) (.*)/);
      if (hm) {
        out.push(
          `<span class="md-header">${this._esc(hm[1])} </span>` +
            `<span class="md-header-text">${this._inlineMarkdown(
              hm[2],
            )}</span>`,
        );
        continue;
      }

      // Blockquote: > ...
      const bq = line.match(/^(>[ ]?)(.*)/);
      if (bq) {
        out.push(
          `<span class="md-quote">${this._esc(bq[1])}</span>` +
            `<span class="md-quote-text">${this._inlineMarkdown(bq[2])}</span>`,
        );
        continue;
      }

      // List item: -, *, +, 1.
      const li = line.match(/^(\s*(?:[-*+]|\d+\.) )(.*)/);
      if (li) {
        out.push(
          `<span class="md-list-marker">${this._esc(li[1])}</span>` +
            `<span class="md-list-text">${this._inlineMarkdown(li[2])}</span>`,
        );
        continue;
      }

      out.push(this._inlineMarkdown(line));
    }

    // Unclosed code block
    if (inCodeBlock && codeLines.length) {
      out.push(`<span class="md-code-block">${codeLines.join("\n")}</span>`);
    }

    return out.join("\n");
  }

  /**
   * Tokenise inline markdown: bold, italic, inline code, links.
   */
  _inlineMarkdown(text) {
    let out = "";
    let i = 0;
    while (i < text.length) {
      // Inline code: `code`
      if (text[i] === "`") {
        const end = text.indexOf("`", i + 1);
        if (end !== -1) {
          out += `<span class="md-code">${this._esc(
            text.slice(i, end + 1),
          )}</span>`;
          i = end + 1;
          continue;
        }
      }
      // Bold: **...**
      if (text[i] === "*" && text[i + 1] === "*") {
        const end = text.indexOf("**", i + 2);
        if (end !== -1) {
          out += `<span class="md-bold">${this._esc(
            text.slice(i, end + 2),
          )}</span>`;
          i = end + 2;
          continue;
        }
      }
      // Bold: __...__
      if (text[i] === "_" && text[i + 1] === "_") {
        const end = text.indexOf("__", i + 2);
        if (end !== -1) {
          out += `<span class="md-bold">${this._esc(
            text.slice(i, end + 2),
          )}</span>`;
          i = end + 2;
          continue;
        }
      }
      // Italic: *...*
      if (text[i] === "*" && text[i + 1] !== "*") {
        const end = text.indexOf("*", i + 1);
        if (end !== -1) {
          out += `<span class="md-italic">${this._esc(
            text.slice(i, end + 1),
          )}</span>`;
          i = end + 1;
          continue;
        }
      }
      // Italic: _..._
      if (text[i] === "_" && text[i + 1] !== "_") {
        const end = text.indexOf("_", i + 1);
        if (end !== -1) {
          out += `<span class="md-italic">${this._esc(
            text.slice(i, end + 1),
          )}</span>`;
          i = end + 1;
          continue;
        }
      }
      // Link: [text](url)
      if (text[i] === "[") {
        const cb = text.indexOf("]", i + 1);
        if (cb !== -1 && text[cb + 1] === "(") {
          const cu = text.indexOf(")", cb + 2);
          if (cu !== -1) {
            out +=
              `<span class="md-link-bracket">[</span>` +
              `<span class="md-link-text">${this._esc(
                text.slice(i + 1, cb),
              )}</span>` +
              `<span class="md-link-bracket">](</span>` +
              `<span class="md-link-url">${this._esc(
                text.slice(cb + 2, cu),
              )}</span>` +
              `<span class="md-link-bracket">)</span>`;
            i = cu + 1;
            continue;
          }
        }
      }
      // Plain character
      out += this._esc(text[i]);
      i++;
    }
    return out;
  }

  // -----------------------------------------------------------------------
  // Public API
  // -----------------------------------------------------------------------

  getValue() {
    return this.editor.value;
  }

  setValue(text) {
    this.editor.value = text;
    this._applyHighlight();
  }

  clear() {
    this.editor.value = "";
    this.overlay.innerHTML = "";
  }

  focus() {
    this.editor.focus();
  }

  setDisabled(disabled) {
    this.editor.disabled = disabled;
    this.container.classList.toggle("disabled", disabled);
  }

  setPlaceholder(text) {
    this.editor.placeholder = text;
  }

  // -----------------------------------------------------------------------
  // Utilities
  // -----------------------------------------------------------------------

  _esc(text) {
    const d = document.createElement("div");
    d.textContent = text;
    return d.innerHTML;
  }

  _wrapSelection(marker) {
    const start = this.editor.selectionStart;
    const end = this.editor.selectionEnd;
    const text = this.editor.value;
    const selected = text.slice(start, end);
    this.editor.value =
      text.slice(0, start) + marker + selected + marker + text.slice(end);
    if (selected) {
      this.editor.selectionStart = start + marker.length;
      this.editor.selectionEnd = end + marker.length;
    } else {
      this.editor.selectionStart = this.editor.selectionEnd =
        start + marker.length;
    }
    this._applyHighlight();
  }
}

window.MarkdownInput = MarkdownInput;
