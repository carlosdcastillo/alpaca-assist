/**
 * TabManager - Handles tab creation, switching, and management
 */
class TabManager {
  constructor(containerId, api) {
    this.container = document.getElementById(containerId);
    this.api = api;
    this.tabs = new Map(); // tabId -> { button, chatDisplay, inputArea, data }
    this._convIds = new Map(); // tabId -> conversation_id (permanent)
    this.activeTabId = null;

    // Scroll button references
    this.scrollLeftBtn = document.getElementById("tab-scroll-left");
    this.scrollRightBtn = document.getElementById("tab-scroll-right");
    this._scrollStep = 44; // fallback pixels per click

    this._setupScrollButtons();
    this._setupOverflowObserver();
  }

  /**
   * Create a new tab (JS calls Python)
   */
  async createTab() {
    const result = await this.api.create_tab();

    if (!result.success) {
      console.error("Failed to create tab:", result.error);
      return null;
    }

    const tabId = result.tab_id;
    const convId = result.conversation_id;
    const title = `#${convId}`;

    this.createTabUI(tabId, title);
    this._convIds.set(tabId, convId);
    return tabId;
  }

  /**
   * Create a new Pack tab — a tab whose backend runs on `host` over SSH.
   * Mirrors createTab() almost exactly; only the API call and the UI's
   * pack badge differ.
   */
  async createPackTab(host, project = "") {
    const result = project
      ? await this.api.create_pack_tab(host, "Pack Tab", project)
      : await this.api.create_pack_tab(host);

    if (!result.success) {
      console.error("Failed to create pack tab:", result.error);
      return null;
    }

    const tabId = result.tab_id;
    const convId = result.conversation_id;
    const title = `#${convId}`;

    this.createTabUI(tabId, title, true, true, result.project || null);
    this._convIds.set(tabId, convId);
    return tabId;
  }

  /**
   * Store the permanent conversation ID for a tab and update the title if
   * it is still showing the placeholder (#ID from a previous allocation).
   */
  setConversationId(tabId, convId) {
    this._convIds.set(tabId, convId);
    const tab = this.tabs.get(tabId);
    if (!tab) return;
    // Update title only when it is still the placeholder set at creation.
    const titleSpan = tab.button.querySelector(".tab-title");
    if (titleSpan && titleSpan.textContent === `#${convId}`) return; // already correct
    // For Python-initiated tabs (restore/revive), title was passed explicitly —
    // don't overwrite it.  For JS-initiated new tabs the title IS #convId already.
  }

  /**
   * Get the permanent conversation ID for a tab.
   */
  getConversationId(tabId) {
    return this._convIds.get(tabId) ?? null;
  }

  /**
   * Create just the UI for a tab (used when Python already created the tab)
   */
  createTabUI(tabId, title, autoSwitch = true, isPack = false, project = null) {
    // Create tab button in UI
    const tabButton = this._createTabButton(tabId, title, isPack, project);
    this.container.appendChild(tabButton);

    // Create tab data structure
    const tabData = {
      id: tabId,
      title: title,
      button: tabButton,
      chatDisplay: null, // Set when tab is activated
      inputArea: null,
      isStreaming: false,
      isPack: isPack,
    };

    this.tabs.set(tabId, tabData);

    // Dispatch tab created event
    document.dispatchEvent(
      new CustomEvent("tabCreated", {
        detail: { tabId, tab: tabData },
      }),
    );

    // Switch to the new tab only if autoSwitch is true
    if (autoSwitch) {
      this.switchToTab(tabId);
    }

    return tabId;
  }

