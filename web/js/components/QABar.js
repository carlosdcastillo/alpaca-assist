/**
 * QABar - Question/Answer action bar component
 *
 * Provides action buttons for questions (edit) and answers (regenerate, fork, navigate siblings)
 */
class QABar extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
  }

  /**
   * Called when element is added to DOM
   */
  connectedCallback() {
    const nodeId = this.getAttribute("data-node-id");
    const kind = this.getAttribute("data-kind"); // 'question' or 'answer'
    const position = parseInt(this.getAttribute("data-position") || "0", 10);
    const siblingCount = parseInt(
      this.getAttribute("data-sibling-count") || "1",
      10,
    );

    this.shadowRoot.innerHTML = `
            <style>
                :host {
                    display: flex;
                    gap: 4px;
                    padding: 4px 0;
                    opacity: 0.6;
                    transition: opacity 0.15s;
                }

                :host(:hover) {
                    opacity: 1;
                }

                button {
                    background: transparent;
                    border: none;
                    color: var(--text-secondary, #858585);
                    cursor: pointer;
                    padding: 4px 8px;
                    border-radius: 4px;
                    font-size: 14px;
                    line-height: 1;
                    transition: all 0.15s;
                }

                button:hover {
                    background: var(--bg-hover, #3c3c3c);
                    color: var(--text-primary, #cccccc);
                }

                button:disabled {
                    opacity: 0.3;
                    cursor: not-allowed;
                }

                .sibling-nav {
                    display: flex;
                    gap: 2px;
                    margin-left: 4px;
                    padding-left: 4px;
                    border-left: 1px solid var(--border-color, #3e3e42);
                }

                .position-indicator {
                    font-size: 11px;
                    color: var(--text-muted, #6e6e6e);
                    padding: 4px;
                    user-select: none;
                }
            </style>
        `;

    if (kind === "question") {
      this._addQuestionButtons(nodeId, position, siblingCount);
    } else {
      this._addAnswerButtons(nodeId, position, siblingCount);
    }
  }

  /**
   * Add buttons for question actions
   */
  _addQuestionButtons(nodeId, position, siblingCount) {
    // Sibling navigation (only if multiple siblings)
    if (siblingCount > 1) {
      const navContainer = document.createElement("div");
      navContainer.className = "sibling-nav";

      // Previous button
      const prevBtn = document.createElement("button");
      prevBtn.innerHTML = "◀";
      prevBtn.title = "Previous version";
      prevBtn.disabled = position <= 0;
      prevBtn.addEventListener("click", () => {
        document.dispatchEvent(
          new CustomEvent("navigateSibling", {
            detail: { nodeId, direction: "prev" },
          }),
        );
      });

      // Position indicator
      const indicator = document.createElement("span");
      indicator.className = "position-indicator";
      indicator.textContent = `${position + 1}/${siblingCount}`;

      // Next button
      const nextBtn = document.createElement("button");
      nextBtn.innerHTML = "▶";
      nextBtn.title = "Next version";
      nextBtn.disabled = position >= siblingCount - 1;
      nextBtn.addEventListener("click", () => {
        document.dispatchEvent(
          new CustomEvent("navigateSibling", {
            detail: { nodeId, direction: "next" },
          }),
        );
      });

      navContainer.appendChild(prevBtn);
      navContainer.appendChild(indicator);
      navContainer.appendChild(nextBtn);
      this.shadowRoot.appendChild(navContainer);
    }

    this._addButton("✏️", "Edit question", () => {
      document.dispatchEvent(
        new CustomEvent("editQuestion", {
          detail: { nodeId },
        }),
      );
    });
  }

  /**
   * Add buttons for answer actions
   */
  _addAnswerButtons(nodeId, position, siblingCount) {
    this._addButton("🔄", "Regenerate answer", () => {
      document.dispatchEvent(
        new CustomEvent("regenerateAnswer", {
          detail: { nodeId },
        }),
      );
    });

    this._addButton("🔀", "Fork conversation", () => {
      document.dispatchEvent(
        new CustomEvent("forkConversation", {
          detail: { nodeId },
        }),
      );
    });

    // Sibling navigation (only if multiple siblings)
    if (siblingCount > 1) {
      const navContainer = document.createElement("div");
      navContainer.className = "sibling-nav";

      // Previous button
      const prevBtn = document.createElement("button");
      prevBtn.innerHTML = "◀";
      prevBtn.title = "Previous version";
      prevBtn.disabled = position <= 0;
      prevBtn.addEventListener("click", () => {
        document.dispatchEvent(
          new CustomEvent("navigateSibling", {
            detail: { nodeId, direction: "prev" },
          }),
        );
      });

      // Position indicator
      const indicator = document.createElement("span");
      indicator.className = "position-indicator";
      indicator.textContent = `${position + 1}/${siblingCount}`;

      // Next button
      const nextBtn = document.createElement("button");
      nextBtn.innerHTML = "▶";
      nextBtn.title = "Next version";
      nextBtn.disabled = position >= siblingCount - 1;
      nextBtn.addEventListener("click", () => {
        document.dispatchEvent(
          new CustomEvent("navigateSibling", {
            detail: { nodeId, direction: "next" },
          }),
        );
      });

      navContainer.appendChild(prevBtn);
      navContainer.appendChild(indicator);
      navContainer.appendChild(nextBtn);
      this.shadowRoot.appendChild(navContainer);
    }
  }

  /**
   * Add a button to the shadow DOM
   */
  _addButton(icon, title, onClick) {
    const btn = document.createElement("button");
    btn.innerHTML = icon;
    btn.title = title;
    btn.addEventListener("click", onClick);
    this.shadowRoot.appendChild(btn);
  }
}

// Register the custom element
customElements.define("qa-bar", QABar);

// Export for use in other components
window.QABar = QABar;
