/**
 * Alpaca Assist - Main Application
 *
 * This is the main entry point for the web frontend. It coordinates all
 * components and handles communication with the Python backend.
 */
class AlpacaApp {
  constructor() {
    // API bridge
    this.api = window.pythonAPI;

    // Components
    this.tabManager = null;
    this.chatDisplay = null;
    this.inputArea = null;
    this.updatePoller = null;

    // State
    this.currentTabId = null;
    // Note: isStreaming is now tracked per-tab in TabManager, not globally

    // Initialize
    this._init();
  }

  /**
   * Initialize the application
   */
  async _init() {
    // Wait for Python API to be available
    await this._waitForApi();

    // Initialize components
    this._initComponents();

    // Bind events
    this._bindEvents();

    // Start update polling
    this.updatePoller = new UpdatePoller(this.api, 50);
    this.updatePoller.start();

    // Load initial data
    await this._loadInitialData();

    console.log("Alpaca Assist initialized");
  }

  /**
   * Wait for Python API to become available
   */
  async _waitForApi() {
    let attempts = 0;
    const maxAttempts = 200; // 20 seconds total (pywebview can be slow to inject)

    while (!this.api.isAvailable() && attempts < maxAttempts) {
      await new Promise((resolve) => setTimeout(resolve, 100));
      attempts++;
    }

    if (!this.api.isAvailable()) {
      console.error("Python API not available after timeout");
      this._showError(
        "Failed to connect to Python backend - pywebview API not found. Try reloading the page.",
      );
    } else {
      console.log("Python API connected successfully");
    }
  }

  /**
   * Initialize UI components
   */
  _initComponents() {
    // Tab manager
    this.tabManager = new TabManager("tabs-container", this.api);

    // Chat display
    this.chatDisplay = new ChatDisplay("chat-container");

    // Input area
    this.inputArea = new InputArea(this.api);
    this.inputArea.onSend((text, images) => this._onSendMessage(text, images));
    this.inputArea.onStop(() => this._onStopStreaming());
  }