  /**
   * Create tab button element
   */
  _createTabButton(tabId, title, isPack = false, project = null) {
    const button = document.createElement("div");
    button.className = isPack ? "tab pack" : "tab";
    button.dataset.tabId = tabId;

    if (project) {
      const projectSpan = document.createElement("span");
      projectSpan.className = "tab-project";
      projectSpan.textContent = project;
      projectSpan.title = `Project: ${project}`;
      button.appendChild(projectSpan);
    }

    const titleSpan = document.createElement("span");
    titleSpan.className = "tab-title";
    titleSpan.textContent = title;
    titleSpan.title = isPack ? `${title} (Pack — remote)` : title;

    const closeBtn = document.createElement("button");
    closeBtn.className = "tab-close";
    closeBtn.innerHTML = "&times;";
    closeBtn.title = "Close tab";

    // Close button click handler
    closeBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      this.closeTab(tabId);
    });

    // Tab click handler
    button.addEventListener("click", () => {
      this.switchToTab(tabId);
    });

    button.appendChild(titleSpan);
    button.appendChild(closeBtn);

    return button;
  }

  /**
   * Switch to a tab
   */
  async switchToTab(tabId) {
    if (!this.tabs.has(tabId)) {
      console.error(`Tab ${tabId} not found`);
      return;
    }

    // Deactivate current tab
    if (this.activeTabId && this.tabs.has(this.activeTabId)) {
      const currentTab = this.tabs.get(this.activeTabId);
      currentTab.button.classList.remove("active");
    }

    // Activate new tab
    const newTab = this.tabs.get(tabId);
    newTab.button.classList.add("active");
    this.activeTabId = tabId;

    // Scroll the active tab into view if it's off-screen
    if (newTab && newTab.button) {
      newTab.button.scrollIntoView({
        behavior: "smooth",
        inline: "nearest",
        block: "nearest",
      });
    }

    // Notify Python
    await this.api.switch_tab(tabId);

    // Trigger tab switch event for app to handle UI updates
    document.dispatchEvent(
      new CustomEvent("tabSwitched", {
        detail: { tabId, tab: newTab },
      }),
    );
  }

  /**
   * Close a tab
   */
  async closeTab(tabId) {
    if (!this.tabs.has(tabId)) return;

    const tab = this.tabs.get(tabId);

    // Find the left and right neighbors before removing the tab.
    // Map preserves insertion order, so iteration gives visual tab order.
    let prevTabId = null;
    let nextTabId = null;
    let found = false;
    for (const id of this.tabs.keys()) {
      if (id === tabId) {
        found = true;
      } else if (!found) {
        prevTabId = id;
      } else if (nextTabId === null) {
        nextTabId = id;
        break;
      }
    }

    // Remove from UI
    tab.button.remove();
    this.tabs.delete(tabId);
    this._convIds.delete(tabId);

    // Notify Python
    await this.api.close_tab(tabId);

    // Switch to another tab if this was active.
    // Prefer the left neighbor (like Chrome/VS Code); fall back to the right.
    if (this.activeTabId === tabId) {
      const fallbackId = prevTabId ?? nextTabId;
      if (fallbackId) {
        this.switchToTab(fallbackId);
      } else {
        this.activeTabId = null;
      }
    }

    document.dispatchEvent(
      new CustomEvent("tabClosed", {
        detail: { tabId },
      }),
    );
  }

  /**
   * Get the currently active tab
   */
  getActiveTab() {
    if (!this.activeTabId) return null;
    return this.tabs.get(this.activeTabId);
  }

  /**
   * Get the active tab ID
   */
  getActiveTabId() {
    return this.activeTabId;
  }

  /**
   * Get a tab by ID
   */
  getTab(tabId) {
    return this.tabs.get(tabId);
  }

  /**
   * Update tab title
   */
  updateTabTitle(tabId, newTitle) {
    const tab = this.tabs.get(tabId);
    if (tab) {
      tab.title = newTitle;
      const titleSpan = tab.button.querySelector(".tab-title");
      if (titleSpan) {
        titleSpan.textContent = newTitle;
        titleSpan.title = newTitle;
      }
    }
  }

  /**
   * Set streaming state for a tab
   */
  setTabStreaming(tabId, isStreaming) {
    const tab = this.tabs.get(tabId);
    if (tab) {
      tab.isStreaming = isStreaming;
      // Update visual indicator
      if (isStreaming) {
        tab.button.classList.add("streaming");
      } else {
        tab.button.classList.remove("streaming");
      }
    }
  }

  /**
   * Check if tab is streaming
   */
  isTabStreaming(tabId) {
    const tab = this.tabs.get(tabId);
    return tab ? tab.isStreaming : false;
  }

  /**
   * Set offline state for a Pack tab (mirrors setTabStreaming).
   */
  setTabOffline(tabId, isOffline) {
    const tab = this.tabs.get(tabId);
    if (tab) {
      tab.isOffline = isOffline;
      if (isOffline) {
        tab.button.classList.add("offline");
      } else {
        tab.button.classList.remove("offline");
      }
    }
  }

  /**
   * Set active tab with visual indication
   */
  setActiveTab(tabId) {
    // Deactivate current tab
    if (this.activeTabId && this.tabs.has(this.activeTabId)) {
      const currentTab = this.tabs.get(this.activeTabId);
      currentTab.button.classList.remove("active");
    }

    // Activate new tab
    if (this.tabs.has(tabId)) {
      const newTab = this.tabs.get(tabId);
      newTab.button.classList.add("active");
      this.activeTabId = tabId;
    }
  }

  /**
   * Navigate to the next tab (wraps around)
   */
  nextTab() {
    const tabIds = Array.from(this.tabs.keys());
    if (tabIds.length <= 1) return;
    const currentIdx = tabIds.indexOf(this.activeTabId);
    const nextIdx = (currentIdx + 1) % tabIds.length;
    this.switchToTab(tabIds[nextIdx]);
  }

  /**
   * Navigate to the previous tab (wraps around)
   */
  prevTab() {
    const tabIds = Array.from(this.tabs.keys());
    if (tabIds.length <= 1) return;
    const currentIdx = tabIds.indexOf(this.activeTabId);
    const prevIdx = (currentIdx - 1 + tabIds.length) % tabIds.length;
    this.switchToTab(tabIds[prevIdx]);
  }

  /**
   * Get all tab IDs
   */
  getAllTabIds() {
    return Array.from(this.tabs.keys());
  }

  /**
   * Get the number of tabs
   */
  getTabCount() {
    return this.tabs.size;
  }

  /**
   * Restore tabs from session data
   */
  async restoreTabs(tabsData) {
    for (const tabData of tabsData) {
      const title = tabData.name || tabData.title || "Chat";
      await this.createTab(title);
    }
  }

  // ── Scroll button support ──────────────────────────────────────────

  /**
   * Wire click handlers and scroll event for the scroll buttons.
   */
  _setupScrollButtons() {
    this.scrollLeftBtn.addEventListener("click", () => {
      const step = this._getScrollStep();
      this.container.scrollBy({ top: -step, behavior: "smooth" });
    });

    this.scrollRightBtn.addEventListener("click", () => {
      const step = this._getScrollStep();
      this.container.scrollBy({ top: step, behavior: "smooth" });
    });

    // Update button enabled/disabled states as the user scrolls
    this.container.addEventListener("scroll", () => {
      this._updateScrollButtonStates();
    });
  }

  /**
   * Return the number of pixels to scroll per click. Tries to use one tab
   * height; falls back to the fixed _scrollStep if no tabs exist yet.
   */
  _getScrollStep() {
    const firstTab = this.container.querySelector(".tab");
    if (firstTab) {
      // offsetHeight + gap (4px from CSS)
      return firstTab.offsetHeight + 4;
    }
    return this._scrollStep;
  }

  /**
   * Enable or disable the scroll buttons based on current scroll position.
   */
  _updateScrollButtonStates() {
    const el = this.container;
    const atStart = el.scrollTop <= 0;
    // Tolerance of 1px to handle floating-point rounding
    const atEnd = el.scrollTop + el.clientHeight >= el.scrollHeight - 1;

    this.scrollLeftBtn.disabled = atStart;
    this.scrollRightBtn.disabled = atEnd;
  }

  /**
   * Set up observers to detect when the tab container overflows (or stops
   * overflowing).  ResizeObserver catches window resizes; MutationObserver
   * catches tab additions and removals.
   */
  _setupOverflowObserver() {
    this._resizeObserver = new ResizeObserver(() => {
      this._checkOverflow();
    });
    this._resizeObserver.observe(this.container);

    this._mutationObserver = new MutationObserver(() => {
      this._checkOverflow();
    });
    this._mutationObserver.observe(this.container, { childList: true });
  }

  /**
   * Show or hide the scroll buttons based on whether the container overflows.
   */
  _checkOverflow() {
    const el = this.container;
    const isOverflowing = el.scrollHeight > el.clientHeight;

    if (isOverflowing) {
      this.scrollLeftBtn.classList.remove("hidden");
      this.scrollRightBtn.classList.remove("hidden");
      this._updateScrollButtonStates();
    } else {
      this.scrollLeftBtn.classList.add("hidden");
      this.scrollRightBtn.classList.add("hidden");
    }
  }

  /**
   * Disconnect observers.  Not strictly required (TabManager lives for the
   * lifetime of the page) but good practice.
   */
  destroy() {
    if (this._resizeObserver) this._resizeObserver.disconnect();
    if (this._mutationObserver) this._mutationObserver.disconnect();
  }
}

// Export for use in app.js
window.TabManager = TabManager;
