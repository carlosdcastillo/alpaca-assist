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
      // Drop this tab's surface panels. Only the local view goes: the
      // remote Xvfb/x11vnc/app keep running, exactly like the Pack daemon
      // itself, and the supervisor's idle reaper collects them.
      window.SurfaceDock?.removeTab(e.detail.tabId);
      // Update input area visibility when tab is closed
      this._updateInputAreaVisibility();
    });

    // Listen for tab creation to update input area visibility
    document.addEventListener("tabCreated", () => {
      this._updateInputAreaVisibility();
    });

    // Toolbar button events
    this._bindToolbarEvents();

    // Conversation sidebar visibility
    this._setupSidebarToggle();

    // Menu category hover/click handling
    this._setupMenuBar();

    // Menu item actions
    document.querySelectorAll(".menu-item").forEach((btn) => {
      if (btn.dataset.action === "copy-markdown") {
        btn.addEventListener("mousedown", (e) => {
          // Preserve the chat selection while opening and clicking the menu.
          e.preventDefault();
        });
      }
      btn.addEventListener("click", (e) => {
        const action = e.currentTarget.dataset.action;
        this._handleMenuAction(action);
        this._closeAllMenus();
      });
    });

    // The toolbar New Tab button opens the Local/Pack menu. Ctrl+N still
    // creates a local tab directly for the fast path.
    const newTabDropdown = document.getElementById("toolbar-new-tab-dropdown");
    const newTabButton = document.getElementById("toolbar-new-tab");
    const newTabMenu = document.getElementById("toolbar-new-tab-menu");
    const closeNewTabDropdown = () => {
      newTabDropdown.classList.remove("open");
      newTabButton.setAttribute("aria-expanded", "false");
    };
    newTabButton.addEventListener("click", (e) => {
      e.stopPropagation();
      const willOpen = !newTabDropdown.classList.contains("open");
      newTabDropdown.classList.toggle("open", willOpen);
      newTabButton.setAttribute("aria-expanded", String(willOpen));
    });
    newTabMenu.querySelectorAll(".new-tab-menu-item").forEach((item) => {
      item.addEventListener("click", (e) => {
        e.stopPropagation();
        closeNewTabDropdown();
        this._handleMenuAction(item.dataset.action);
      });
    });

    // Close the new-tab dropdown on outside-click or Escape.
    document.addEventListener("click", (e) => {
      if (!e.target.closest(".toolbar-new-tab-dropdown")) {
        closeNewTabDropdown();
      }
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        closeNewTabDropdown();
      }
    });

    // Close menus when clicking elsewhere
    document.addEventListener("click", (e) => {
      if (!e.target.closest(".menu-category")) {
        this._closeAllMenus();
      }
    });

    // Intercept internal alpaca:// navigation links (e.g. handoff back-references).
    // Rendered markdown produces <a href="alpaca://conv/{convId}"> elements; we
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
              `The original task could not be found (${
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
          "#mcp-config-close-btn, #mcp-tools-close-btn, #skills-cancel-btn, " +
          "#workspace-changes-close-btn, #pack-tab-cancel",
      )
      .forEach((btn) => {
        btn.addEventListener("click", () => this._closeDialogs());
      });

    // Workspace changes panel, opened from the header's changes chip
    document
      .getElementById("workspace-header-changes")
      .addEventListener("click", () => this._showWorkspaceChanges());
    document
      .getElementById("workspace-changes-refresh-btn")
      .addEventListener("click", () => this._loadWorkspaceChanges());

    document.getElementById("pack-tab-form").addEventListener("submit", (e) => {
      e.preventDefault();
      this._createPackTabFromDialog();
    });
    document
      .getElementById("pack-custom-host")
      .addEventListener("input", () => {
        document.querySelector(
          'input[name="pack-host"][value="__custom__"]',
        ).checked = true;
        document
          .getElementById("pack-custom-host")
          .removeAttribute("aria-invalid");
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
    document
      .getElementById("history-rename-btn")
      .addEventListener("click", () => this._renameHistoryEntry());
    document
      .getElementById("history-pin-btn")
      .addEventListener("click", () => this._toggleHistoryMetadata("pinned"));
    document
      .getElementById("history-archive-btn")
      .addEventListener("click", () => this._toggleHistoryMetadata("archived"));
    document
      .getElementById("history-folder-btn")
      .addEventListener("click", () => this._editHistoryFolder());
    document
      .getElementById("history-tags-btn")
      .addEventListener("click", () => this._editHistoryTags());
    document
      .getElementById("history-backup-btn")
      .addEventListener("click", () => this._backupHistory());
    document
      .getElementById("history-import-btn")
      .addEventListener("click", () => this._importHistory());
    document
      .getElementById("history-select-all")
      .addEventListener("change", (e) =>
        this._selectAllHistory(e.target.checked),
      );
    document.querySelectorAll("[data-history-filter]").forEach((button) => {
      button.addEventListener("click", () => this._setHistoryFilter(button));
    });

    const historyList = document.getElementById("history-list");
    const historyScrollbar = document.getElementById("history-scrollbar");
    const historyScrollbarThumb = document.getElementById(
      "history-scrollbar-thumb",
    );
    historyList.addEventListener("scroll", () =>
      this._updateHistoryScrollbar(),
    );
    historyScrollbar.addEventListener("click", (event) => {
      if (event.target === historyScrollbarThumb) return;
      const track = historyScrollbar.getBoundingClientRect();
      const maxScroll = historyList.scrollHeight - historyList.clientHeight;
      const maxThumbTop = track.height - historyScrollbarThumb.offsetHeight;
      historyList.scrollTop =
        ((event.clientY - track.top - historyScrollbarThumb.offsetHeight / 2) /
          maxThumbTop) *
        maxScroll;
    });
    historyScrollbarThumb.addEventListener("pointerdown", (event) => {
      event.preventDefault();
      const startY = event.clientY;
      const startScrollTop = historyList.scrollTop;
      const maxScroll = historyList.scrollHeight - historyList.clientHeight;
      const maxThumbTop =
        historyScrollbar.clientHeight - historyScrollbarThumb.offsetHeight;
      historyScrollbarThumb.setPointerCapture(event.pointerId);
      const onPointerMove = (moveEvent) => {
        historyList.scrollTop =
          startScrollTop +
          ((moveEvent.clientY - startY) / maxThumbTop) * maxScroll;
      };
      historyScrollbarThumb.addEventListener("pointermove", onPointerMove);
      historyScrollbarThumb.addEventListener(
        "pointerup",
        () =>
          historyScrollbarThumb.removeEventListener(
            "pointermove",
            onPointerMove,
          ),
        { once: true },
      );
    });
    window.addEventListener("resize", () => this._updateHistoryScrollbar());

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

    // Splitter drag events
    this._setupSplitter();
    this._setupSurfaceSplitter();

    // Keyboard shortcuts
    document.addEventListener("keydown", (e) => {
      if (e.ctrlKey || e.metaKey) {
        // Ctrl+Shift combos
        if (e.shiftKey) {
          switch (e.key) {
            case "C":
              e.preventDefault();
              this._handleMenuAction("copy-markdown");
              return;
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
   * Keep the sidebar optional without hiding the control that restores it.
   * This is a local layout preference rather than conversation data.
   */
  _setupSidebarToggle() {
    const hideToggle = document.getElementById("sidebar-hide-toggle");
    const showToggle = document.getElementById("sidebar-show-toggle");
    if (!hideToggle || !showToggle) return;

    let collapsed = false;
    try {
      collapsed = localStorage.getItem("sidebar-collapsed") === "true";
    } catch (_error) {
      // Storage can be unavailable for restricted WebView origins.
    }
    this._setSidebarCollapsed(collapsed);

    hideToggle.addEventListener("click", () => this._setSidebarCollapsed(true));
    showToggle.addEventListener("click", () =>
      this._setSidebarCollapsed(false),
    );
  }

  _setSidebarCollapsed(collapsed) {
    const appBody = document.querySelector(".app-body");
    const sidebar = document.getElementById("tab-bar");
    const hideToggle = document.getElementById("sidebar-hide-toggle");
    const showToggle = document.getElementById("sidebar-show-toggle");
    if (!appBody || !sidebar || !hideToggle || !showToggle) return;

    appBody.classList.toggle("sidebar-collapsed", collapsed);
    sidebar.setAttribute("aria-hidden", String(collapsed));
    sidebar.inert = collapsed;
    hideToggle.setAttribute("aria-expanded", String(!collapsed));
    showToggle.setAttribute("aria-expanded", String(!collapsed));
    showToggle.hidden = !collapsed;

    try {
      localStorage.setItem("sidebar-collapsed", String(collapsed));
    } catch (_error) {
      // The toggle still works for this session when storage is unavailable.
    }
  }

  /**
   * Setup draggable splitter between chat and input area
   */
  _setupSplitter() {
    const splitter = document.getElementById("splitter");
    const mainContent = document.querySelector(".main-content");
    const chatContainer = document.getElementById("chat-container");
    const inputArea = document.getElementById("input-area");

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
   * Open a live app surface on the current Pack tab's remote host.
   *
   * Profiles are offered first and a raw command is the fallback. That
   * ordering is the same one the supervisor enforces from the other side:
   * the model may only ever name a profile, while this path — a human who
   * already has ssh access to that host — may type a command.
   */
  async _openAppSurface() {
    const tabId = this.currentTabId;
    if (!tabId) return;

    const listed = await this.api.surface_control(tabId, "surface_list", {});
    if (!listed?.success) {
      await this._showAlert(listed?.error || "Surfaces are unavailable here.");
      return;
    }

    const profiles = listed.profiles || [];
    const choice = await this._showMessageDialog(
      profiles.length
        ? "Choose an app to run on the remote display:"
        : "No profiles are configured. Enter a command to run on the remote display:",
      {
        title: "Open App Surface",
        okText: "Open",
        cancelText: "Cancel",
        withInput: profiles.length === 0,
        selectOptions: profiles.map((name) => ({ value: name, label: name })),
        selectCustomLabel: "Custom command…",
      },
    );
    if (choice === null || !String(choice).trim()) return;

    const value = String(choice).trim();
    // Whitespace splitting, not a shell parse: nothing here reaches a shell
    // (the supervisor spawns an argv list), and a command needing real
    // quoting belongs in surface_profiles.json anyway.
    const spec = profiles.includes(value)
      ? { profile: value }
      : { argv: value.split(/\s+/) };

    // Opening a surface spawns Xvfb, x11vnc, the app itself and an SSH
    // tunnel, so it takes a couple of seconds — say so rather than letting
    // the menu just close.
    const status = document.getElementById("status-text");
    if (status) status.textContent = "Starting app surface…";
    const result = await this.api.surface_open(tabId, spec, 1280, 800);
    if (!result?.success) {
      this._updateStatusBar();
      await this._showAlert(`Could not open the surface: ${result?.error}`);
      return;
    }
    await window.SurfaceDock?.show(tabId, result);
    this._updateStatusBar();
  }

  /**
   * Setup draggable splitter between the chat area and the surface dock.
   *
   * Horizontal twin of _setupSplitter: that one resizes the input area
   * against the chat, this one resizes the dock against the whole of
   * .main-content. Both are no-ops until their elements exist, and the dock
   * splitter stays hidden until a surface is actually open.
   */
  _setupSurfaceSplitter() {
    const splitter = document.getElementById("surface-splitter");
    const dock = document.getElementById("surface-dock");
    if (!splitter || !dock) return;

    let isDragging = false;
    let startX = 0;
    let startWidth = 0;

    splitter.addEventListener("mousedown", (e) => {
      isDragging = true;
      startX = e.clientX;
      startWidth = dock.getBoundingClientRect().width;
      splitter.classList.add("dragging");
      document.body.style.cursor = "col-resize";
      e.preventDefault();
    });

    document.addEventListener("mousemove", (e) => {
      if (!isDragging) return;
      // Dragging left widens the dock; it is pinned to the right edge.
      const newWidth = Math.max(260, startWidth - (e.clientX - startX));
      dock.style.flex = `0 0 ${newWidth}px`;
    });

    document.addEventListener("mouseup", () => {
      if (!isDragging) return;
      isDragging = false;
      splitter.classList.remove("dragging");
      document.body.style.cursor = "";
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
      this.tabManager.createTab("New Task");
    }
    this._updateInputAreaVisibility();
  }

  /**
   * Apply preferences to UI
   */
  _applyPreferences(prefs) {
    // Theme. Only "dark" (the default, no attribute needed) and "light"
    // (web/css/themes.css [data-theme="light"]) are real themes. A stale or
    // otherwise unrecognized value (e.g. a preferences.json predating this
    // check) silently falls back to dark instead of setting an attribute
    // nothing styles and leaving the Preferences dialog's Theme dropdown
    // blank.
    const theme = prefs.theme === "light" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", theme);

    // Font. --font-ui drives chrome (menus, tabs, buttons); --font-content is
    // the separate reading face for message prose, so switching one doesn't
    // drag the other along.
    if (prefs.font_family) {
      document.documentElement.style.setProperty(
        "--font-ui",
        prefs.font_family,
      );
    }
    if (prefs.content_font_family) {
      document.documentElement.style.setProperty(
        "--font-content",
        prefs.content_font_family,
      );
    }
    // Message/answer text size. Was read from and saved to preferences.json
    // but never applied anywhere — the Preferences dialog's Font Size field
    // did nothing.
    if (prefs.font_size) {
      document.documentElement.style.setProperty(
        "--content-font-size",
        `${prefs.font_size}px`,
      );
    }

    // Model
    if (prefs.model) {
      this.inputArea.setSelectedModel(prefs.model);
    }

    // Update preferences dialog fields
    document.getElementById("pref-api-url").value = prefs.api_url || "";
    document.getElementById("pref-font-family").value = prefs.font_family || "";
    document.getElementById("pref-content-font-family").value =
      prefs.content_font_family || "";
    document.getElementById("pref-font-size").value = prefs.font_size || 12;
    document.getElementById("pref-theme").value = theme;
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
      case "new-pack-tab": {
        await this._showPackTabDialog();
        break;
      }
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
      case "copy-markdown":
        await this._copyTextAsMarkdown();
        break;
      case "paste":
        this._pasteText();
        break;
      case "find":
        this._showFindDialog();
        break;

      // Chat menu
      case "submit":
        this.submit_current_tab();
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
                "Nothing to truncate: this task has only one Q\u2060/\u2060A pair.",
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
      case "recompute-title":
        if (this.currentTabId) {
          if (this.tabManager.isTabStreaming(this.currentTabId)) {
            await this._showAlert(
              "Cannot recompute the title while streaming.",
            );
            break;
          }
          try {
            const result = await this.api.recompute_title(this.currentTabId);
            if (result.success && result.started) {
              this._showToast("Recomputing title…", {
                type: "success",
                duration: 2000,
              });
            } else if (result.success && result.reason === "empty") {
              await this._showAlert(
                "Add a question and answer before recomputing the title.",
              );
            } else {
              await this._showAlert(
                `Recompute title failed: ${
                  result.error || result.reason || "unknown error"
                }`,
              );
            }
          } catch (e) {
            await this._showAlert(`Recompute title error: ${e.message || e}`);
          }
        }
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
      case "open-surface":
        await this._openAppSurface();
        break;
      case "copy-conversation-id": {
        const convId = this.tabManager.getConversationId(
          this.tabManager.activeTabId,
        );
        if (convId !== null) {
          await navigator.clipboard.writeText(String(convId));
          this._showToast(`Task ID #${convId} copied`, {
            type: "success",
            duration: 2000,
          });
        }
        break;
      }

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

      // Desktop menus should not steal an active chat/text selection.
      btn.addEventListener("mousedown", (e) => e.preventDefault());

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
    // New Tab button: opens the Local/Pack dropdown, wired in _bindEvents via
    // setupNewTabDropdown, so there is nothing to bind here.

    document
      .getElementById("toolbar-tab-back")
      ?.addEventListener("click", () => this.tabManager.goBack());
    document
      .getElementById("toolbar-tab-forward")
      ?.addEventListener("click", () => this.tabManager.goForward());

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
   * Copy the current chat selection while preserving Markdown formatting.
   */
  async _copyTextAsMarkdown() {
    const copied = await this.chatDisplay.copySelectionAsMarkdown();
    const status = document.getElementById("status-text");
    status.textContent = copied
      ? "Copied selection as Markdown"
      : "Select chat text to copy as Markdown";
    setTimeout(() => {
      if (
        status.textContent === "Copied selection as Markdown" ||
        status.textContent === "Select chat text to copy as Markdown"
      ) {
        status.textContent = "Ready";
      }
    }, 2000);
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
    document.getElementById("history-more-menu").removeAttribute("open");
    document.getElementById("history-search").value = "";
    this._historyFilter = "all";
    this._historyFolder = null;
    this._historyConversations = [];
    this._historySelectedIds = new Set();
    document.querySelectorAll("[data-history-filter]").forEach((button) => {
      button.classList.toggle("active", button.dataset.historyFilter === "all");
    });
    document.getElementById("history-search").focus();
    await this._loadHistory("");
  }

  /**
   * Load history entries from Python, optionally filtered by search term
   */
  async _loadHistory(searchTerm = "") {
    const list = document.getElementById("history-list");
    list.innerHTML = '<div class="history-empty">Loading...</div>';
    this._updateHistoryScrollbar();

    const archived = this._historyFilter === "archived";
    const result = await this.api.get_history(
      searchTerm,
      this._historyFolder,
      archived,
    );
    if (!result.success) {
      list.innerHTML =
        '<div class="history-empty">Failed to load history.</div>';
      this._updateHistoryScrollbar();
      return;
    }

    let conversations = result.conversations || [];
    if (this._historyFilter === "pinned") {
      conversations = conversations.filter((conv) => conv.pinned);
    }
    this._historyConversations = conversations;
    this._historySelectedIds = new Set(
      [...(this._historySelectedIds || [])].filter((id) =>
        conversations.some((conv) => conv.id === id),
      ),
    );
    this._renderHistoryFolders(result.folders || []);
    document.getElementById("history-count").textContent =
      conversations.length === 0
        ? ""
        : `${conversations.length} task${
            conversations.length !== 1 ? "s" : ""
          }`;

    if (conversations.length === 0) {
      list.innerHTML = '<div class="history-empty">No tasks found.</div>';
      this._updateHistorySelection();
      this._updateHistoryScrollbar();
      return;
    }

    list.innerHTML = "";
    for (const conv of conversations) {
      const row = document.createElement("div");
      row.className =
        conv.tab_type === "pack" ? "history-row pack" : "history-row";
      row.dataset.convId = conv.id;

      const closedDate = this._formatHistoryDate(conv.closed_date);
      const createdDate = this._formatHistoryDate(conv.created_date);
      const check = document.createElement("input");
      check.type = "checkbox";
      check.className = "history-row-check";
      check.checked = this._historySelectedIds.has(conv.id);
      check.setAttribute("aria-label", `Select ${conv.title}`);
      check.addEventListener("click", (event) => event.stopPropagation());
      check.addEventListener("change", () => {
        if (check.checked) this._historySelectedIds.add(conv.id);
        else this._historySelectedIds.delete(conv.id);
        this._updateHistorySelection();
      });

      const pinEl = document.createElement("span");
      pinEl.className = "history-row-pin";
      pinEl.textContent = conv.pinned ? "★" : "";

      const mainEl = document.createElement("div");
      mainEl.className = "history-row-main";
      const titleEl = document.createElement("div");
      titleEl.className = "history-row-title";
      titleEl.textContent = conv.title;
      titleEl.title = conv.title;
      const snippetEl = document.createElement("div");
      snippetEl.className = "history-row-snippet";
      snippetEl.textContent = conv.preview || "No text preview available";
      const badgesEl = document.createElement("div");
      badgesEl.className = "history-row-badges";
      [conv.folder, ...(conv.tags || [])].filter(Boolean).forEach((label) => {
        const badge = document.createElement("span");
        badge.className = "history-badge";
        badge.textContent = label;
        badgesEl.appendChild(badge);
      });
      mainEl.append(titleEl, snippetEl, badgesEl);

      const dateEl = document.createElement("div");
      dateEl.className = "history-row-date";
      dateEl.textContent = closedDate;
      dateEl.title = `Created: ${createdDate}\nClosed: ${closedDate}`;

      row.append(check, pinEl, mainEl, dateEl);
      row.addEventListener("click", () => this._selectHistoryRow(row, conv));
      row.addEventListener("dblclick", () =>
        this._reviveSelectedConversation(),
      );
      list.appendChild(row);
    }
    this._updateHistorySelection();
    this._updateHistoryScrollbar();
  }

  _updateHistoryScrollbar() {
    const list = document.getElementById("history-list");
    const scrollbar = document.getElementById("history-scrollbar");
    const thumb = document.getElementById("history-scrollbar-thumb");
    const maxScroll = list.scrollHeight - list.clientHeight;
    scrollbar.classList.toggle("hidden", maxScroll <= 0);
    if (maxScroll <= 0) return;

    const thumbHeight = Math.max(
      32,
      (scrollbar.clientHeight * list.clientHeight) / list.scrollHeight,
    );
    const thumbTop =
      (list.scrollTop / maxScroll) * (scrollbar.clientHeight - thumbHeight);
    thumb.style.height = `${thumbHeight}px`;
    thumb.style.transform = `translateY(${thumbTop}px)`;
  }

  _renderHistoryFolders(folders) {
    const container = document.getElementById("history-folders");
    container.innerHTML = "";
    for (const folder of folders) {
      const button = document.createElement("button");
      button.className = "history-filter";
      button.textContent = `▱ ${folder}`;
      button.title = folder;
      button.classList.toggle("active", this._historyFolder === folder);
      button.addEventListener("click", () => {
        this._historyFolder = folder;
        this._historyFilter = "all";
        document
          .querySelectorAll(".history-filter")
          .forEach((item) => item.classList.toggle("active", item === button));
        this._reloadHistory();
      });
      container.appendChild(button);
    }
  }

  _setHistoryFilter(button) {
    this._historyFilter = button.dataset.historyFilter;
    this._historyFolder = null;
    document
      .querySelectorAll(".history-filter")
      .forEach((item) => item.classList.toggle("active", item === button));
    this._reloadHistory();
  }

  _reloadHistory() {
    return this._loadHistory(
      document.getElementById("history-search").value.trim(),
    );
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

  _selectHistoryRow(row, conversation = null) {
    document
      .querySelectorAll("#history-list .history-row.selected")
      .forEach((r) => r.classList.remove("selected"));
    row.classList.add("selected");
    const conv =
      conversation ||
      this._historyConversations.find(
        (item) => item.id === parseInt(row.dataset.convId, 10),
      );
    if (conv) {
      this._renderHistoryPreview(conv);
      document.getElementById("history-pin-btn").textContent = conv.pinned
        ? "Unpin"
        : "Pin";
      document.getElementById("history-archive-btn").textContent = conv.archived
        ? "Restore"
        : "Archive";
    }
  }

  _renderHistoryPreview(conv) {
    const preview = document.getElementById("history-preview");
    preview.innerHTML = "";

    const eyebrow = document.createElement("div");
    eyebrow.className = "history-preview-eyebrow";
    eyebrow.textContent = conv.pinned ? "★ Pinned task" : "Task";

    const title = document.createElement("h4");
    title.className = "history-preview-title";
    title.textContent = conv.title;

    const meta = document.createElement("div");
    meta.className = "history-preview-meta";
    const dates = [
      ["Created", this._formatHistoryDate(conv.created_date)],
      ["Last used", this._formatHistoryDate(conv.closed_date)],
    ];
    for (const [label, value] of dates) {
      const item = document.createElement("div");
      const term = document.createElement("span");
      term.textContent = label;
      const date = document.createElement("time");
      date.textContent = value;
      item.append(term, date);
      meta.appendChild(item);
    }

    const labels = document.createElement("div");
    labels.className = "history-preview-labels";
    [conv.folder, ...(conv.tags || [])].filter(Boolean).forEach((label) => {
      const badge = document.createElement("span");
      badge.className = "history-badge";
      badge.textContent = label;
      labels.appendChild(badge);
    });

    const divider = document.createElement("div");
    divider.className = "history-preview-divider";
    divider.textContent = "Preview";

    const text = document.createElement("div");
    text.className = "history-preview-text";
    const markdown =
      conv.preview_markdown ||
      conv.preview ||
      "No text preview is available for this task.";
    text.innerHTML = DOMPurify.sanitize(marked.parse(markdown), {
      FORBID_ATTR: ["href"],
    });

    preview.append(eyebrow, title, meta);
    if (labels.childElementCount) preview.appendChild(labels);
    preview.append(divider, text);
  }

  _selectedHistoryIds() {
    if (this._historySelectedIds?.size) return [...this._historySelectedIds];
    const primary = document.querySelector(
      "#history-list .history-row.selected",
    );
    return primary ? [parseInt(primary.dataset.convId, 10)] : [];
  }

  _primaryHistoryConversation() {
    const primary = document.querySelector(
      "#history-list .history-row.selected",
    );
    if (!primary) return null;
    const id = parseInt(primary.dataset.convId, 10);
    return this._historyConversations.find((conv) => conv.id === id) || null;
  }

  _selectAllHistory(checked) {
    this._historySelectedIds = new Set(
      checked ? this._historyConversations.map((conv) => conv.id) : [],
    );
    document.querySelectorAll(".history-row-check").forEach((input) => {
      input.checked = checked;
    });
    this._updateHistorySelection();
  }

  _updateHistorySelection() {
    const count = this._historySelectedIds?.size || 0;
    document.getElementById("history-selection-count").textContent = count
      ? `${count} selected`
      : "";
    const selectAll = document.getElementById("history-select-all");
    selectAll.checked =
      count > 0 && count === this._historyConversations.length;
    selectAll.indeterminate =
      count > 0 && count < this._historyConversations.length;
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
        `Failed to open task: ${result.error || "unknown error"}`,
      );
    }
  }

  async _updateSelectedHistory(changes) {
    const ids = this._selectedHistoryIds();
    if (!ids.length) return;
    const results = await Promise.all(
      ids.map((id) => this.api.update_history_entry(id, { ...changes })),
    );
    if (results.some((result) => !result.success)) {
      await this._showAlert("One or more tasks could not be updated.");
    }
    await this._reloadHistory();
  }

  async _renameHistoryEntry() {
    const conv = this._primaryHistoryConversation();
    if (!conv) return;
    const title = await this._showPrompt(
      "Task name",
      conv.title,
      "Rename task",
    );
    if (title && title.trim()) {
      await this.api.update_history_entry(conv.id, { title: title.trim() });
      await this._reloadHistory();
    }
  }

  async _toggleHistoryMetadata(field) {
    const conv = this._primaryHistoryConversation();
    if (!conv) return;
    await this._updateSelectedHistory({ [field]: !conv[field] });
  }

  async _editHistoryFolder() {
    const conv = this._primaryHistoryConversation();
    if (!conv) return;
    const folder = await this._showPrompt(
      "Folder name (leave blank to remove from a folder)",
      conv.folder || "",
      "Move task",
    );
    if (folder !== null)
      await this._updateSelectedHistory({ folder: folder.trim() });
  }

  async _editHistoryTags() {
    const conv = this._primaryHistoryConversation();
    if (!conv) return;
    const tags = await this._showPrompt(
      "Comma-separated tags",
      (conv.tags || []).join(", "),
      "Edit tags",
    );
    if (tags !== null) {
      await this._updateSelectedHistory({
        tags: tags
          .split(",")
          .map((tag) => tag.trim())
          .filter(Boolean),
      });
    }
  }

  async _backupHistory() {
    document.getElementById("history-more-menu").removeAttribute("open");
    const result = await this.api.export_history_backup(
      this._selectedHistoryIds(),
    );
    if (result.success) {
      await this._showAlert(
        `Backed up ${result.count} task${result.count === 1 ? "" : "s"}.`,
      );
    } else if (!result.cancelled) {
      await this._showAlert(
        `Backup failed: ${result.error || "unknown error"}`,
      );
    }
  }

  async _importHistory() {
    document.getElementById("history-more-menu").removeAttribute("open");
    const result = await this.api.import_history_backup();
    if (result.success) {
      await this._reloadHistory();
      await this._showAlert(
        `Imported ${result.imported} task${result.imported === 1 ? "" : "s"}.`,
      );
    } else if (!result.cancelled) {
      await this._showAlert(
        `Import failed: ${result.error || "unknown error"}`,
      );
    }
  }

  async _deleteHistoryEntry() {
    const ids = this._selectedHistoryIds();
    if (!ids.length) return;

    const confirmed = await this._showConfirm(
      `Permanently delete ${ids.length} task${
        ids.length === 1 ? "" : "s"
      }? This cannot be undone.`,
      "Delete tasks",
    );
    if (!confirmed) return;

    const result = await this.api.delete_history_entries(ids);
    if (result.success) {
      this._historySelectedIds.clear();
      document.getElementById("history-preview").innerHTML =
        '<div class="history-preview-empty">Select a task to see its preview.</div>';
      await this._reloadHistory();
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
   * Show about dialog
   */
  async _showAboutDialog() {
    const aboutText =
      "Version 0.11\n\n" +
      "Alpaca Assist is a desktop AI assistant that can act, not just " +
      "answer — it calls tools, runs shell commands, and reads and edits " +
      "files as it works through a task, with support for external tools " +
      "and services through MCP (Model Context Protocol).\n\n" +
      "• Anthropic Claude, Fireworks AI, and local models, side by side\n" +
      "• Agentic tool calling: files, shell commands, MCP servers\n" +
      "• Agent Skills for extending what it can do\n" +
      "• Local and offloaded tasks with searchable history";
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
    this._updateWorkspaceHeader(null);
    this._tabSwitchSeq = (this._tabSwitchSeq || 0) + 1;
    const mySeq = this._tabSwitchSeq;
    // Surface panels are only hidden, never rebuilt — they live in the dock
    // outside #chat-container precisely so a live session survives this.
    window.SurfaceDock?.setActiveTab(tabId);
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

    // Walk active path, pairing user/assistant nodes and rendering them.
    let answerIndex = 0;
    let pendingUserId = null;
    // When the previous turn stopped producing output, so each question's gap
    // measures the user's own pause rather than including the model's work.
    let previousEnd = null;

    for (const id of activePath) {
      const node = graph.nodes[id];
      if (!node) continue;

      if (node.role === "user") {
        pendingUserId = id;
      } else if (node.role === "assistant" && pendingUserId !== null) {
        const userNode = graph.nodes[pendingUserId];

        // Render question
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
          timestamp: userNode.created_at,
          previousEnd,
        });

        // Registered before rendering so the header picks it up the moment
        // the answer buffer is created.
        if (node.timing) {
          this.chatDisplay.setAnswerTiming(answerIndex, node.timing);
        }
        previousEnd =
          node.timing?.completed_at ??
          window.TimeFormat.toEpoch(node.created_at);

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
          } else if (content.components && content.components.length > 0) {
            this._renderFullAnswer(content, answerIndex);
          }
        }

        answerIndex++;
        pendingUserId = null;
      }
    }

    // Carry the last turn's end forward so the first question typed after
    // reopening an old conversation gets its divider immediately, rather than
    // only once the conversation is reloaded from disk again.
    this._lastTurnEnd = previousEnd;
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
          this.chatDisplay.registerToolResult(
            answerIndex,
            component.id,
            component.content,
          );
          this.chatDisplay.injectFoldWithId(
            answerIndex,
            component.content,
            "result",
            foldId,
            component.duration_ms ?? null,
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
    const questionTimes = chatState.question_times || [];
    const turnTimings = chatState.turn_timings || [];
    let previousEnd = null;

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
      this.chatDisplay.addQuestion(questions[i], questionImages, {
        timestamp: questionTimes[i],
        previousEnd,
      });
      if (turnTimings[i]) {
        this.chatDisplay.setAnswerTiming(i, turnTimings[i]);
      }
      previousEnd =
        turnTimings[i]?.completed_at ??
        window.TimeFormat.toEpoch(questionTimes[i]);

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

    this._lastTurnEnd = previousEnd;
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

    // Send to Python (fast call, just starts a thread)
    try {
      await this.api.send_message(this.currentTabId, text, base64s);
      this.chatDisplay.addQuestion(text, dataUris, {
        timestamp: Date.now() / 1000,
        previousEnd: this._lastTurnEnd,
      });
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
    // Receiving any live notification proves a Pack tab's connection is up.
    this.tabManager.setTabOffline(tabId, false);

    // Only update UI if this is the current tab
    if (tabId === this.currentTabId) {
      this.chatDisplay.setStreaming(true);
      this.chatDisplay.startAnswerTimer(answerIndex);
      this.inputArea.setStreaming(true);
      this._startStatusTicker();
      this._updateStatusBar();
    }
  }

  /**
   * Called when streaming ends
   */
  onStreamingEnd(tabId, answerIndex) {
    // Always update the tab's streaming state, even if not the current tab
    this.tabManager.setTabStreaming(tabId, false);

    // The turn stopwatch is deliberately NOT stopped here — this fires once
    // per LLM invocation, so a tool loop hits it several times mid-turn.
    // onTurnTiming is the once-per-turn signal that ends it.

    // Only update UI if this is the current tab
    if (tabId === this.currentTabId) {
      this.chatDisplay.setStreaming(false);
      this.inputArea.setStreaming(false);
      this._updateStatusBar();
    }
  }

  /**
   * Called by Python once a whole turn (including its tool loop) has finished,
   * with the wall/model/tool breakdown for it.
   */
  onTurnTiming(tabId, answerIndex, timing) {
    if (tabId !== this.currentTabId) return;
    // Anchor for the next question's gap, so a divider appears live during a
    // session and not only after the conversation is reloaded from disk.
    this._lastTurnEnd = timing?.completed_at ?? Date.now() / 1000;
    this.chatDisplay.stopAnswerTimer();
    this._stopStatusTicker();
    this.chatDisplay.setAnswerTiming(answerIndex, timing);
    this._updateStatusBar();
  }

  /**
   * Drive the status bar's live elapsed counter while a turn is in flight.
   *
   * Kept separate from _scheduleStatusUpdate: that one throttles refreshes
   * caused by arriving content, and a turn that is waiting on a slow tool
   * produces no content at all — exactly when the user most wants to see the
   * clock moving.
   */
  _startStatusTicker() {
    if (this._statusTicker) return;
    this._statusTicker = setInterval(() => this._updateStatusBar(), 1000);
  }

  _stopStatusTicker() {
    if (this._statusTicker) {
      clearInterval(this._statusTicker);
      this._statusTicker = null;
    }
  }

  /**
   * Called when error occurs
   */
  onError(tabId, error) {
    // Always clear streaming state, even for background tabs
    console.error(`[APP] Error on tab ${tabId}:`, error.message);
    this._setStreamingComplete(tabId);

    // A Pack tab's connection errors (unreachable host, daemon spawn
    // failure, lost SSH link) surface through this same generic push —
    // badge it as offline rather than adding a dedicated notification.
    const tab = this.tabManager.tabs.get(tabId);
    if (tab && tab.isPack) {
      this.tabManager.setTabOffline(tabId, true);
      if (tabId === this.currentTabId) {
        this._updateStatusBar();
      }
    }

    // Show a toast so errors from background tabs are never silently dropped.
    const tabHint =
      tabId !== this.currentTabId && tab ? `From task: ${tab.title}` : null;
    const message =
      tab?.isPack && /pack tab offline|ssh|daemon/i.test(error.message)
        ? "Remote work paused. Reopen this offload to reconnect."
        : error.message;
    this._showToast(message, { type: "error", tabHint });
  }

  /**
   * Called by Python when a Pack tab (re)connects and its remote state may
   * have changed while nobody was attached (e.g. content that streamed
   * while the app was closed). Reuses the same refetch path background
   * tabs already rely on when switched into.
   */
  async onPackStateSynced(tabId) {
    this.tabManager.setTabOffline(tabId, false);
    if (tabId !== this.currentTabId) return;
    await this._reloadConversationDisplay();
    this._updateStatusBar();
  }

  /**
   * Called by Python when a Pack tab reconnected and found its remote
   * daemon had no persisted session (host restarted, session directory
   * deleted, etc.) even though we still hold a local copy of the
   * conversation. Ask whether to recreate the remote session from that
   * local copy, or accept a fresh empty one.
   */
  async onPackSessionLost(tabId) {
    const tab = this.tabManager.tabs.get(tabId);
    const label = tab ? tab.title : "This offloaded task";
    const recreate = await this._showMessageDialog(
      `${label} was interrupted. Your work is safe here.\n\n` +
        `Resume the task from your local copy, or start over?`,
      {
        title: "Offload interrupted",
        okText: "Resume task",
        cancelText: "Start over",
      },
    );
    await this.api.resolve_pack_session_lost(tabId, recreate);
    if (tabId === this.currentTabId) {
      await this._reloadConversationDisplay();
    }
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
      // Python builds result fold_ids as `fold-result-{answer_index}-{tool_id}`
      // (see core/chat_tab_tools.py) — strip the known prefix to recover the
      // real tool-call id for alpaca://image/<id> resolution, rather than
      // adding a new field across the local/Pack wire protocol for it.
      if (foldData.type === "result") {
        const prefix = `fold-result-${foldData.answer_index}-`;
        if (foldData.fold_id.startsWith(prefix)) {
          const toolCallId = foldData.fold_id.slice(prefix.length);
          this.chatDisplay.registerToolResult(
            foldData.answer_index,
            toolCallId,
            foldData.body,
          );
        }
      }
      this.chatDisplay.injectFoldWithId(
        foldData.answer_index,
        foldData.body,
        foldData.type,
        foldData.fold_id,
        foldData.duration_ms ?? null,
      );
    }
  }

  // =======================================================================
  // Dialogs
  // =======================================================================

  async _showPackTabDialog() {
    const hostChoices = document.getElementById("pack-host-choices");
    const projectChoices = document.getElementById("pack-project-choices");
    const customChoice = document.getElementById("pack-custom-host-choice");
    const customInput = document.getElementById("pack-custom-host");
    const createBtn = document.getElementById("pack-tab-create");

    hostChoices.textContent = "Loading hosts…";
    projectChoices.textContent = "Loading projects…";
    customChoice.style.display = "none";
    customInput.value = "";
    customInput.removeAttribute("aria-invalid");
    createBtn.disabled = true;
    this._showDialog("pack-tab-dialog");

    let hostsResult;
    let projectsResult;
    try {
      [hostsResult, projectsResult] = await Promise.all([
        this.api.get_pack_hosts(),
        this.api.get_projects(),
      ]);
    } catch (error) {
      console.error("Failed to load offload choices:", error);
    }

    const hosts = hostsResult?.success ? hostsResult.hosts || [] : [];
    const projects = projectsResult?.success
      ? projectsResult.projects || []
      : [];
    hostChoices.textContent = "";
    projectChoices.textContent = "";
    customChoice.style.display = "";

    const addChoice = (container, name, value, title, detail, checked) => {
      const card = document.createElement("label");
      card.className = "pack-choice-card";
      card.title = detail;

      const radio = document.createElement("input");
      radio.type = "radio";
      radio.name = name;
      radio.value = value;
      radio.checked = checked;

      const content = document.createElement("span");
      content.className = "pack-choice-content";
      const heading = document.createElement("strong");
      heading.textContent = title;
      const description = document.createElement("span");
      description.textContent = detail;
      content.append(heading, description);
      card.append(radio, content);
      container.appendChild(card);
    };

    hosts.forEach((host, index) => {
      addChoice(
        hostChoices,
        "pack-host",
        host.hostname,
        host.display_name || host.hostname,
        host.display_name && host.display_name !== host.hostname
          ? host.hostname
          : "Configured remote host",
        index === 0,
      );
    });
    const customRadio = customChoice.querySelector('input[type="radio"]');
    customRadio.checked = hosts.length === 0;

    addChoice(
      projectChoices,
      "pack-project",
      "",
      "No project",
      "Run without a managed project workspace",
      true,
    );
    projects.forEach((project) => {
      addChoice(
        projectChoices,
        "pack-project",
        project.name,
        project.name,
        `${project.repo_url}${project.branch ? ` · ${project.branch}` : ""}`,
        false,
      );
    });

    createBtn.disabled = false;
    if (
      document.getElementById("pack-tab-dialog").classList.contains("active")
    ) {
      (hosts.length ? hostChoices.querySelector("input") : customInput).focus();
    }
  }

  async _createPackTabFromDialog() {
    const selectedHost = document.querySelector(
      'input[name="pack-host"]:checked',
    );
    const customInput = document.getElementById("pack-custom-host");
    const host =
      selectedHost?.value === "__custom__"
        ? customInput.value.trim()
        : selectedHost?.value?.trim();
    if (!host) {
      customInput.setAttribute("aria-invalid", "true");
      customInput.focus();
      return;
    }

    const project =
      document.querySelector('input[name="pack-project"]:checked')?.value || "";
    const createBtn = document.getElementById("pack-tab-create");
    createBtn.disabled = true;
    createBtn.textContent = "Offloading…";
    try {
      const tabId = await this.tabManager.createPackTab(host, project);
      if (tabId) {
        this._closeDialogs();
      } else {
        this._showToast("Could not offload the task.", { type: "error" });
      }
    } catch (error) {
      console.error("Failed to offload task:", error);
      this._showToast(error?.message || "Could not offload the task.", {
        type: "error",
      });
    } finally {
      createBtn.disabled = false;
      createBtn.textContent = "Offload Task";
    }
  }

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
   *   withInput?: boolean, inputDefault?: string,
   *   selectOptions?: {value: string, label: string}[],
   *   selectCustomLabel?: string}} opts
   *   cancelText: null (default) shows only an OK button (alert-style).
   *   Pass a label to also show a Cancel button (confirm-style).
   *   withInput shows a text field (prompt-style); the resolved value is
   *   then the input's string (or null if cancelled) instead of a boolean.
   *   selectOptions shows a dropdown of quick-pick {value, label} pairs
   *   instead of the text field, plus a trailing selectCustomLabel entry
   *   that reveals the text field for a one-off value; the resolved
   *   value is whichever of the two was actually chosen (a value string,
   *   never a label). Ignored when empty/omitted.
   * @returns {Promise<boolean|string|null>}
   */
  _showMessageDialog(message, opts = {}) {
    const {
      title = "Alpaca Assist",
      okText = "OK",
      cancelText = null,
      withInput = false,
      inputDefault = "",
      selectOptions = [],
      selectCustomLabel = "Custom…",
    } = opts;
    const hasSelect = selectOptions.length > 0;
    const resolvesToText = withInput || hasSelect;
    return new Promise((resolve) => {
      document.getElementById("message-dialog-title").textContent = title;
      document.getElementById("message-dialog-text").textContent = message;

      const okBtn = document.getElementById("message-dialog-ok-btn");
      const cancelBtn = document.getElementById("message-dialog-cancel-btn");
      const input = document.getElementById("message-dialog-input");
      const select = document.getElementById("message-dialog-select");
      okBtn.textContent = okText;
      cancelBtn.style.display = cancelText !== null ? "" : "none";
      cancelBtn.textContent = cancelText || "Cancel";

      select.classList.toggle("active", hasSelect);
      select.innerHTML = "";
      if (hasSelect) {
        for (const { value, label } of selectOptions) {
          const option = document.createElement("option");
          option.value = value;
          option.textContent = label;
          select.appendChild(option);
        }
        if (selectCustomLabel !== null) {
          const customOption = document.createElement("option");
          customOption.value = "__custom__";
          customOption.textContent = selectCustomLabel;
          select.appendChild(customOption);
        }
        select.value = selectOptions[0].value;
      }

      const showInputNow = withInput && !hasSelect;
      input.classList.toggle(
        "active",
        showInputNow || (hasSelect && select.value === "__custom__"),
      );
      input.value = resolvesToText ? inputDefault : "";

      const overlay = document.getElementById("message-dialog-overlay");

      const onSelectChange = () => {
        const isCustom = select.value === "__custom__";
        input.classList.toggle("active", isCustom);
        if (isCustom) input.focus();
      };
      if (hasSelect) select.addEventListener("change", onSelectChange);

      const cleanup = (confirmed) => {
        okBtn.removeEventListener("click", onOk);
        cancelBtn.removeEventListener("click", onCancel);
        input.removeEventListener("keydown", onInputKeydown);
        if (hasSelect) select.removeEventListener("change", onSelectChange);
        overlay.classList.remove("active");
        this._messageDialogDismiss = null;
        if (!resolvesToText) {
          resolve(confirmed);
          return;
        }
        if (!confirmed) {
          resolve(null);
          return;
        }
        resolve(
          hasSelect && select.value !== "__custom__"
            ? select.value
            : input.value,
        );
      };
      const onOk = () => cleanup(true);
      const onCancel = () => cleanup(false);
      const onInputKeydown = (e) => {
        if (e.key === "Enter") onOk();
      };

      okBtn.addEventListener("click", onOk);
      cancelBtn.addEventListener("click", onCancel);
      if (resolvesToText) input.addEventListener("keydown", onInputKeydown);
      this._messageDialogDismiss = onCancel;

      overlay.classList.add("active");
      if (hasSelect) {
        select.focus();
      } else if (withInput) {
        input.focus();
      } else {
        okBtn.focus();
      }
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
      content_font_family: document.getElementById("pref-content-font-family")
        .value,
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
      if (this.currentTabId) {
        const currentValue = this.inputArea.getValue();
        this.inputArea.setValue(
          (currentValue ? currentValue + "\n" : "") + result.result,
        );
        this.inputArea.focus();
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
      this._updateConnectionBadge(null);
      this._updateWorkspaceHeader(null);
      return;
    }

    // Capture streaming state BEFORE the async call — if onStreamingEnd fires
    // during the await, reading isTabStreaming afterwards would give the wrong
    // (post-end) value and show "⚪ Idle" while streaming is still in progress.
    const isStreaming = this.tabManager
      ? this.tabManager.isTabStreaming(this.currentTabId)
      : false;

    const tabId = this.currentTabId;
    const result = await this.api.get_status_info(tabId);
    if (tabId !== this.currentTabId) return;
    if (!result || !result.success) {
      this._updateWorkspaceHeader(null);
      return;
    }

    // Connection badge: local vs pack, host, and connectivity state.
    this._updateConnectionBadge(result);
    this._updateWorkspaceHeader(result);
    if (result.is_pack) {
      this.tabManager?.setPackWorkspaceStatus(tabId, result);
    }

    // Left: conversation size + token info
    const leftParts = [
      `Task: ${result.char_count.toLocaleString()} chars, ${result.line_count.toLocaleString()} lines`,
    ];
    if (result.session_output_tokens > 0 || result.session_input_tokens > 0) {
      // "in" already includes cached tokens (both cache writes and cache
      // reads) — surface how much of it was cache-discounted rather than
      // billed at full price, so the raw total doesn't read as sticker
      // shock. cache_read is typically ~10% of input price; cache_creation
      // is a smaller portion, usually a premium over plain input.
      let tok = `Session: in:${result.session_input_tokens.toLocaleString()}`;
      if (result.session_cached_input_tokens > 0) {
        tok += ` (${result.session_cached_input_tokens.toLocaleString()} cached)`;
      }
      tok += ` out:${result.session_output_tokens.toLocaleString()} tokens`;
      if (result.latency_ms) {
        tok += `, ${(result.latency_ms / 1000).toFixed(1)}s`;
      }
      leftParts.push(tok);
    } else {
      leftParts.push(`~${result.token_estimate.toLocaleString()} tokens (est)`);
    }
    statusText.textContent = leftParts.join(", ");
    statusText.title = "Task and session metrics";

    // Right: streaming indicator + skills. While a turn is in flight the
    // indicator counts up, so a long wait on a slow tool reads as progress
    // rather than as a hang.
    const elapsed = this.chatDisplay?.liveTurnElapsedMs?.();
    const workingLabel = result.is_pack ? "Working remotely" : "Streaming";
    const rightParts = [
      isStreaming
        ? elapsed !== null && elapsed !== undefined
          ? `🟢 ${workingLabel} ${window.TimeFormat.formatStopwatch(elapsed)}`
          : `🟢 ${workingLabel}`
        : "⚪ Idle",
    ];
    if (result.skill_count > 0) {
      rightParts.push(`📚 ${result.skill_count} skills`);
    }
    tokenCount.textContent = rightParts.join("  ");
  }

  /**
   * Present a Pack tab as an offloaded task. Host, daemon, branch and sync
   * details are deliberately omitted: the useful promise is that work is
   * happening elsewhere and this machine remains available.
   */
  _updateWorkspaceHeader(info) {
    const header = document.getElementById("workspace-header");
    if (!header) return;
    if (!info?.is_pack) {
      header.classList.add("hidden");
      return;
    }

    const workspace = info.workspace_status || {};
    const project = document.getElementById("workspace-header-project");
    const location = document.getElementById("workspace-header-location");
    const branch = document.getElementById("workspace-header-branch");
    const changes = document.getElementById("workspace-header-changes");
    const sync = document.getElementById("workspace-header-sync");

    project.textContent = info.project || "Remote task";
    location.textContent = info.connected
      ? "Working remotely · your laptop is untouched"
      : "Remote work is paused";
    location.title = "";
    branch.textContent = "";
    branch.className = "workspace-fact hidden";

    changes.className = "workspace-fact workspace-fact--action";
    sync.className = "workspace-fact";
    changes.title = "";
    if (!info.connected) {
      changes.textContent = "Reconnect to continue";
      changes.classList.add("workspace-fact--error");
    } else if (info.project_setup_state === "setting_up") {
      changes.textContent = "Preparing task…";
    } else if (info.project_setup_error) {
      changes.textContent = "Needs attention";
      changes.title = info.project_setup_error;
      changes.classList.add("workspace-fact--error");
    } else if (workspace.dirty > 0) {
      changes.textContent = "Changes ready to review";
      changes.classList.add("workspace-fact--dirty");
    } else {
      changes.textContent = "Ready";
      changes.classList.add("workspace-fact--clean");
    }
    sync.textContent = "";
    sync.className = "workspace-fact hidden";

    header.classList.remove("hidden");
  }

  /**
   * Open the workspace changes panel — `git status` as a file list, with
   * the selected file's diff beside it.
   */
  async _showWorkspaceChanges() {
    this._showDialog("workspace-changes-dialog");
    await this._loadWorkspaceChanges();
  }

  /**
   * Fetch the current status/diff for the active tab's Pack workspace.
   * Also the Refresh button's handler: the workspace keeps changing
   * under a running turn, so this is deliberately not cached.
   */
  async _loadWorkspaceChanges() {
    const files = document.getElementById("workspace-changes-files");
    const diff = document.getElementById("workspace-changes-diff");
    document.getElementById("workspace-changes-summary").textContent = "";
    document.getElementById("workspace-changes-subtitle").textContent = "";
    files.innerHTML = "";
    diff.innerHTML =
      '<div class="workspace-changes-empty">Loading changes…</div>';

    if (!this.currentTabId) {
      this._renderWorkspaceChanges({ success: false, error: "No active tab" });
      return;
    }
    let result;
    try {
      result = await this.api.get_workspace_changes(this.currentTabId);
    } catch (error) {
      result = { success: false, error: error.message };
    }
    this._renderWorkspaceChanges(result);
    // The header chip only refreshes on activity, so a workspace that
    // changed while the tab sat idle would keep claiming to be clean
    // behind a panel that just proved otherwise.
    this._updateStatusBar();
  }

  /**
   * Render a get_workspace_changes result into the panel.
   *
   * @param {object} result
   */
  _renderWorkspaceChanges(result) {
    const files = document.getElementById("workspace-changes-files");
    const diff = document.getElementById("workspace-changes-diff");
    const subtitle = document.getElementById("workspace-changes-subtitle");
    const summary = document.getElementById("workspace-changes-summary");
    files.innerHTML = "";
    diff.innerHTML = "";
    this._workspaceChangeEntries = [];

    if (!result || !result.success) {
      diff.appendChild(
        this._workspaceChangesNotice(
          (result && result.error) || "Could not read workspace changes",
        ),
      );
      return;
    }

    const parts = [];
    if (result.branch) parts.push(`⎇ ${result.branch}`);
    if (result.head) parts.push(result.head);
    if (result.workspace_path) parts.push(result.workspace_path);
    subtitle.textContent = parts.join("  ·  ");
    subtitle.title = result.workspace_path || "";

    if (!result.is_git) {
      diff.appendChild(
        this._workspaceChangesNotice(
          result.exists
            ? "This workspace is not a Git repository."
            : "This workspace does not exist yet.",
        ),
      );
      return;
    }

    const entries = result.entries || [];
    this._workspaceChangeEntries = entries;
    if (entries.length === 0) {
      summary.textContent = "Working tree clean";
      diff.appendChild(
        this._workspaceChangesNotice("Nothing to show — the tree is clean."),
      );
      return;
    }

    let added = 0;
    let removed = 0;
    for (const entry of entries) {
      const counts = this._countDiffLines(entry.diff || "");
      entry._added = counts.added;
      entry._removed = counts.removed;
      added += counts.added;
      removed += counts.removed;
    }

    const summaryParts = [
      `${entries.length} file${entries.length !== 1 ? "s" : ""}`,
      `+${added}`,
      `−${removed}`,
    ];
    if (result.omitted_files > 0) {
      summaryParts.push(`${result.omitted_files} more not shown`);
    }
    if (result.truncated) summaryParts.push("diff truncated");
    summary.textContent = summaryParts.join("  ·  ");

    if (entries.length > 1) {
      files.appendChild(
        this._workspaceChangeRow(
          { path: "All changes", _all: true, _added: added, _removed: removed },
          -1,
        ),
      );
    }
    entries.forEach((entry, index) => {
      files.appendChild(this._workspaceChangeRow(entry, index));
    });

    this._selectWorkspaceChange(entries.length > 1 ? -1 : 0);
  }

  /**
   * One row in the changed-file list. index -1 is the "All changes" row.
   */
  _workspaceChangeRow(entry, index) {
    const status = this._workspaceChangeStatus(entry);
    const row = document.createElement("button");
    row.type = "button";
    row.className = "workspace-change-row";
    row.dataset.changeIndex = String(index);

    const badge = document.createElement("span");
    badge.className = `workspace-change-badge workspace-change-badge--${status.kind}`;
    badge.textContent = status.code;
    badge.title = status.label;
    row.appendChild(badge);

    const path = document.createElement("span");
    path.className = "workspace-change-path";
    path.textContent = entry.path;
    row.appendChild(path);

    const counts = document.createElement("span");
    counts.className = "workspace-change-counts";
    counts.textContent = `+${entry._added || 0} −${entry._removed || 0}`;
    row.appendChild(counts);

    row.title = entry.renamed_from
      ? `${status.label} from ${entry.renamed_from}`
      : `${status.label}: ${entry.path}`;
    row.addEventListener("click", () => this._selectWorkspaceChange(index));
    return row;
  }

  /**
   * Turn a porcelain XY status pair into something a human reads.
   * Worst-case wins: a delete that is also staged still reads "Deleted".
   */
  _workspaceChangeStatus(entry) {
    if (entry._all) return { code: "Σ", kind: "all", label: "All changes" };
    if (entry.untracked) {
      return { code: "?", kind: "untracked", label: "Untracked" };
    }
    const codes = `${entry.index || ""}${entry.worktree || ""}`;
    const staged = entry.index && entry.index !== " " ? " (staged)" : "";
    if (codes.includes("U")) {
      return { code: "U", kind: "conflict", label: "Conflicted" };
    }
    if (codes.includes("D")) {
      return { code: "D", kind: "deleted", label: `Deleted${staged}` };
    }
    if (codes.includes("R")) {
      return { code: "R", kind: "renamed", label: `Renamed${staged}` };
    }
    if (codes.includes("A")) {
      return { code: "A", kind: "added", label: `Added${staged}` };
    }
    if (codes.includes("M")) {
      return { code: "M", kind: "modified", label: `Modified${staged}` };
    }
    return { code: codes.trim() || "•", kind: "modified", label: "Changed" };
  }

  /**
   * Show one file's diff, or every file's when index is -1.
   */
  _selectWorkspaceChange(index) {
    const files = document.getElementById("workspace-changes-files");
    const diff = document.getElementById("workspace-changes-diff");
    files.querySelectorAll(".workspace-change-row").forEach((row) => {
      row.classList.toggle("active", row.dataset.changeIndex === String(index));
    });

    const entries = this._workspaceChangeEntries || [];
    const shown = index === -1 ? entries : [entries[index]].filter(Boolean);
    diff.innerHTML = "";
    diff.scrollTop = 0;
    for (const entry of shown) {
      if (shown.length > 1) {
        const heading = document.createElement("div");
        heading.className = "workspace-diff-file";
        heading.textContent = entry.path;
        diff.appendChild(heading);
      }
      if (entry.diff) {
        diff.appendChild(this._renderDiffLines(entry.diff));
      }
      if (entry.truncated) {
        diff.appendChild(
          this._workspaceChangesNotice(
            "Diff too large to show in full — open the file to see the rest.",
          ),
        );
      } else if (!entry.diff) {
        diff.appendChild(
          this._workspaceChangesNotice(
            "No textual diff (binary file, or a mode change only).",
          ),
        );
      }
    }
  }

  /**
   * Colourize a unified diff. Built from text nodes rather than markup so
   * file contents can never become DOM.
   *
   * @param {string} text
   * @returns {DocumentFragment}
   */
  _renderDiffLines(text) {
    const fragment = document.createDocumentFragment();
    for (const line of text.split("\n")) {
      // A trailing newline would otherwise render as a blank last line.
      if (line === "" && text.endsWith("\n")) continue;
      const el = document.createElement("div");
      el.className = `diff-line diff-line--${this._diffLineKind(line)}`;
      el.textContent = line;
      fragment.appendChild(el);
    }
    return fragment;
  }

  _diffLineKind(line) {
    if (line.startsWith("@@")) return "hunk";
    if (line.startsWith("+++") || line.startsWith("---")) return "meta";
    if (line.startsWith("diff ") || line.startsWith("index ")) return "meta";
    if (
      line.startsWith("new file") ||
      line.startsWith("deleted file") ||
      line.startsWith("old mode") ||
      line.startsWith("new mode") ||
      line.startsWith("similarity index") ||
      line.startsWith("rename from") ||
      line.startsWith("rename to") ||
      line.startsWith("Binary files")
    ) {
      return "meta";
    }
    if (line.startsWith("+")) return "add";
    if (line.startsWith("-")) return "del";
    return "context";
  }

  _countDiffLines(text) {
    let added = 0;
    let removed = 0;
    for (const line of text.split("\n")) {
      if (line.startsWith("+") && !line.startsWith("+++")) added++;
      else if (line.startsWith("-") && !line.startsWith("---")) removed++;
    }
    return { added, removed };
  }

  _workspaceChangesNotice(message) {
    const notice = document.createElement("div");
    notice.className = "workspace-changes-empty";
    notice.textContent = message;
    return notice;
  }

  /**
   * Render the Local/Pack connection badge in the status bar.
   *
   * @param {object|null} info - result from get_status_info, or null to reset
   */
  _updateConnectionBadge(info) {
    let badge = document.getElementById("status-connection");
    if (!badge) {
      badge = document.createElement("span");
      badge.id = "status-connection";
      badge.className = "status-connection";
      const statusBar = document.getElementById("status-bar");
      const statusText = document.getElementById("status-text");
      if (statusBar && statusText) {
        statusBar.insertBefore(badge, statusText);
      } else {
        return;
      }
    }

    badge.classList.remove(
      "status-connection--local",
      "status-connection--connected",
      "status-connection--disconnected",
    );

    if (!info) {
      badge.textContent = "";
      badge.title = "";
      badge.style.display = "none";
      return;
    }

    badge.style.display = "";

    if (info.is_pack) {
      if (info.connected) {
        badge.textContent = "Working remotely · your laptop is untouched";
        badge.title = "This task is running away from your laptop";
        badge.classList.add("status-connection--connected");
      } else {
        badge.textContent = "Remote work paused";
        badge.title = "Reconnect this offload to continue";
        badge.classList.add("status-connection--disconnected");
      }
    } else {
      badge.textContent = "Local";
      badge.title = "Running on this machine";
      badge.classList.add("status-connection--local");
    }
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
   *   tabHint  — shown as a secondary line, e.g. "from task: Landing page"
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