  /**
   * Bind event handlers
   */
  _bindEvents() {
    // Tab events
    document.addEventListener("tabSwitched", (e) => {
      this.currentTabId = e.detail.tabId;
      this._onTabSwitched(e.detail.tabId);
    });

    document.addEventListener("tabClosed", (e) => {
      if (this.currentTabId === e.detail.tabId) {
        this.chatDisplay.clear();
      }
      // Update input area visibility when tab is closed
      this._updateInputAreaVisibility();
    });

    // Listen for tab creation to update input area visibility
    document.addEventListener("tabCreated", () => {
      this._updateInputAreaVisibility();
    });

    // Toolbar button events
    this._bindToolbarEvents();

    // Menu category hover/click handling
    this._setupMenuBar();

    // Menu item actions
    document.querySelectorAll(".menu-item").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        const action = e.currentTarget.dataset.action;
        this._handleMenuAction(action);
        this._closeAllMenus();
      });
    });

    // New tab button
    document.getElementById("new-tab-btn").addEventListener("click", () => {
      this.tabManager.createTab();
    });

    // Close menus when clicking elsewhere
    document.addEventListener("click", (e) => {
      if (!e.target.closest(".menu-category")) {
        this._closeAllMenus();
      }
    });

    // Intercept internal alpaca:// navigation links (e.g. handoff back-references).
    // Rendered markdown produces <a href="alpaca://tab/{tabId}"> elements; we
    // catch them here instead of letting the WebView try to load the URL.
    document.addEventListener("click", (e) => {
      const link = e.target.closest("a");
      if (!link) return;
      const href = link.getAttribute("href") || "";
      if (href.startsWith("alpaca://conv/")) {
        e.preventDefault();
        const convId = parseInt(href.slice("alpaca://conv/".length), 10);
        this.api.navigate_to_conv(convId).then((result) => {
          if (!result || !result.success) {
            this._showAlert(
              `The original conversation could not be found (${
                result?.error || "unknown"
              }).`,
            );
            return;
          }
          if (result.action === "switch") {
            this.tabManager.switchToTab(result.tab_id);
          }
          // "restore_queued": createTabUI arrives via the poll channel
        });
      }
    });

    // Dialog close buttons (all .dialog-close and named close/cancel buttons)
    document
      .querySelectorAll(
        ".dialog-close, #cancel-prefs, #history-close-btn, #history-dialog-close, " +
          "#mcp-config-close-btn, #mcp-tools-close-btn, #skills-cancel-btn",
      )
      .forEach((btn) => {
        btn.addEventListener("click", () => this._closeDialogs());
      });

    // MCP Config dialog buttons
    document
      .getElementById("mcp-add-btn")
      .addEventListener("click", () => this._addMCPServer());
    document
      .getElementById("mcp-update-btn")
      .addEventListener("click", () => this._updateMCPServer());
    document
      .getElementById("mcp-remove-btn")
      .addEventListener("click", () => this._removeMCPServer());
    document
      .getElementById("mcp-test-btn")
      .addEventListener("click", () => this._testMCPConnection());
    document
      .getElementById("mcp-reload-btn")
      .addEventListener("click", () => this._reloadMCPConfig());
    document
      .getElementById("mcp-browse-btn")
      .addEventListener("click", () => this._browseMCPCommand());

    // MCP Tools dialog buttons
    document
      .getElementById("mcp-call-tool-btn")
      .addEventListener("click", () => this._callSelectedTool());

    // Agent Skills dialog buttons
    document
      .getElementById("skills-refresh-btn")
      .addEventListener("click", () => this._refreshSkills());
    document
      .getElementById("skills-ok-btn")
      .addEventListener("click", () => this._saveAgentSkills());

    // Click backdrop to close dialog
    document.getElementById("dialog-overlay").addEventListener("click", (e) => {
      if (e.target === e.currentTarget) this._closeDialogs();
    });

    // Click backdrop to dismiss the message dialog (treated as Cancel)
    document
      .getElementById("message-dialog-overlay")
      .addEventListener("click", (e) => {
        if (e.target === e.currentTarget && this._messageDialogDismiss) {
          this._messageDialogDismiss();
        }
      });

    // Preferences save
    document.getElementById("save-prefs").addEventListener("click", () => {
      this._savePreferences();
    });

    // History dialog buttons
    document
      .getElementById("history-revive-btn")
      .addEventListener("click", () => this._reviveSelectedConversation());
    document
      .getElementById("history-delete-btn")
      .addEventListener("click", () => this._deleteHistoryEntry());

    // History search (debounced)
    document.getElementById("history-search").addEventListener("input", (e) => {
      clearTimeout(this._historySearchTimer);
      this._historySearchTimer = setTimeout(() => {
        this._loadHistory(e.target.value.trim());
      }, 200);
    });
    document
      .getElementById("history-search")
      .addEventListener("keydown", (e) => {
        if (e.key === "Enter") this._reviveSelectedConversation();
        if (e.key === "Escape") {
          e.stopPropagation();
          this._closeDialogs();
        }
      });

    // Q/A action events
    // Q/A action events
    document.addEventListener("editQuestion", (e) =>
      this._onEditQuestion(e.detail.nodeId),
    );
    // Fork button is on the answer bar; nodeId is an assistant node.
    // The Python side resolves assistant node IDs to their parent user nodes.
    document.addEventListener("forkConversation", (e) =>
      this._onForkConversation(e.detail.nodeId),
    );
    document.addEventListener("regenerateAnswer", (e) =>
      this._onRegenerateAnswer(e.detail.nodeId),
    );
    document.addEventListener("navigateSibling", (e) =>
      this._onNavigateSibling(e.detail.nodeId, e.detail.direction),
    );

    // Splitter drag events
    this._setupSplitter();

    // Keyboard shortcuts
    document.addEventListener("keydown", (e) => {
      if (e.ctrlKey || e.metaKey) {
        // Ctrl+Shift combos
        if (e.shiftKey) {
          switch (e.key) {
            case "P":
              e.preventDefault();
              this._handleMenuAction("truncate");
              return;
            case "H":
              e.preventDefault();
              this._handleMenuAction("handoff");
              return;
          }
        }
        // Plain Ctrl combos
        switch (e.key) {
          case "n":
            e.preventDefault();
            this.tabManager.createTab();
            break;
          case "w":
            e.preventDefault();
            if (this.currentTabId) {
              this.tabManager.closeTab(this.currentTabId);
            }
            break;
          case "t":
            e.preventDefault();
            this._handleMenuAction("export");
            break;
          case "y":
            e.preventDefault();
            this._showHistoryDialog();
            break;
          case ",":
            e.preventDefault();
            this._showPreferencesDialog();
            break;
          case "f":
            e.preventDefault();
            this._showFindDialog();
            break;
          case "p":
            e.preventDefault();
            this._handleMenuAction("compact");
            break;
          case "r":
            e.preventDefault();
            this.inputArea.loadModels();
            break;
          case "PageDown":
            e.preventDefault();
            this.tabManager.nextTab();
            break;
          case "PageUp":
            e.preventDefault();
            this.tabManager.prevTab();
            break;
        }
      }
      // Escape closes dialogs, then find bar
      if (e.key === "Escape") {
        const overlay = document.getElementById("dialog-overlay");
        if (overlay && overlay.classList.contains("active")) {
          this._closeDialogs();
        } else {
          this._closeFindBar();
        }
      }
    });

    // Initialize find bar
    this._initFindBar();
  }

  /**
   * Setup draggable splitter between chat and input area
   */
  _setupSplitter() {
    const splitter = document.getElementById("splitter");
    const mainContent = document.querySelector(".main-content");
    const chatContainer = document.getElementById("chat-container");
    const inputArea = document.getElementById("input-area");
    const messageInput = document.getElementById("message-input");

    if (!splitter || !chatContainer || !inputArea || !mainContent) {
      console.warn("[SPLITTER] Missing required elements for splitter");
      return;
    }

    // Initialize flex values for resizable layout
    // chat-container should take available space, input-area has initial size
    chatContainer.style.flex = "1 1 auto";
    inputArea.style.flex = "0 0 auto";
    inputArea.style.display = "flex";
    inputArea.style.flexDirection = "column";

    // Make textarea fill available space in input area
    if (messageInput) {
      messageInput.style.flex = "1 1 auto";
      messageInput.style.resize = "none"; // Disable manual resize, let it grow with panel
      messageInput.style.minHeight = "60px";
    }

    let isDragging = false;
    let startY = 0;
    let startInputHeight = 0;

    splitter.addEventListener("mousedown", (e) => {
      isDragging = true;
      startY = e.clientY;
      // Get current input area height (this is what we're resizing)
      startInputHeight = inputArea.getBoundingClientRect().height;
      splitter.classList.add("dragging");
      document.body.style.cursor = "row-resize";
      e.preventDefault();
      console.log("[SPLITTER] Drag started, input height:", startInputHeight);
    });

    document.addEventListener("mousemove", (e) => {
      if (!isDragging) return;

      // Calculate how much the mouse moved
      // Dragging DOWN (e.clientY > startY) should DECREASE input area height
      // Dragging UP (e.clientY < startY) should INCREASE input area height
      const deltaY = e.clientY - startY;
      const newInputHeight = Math.max(100, startInputHeight - deltaY); // Minimum 100px

      // Apply the new height directly to input area
      // Chat container will fill remaining space due to flex: 1 1 auto
      inputArea.style.height = `${newInputHeight}px`;
      inputArea.style.flex = "0 0 auto"; // Don't grow or shrink from this height
      chatContainer.style.flex = "1 1 auto"; // Take all remaining space

      console.log("[SPLITTER] Resizing, input height:", newInputHeight);
    });

    document.addEventListener("mouseup", () => {
      if (isDragging) {
        isDragging = false;
        splitter.classList.remove("dragging");
        document.body.style.cursor = "";
        console.log("[SPLITTER] Drag ended");
      }
    });
  }

  /**
   * Load initial data (models, preferences, etc.)
   */
  async _loadInitialData() {
    // Load models and start the connection health poll
    await this.inputArea.loadModels();
    this._startConnectionHealthPoll();

    // Load preferences
    const prefsResult = await this.api.get_preferences();
    if (prefsResult.success) {
      this._applyPreferences(prefsResult.preferences);
    }

    // Tab creation is handled by onSessionRestoreComplete(), called by Python
    // after _restore_session() finishes. Do NOT create tabs here — that races
    // with session restore and produces a spurious empty tab on every launch.
  }

  /**
   * Poll the backend every 30s so the connection indicator stays current.
   * Also refreshes the model list in case new models have been loaded.
   */
  _startConnectionHealthPoll() {
    if (this._connectionPollTimer) return; // already running
    this._connectionPollTimer = setInterval(
      () => this.inputArea.loadModels(),
      30_000,
    );
  }

  /**
   * Called by Python once session restore is fully complete.
   * Creates a default tab only if Python sent us nothing.
   */
  onSessionRestoreComplete() {
    if (this.tabManager.tabs.size === 0) {
      this.tabManager.createTab("New Chat");
    }
    this._updateInputAreaVisibility();
  }

  /**
   * Apply preferences to UI
   */
  _applyPreferences(prefs) {
    // Theme
    if (prefs.theme) {
      document.documentElement.setAttribute("data-theme", prefs.theme);
    }

    // Font
    if (prefs.font_family) {
      document.documentElement.style.setProperty(
        "--font-ui",
        prefs.font_family,
      );
    }

    // Model
    if (prefs.model) {
      this.inputArea.setSelectedModel(prefs.model);
    }

    // Update preferences dialog fields
    document.getElementById("pref-api-url").value = prefs.api_url || "";
    document.getElementById("pref-font-family").value = prefs.font_family || "";
    document.getElementById("pref-font-size").value = prefs.font_size || 11;
    document.getElementById("pref-theme").value = prefs.theme || "dark";
  }

  /**
   * Handle menu actions
   */
  async _handleMenuAction(action) {
    switch (action) {
      // File menu
      case "new-tab":
        await this.tabManager.createTab();
        break;
      case "close-tab":
        if (this.currentTabId) {
          await this.tabManager.closeTab(this.currentTabId);
        }
        break;
      case "history":
        await this._showHistoryDialog();
        break;
      case "export":
        if (this.currentTabId) {
          await this.api.export_conversation(this.currentTabId);
        }
        break;
      case "preferences":
        this._showPreferencesDialog();
        break;
      case "exit":
        await this._saveAndClose();
        break;

      // Edit menu
      case "undo":
        this._undoText();
        break;
      case "copy":
        this._copyText();
        break;
      case "paste":
        this._pasteText();
        break;
      case "copy-code":
        this._copyCodeBlock();
        break;
      case "find":
        this._showFindDialog();
        break;

      // Chat menu
      case "submit":
        this.submit_current_tab();
        break;
      case "next-qa":
        if (this.currentTabId) {
          await this.api.navigate_qa(this.currentTabId, "next");
        }
        break;
      case "prev-qa":
        if (this.currentTabId) {
          await this.api.navigate_qa(this.currentTabId, "prev");
        }
        break;
      case "compact":
        if (this.currentTabId) {
          if (this.tabManager.isTabStreaming(this.currentTabId)) {
            await this._showAlert("Cannot compact while streaming.");
            break;
          }
          try {
            const compactResult = await this.api.compact_conversation(
              this.currentTabId,
            );
            if (compactResult.success && compactResult.compacted) {
              await this._reloadConversationDisplay();
            } else if (compactResult.success && !compactResult.compacted) {
              await this._showAlert(
                compactResult.reason === "nothing_to_compact"
                  ? "Nothing to compact: no tool call details found."
                  : `Cannot compact: ${compactResult.reason}.`,
              );
            } else if (!compactResult.success) {
              await this._showAlert(
                `Compact failed: ${compactResult.error || "unknown error"}`,
              );
            }
          } catch (e) {
            await this._showAlert(`Compact error: ${e.message || e}`);
          }
        }
        break;
      case "truncate":
        if (this.currentTabId) {
          if (this.tabManager.isTabStreaming(this.currentTabId)) {
            await this._showAlert("Cannot truncate while streaming.");
            break;
          }
          try {
            const truncateResult = await this.api.truncate_conversation(
              this.currentTabId,
            );
            if (truncateResult.success && truncateResult.truncated) {
              await this._reloadConversationDisplay();
            } else if (truncateResult.success && !truncateResult.truncated) {
              await this._showAlert(
                "Nothing to truncate: conversation has only one Q\u2060/\u2060A pair.",
              );
            } else if (!truncateResult.success) {
              await this._showAlert(
                `Truncate failed: ${truncateResult.error || "unknown error"}`,
              );
            }
          } catch (e) {
            await this._showAlert(`Truncate error: ${e.message || e}`);
          }
        }
        break;
      case "pop":
        await this._popConversation();
        break;
      case "handoff":
        await this._performHandoff();
        break;
      case "clone-conversation":
        await this._cloneConversation();
        break;

      // Tools menu
      case "refresh-models":
        await this.inputArea.loadModels();
        break;
      case "mcp-config":
        await this._showMCPConfigDialog();
        break;
      case "mcp-tools":
        await this._showToolsDialog();
        break;
      case "agent-skills":
        await this._showAgentSkillsDialog();
        break;

      // Help menu
      case "about":
        this._showAboutDialog();
        break;
    }
  }

  /**
   * Setup menu bar hover and click behavior
   */
  _setupMenuBar() {
    const categories = document.querySelectorAll(".menu-category");
    let activeCategory = null;

    categories.forEach((category) => {
      const btn = category.querySelector(".menu-category-btn");

      // Click to toggle
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        if (category.classList.contains("active")) {
          category.classList.remove("active");
          activeCategory = null;
        } else {
          this._closeAllMenus();
          category.classList.add("active");
          activeCategory = category;
        }
      });

      // Hover to switch if another menu is open
      category.addEventListener("mouseenter", () => {
        if (activeCategory && activeCategory !== category) {
          activeCategory.classList.remove("active");
          category.classList.add("active");
          activeCategory = category;
        }
      });
    });

    // Close menus on Escape
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        this._closeAllMenus();
        activeCategory = null;
      }
    });
  }

  /**
   * Bind toolbar button events
   */
  _bindToolbarEvents() {
    // New Tab button
    const newTabBtn = document.getElementById("toolbar-new-tab");
    if (newTabBtn) {
      newTabBtn.addEventListener("click", () => {
        this.tabManager.createTab();
      });
    }

    // Close Tab button
    const closeTabBtn = document.getElementById("toolbar-close-tab");
    if (closeTabBtn) {
      closeTabBtn.addEventListener("click", () => {
        if (this.currentTabId) {
          this.tabManager.closeTab(this.currentTabId);
        }
      });
    }

    // History button
    const historyBtn = document.getElementById("toolbar-history");
    if (historyBtn) {
      historyBtn.addEventListener("click", () => {
        this._showHistoryDialog();
      });
    }

    // Toolbar model selector
    const toolbarModel = document.getElementById("toolbar-model");
    if (toolbarModel) {
      toolbarModel.addEventListener("change", (e) => {
        const selectedModel = e.target.value;
        // Save to preferences via API
        this.api.save_preferences({ model: selectedModel });
      });
    }
  }

  /**
   * Close all open menus
   */
  _closeAllMenus() {
    document.querySelectorAll(".menu-category").forEach((cat) => {
      cat.classList.remove("active");
    });
  }

  /**
   * Undo text in focused widget
   */
  _undoText() {
    const focused = document.activeElement;
    if (focused && focused.tagName === "TEXTAREA") {
      document.execCommand("undo");
    }
  }

  /**
   * Copy selected text
   */
  _copyText() {
    document.execCommand("copy");
  }

  /**
   * Paste text
   */
  _pasteText() {
    const focused = document.activeElement;
    if (focused && focused.tagName === "TEXTAREA") {
      document.execCommand("paste");
    }
  }

  /**
   * Copy code block at cursor
   */
  _copyCodeBlock() {
    // TODO: Implement code block detection and copying
    console.log("Copy code block - not yet implemented");
  }

  /**
   * Initialize find bar event bindings
   */
  _initFindBar() {
    this._findBar = document.getElementById("find-bar");
    this._findInput = document.getElementById("find-input");
    this._findCountEl = document.getElementById("find-count");
    this._findMatches = [];
    this._findCurrentIdx = -1;
    this._findTimer = null;

    if (!this._findBar) return;

    document
      .getElementById("find-next")
      .addEventListener("click", () => this._findNext());
    document
      .getElementById("find-prev")
      .addEventListener("click", () => this._findPrev());
    document
      .getElementById("find-close")
      .addEventListener("click", () => this._closeFindBar());

    this._findInput.addEventListener("input", () => {
      clearTimeout(this._findTimer);
      const query = this._findInput.value;
      if (query.length >= 1) {
        this._findTimer = setTimeout(() => this._findInChat(query), 150);
      } else {
        this._clearFindHighlights();
        this._updateFindCount();
      }
    });

    this._findInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        if (e.shiftKey) {
          this._findPrev();
        } else {
          this._findNext();
        }
      }
      if (e.key === "Escape") {
        e.stopPropagation();
        this._closeFindBar();
      }
    });
  }

  /**
   * Open the find bar and focus its input
   */
  _showFindDialog() {
    if (!this._findBar) return;
    this._findBar.classList.remove("hidden");
    this._findInput.focus();
    this._findInput.select();
    // Re-run search if there's already a query
    if (this._findInput.value) {
      this._findInChat(this._findInput.value);
    }
  }

  /**
   * Close the find bar and clear highlights
   */
  _closeFindBar() {
    if (!this._findBar || this._findBar.classList.contains("hidden")) return;
    this._clearFindHighlights();
    this._findBar.classList.add("hidden");
    this._updateFindCount();
  }

  /**
   * Search chat container for query, highlight all matches
   */
  _findInChat(query) {
    this._clearFindHighlights();
    if (!query) {
      this._updateFindCount();
      return;
    }

    const container = document.getElementById("chat-container");
    if (!container) return;

    const lowerQuery = query.toLowerCase();

    // Collect all text nodes (skip those in shadow DOM — tool folds)
    const walker = document.createTreeWalker(
      container,
      NodeFilter.SHOW_TEXT,
      null,
    );
    const textNodes = [];
    let node;
    while ((node = walker.nextNode())) {
      if (node.textContent) textNodes.push(node);
    }

    this._findMatches = [];

    for (const textNode of textNodes) {
      const text = textNode.textContent;
      const lowerText = text.toLowerCase();
      const parent = textNode.parentNode;
      if (!parent) continue;

      // Find all match positions in this text node
      const positions = [];
      let idx = 0;
      while (true) {
        const pos = lowerText.indexOf(lowerQuery, idx);
        if (pos === -1) break;
        positions.push(pos);
        idx = pos + lowerQuery.length;
      }
      if (positions.length === 0) continue;

      // Build a document fragment: text before/between/after matches
      const frag = document.createDocumentFragment();
      let lastEnd = 0;
      for (const pos of positions) {
        if (pos > lastEnd) {
          frag.appendChild(document.createTextNode(text.slice(lastEnd, pos)));
        }
        const mark = document.createElement("mark");
        mark.className = "find-match";
        mark.textContent = text.slice(pos, pos + query.length);
        frag.appendChild(mark);
        this._findMatches.push(mark);
        lastEnd = pos + query.length;
      }
      if (lastEnd < text.length) {
        frag.appendChild(document.createTextNode(text.slice(lastEnd)));
      }

      parent.replaceChild(frag, textNode);
    }

    this._findCurrentIdx = this._findMatches.length > 0 ? 0 : -1;
    this._highlightCurrentMatch();
    this._updateFindCount();
  }

  /**
   * Clear all find highlights
   */
  _clearFindHighlights() {
    for (const mark of this._findMatches || []) {
      const parent = mark.parentNode;
      if (parent) {
        parent.replaceChild(document.createTextNode(mark.textContent), mark);
      }
    }
    this._findMatches = [];
    this._findCurrentIdx = -1;
    // Merge adjacent text nodes
    const container = document.getElementById("chat-container");
    if (container) container.normalize();
  }

  /**
   * Highlight the current match and scroll it into view
   */
  _highlightCurrentMatch() {
    for (const mark of this._findMatches || []) {
      mark.className = "find-match";
    }
    if (
      this._findCurrentIdx >= 0 &&
      this._findCurrentIdx < this._findMatches.length
    ) {
      const current = this._findMatches[this._findCurrentIdx];
      current.className = "find-current";
      current.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
  }

  /**
   * Navigate to next find match
   */
  _findNext() {
    if (!this._findMatches || this._findMatches.length === 0) return;
    this._findCurrentIdx =
      (this._findCurrentIdx + 1) % this._findMatches.length;
    this._highlightCurrentMatch();
    this._updateFindCount();
  }

  /**
   * Navigate to previous find match
   */
  _findPrev() {
    if (!this._findMatches || this._findMatches.length === 0) return;
    this._findCurrentIdx =
      (this._findCurrentIdx - 1 + this._findMatches.length) %
      this._findMatches.length;
    this._highlightCurrentMatch();
    this._updateFindCount();
  }

  /**
   * Update the match count display
   */
  _updateFindCount() {
    if (!this._findCountEl) return;
    const total = this._findMatches ? this._findMatches.length : 0;
    if (
      !this._findBar ||
      this._findBar.classList.contains("hidden") ||
      total === 0
    ) {
      this._findCountEl.textContent =
        total === 0 && this._findInput?.value ? "No matches" : "";
    } else {
      this._findCountEl.textContent = `${this._findCurrentIdx + 1} of ${total}`;
    }
  }

  /**
   * Show conversation history dialog
   */
  async _showHistoryDialog() {
    this._showDialog("history-dialog");
    document.getElementById("history-search").value = "";
    document.getElementById("history-search").focus();
    await this._loadHistory("");
  }

  /**
   * Load history entries from Python, optionally filtered by search term
   */
  async _loadHistory(searchTerm = "") {
    const list = document.getElementById("history-list");
    list.innerHTML = '<div class="history-empty">Loading...</div>';

    const result = await this.api.get_history(searchTerm);
    if (!result.success) {
      list.innerHTML =
        '<div class="history-empty">Failed to load history.</div>';
      return;
    }

    const conversations = result.conversations || [];
    document.getElementById("history-count").textContent =
      conversations.length === 0
        ? ""
        : `${conversations.length} conversation${
            conversations.length !== 1 ? "s" : ""
          }`;

    if (conversations.length === 0) {
      list.innerHTML =
        '<div class="history-empty">No conversations found.</div>';
      return;
    }

    list.innerHTML = "";
    for (const conv of conversations) {
      const row = document.createElement("div");
      row.className = "history-row";
      row.dataset.convId = conv.id;

      const closedDate = this._formatHistoryDate(conv.closed_date);
      const createdDate = this._formatHistoryDate(conv.created_date);
      const titleEl = document.createElement("div");
      titleEl.className = "history-row-title";
      titleEl.textContent = conv.title;
      titleEl.title = conv.title;

      const dateEl = document.createElement("div");
      dateEl.className = "history-row-date";
      dateEl.textContent = closedDate;
      dateEl.title = `Created: ${createdDate}\nClosed: ${closedDate}`;

      row.appendChild(titleEl);
      row.appendChild(dateEl);
      row.addEventListener("click", () => this._selectHistoryRow(row));
      row.addEventListener("dblclick", () =>
        this._reviveSelectedConversation(),
      );
      list.appendChild(row);
    }
  }

  _formatHistoryDate(dateStr) {
    if (!dateStr) return "";
    try {
      const d = new Date(dateStr);
      return d.toLocaleString(undefined, {
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
      });
    } catch (e) {
      return dateStr;
    }
  }

  _selectHistoryRow(row) {
    document
      .querySelectorAll("#history-list .history-row.selected")
      .forEach((r) => r.classList.remove("selected"));
    row.classList.add("selected");
  }

  async _reviveSelectedConversation() {
    const selected = document.querySelector(
      "#history-list .history-row.selected",
    );
    if (!selected) return;

    const convId = parseInt(selected.dataset.convId, 10);
    const result = await this.api.revive_conversation(convId);
    if (result.success) {
      this._closeDialogs();
      // Python has queued createTabUI + switch — polling will pick it up
    } else if (result.error === "already_open") {
      this._closeDialogs();
      // Nothing to do — user can see the tab is already there
    } else {
      await this._showAlert(
        `Failed to revive conversation: ${result.error || "unknown error"}`,
      );
    }
  }

  async _deleteHistoryEntry() {
    const selected = document.querySelector(
      "#history-list .history-row.selected",
    );
    if (!selected) return;

    const confirmed = await this._showConfirm(
      "Permanently delete this conversation? This cannot be undone.",
      "Delete Conversation",
    );
    if (!confirmed) return;

    const convId = parseInt(selected.dataset.convId, 10);
    const result = await this.api.delete_history_entry(convId);
    if (result.success) {
      selected.remove();
      const list = document.getElementById("history-list");
      const remaining = list.querySelectorAll(".history-row").length;
      document.getElementById("history-count").textContent =
        remaining === 0
          ? ""
          : `${remaining} conversation${remaining !== 1 ? "s" : ""}`;
      if (remaining === 0) {
        list.innerHTML =
          '<div class="history-empty">No conversations found.</div>';
      }
    }
  }

  // =======================================================================
  // MCP Config Dialog
  // =======================================================================

  async _showMCPConfigDialog() {
    this._showDialog("mcp-config-dialog");
    this._selectedMCPServer = null;
    this._mcpConfig = {};
    document.getElementById("mcp-server-name").value = "";
    document.getElementById("mcp-server-command").value = "";
    document.getElementById("mcp-server-args").value = "";
    document.getElementById("mcp-tools-checkboxes").innerHTML =
      '<div class="mcp-tools-empty">Select a server to view tools</div>';
    document.getElementById("mcp-tools-header").textContent = "Tools";
    document.getElementById("mcp-status-msg").textContent = "";
    await this._loadMCPConfig();
  }

  async _loadMCPConfig() {
    const result = await this.api.get_mcp_config();
    if (!result.success) {
      document.getElementById("mcp-server-list").innerHTML =
        '<div class="mcp-tools-empty">Error loading config</div>';
      return;
    }
    this._mcpConfig = result.servers || {};
    this._renderMCPServerList();
    // Re-select if still present
    if (this._selectedMCPServer && this._mcpConfig[this._selectedMCPServer]) {
      this._selectMCPServer(this._selectedMCPServer);
    }
  }

  _renderMCPServerList() {
    const list = document.getElementById("mcp-server-list");
    list.innerHTML = "";
    const names = Object.keys(this._mcpConfig);
    if (names.length === 0) {
      list.innerHTML =
        '<div class="mcp-tools-empty">No servers configured</div>';
      return;
    }
    for (const name of names) {
      const item = document.createElement("div");
      item.className =
        "mcp-server-item" +
        (name === this._selectedMCPServer ? " selected" : "");
      item.textContent = name;
      item.dataset.name = name;
      item.addEventListener("click", () => this._selectMCPServer(name));
      list.appendChild(item);
    }
  }

  _selectMCPServer(name) {
    this._selectedMCPServer = name;
    const config = this._mcpConfig[name] || {};

    document.getElementById("mcp-server-name").value = name;
    document.getElementById("mcp-server-command").value = (
      config.command || []
    ).join(" ");
    document.getElementById("mcp-server-args").value = (config.args || []).join(
      " ",
    );

    document.querySelectorAll(".mcp-server-item").forEach((el) => {
      el.classList.toggle("selected", el.dataset.name === name);
    });

    const tools = config.available_tools || [];
    const header = document.getElementById("mcp-tools-header");
    const enabledCount = tools.filter((t) => t.enabled).length;
    header.textContent =
      tools.length > 0 ? `Tools (${enabledCount}/${tools.length})` : "Tools";

    const container = document.getElementById("mcp-tools-checkboxes");
    if (tools.length === 0) {
      container.innerHTML =
        '<div class="mcp-tools-empty">No tools (server may not be connected)</div>';
      return;
    }
    container.innerHTML = "";
    for (const tool of tools) {
      const item = document.createElement("div");
      item.className = "mcp-tool-item";

      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = tool.enabled;
      cb.addEventListener("change", async () => {
        await this.api.toggle_mcp_tool(name, tool.name, cb.checked);
        tool.enabled = cb.checked;
        const n = tools.filter((t) => t.enabled).length;
        header.textContent = `Tools (${n}/${tools.length})`;
      });

      const info = document.createElement("div");
      info.className = "mcp-tool-info";
      const tName = document.createElement("div");
      tName.className = "mcp-tool-name";
      tName.textContent = tool.name;
      info.appendChild(tName);
      if (tool.description) {
        const tDesc = document.createElement("div");
        tDesc.className = "mcp-tool-desc";
        tDesc.textContent = tool.description;
        info.appendChild(tDesc);
      }
      item.appendChild(cb);
      item.appendChild(info);
      container.appendChild(item);
    }
  }

  async _addMCPServer() {
    const name = document.getElementById("mcp-server-name").value.trim();
    const command = document.getElementById("mcp-server-command").value.trim();
    const args = document.getElementById("mcp-server-args").value.trim();
    if (!name || !command) {
      this._setMCPStatus("Name and Command are required", true);
      return;
    }
    const result = await this.api.add_mcp_server(name, command, args);
    if (result.success) {
      this._setMCPStatus(`Server '${name}' added — connecting...`);
      this._selectedMCPServer = name;
      await this._loadMCPConfig();
    } else {
      this._setMCPStatus(result.error || "Failed to add server", true);
    }
  }

  async _updateMCPServer() {
    if (!this._selectedMCPServer) {
      this._setMCPStatus("Select a server first", true);
      return;
    }
    const newName = document.getElementById("mcp-server-name").value.trim();
    const command = document.getElementById("mcp-server-command").value.trim();
    const args = document.getElementById("mcp-server-args").value.trim();
    if (!newName || !command) {
      this._setMCPStatus("Name and Command are required", true);
      return;
    }
    const result = await this.api.update_mcp_server(
      this._selectedMCPServer,
      newName,
      command,
      args,
    );
    if (result.success) {
      this._setMCPStatus("Server updated");
      this._selectedMCPServer = newName;
      await this._loadMCPConfig();
    } else {
      this._setMCPStatus(result.error || "Failed to update server", true);
    }
  }

  async _removeMCPServer() {
    if (!this._selectedMCPServer) {
      this._setMCPStatus("Select a server first", true);
      return;
    }
    const confirmed = await this._showConfirm(
      `Remove server '${this._selectedMCPServer}'?`,
      "Remove Server",
    );
    if (!confirmed) return;
    const result = await this.api.remove_mcp_server(this._selectedMCPServer);
    if (result.success) {
      this._setMCPStatus("Server removed");
      this._selectedMCPServer = null;
      document.getElementById("mcp-server-name").value = "";
      document.getElementById("mcp-server-command").value = "";
      document.getElementById("mcp-server-args").value = "";
      document.getElementById("mcp-tools-checkboxes").innerHTML =
        '<div class="mcp-tools-empty">Select a server to view tools</div>';
      document.getElementById("mcp-tools-header").textContent = "Tools";
      await this._loadMCPConfig();
    } else {
      this._setMCPStatus(result.error || "Failed to remove server", true);
    }
  }

  async _testMCPConnection() {
    const command = document.getElementById("mcp-server-command").value.trim();
    const args = document.getElementById("mcp-server-args").value.trim();
    if (!command) {
      this._setMCPStatus("Enter a command first", true);
      return;
    }
    this._setMCPStatus("Testing connection...");
    const result = await this.api.test_mcp_connection(command, args);
    if (result.success) {
      this._setMCPStatus(`Connected — found ${result.tool_count} tool(s)`);
    } else {
      this._setMCPStatus(result.error || "Connection failed", true);
    }
  }

  async _reloadMCPConfig() {
    this._setMCPStatus("Reloading all servers...");
    const result = await this.api.reload_mcp_config();
    if (result.success) {
      this._setMCPStatus("Reloaded");
      this._selectedMCPServer = null;
      await this._loadMCPConfig();
    } else {
      this._setMCPStatus(result.error || "Reload failed", true);
    }
  }

  async _browseMCPCommand() {
    const result = await this.api.open_file_dialog_mcp();
    if (result && result.path) {
      document.getElementById("mcp-server-command").value = result.path;
    }
  }

  _setMCPStatus(text, isError = false) {
    const el = document.getElementById("mcp-status-msg");
    if (el) {
      el.textContent = text;
      el.style.color = isError ? "#e74c3c" : "var(--text-secondary)";
    }
  }

  // =======================================================================
  // Available MCP Tools Dialog
  // =======================================================================

  /**
   * Pop conversation (remove last Q/A)
   */
  async _popConversation() {
    if (!this.currentTabId) return;
    if (this.tabManager.isTabStreaming(this.currentTabId)) return;
    const result = await this.api.pop_conversation(this.currentTabId);
    if (result.success && result.popped) {
      await this._reloadConversationDisplay();
      if (result.question) {
        this.inputArea.setValue(result.question);
      }
    }
  }

  /**
   * Perform handoff — clone current conversation into a new tab and
   * generate an AI summary of what was accomplished and where we left off.
   */
  async _performHandoff() {
    if (!this.currentTabId) return;
    if (this.tabManager.isTabStreaming(this.currentTabId)) {
      await this._showAlert("Cannot perform handoff while streaming.");
      return;
    }
    try {
      const result = await this.api.perform_handoff(this.currentTabId);
      if (!result.success) {
        await this._showAlert(
          `Handoff failed: ${result.error || "unknown error"}`,
        );
      }
      // All further work (tab creation, streaming, post-processing, display
      // reload) is orchestrated by Python via the JS update queue.
    } catch (e) {
      await this._showAlert(`Handoff error: ${e.message || e}`);
    }
  }

  /**
   * Duplicate the current conversation into a new tab.
   */
  async _cloneConversation() {
    if (!this.currentTabId) return;
    if (this.tabManager.isTabStreaming(this.currentTabId)) {
      await this._showAlert("Cannot clone while streaming.");
      return;
    }
    try {
      const result = await this.api.clone_conversation(this.currentTabId);
      if (!result.success) {
        await this._showAlert(
          `Clone failed: ${result.error || "unknown error"}`,
        );
      }
      // Tab creation/switch is queued by Python; polling picks it up.
    } catch (e) {
      await this._showAlert(`Clone error: ${e.message || e}`);
    }
  }

  /**
   * Called by Python after handoff streaming completes and the conversation
   * has been truncated to the single handoff Q/A pair.
   * Reloads the display so the user sees the clean summary.
   */
  async onHandoffComplete(tabId) {
    if (tabId !== this.currentTabId) return;
    await this._reloadConversationDisplay();
  }

  /**
   * Called by Python when a graph branch is created (edit/regen/fork).
   * Reloads the conversation display so the new branch is rendered,
   * then streaming content fills in the new answer.
   */
  async onGraphBranchCreated(tabId, answerIndex) {
    if (tabId !== this.currentTabId) return;
    await this._reloadConversationDisplay();
    // Update streaming state so the toolbar shows the right indicator
    this.inputArea.setStreaming(true);
    this.tabManager.setTabStreaming(tabId, true);
  }

  /**
   * Show about dialog
   */
  async _showAboutDialog() {
    const aboutText =
      "Version 0.11\n\nA chat application using the Ollama API.";
    await this._showAlert(aboutText, "About Alpaca Assist");
  }

  /**
   * Reload the conversation display from Python state.
   * Called after in-place mutations (compact, truncate, pop).
   */
  async _reloadConversationDisplay() {
    if (!this.currentTabId) return;
    this.chatDisplay.clear();
    const result = await this.api.get_conversation_state(this.currentTabId);
    if (result.success && result.state) {
      this._renderConversationState(result.state);
    }
    this._updateStatusBar();
  }

  /**
   * Handle tab switch
   */
  async _onTabSwitched(tabId) {
    this.currentTabId = tabId;
    this._tabSwitchSeq = (this._tabSwitchSeq || 0) + 1;
    const mySeq = this._tabSwitchSeq;
    // Clear any active find highlights before rebuilding chat DOM
    this._findMatches = [];
    this._findCurrentIdx = -1;
    this.chatDisplay.clear();

    // Load conversation state
    const result = await this.api.get_conversation_state(tabId);

    // Guard: discard stale results — covers both tab-change races and
    // same-tab re-switches (e.g. revival's initial load resolving after
    // a user-initiated switch-back fetches a newer state).
    if (tabId !== this.currentTabId || mySeq !== this._tabSwitchSeq) return;

    if (result.success && result.state) {
      this._renderConversationState(result.state);
    }

    // Update window title
    const tab = this.tabManager.getTab(tabId);
    if (tab) {
      document.title = `Alpaca Assist - ${tab.title}`;
    }

    // Sync input area streaming state with the tab's streaming state
    const isTabStreaming = this.tabManager.isTabStreaming(tabId);
    this.inputArea.setStreaming(isTabStreaming);

    this._updateStatusBar();
  }

  /**
   * Render conversation state to chat display
   */
  _renderConversationState(state) {
    console.log(
      "[DEBUG] Rendering conversation state:",
      JSON.stringify(state, null, 2),
    );

    if (!state || !state.chat_state) {
      console.warn("[DEBUG] No chat_state in state");
      return;
    }

    const chatState = state.chat_state;
    console.log("[DEBUG] chatState type:", typeof chatState);
    console.log("[DEBUG] chatState keys:", Object.keys(chatState));

    // Check if it's ConversationGraph format (has 'graph' key) or legacy ChatState format
    if (chatState.graph) {
      console.log("[DEBUG] Rendering ConversationGraph format");
      // New ConversationGraph format
      this._renderConversationGraph(chatState.graph);
    } else if (chatState.questions && chatState.answers) {
      console.log("[DEBUG] Rendering legacy ChatState format");
      console.log("[DEBUG] Questions count:", chatState.questions.length);
      console.log("[DEBUG] Answers count:", chatState.answers.length);
      console.log("[DEBUG] First question:", chatState.questions[0]);
      console.log("[DEBUG] First answer:", chatState.answers[0]);
      // Legacy ChatState format
      this._renderLegacyChatState(chatState);
    } else {
      console.warn(
        "[DEBUG] Unknown chat state format:",
        Object.keys(chatState),
      );
    }
  }

  /**
   * Render ConversationGraph format using the active path only.
   */
  _renderConversationGraph(graph) {
    if (!graph.nodes || !graph.active_node_id) return;

    // Reconstruct active path by walking parent_id links from active_node_id
    const activePath = [];
    let nodeId = graph.active_node_id;
    while (nodeId) {
      const node = graph.nodes[nodeId];
      if (!node) break;
      activePath.unshift(nodeId);
      nodeId = node.parent_id ?? null;
    }

    // Build children index for sibling counting
    const childrenIndex = {};
    for (const [id, node] of Object.entries(graph.nodes)) {
      if (node.parent_id != null) {
        childrenIndex[node.parent_id] = childrenIndex[node.parent_id] || [];
        childrenIndex[node.parent_id].push(id);
      }
    }

    // Walk active path, pairing user/assistant nodes and rendering with QA bars
    let answerIndex = 0;
    let pendingUserId = null;

    for (const id of activePath) {
      const node = graph.nodes[id];
      if (!node) continue;

      if (node.role === "user") {
        pendingUserId = id;
      } else if (node.role === "assistant" && pendingUserId !== null) {
        const userNode = graph.nodes[pendingUserId];

        // Sibling info for the user (question) node.
        // For root-level nodes (parent_id == null), siblings are all root-level nodes —
        // mirrors Python's get_siblings() which returns [n for n in nodes if n.parent_id is None].
        let userSiblings;
        if (userNode.parent_id != null) {
          userSiblings = childrenIndex[userNode.parent_id] || [pendingUserId];
        } else {
          userSiblings = Object.keys(graph.nodes).filter(
            (nid) => graph.nodes[nid].parent_id == null,
          );
          if (userSiblings.length === 0) userSiblings = [pendingUserId];
        }
        const userPos = userSiblings.indexOf(pendingUserId);

        // Render question with QA bar
        const images = (userNode.images || []).map((b64) =>
          this._base64ToDataUri(b64),
        );
        const questionText =
          typeof userNode.content === "string"
            ? userNode.content
            : userNode.content?.components
              ? userNode.content.components
                  .filter((c) => typeof c === "string" || c.type === "text")
                  .map((c) => (typeof c === "string" ? c : c.content))
                  .join("")
              : "";
        this.chatDisplay.addQuestion(questionText, images, {
          nodeId: pendingUserId,
          siblingCount: userSiblings.length,
          position: userPos,
        });

        // Sibling info for the assistant (answer) node
        const asstSiblings = childrenIndex[pendingUserId] || [id];
        const asstPos = asstSiblings.indexOf(id);

        // Render answer content
        const content = node.content;
        if (content) {
          if (typeof content === "string" && content.trim()) {
            this.chatDisplay.appendContent({
              type: "content",
              content,
              answer_index: answerIndex,
              is_done: true,
            });
            this.chatDisplay.finalizeAnswerBar(answerIndex, {
              nodeId: id,
              siblingCount: asstSiblings.length,
              position: asstPos,
            });
          } else if (content.components && content.components.length > 0) {
            this._renderFullAnswer(content, answerIndex);
            this.chatDisplay.finalizeAnswerBar(answerIndex, {
              nodeId: id,
              siblingCount: asstSiblings.length,
              position: asstPos,
            });
          }
        }

        answerIndex++;
        pendingUserId = null;
      }
    }
  }

  /**
   * Render FullAnswer with components.
   *
   * Text that comes BEFORE the first tool_call goes into the pre-folds div
   * (rendered above the folds).  Text that comes AFTER goes into the normal
   * answer div (rendered below the folds).
   */
  _renderFullAnswer(fullAnswer, answerIndex) {
    const components = fullAnswer.components || [];

    // Find the index of the first tool_call to split pre/post fold text.
    const firstFoldIdx = components.findIndex(
      (c) =>
        typeof c === "object" &&
        (c.type === "tool_call" || c.type === "tool_result"),
    );

    // Collect and render pre-fold text (text components before the first fold).
    if (firstFoldIdx > 0) {
      const preFoldText = components
        .slice(0, firstFoldIdx)
        .filter((c) => c.type === "text" || typeof c === "string")
        .map((c) => (typeof c === "string" ? c : c.content))
        .join("");
      if (preFoldText) {
        this.chatDisplay.setPreFoldsText(answerIndex, preFoldText);
      }
    }

    // Pre-compute pair indices so every TC and its matching TR share the same
    // index.  This allows _appendFold to place them adjacent regardless of
    // whether they appear interleaved [TC, TR, TC, TR] or batched [TC, TC, TR, TR].
    //
    // Strategy: pair by component.id (set consistently since the Python fix);
    // fall back to positional matching for old sessions with mismatched IDs.
    const pairIndexFor = new Map(); // component object -> pairIndex
    {
      const tcs = components.filter(
        (c) => typeof c === "object" && c.type === "tool_call",
      );
      const trs = components.filter(
        (c) => typeof c === "object" && c.type === "tool_result",
      );
      const trById = {};
      trs.forEach((tr) => {
        if (tr.id) trById[tr.id] = tr;
      });

      let pi = 0;
      const unpairedTcs = [];
      const usedTrIds = new Set();
      for (const tc of tcs) {
        const tr = tc.id && !usedTrIds.has(tc.id) ? trById[tc.id] : null;
        const idx = pi++;
        pairIndexFor.set(tc, idx);
        if (tr) {
          pairIndexFor.set(tr, idx);
          usedTrIds.add(tc.id);
        } else {
          unpairedTcs.push(tc);
        }
      }
      // Positional fallback: pair unmatched TCs with unmatched TRs in order.
      const unpairedTrs = trs.filter((tr) => !pairIndexFor.has(tr));
      for (let k = 0; k < unpairedTcs.length && k < unpairedTrs.length; k++) {
        pairIndexFor.set(unpairedTrs[k], pairIndexFor.get(unpairedTcs[k]));
      }
      // Any remaining orphan TRs get their own indices.
      unpairedTrs
        .slice(unpairedTcs.length)
        .forEach((tr) => pairIndexFor.set(tr, pi++));
    }

    // Render folds and post-fold text.
    let foldIndex = 0;
    const startIdx = firstFoldIdx === -1 ? 0 : firstFoldIdx;
    for (let i = startIdx; i < components.length; i++) {
      const component = components[i];
      if (typeof component === "object" && component.type) {
        if (component.type === "text") {
          this.chatDisplay.appendContent({
            type: "content",
            content: component.content,
            answer_index: answerIndex,
            is_done: false,
          });
        } else if (component.type === "tool_call") {
          const pairIdx = pairIndexFor.has(component)
            ? pairIndexFor.get(component)
            : foldIndex++;
          const foldId = `fold-call-${answerIndex}-r${pairIdx}`;
          this.chatDisplay.injectFoldWithId(
            answerIndex,
            component.content,
            "call",
            foldId,
          );
        } else if (component.type === "tool_result") {
          const pairIdx = pairIndexFor.has(component)
            ? pairIndexFor.get(component)
            : foldIndex++;
          const foldId = `fold-result-${answerIndex}-r${pairIdx}`;
          this.chatDisplay.injectFoldWithId(
            answerIndex,
            component.content,
            "result",
            foldId,
          );
        }
      } else if (typeof component === "string") {
        this.chatDisplay.appendContent({
          type: "content",
          content: component,
          answer_index: answerIndex,
          is_done: false,
        });
      }
    }

    // Mark as done — creates the answer buffer if nothing else did, flushing pending folds.
    this.chatDisplay.appendContent({
      type: "done",
      content: "",
      answer_index: answerIndex,
      is_done: true,
    });
  }

  /**
   * Render legacy ChatState format
   */
  _renderLegacyChatState(chatState) {
    const questions = chatState.questions || [];
    const answers = chatState.answers || [];

    console.log("[DEBUG] _renderLegacyChatState called");
    console.log("[DEBUG] questions:", questions.length);
    console.log("[DEBUG] answers:", answers.length);

    for (let i = 0; i < questions.length; i++) {
      console.log(`[DEBUG] Rendering Q&A pair ${i}`);

      // Render question
      const questionImages = (chatState.question_images?.[i] || []).map((b64) =>
        this._base64ToDataUri(b64),
      );
      console.log(
        `[DEBUG] Adding question ${i}:`,
        questions[i].substring(0, 50),
      );
      this.chatDisplay.addQuestion(questions[i], questionImages);

      // Render answer if exists
      if (i < answers.length && answers[i]) {
        const answer = answers[i];
        console.log(`[DEBUG] Answer ${i} type:`, typeof answer);
        console.log(`[DEBUG] Answer ${i} value:`, answer);

        if (typeof answer === "string") {
          console.log(`[DEBUG] Rendering string answer ${i}`);
          this.chatDisplay.appendContent({
            type: "content",
            content: answer,
            answer_index: i,
            is_done: true,
          });
        } else if (answer.components) {
          console.log(`[DEBUG] Rendering FullAnswer ${i} with components`);
          this._renderFullAnswer(answer, i);
        } else {
          console.warn(
            `[DEBUG] Answer ${i} has unknown format:`,
            Object.keys(answer),
          );
        }
      } else {
        console.log(`[DEBUG] No answer for question ${i}`);
      }
    }

    console.log("[DEBUG] _renderLegacyChatState complete");
  }

  /**
   * Handle send message
   */
  async _onSendMessage(text, images) {
    if (!this.currentTabId || !text.trim()) return;

    // Transform image data for different consumers:
    // - ChatDisplay.addQuestion needs data URI strings (data:image/jpeg;base64,...)
    // - Ollama API needs plain base64 strings
    const dataUris = images.map(
      (img) => `data:${img.mime_type};base64,${img.data}`,
    );
    const base64s = images.map((img) => img.data);

    // Set streaming state for this tab
    this.inputArea.setStreaming(true);
    this.tabManager.setTabStreaming(this.currentTabId, true);

    // Send to Python — returns node info for QA bar (fast call, just starts a thread)
    try {
      const result = await this.api.send_message(
        this.currentTabId,
        text,
        base64s,
      );
      // Add question to display with QA bar if node info is available
      const qaInfo =
        result && result.user_node_id
          ? {
              nodeId: result.user_node_id,
              siblingCount: result.user_sibling_count || 1,
              position: result.user_position || 0,
            }
          : null;
      this.chatDisplay.addQuestion(text, dataUris, qaInfo);
    } catch (e) {
      // Nothing was actually persisted on the Python side, so don't show a
      // bubble implying it was sent — restore the text instead so it isn't
      // lost, and offer to retry the exact same send.
      console.error("Error sending message:", e);
      this._setStreamingComplete(this.currentTabId);
      this.inputArea.setValue(text);

      const retry = await this._showMessageDialog(
        `Failed to send message: ${e.message || e}`,
        { title: "Send Failed", okText: "Retry", cancelText: "Dismiss" },
      );
      if (retry) {
        await this._onSendMessage(text, images);
      }
    }
  }

  /**
   * Handle stop streaming
   */
  async _onStopStreaming() {
    if (!this.currentTabId) return;

    const isStreaming = this.tabManager.isTabStreaming(this.currentTabId);
    if (!isStreaming) return;

    await this.api.stop_streaming(this.currentTabId);
    this._setStreamingComplete(this.currentTabId);
  }

  /**
   * Set streaming as complete for a specific tab
   */
  _setStreamingComplete(tabId) {
    this.tabManager.setTabStreaming(tabId, false);
    // Only update input area UI if this is the current tab
    if (tabId === this.currentTabId) {
      this.inputArea.setStreaming(false);
    }
  }

  // =======================================================================
  // Python-called callbacks (called via UpdatePoller)
  // =======================================================================

  /**
   * Called when content update arrives from Python
   */
  onContentUpdate(tabId, update) {
    console.log(
      `[APP DEBUG] onContentUpdate called: tabId=${tabId}, currentTabId=${this.currentTabId}, type=${update?.type}`,
    );

    // Always process completion signals, even for non-active tabs
    if (update.is_done || update.type === "done") {
      console.log(`[APP DEBUG] Streaming complete for tab ${tabId}`);
      this._setStreamingComplete(tabId);
      // Don't return here - still need to render if this is the current tab
    }

    // Only render content if this is the current tab
    if (tabId !== this.currentTabId) {
      console.log(`[APP DEBUG] Ignoring content update - tab not focused`);
      return;
    }

    console.log(`[APP DEBUG] Calling chatDisplay.appendContent`);
    this.chatDisplay.appendContent(update);

    // Refresh status bar: immediately when metrics arrive, throttled otherwise
    if (update.metrics) {
      this._updateStatusBar();
    } else {
      this._scheduleStatusUpdate();
    }
  }

  /**
   * Called when streaming starts
   */
  onStreamingStart(tabId, answerIndex) {
    // Always update the tab's streaming state, even if not the current tab
    this.tabManager.setTabStreaming(tabId, true);

    // Only update UI if this is the current tab
    if (tabId === this.currentTabId) {
      this.chatDisplay.setStreaming(true);
      this.inputArea.setStreaming(true);
      this._updateStatusBar();
    }
  }

  /**
   * Called when streaming ends
   */
  onStreamingEnd(tabId, answerIndex) {
    // Always update the tab's streaming state, even if not the current tab
    this.tabManager.setTabStreaming(tabId, false);

    // Only update UI if this is the current tab
    if (tabId === this.currentTabId) {
      this.chatDisplay.setStreaming(false);
      this.inputArea.setStreaming(false);
      this._updateStatusBar();
    }
  }

  /**
   * Called when error occurs
   */
  onError(tabId, error) {
    // Always clear streaming state, even for background tabs
    console.error(`[APP] Error on tab ${tabId}:`, error.message);
    this._setStreamingComplete(tabId);

    // Show a toast so errors from background tabs are never silently dropped.
    const tab = this.tabManager.tabs.get(tabId);
    const tabHint =
      tabId !== this.currentTabId && tab ? `From tab: ${tab.title}` : null;
    this._showToast(error.message, { type: "error", tabHint });
  }

  /**
   * Called when tab title should be updated
   */
  updateTabTitle(tabId, title) {
    this.tabManager.updateTabTitle(tabId, title);
    if (tabId === this.currentTabId) {
      document.title = `Alpaca Assist - ${title}`;
    }
  }

  /**
   * Called by Python to select a specific tab during session restoration
   */
  selectTab(tabId) {
    console.log(`[APP DEBUG] selectTab called: ${tabId}`);
    if (this.tabManager) {
      this.tabManager.switchToTab(tabId);
    }
  }

  /**
   * Called when tool fold should be injected
   */
  injectToolFold(tabId, foldData) {
    if (tabId !== this.currentTabId) return;

    console.log(`[APP DEBUG] injectToolFold called:`, foldData);

    // Inject the fold via ChatDisplay using the specific fold_id from Python
    if (this.chatDisplay && foldData.fold_id) {
      this.chatDisplay.injectFoldWithId(
        foldData.answer_index,
        foldData.body,
        foldData.type,
        foldData.fold_id,
      );
    }
  }

  // =======================================================================
  // Q/A Actions
  // =======================================================================

  async _onEditQuestion(nodeId) {
    if (!this.currentTabId) return;
    if (this.tabManager.isTabStreaming(this.currentTabId)) return;

    const container = this.chatDisplay.container;

    // Only one inline editor at a time
    if (container.querySelector(".inline-edit-container")) return;

    const msgEl = container.querySelector(
      `.message.question[data-node-id="${nodeId}"]`,
    );
    if (!msgEl) return;

    const contentEl = msgEl.querySelector(".message-content");
    const rawText =
      msgEl.dataset.rawText || contentEl?.textContent?.trim() || "";

    const editorEl = document.createElement("div");
    editorEl.className = "inline-edit-container";
    editorEl.innerHTML = `
      <textarea class="inline-edit-textarea"></textarea>
      <div class="inline-edit-hint">Ctrl+Enter to save · Esc to cancel</div>
      <div class="inline-edit-actions">
        <button class="inline-edit-save">Save</button>
        <button class="inline-edit-cancel">Cancel</button>
      </div>`;
    editorEl.querySelector(".inline-edit-textarea").value = rawText;

    contentEl.style.display = "none";
    msgEl.appendChild(editorEl);

    const textarea = editorEl.querySelector(".inline-edit-textarea");
    textarea.focus();
    textarea.setSelectionRange(textarea.value.length, textarea.value.length);

    const cleanup = () => {
      editorEl.remove();
      contentEl.style.display = "";
    };

    const save = async () => {
      const newText = textarea.value.trim();
      if (!newText) {
        cleanup();
        return;
      }
      cleanup();
      const result = await this.api.edit_question(
        this.currentTabId,
        nodeId,
        newText,
      );
      if (!result.success) {
        this._showError(result.error || "Could not edit question");
      }
    };

    editorEl.querySelector(".inline-edit-save").addEventListener("click", save);
    editorEl
      .querySelector(".inline-edit-cancel")
      .addEventListener("click", cleanup);
    textarea.addEventListener("keydown", (e) => {
      if (e.key === "Escape") cleanup();
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) save();
    });
  }

  async _onForkConversation(nodeId) {
    if (!this.currentTabId) return;
    if (this.tabManager.isTabStreaming(this.currentTabId)) return;

    const container = this.chatDisplay.container;

    // Only one fork panel at a time
    if (container.querySelector(".inline-fork-container")) return;

    // Place the fork panel after the answer wrapper that follows this question
    const msgEl = container.querySelector(
      `.message.question[data-node-id="${nodeId}"]`,
    );
    const anchor = msgEl?.nextElementSibling?.classList.contains(
      "answer-wrapper",
    )
      ? msgEl.nextElementSibling
      : msgEl;

    const forkEl = document.createElement("div");
    forkEl.className = "inline-fork-container";
    forkEl.innerHTML = `
      <div class="inline-fork-label">Fork — new follow-up question</div>
      <textarea class="inline-fork-textarea" placeholder="Enter your question..."></textarea>
      <div class="inline-fork-hint">Ctrl+Enter to submit · Esc to cancel</div>
      <div class="inline-fork-actions">
        <button class="inline-fork-submit">Submit</button>
        <button class="inline-fork-cancel">Cancel</button>
      </div>`;

    anchor ? anchor.after(forkEl) : container.appendChild(forkEl);
    forkEl.scrollIntoView({ block: "nearest" });

    const textarea = forkEl.querySelector(".inline-fork-textarea");
    textarea.focus();

    const cleanup = () => forkEl.remove();

    const submit = async () => {
      const newQuestion = textarea.value.trim();
      if (!newQuestion) {
        cleanup();
        return;
      }
      cleanup();
      const result = await this.api.fork_conversation(
        this.currentTabId,
        nodeId,
        newQuestion,
      );
      if (!result.success) {
        this._showError(result.error || "Could not fork conversation");
      }
    };

    forkEl
      .querySelector(".inline-fork-submit")
      .addEventListener("click", submit);
    forkEl
      .querySelector(".inline-fork-cancel")
      .addEventListener("click", cleanup);
    textarea.addEventListener("keydown", (e) => {
      if (e.key === "Escape") cleanup();
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) submit();
    });
  }

  async _onRegenerateAnswer(nodeId) {
    if (!this.currentTabId) return;
    if (this.tabManager.isTabStreaming(this.currentTabId)) return;

    // nodeId is the assistant node.  _find_pair_index_for_node on the
    // Python side accepts either user or assistant node IDs, so we can
    // pass it directly.
    const result = await this.api.regenerate_answer(this.currentTabId, nodeId);
    if (!result.success) {
      await this._showAlert(
        `Could not regenerate: ${result.error || "unknown error"}`,
      );
    }
    // onGraphBranchCreated will be pushed by Python to reload the display
  }

  async _onNavigateSibling(nodeId, direction) {
    if (!this.currentTabId) return;
    if (this.tabManager.isTabStreaming(this.currentTabId)) return;

    const result = await this.api.navigate_sibling(
      this.currentTabId,
      nodeId,
      direction,
    );
    if (result.success) {
      const prevScrollTop = this.chatDisplay.container.scrollTop;
      await this._reloadConversationDisplay();
      this.chatDisplay.container.scrollTop = prevScrollTop;
    }
  }

  // =======================================================================
  // Dialogs
  // =======================================================================

  _showDialog(dialogId) {
    document
      .querySelectorAll(".dialog")
      .forEach((d) => d.classList.remove("active"));
    document.getElementById(dialogId).classList.add("active");
    document.getElementById("dialog-overlay").classList.add("active");
  }

  _showPreferencesDialog() {
    this._showDialog("preferences-dialog");
  }

  _closeDialogs() {
    // Scoped to #dialog-overlay's own children — the message dialog lives
    // in its own overlay (see _showMessageDialog) and manages itself.
    document.getElementById("dialog-overlay").classList.remove("active");
    document
      .querySelectorAll("#dialog-overlay .dialog")
      .forEach((d) => d.classList.remove("active"));
  }

  /**
   * Show a custom alert/confirm/prompt dialog. Replaces native
   * alert()/confirm()/prompt() so the page origin is never shown to the
   * user, and so the dialog centers on the app window instead of the OS
   * screen. Lives in its own overlay so it can stack above an
   * already-open dialog (e.g. confirming inside MCP Config) without
   * disturbing it.
   *
   * @param {string} message
   * @param {{title?: string, okText?: string, cancelText?: string|null,
   *   withInput?: boolean, inputDefault?: string}} opts
   *   cancelText: null (default) shows only an OK button (alert-style).
   *   Pass a label to also show a Cancel button (confirm-style).
   *   withInput shows a text field (prompt-style); the resolved value is
   *   then the input's string (or null if cancelled) instead of a boolean.
   * @returns {Promise<boolean|string|null>}
   */
  _showMessageDialog(message, opts = {}) {
    const {
      title = "Alpaca Assist",
      okText = "OK",
      cancelText = null,
      withInput = false,
      inputDefault = "",
    } = opts;
    return new Promise((resolve) => {
      document.getElementById("message-dialog-title").textContent = title;
      document.getElementById("message-dialog-text").textContent = message;

      const okBtn = document.getElementById("message-dialog-ok-btn");
      const cancelBtn = document.getElementById("message-dialog-cancel-btn");
      const input = document.getElementById("message-dialog-input");
      okBtn.textContent = okText;
      cancelBtn.style.display = cancelText !== null ? "" : "none";
      cancelBtn.textContent = cancelText || "Cancel";
      input.classList.toggle("active", withInput);
      input.value = withInput ? inputDefault : "";

      const overlay = document.getElementById("message-dialog-overlay");

      const cleanup = (confirmed) => {
        okBtn.removeEventListener("click", onOk);
        cancelBtn.removeEventListener("click", onCancel);
        input.removeEventListener("keydown", onInputKeydown);
        overlay.classList.remove("active");
        this._messageDialogDismiss = null;
        resolve(withInput ? (confirmed ? input.value : null) : confirmed);
      };
      const onOk = () => cleanup(true);
      const onCancel = () => cleanup(false);
      const onInputKeydown = (e) => {
        if (e.key === "Enter") onOk();
      };

      okBtn.addEventListener("click", onOk);
      cancelBtn.addEventListener("click", onCancel);
      if (withInput) input.addEventListener("keydown", onInputKeydown);
      this._messageDialogDismiss = onCancel;

      overlay.classList.add("active");
      (withInput ? input : okBtn).focus();
    });
  }

  async _showAlert(message, title = "Alpaca Assist") {
    await this._showMessageDialog(message, { title, cancelText: null });
  }

  async _showConfirm(message, title = "Confirm") {
    return this._showMessageDialog(message, { title, cancelText: "Cancel" });
  }

  async _showPrompt(message, inputDefault = "", title = "Alpaca Assist") {
    return this._showMessageDialog(message, {
      title,
      cancelText: "Cancel",
      withInput: true,
      inputDefault,
    });
  }

  async _savePreferences() {
    const preferences = {
      api_url: document.getElementById("pref-api-url").value,
      font_family: document.getElementById("pref-font-family").value,
      font_size: parseInt(document.getElementById("pref-font-size").value, 10),
      theme: document.getElementById("pref-theme").value,
    };

    const result = await this.api.save_preferences(preferences);
    if (result.success) {
      this._applyPreferences(preferences);
      this._closeDialogs();
    }
  }

  async _showToolsDialog() {
    this._showDialog("mcp-tools-dialog");
    this._selectedToolRow = null;
    const tbody = document.getElementById("mcp-tools-table-body");
    tbody.innerHTML =
      '<tr><td colspan="3" class="mcp-tools-empty-row">Loading...</td></tr>';

    const result = await this.api.get_mcp_tools();
    if (!result.success) {
      tbody.innerHTML =
        '<tr><td colspan="3" class="mcp-tools-empty-row">Failed to load tools</td></tr>';
      return;
    }
    const tools = result.tools || [];
    if (tools.length === 0) {
      tbody.innerHTML =
        '<tr><td colspan="3" class="mcp-tools-empty-row">No tools available — configure MCP servers first</td></tr>';
      return;
    }
    tbody.innerHTML = "";
    for (const tool of tools) {
      const fn = tool.function || {};
      const fullName = fn.name || "";
      const idx = fullName.indexOf("_");
      const serverName = idx > 0 ? fullName.slice(0, idx) : fullName;
      const toolName = idx > 0 ? fullName.slice(idx + 1) : "";

      const tr = document.createElement("tr");
      tr.dataset.serverName = serverName;
      tr.dataset.toolName = toolName;

      const tdS = document.createElement("td");
      tdS.textContent = serverName;
      const tdT = document.createElement("td");
      tdT.textContent = toolName;
      const tdD = document.createElement("td");
      tdD.textContent = fn.description || "";

      tr.appendChild(tdS);
      tr.appendChild(tdT);
      tr.appendChild(tdD);
      tr.addEventListener("click", () => {
        document
          .querySelectorAll("#mcp-tools-table-body tr.selected")
          .forEach((r) => r.classList.remove("selected"));
        tr.classList.add("selected");
        this._selectedToolRow = tr;
      });
      tbody.appendChild(tr);
    }
  }

  async _callSelectedTool() {
    if (!this._selectedToolRow) {
      await this._showAlert("Select a tool first");
      return;
    }
    const serverName = this._selectedToolRow.dataset.serverName;
    const toolName = this._selectedToolRow.dataset.toolName;
    const argsJson = await this._showPrompt(
      `Call ${serverName} / ${toolName}\n\nJSON arguments:`,
      "{}",
    );
    if (argsJson === null) return;

    const result = await this.api.call_mcp_tool_direct(
      serverName,
      toolName,
      argsJson,
    );
    if (result.success) {
      const msgInput = document.getElementById("message-input");
      if (msgInput && this.currentTabId) {
        msgInput.value =
          (msgInput.value ? msgInput.value + "\n" : "") + result.result;
      } else {
        await this._showAlert("Result:\n" + result.result);
      }
      this._closeDialogs();
    } else {
      await this._showAlert("Error: " + (result.error || "Unknown error"));
    }
  }

  // =======================================================================
  // Agent Skills Dialog
  // =======================================================================

  async _showAgentSkillsDialog() {
    this._showDialog("agent-skills-dialog");
    document.getElementById("skills-count").textContent = "Loading...";
    document.getElementById("skills-list").innerHTML = "";

    const result = await this.api.get_agent_skills_config();
    if (!result.success) {
      document.getElementById("skills-count").textContent =
        "Error loading config";
      return;
    }
    this._originalSkillsConfig = {
      enabled: result.enabled,
      directories: result.directories,
    };

    document.getElementById("skills-enabled").checked = result.enabled;
    document.getElementById("skills-default-dir").textContent =
      result.default_dir || "";
    document.getElementById("skills-directories").value = (
      result.directories || []
    ).join("\n");

    this._renderSkillsList(result.skills || []);
    const count = (result.skills || []).length;
    document.getElementById("skills-count").textContent =
      count > 0 ? `${count} skill(s) available` : "No skills discovered";
  }

  _renderSkillsList(skills) {
    const container = document.getElementById("skills-list");
    if (!skills || skills.length === 0) {
      container.innerHTML =
        '<div class="skills-empty">No skills discovered</div>';
      return;
    }
    container.innerHTML = "";
    for (const skill of skills) {
      const card = document.createElement("div");
      card.className = "skill-card";

      const header = document.createElement("div");
      header.className = "skill-card-header";
      header.textContent =
        skill.name + (skill.license ? ` (${skill.license})` : "");

      const desc = document.createElement("div");
      desc.className = "skill-card-desc";
      desc.textContent = skill.description || "";

      const loc = document.createElement("div");
      loc.className = "skill-card-location";
      loc.textContent = skill.location || "";

      card.appendChild(header);
      if (skill.description) card.appendChild(desc);
      card.appendChild(loc);
      container.appendChild(card);
    }
  }

  async _refreshSkills() {
    document.getElementById("skills-count").textContent = "Refreshing...";
    const result = await this.api.refresh_skills();
    if (result.success) {
      this._renderSkillsList(result.skills || []);
      const count = result.skill_count || 0;
      document.getElementById("skills-count").textContent =
        count > 0 ? `${count} skill(s) available` : "No skills discovered";
    } else {
      document.getElementById("skills-count").textContent = "Error refreshing";
    }
  }

  async _saveAgentSkills() {
    const enabled = document.getElementById("skills-enabled").checked;
    const dirsText = document.getElementById("skills-directories").value;
    const directories = dirsText
      .split("\n")
      .map((d) => d.trim())
      .filter(Boolean);
    const result = await this.api.save_agent_skills_config({
      enabled,
      directories,
    });
    if (result.success) {
      this._closeDialogs();
      this._updateStatusBar();
    } else {
      await this._showAlert(
        "Failed to save: " + (result.error || "Unknown error"),
      );
    }
  }

  // =======================================================================
  // Status Bar
  // =======================================================================

  /**
   * Fetch stats from Python and render the status bar for the current tab.
   * Follows the same format/rules as the ollama_query reference implementation.
   */
  async _updateStatusBar() {
    const statusText = document.getElementById("status-text");
    const tokenCount = document.getElementById("token-count");
    if (!statusText || !tokenCount) return;

    if (!this.currentTabId) {
      statusText.textContent = "";
      tokenCount.textContent = "⚪ Idle";
      return;
    }

    // Capture streaming state BEFORE the async call — if onStreamingEnd fires
    // during the await, reading isTabStreaming afterwards would give the wrong
    // (post-end) value and show "⚪ Idle" while streaming is still in progress.
    const isStreaming = this.tabManager
      ? this.tabManager.isTabStreaming(this.currentTabId)
      : false;

    const result = await this.api.get_status_info(this.currentTabId);
    if (!result || !result.success) return;

    // Left: conversation size + token info
    const leftParts = [
      `Chat: ${result.char_count.toLocaleString()} chars, ${result.line_count.toLocaleString()} lines`,
    ];
    if (result.session_output_tokens > 0 || result.session_input_tokens > 0) {
      let tok = `API: in:${result.session_input_tokens.toLocaleString()} out:${result.session_output_tokens.toLocaleString()} tokens`;
      if (result.latency_ms) {
        tok += `, ${(result.latency_ms / 1000).toFixed(1)}s`;
      }
      leftParts.push(tok);
    } else {
      leftParts.push(`~${result.token_estimate.toLocaleString()} tokens (est)`);
    }
    statusText.textContent = leftParts.join(", ");

    // Right: streaming indicator + skills
    const rightParts = [isStreaming ? "🟢 Streaming" : "⚪ Idle"];
    if (result.skill_count > 0) {
      rightParts.push(`📚 ${result.skill_count} skills`);
    }
    tokenCount.textContent = rightParts.join("  ");
  }

  /**
   * Schedule a throttled status bar refresh (coalesces rapid calls to 1/s).
   */
  _scheduleStatusUpdate() {
    if (this._statusUpdateTimer) return;
    this._statusUpdateTimer = setTimeout(() => {
      this._statusUpdateTimer = null;
      this._updateStatusBar();
    }, 1000);
  }

  /**
   * Show a temporary message in the status bar, then restore normal stats.
   */
  setStatusMessage(text, duration = 3000) {
    const statusText = document.getElementById("status-text");
    if (statusText) statusText.textContent = text;
    clearTimeout(this._statusMsgTimer);
    this._statusMsgTimer = setTimeout(() => this._updateStatusBar(), duration);
  }

  // =======================================================================
  // Utilities
  // =======================================================================

  /**
   * Show a non-blocking toast notification.
   *
   * Unlike _showAlert (modal, blocks the UI) this renders an ambient banner
   * that auto-dismisses and is visible regardless of which tab is active.
   *
   * @param {string} message
   * @param {{type?: 'error'|'warning'|'info'|'success',
   *           duration?: number,
   *           tabHint?: string}} opts
   *   type     — visual style (default 'error')
   *   duration — ms before auto-dismiss (default 6000; 0 = never)
   *   tabHint  — shown as a secondary line, e.g. "from tab: My Chat"
   */
  _showToast(message, opts = {}) {
    const { type = "error", duration = 6000, tabHint = null } = opts;

    const container = document.getElementById("toast-container");
    if (!container) return;

    const toast = document.createElement("div");
    toast.className = `toast toast--${type}`;

    const body = document.createElement("div");
    body.className = "toast__body";
    body.textContent = message;

    if (tabHint) {
      const hint = document.createElement("span");
      hint.className = "toast__tab-hint";
      hint.textContent = tabHint;
      body.appendChild(hint);
    }

    const closeBtn = document.createElement("button");
    closeBtn.className = "toast__close";
    closeBtn.textContent = "×";
    closeBtn.title = "Dismiss";

    toast.appendChild(body);
    toast.appendChild(closeBtn);
    container.appendChild(toast);

    const dismiss = () => {
      if (!toast.isConnected) return;
      toast.classList.add("toast-hiding");
      toast.addEventListener("transitionend", () => toast.remove(), {
        once: true,
      });
      setTimeout(() => toast.remove(), 400); // failsafe
    };

    closeBtn.addEventListener("click", dismiss);
    if (duration > 0) setTimeout(dismiss, duration);

    return dismiss; // caller can dismiss early if needed
  }

  async _saveAndClose() {
    const result = await this.api.save_and_close();
    if (!result.success) {
      // Save failed — show persistent error and keep window open so the user
      // can retry or copy important content before closing.
      this._showToast(
        `Session save failed: ${result.error || "unknown error"}`,
        { type: "error", duration: 0 },
      );
    }
    // On success, Python has already called window.destroy() — nothing to do.
  }

  async _showError(message) {
    console.error(message);
    await this._showAlert(message, "Error");
  }

  /**
   * Update input area visibility based on tab count
   * Hide input area when no tabs exist
   */
  _updateInputAreaVisibility() {
    const tabCount = this.tabManager ? this.tabManager.getTabCount() : 0;
    const inputArea = document.getElementById("input-area");

    if (inputArea) {
      if (tabCount === 0) {
        inputArea.classList.add("hidden");
        console.log("[APP DEBUG] Input area hidden - no tabs");
      } else {
        inputArea.classList.remove("hidden");
        console.log("[APP DEBUG] Input area visible - tabs exist");
      }
    }
  }

  /**
   * Convert a plain base64 image string to a data URI.
   * Sniffs MIME type from the leading base64 characters (magic bytes).
   */
  _base64ToDataUri(b64) {
    let mime = "image/jpeg"; // fallback
    if (b64.startsWith("iVBORw")) mime = "image/png";
    else if (b64.startsWith("R0lGOD")) mime = "image/gif";
    else if (b64.startsWith("UklG")) mime = "image/webp";
    return `data:${mime};base64,${b64}`;
  }

  /**
   * Copy code to clipboard
   */
  async copyCode(button) {
    const codeBlock = button.closest(".code-block").querySelector("code");
    const code = codeBlock.textContent;

    const success = await Helpers.copyToClipboard(code);
    if (success) {
      const originalText = button.textContent;
      button.textContent = "Copied!";
      setTimeout(() => {
        button.textContent = originalText;
      }, 2000);
    }
  }
}

// Export class for testing
window.AlpacaApp = AlpacaApp;

// Initialize app when DOM is ready
document.addEventListener("DOMContentLoaded", () => {
  window.app = new AlpacaApp();
});
