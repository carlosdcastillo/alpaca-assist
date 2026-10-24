/**
 * TabManager - Handles tab creation, switching, and management
 */
class TabManager {
  constructor(containerId, api) {
    this.container = document.getElementById(containerId);
    this.api = api;
    this.tabs = new Map(); // tabId -> { button, chatDisplay, inputArea, data }
    this._convIds = new Map(); // tabId -> conversation_id (permanent)
    this._tabSequence = 0;
    this.activeTabId = null;
    this.navigationHistory = [];
    this.navigationHistoryIndex = -1;
    this.layout = {};
    this._saveLayoutQueue = Promise.resolve();
    this._dismissTabMenu = (event) => {
      if (this.tabMenu && !this.tabMenu.contains(event.target))
        this._closeTabMenu();
    };
    document.addEventListener("pointerdown", this._dismissTabMenu);

    this.backBtn = document.getElementById("toolbar-tab-back");
    this.forwardBtn = document.getElementById("toolbar-tab-forward");
    this.scrollbar = document.getElementById("tabs-scrollbar");
    this.scrollbarThumb = document.getElementById("tabs-scrollbar-thumb");

    this._setupScrollbar();
    this._updateNavigationButtons();
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
    this.setConversationId(tabId, convId);
    return tabId;
  }

  /**
   * Create a new Pack tab — a tab whose backend runs on `host` over SSH.
   * Mirrors createTab() almost exactly; only the API call and the UI's
   * pack badge differ.
   */
  async createPackTab(host, project = "") {
    const result = project
      ? await this.api.create_pack_tab(host, "Offloaded task", project)
      : await this.api.create_pack_tab(host, "Offloaded task");

    if (!result.success) {
      console.error("Failed to create pack tab:", result.error);
      return null;
    }

    const tabId = result.tab_id;
    const convId = result.conversation_id;
    const title = `#${convId}`;

    this.createTabUI(
      tabId,
      title,
      true,
      true,
      result.project || null,
      result.display_name || result.host || host,
    );
    this.setConversationId(tabId, convId);
    return tabId;
  }

  /**
   * Store the permanent conversation ID for a tab and update the title if
   * it is still showing the placeholder (#ID from a previous allocation).
   */
  setConversationId(tabId, convId) {
    this._convIds.set(tabId, convId);
    this._applyTabLayout(this.tabs.get(tabId));
    this._refreshSubagents();
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

  /** Mark an existing conversation as a child without activating it. */
  setSubagentInfo(tabId, info) {
    const tab = this.tabs.get(tabId);
    if (!tab) return;
    tab.subagentInfo = { ...info };
    if (!tab.button.querySelector(".tab-subagent-meta")) {
      const meta = document.createElement("span");
      meta.className = "tab-subagent-meta";
      const status = document.createElement("span");
      status.className = "tab-subagent-status";
      const back = document.createElement("button");
      back.type = "button";
      back.className = "tab-subagent-parent";
      back.textContent = "↑ Parent";
      back.addEventListener("click", (event) => {
        event.stopPropagation();
        const parent = this._subagentParent(tab);
        if (parent) this.switchToTab(parent.id);
      });
      meta.append(status, back);
      tab.button.appendChild(meta);
      tab.button.classList.add("subagent");
    }
    this._refreshSubagents();
  }

  _subagentParent(tab) {
    if (!tab.subagentInfo) return null;
    return Array.from(this.tabs.values()).find(
      (parent) =>
        parent !== tab &&
        this._convIds.get(parent.id) ===
          tab.subagentInfo.parent_conversation_id,
    );
  }

  _refreshSubagents() {
    // Creation order keeps siblings stable even when metadata arrives backwards.
    const tabs = Array.from(this.tabs.values()).sort(
      (a, b) => a.order - b.order,
    );
    const children = new Map();
    for (const tab of tabs) {
      if (!tab.subagentInfo) continue;
      const parent = this._subagentParent(tab);
      if (parent) {
        if (!children.has(parent.id)) children.set(parent.id, []);
        children.get(parent.id).push(tab);
      }
      const { status, task_id, parent_conversation_id } = tab.subagentInfo;
      const meta = tab.button.querySelector(".tab-subagent-meta");
      meta.querySelector(".tab-subagent-status").textContent =
        `↳ Child · ${status}`;
      meta.title = `Task: ${task_id}`;
      const back = meta.querySelector(".tab-subagent-parent");
      back.disabled = !parent;
      back.title = parent
        ? `Back to parent #${parent_conversation_id}`
        : `Parent #${parent_conversation_id} is not open`;
    }
    const ordered = [];
    const visited = new Set();
    const visit = (tab) => {
      if (visited.has(tab.id)) return; // Also tolerate malformed cycles.
      visited.add(tab.id);
      ordered.push(tab);
      for (const child of children.get(tab.id) || []) visit(child);
    };
    const roots = tabs.filter((tab) => !this._subagentParent(tab));
    roots.sort((a, b) => Number(b.pinned) - Number(a.pinned));
    for (const tab of roots) visit(tab);
    for (const tab of tabs) visit(tab);
    this.container.querySelector(".tab-group-divider")?.remove();
    this.tabs.clear();
    ordered.forEach((tab, index) => {
      this.tabs.set(tab.id, tab);
      if (this.container.children[index] !== tab.button) {
        this.container.insertBefore(
          tab.button,
          this.container.children[index] || null,
        );
      }
    });
    const firstUnpinned = roots.find((tab) => !tab.pinned);
    if (roots.some((tab) => tab.pinned) && firstUnpinned) {
      const divider = document.createElement("div");
      divider.className = "tab-group-divider";
      divider.setAttribute("role", "separator");
      divider.setAttribute("aria-label", "Other tasks");
      divider.textContent = "Other tasks";
      this.container.insertBefore(divider, firstUnpinned.button);
    }
  }

  /**
   * Create just the UI for a tab (used when Python already created the tab)
   */
  createTabUI(
    tabId,
    title,
    autoSwitch = true,
    isPack = false,
    project = null,
    packHostName = null,
  ) {
    // Create tab button in UI
    const tabButton = this._createTabButton(
      tabId,
      title,
      isPack,
      project,
      packHostName,
    );
    this.container.appendChild(tabButton);

    // Create tab data structure
    const tabData = {
      id: tabId,
      // Unsaved tabs follow restored ranks, including during incremental restore.
      order: 1e9 + this._tabSequence++,
      title: title,
      button: tabButton,
      chatDisplay: null, // Set when tab is activated
      inputArea: null,
      isStreaming: false,
      isPack: isPack,
      packHostName: packHostName,
      pinned: false,
      color: "none",
    };

    this.tabs.set(tabId, tabData);
    this._refreshSubagents();

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
  _createTabButton(
    tabId,
    title,
    isPack = false,
    project = null,
    packHostName = null,
  ) {
    const button = document.createElement("div");
    button.className = isPack ? "tab pack" : "tab";
    button.dataset.tabId = tabId;
    button.tabIndex = 0;
    button.draggable = true;
    button.setAttribute(
      "aria-keyshortcuts",
      "Alt+Shift+ArrowUp Alt+Shift+ArrowDown Shift+F10",
    );
    button.setAttribute("aria-haspopup", "dialog");
    button.addEventListener("keydown", (event) => {
      if (event.target !== button) return;
      if (
        event.altKey &&
        event.shiftKey &&
        ["ArrowUp", "ArrowDown"].includes(event.key)
      ) {
        event.preventDefault();
        event.stopPropagation();
        this.moveTab(tabId, event.key === "ArrowUp" ? -1 : 1);
      } else if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        this.switchToTab(tabId);
      } else if (
        event.key === "ContextMenu" ||
        (event.shiftKey && event.key === "F10")
      ) {
        event.preventDefault();
        this._openTabMenu(tabId);
      }
    });
    button.addEventListener("contextmenu", (event) => {
      event.preventDefault();
      this._openTabMenu(tabId);
    });
    button.addEventListener("dragstart", (event) => {
      if (event.target.closest("button")) {
        event.preventDefault();
        return;
      }
      this._dragTabId = tabId;
      event.dataTransfer.setData("text/plain", tabId);
      event.dataTransfer.effectAllowed = "move";
      this._closeTabMenu(false);
    });
    button.addEventListener("dragover", (event) => {
      if (!this._canMoveTab(this._dragTabId, tabId)) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = "move";
      const after =
        event.clientY >
        button.getBoundingClientRect().top + button.offsetHeight / 2;
      button.classList.toggle("drop-before", !after);
      button.classList.toggle("drop-after", after);
      const bounds = this.container.getBoundingClientRect();
      if (event.clientY < bounds.top + 32) this.container.scrollTop -= 16;
      if (event.clientY > bounds.bottom - 32) this.container.scrollTop += 16;
    });
    button.addEventListener("dragleave", () =>
      button.classList.remove("drop-before", "drop-after"),
    );
    button.addEventListener("drop", (event) => {
      event.preventDefault();
      const after = button.classList.contains("drop-after");
      this.reorderTab(this._dragTabId, tabId, after);
      this._clearTabDrag();
    });
    button.addEventListener("dragend", () => this._clearTabDrag());

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
    titleSpan.title = isPack ? `${title} (offloaded task)` : title;

    let workspaceMeta = null;
    if (isPack) {
      workspaceMeta = document.createElement("span");
      workspaceMeta.className = "tab-workspace-meta";
      workspaceMeta.textContent = packHostName
        ? `Runs on ${packHostName}`
        : "Remote task";
    }

    const closeBtn = document.createElement("button");
    closeBtn.className = "tab-close";
    closeBtn.innerHTML = "&times;";
    closeBtn.title = "Close task";

    // Close button click handler
    closeBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      this.closeTab(tabId);
    });

    // Tab click handler
    button.addEventListener("click", () => {
      this.switchToTab(tabId);
    });

    const marker = document.createElement("span");
    marker.className = "tab-marker";
    marker.setAttribute("aria-hidden", "true");
    titleSpan.prepend(marker);
    const color = document.createElement("span");
    color.className = "tab-color";
    color.setAttribute("aria-hidden", "true");
    color.title = "Right-click tab for pin, color, and reorder options";
    button.appendChild(titleSpan);
    button.appendChild(color);
    button.appendChild(closeBtn);
    if (workspaceMeta) button.appendChild(workspaceMeta);

    return button;
  }

  /**
   * Switch to a tab
   */
  async switchToTab(tabId, fromHistory = false) {
    if (!this.tabs.has(tabId)) {
      console.error(`Tab ${tabId} not found`);
      return;
    }

    const isNewDestination = tabId !== this.activeTabId;
    if (isNewDestination && !fromHistory) {
      this.navigationHistory = this.navigationHistory.slice(
        0,
        this.navigationHistoryIndex + 1,
      );
      this.navigationHistory.push(tabId);
      this.navigationHistoryIndex = this.navigationHistory.length - 1;
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
    this._updateNavigationButtons();

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
    this._closeTabMenu(false);

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
    this._refreshSubagents();
    this._updateNavigationButtons();

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
   * Navigate through visit history, skipping tabs that have since been closed.
   */
  async goBack() {
    const destinationIndex = this._findHistoryDestination(-1);
    if (destinationIndex === -1) return;
    this.navigationHistoryIndex = destinationIndex;
    await this.switchToTab(this.navigationHistory[destinationIndex], true);
  }

  async goForward() {
    const destinationIndex = this._findHistoryDestination(1);
    if (destinationIndex === -1) return;
    this.navigationHistoryIndex = destinationIndex;
    await this.switchToTab(this.navigationHistory[destinationIndex], true);
  }

  _findHistoryDestination(direction) {
    for (
      let index = this.navigationHistoryIndex + direction;
      index >= 0 && index < this.navigationHistory.length;
      index += direction
    ) {
      if (this.tabs.has(this.navigationHistory[index])) return index;
    }
    return -1;
  }

  _updateNavigationButtons() {
    if (this.backBtn) {
      this.backBtn.disabled = this._findHistoryDestination(-1) === -1;
    }
    if (this.forwardBtn) {
      this.forwardBtn.disabled = this._findHistoryDestination(1) === -1;
    }
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
        this._applyTabLayout(tab);
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
        if (tab.isPack) {
          const meta = tab.button.querySelector(".tab-workspace-meta");
          if (meta && tab.packHostName) {
            meta.textContent = `Runs on ${tab.packHostName} — without tying up your computer`;
          }
        }
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
   * Keep the host and task outcome visible on offloaded tabs without exposing
   * branch or synchronization machinery.
   */
  setPackWorkspaceStatus(tabId, info) {
    const tab = this.tabs.get(tabId);
    if (!tab?.isPack) return;
    const meta = tab.button.querySelector(".tab-workspace-meta");
    if (!meta) return;

    const workspace = info?.workspace_status || {};
    tab.packHostName = info?.display_name || info?.host || tab.packHostName;

    meta.classList.remove(
      "tab-workspace-meta--dirty",
      "tab-workspace-meta--error",
    );
    if (!info?.connected) {
      meta.textContent = "Remote work paused";
      meta.classList.add("tab-workspace-meta--error");
    } else if (info.project_setup_state === "setting_up") {
      meta.textContent = "Preparing task…";
    } else if (info.project_setup_error) {
      meta.textContent = "Needs attention";
      meta.classList.add("tab-workspace-meta--error");
    } else if (tab.isStreaming) {
      meta.textContent = tab.packHostName
        ? `Runs on ${tab.packHostName} — without tying up your computer`
        : "Working remotely — without tying up your computer";
    } else if (workspace.dirty > 0) {
      meta.textContent = "Changes ready to review";
      meta.classList.add("tab-workspace-meta--dirty");
    } else {
      meta.textContent = "Ready";
    }
    meta.title = meta.textContent;
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

  // Preferences use permanent conversation IDs, never ephemeral session tab IDs.
  applyLayout(layout) {
    this.layout = layout || {};
    for (const tab of this.tabs.values()) this._applyTabLayout(tab);
    this._refreshSubagents();
  }

  _applyTabLayout(tab) {
    if (!tab) return;
    const saved = this.layout[this.getConversationId(tab.id)];
    if (saved) {
      tab.pinned = saved.pinned === true;
      tab.color = ["blue", "green", "amber", "purple"].includes(saved.color)
        ? saved.color
        : "none";
      if (Number.isFinite(saved.order)) tab.order = saved.order;
    }
    tab.button.classList.toggle("pinned", tab.pinned);
    tab.button.dataset.color = tab.color;
    tab.button.setAttribute(
      "aria-label",
      `${tab.title}${tab.pinned ? ", pinned" : ""}, color: ${tab.color}`,
    );
    const title = tab.button.querySelector(".tab-title");
    let marker = title.querySelector(".tab-marker");
    if (!marker) {
      marker = document.createElement("span");
      marker.className = "tab-marker";
      marker.setAttribute("aria-hidden", "true");
      title.prepend(marker);
    }
    marker.innerHTML = tab.pinned
      ? '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><path d="M16 3l5 5-4 1-3 5v3l-2 2-7-7 2-2h3l5-3zM9 15l-6 6"/></svg>'
      : "";
    marker.title = tab.pinned ? "Pinned" : "";
  }

  _saveLayout() {
    for (const [order, tab] of [...this.tabs.values()].entries()) {
      tab.order = order;
      const id = this.getConversationId(tab.id);
      if (id !== null)
        this.layout[id] = { order, pinned: tab.pinned, color: tab.color };
    }
    this._tabSequence = Math.max(this._tabSequence, this.tabs.size);
    const snapshot = JSON.parse(JSON.stringify(this.layout));
    this._saveLayoutQueue = this._saveLayoutQueue
      .then(async () => {
        const result = await this.api.save_preferences({
          tab_layout: snapshot,
        });
        if (!result.success)
          throw new Error(result.error || "Preferences write failed");
      })
      .catch((error) => {
        console.error("Tab layout not saved:", error);
      });
    return this._saveLayoutQueue;
  }

  setTabPinned(tabId, pinned) {
    const tab = this.tabs.get(tabId);
    if (!tab || this._subagentParent(tab)) return;
    tab.pinned = pinned;
    this._refreshSubagents();
    this._saveLayout();
    this._applyTabLayout(tab);
  }

  setTabColor(tabId, color) {
    if (!["none", "blue", "green", "amber", "purple"].includes(color)) return;
    const tab = this.tabs.get(tabId);
    if (!tab) return;
    tab.color = color;
    this._saveLayout();
    this._applyTabLayout(tab);
  }

  _canMoveTab(sourceId, targetId) {
    const source = this.tabs.get(sourceId),
      target = this.tabs.get(targetId);
    return (
      source &&
      target &&
      source !== target &&
      this._subagentParent(source) === this._subagentParent(target) &&
      (this._subagentParent(source) || source.pinned === target.pinned)
    );
  }

  moveTab(tabId, direction) {
    const peers = [...this.tabs.keys()].filter(
      (id) => id === tabId || this._canMoveTab(tabId, id),
    );
    const targetId = peers[peers.indexOf(tabId) + direction];
    if (targetId) this.reorderTab(tabId, targetId, direction > 0);
  }

  reorderTab(sourceId, targetId, after = false) {
    if (!this._canMoveTab(sourceId, targetId)) return false;
    const tabs = [...this.tabs.values()].filter((tab) => tab.id !== sourceId);
    tabs.splice(
      tabs.findIndex((tab) => tab.id === targetId) + Number(after),
      0,
      this.tabs.get(sourceId),
    );
    tabs.forEach((tab, index) => {
      tab.order = index;
    });
    this._refreshSubagents();
    this._saveLayout();
    this.tabs.get(sourceId).button.focus();
    return true;
  }

  _clearTabDrag() {
    this._dragTabId = null;
    for (const tab of this.tabs.values())
      tab.button.classList.remove("drop-before", "drop-after");
  }

  _closeTabMenu(restoreFocus = true) {
    if (!this.tabMenu) return;
    const owner = this.tabs.get(this.tabMenu.dataset.tabId);
    this.tabMenu.remove();
    this.tabMenu = null;
    if (restoreFocus) owner?.button.focus();
  }

  _openTabMenu(tabId) {
    this._closeTabMenu(false);
    const tab = this.tabs.get(tabId);
    const menu = document.createElement("div");
    menu.className = "tab-layout-menu";
    menu.dataset.tabId = tabId;
    menu.setAttribute("role", "dialog");
    menu.setAttribute("aria-label", `Organize ${tab.title}`);
    const add = (label, action, disabled = false) => {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = label;
      button.disabled = disabled;
      button.addEventListener("click", () => {
        action();
        this._closeTabMenu();
      });
      menu.appendChild(button);
      return button;
    };
    const parent = this._subagentParent(tab);
    add(
      tab.pinned ? "Unpin tab" : "Pin tab",
      () => this.setTabPinned(tabId, !tab.pinned),
      !!parent,
    );
    const label = document.createElement("p");
    label.textContent = "Tab color";
    menu.appendChild(label);
    for (const color of ["none", "blue", "green", "amber", "purple"]) {
      const button = add(color[0].toUpperCase() + color.slice(1), () =>
        this.setTabColor(tabId, color),
      );
      button.dataset.color = color;
      button.setAttribute("aria-pressed", String(tab.color === color));
    }
    const peers = [...this.tabs.keys()].filter(
      (id) => id === tabId || this._canMoveTab(tabId, id),
    );
    const index = peers.indexOf(tabId);
    add("Move up · Alt+Shift+↑", () => this.moveTab(tabId, -1), index === 0);
    add(
      "Move down · Alt+Shift+↓",
      () => this.moveTab(tabId, 1),
      index === peers.length - 1,
    );
    const hint = document.createElement("p");
    hint.textContent = parent
      ? "Children move within their parent group."
      : "Drag to reorder within pinned or unpinned tabs. Children follow their parent.";
    menu.appendChild(hint);
    menu.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        this._closeTabMenu();
      }
      if (event.key === "Tab") {
        const buttons = [...menu.querySelectorAll("button:not(:disabled)")];
        const next =
          (buttons.indexOf(document.activeElement) +
            (event.shiftKey ? -1 : 1) +
            buttons.length) %
          buttons.length;
        event.preventDefault();
        buttons[next].focus();
      }
    });
    document.body.appendChild(menu);
    this.tabMenu = menu;
    const bounds = tab.button.getBoundingClientRect();
    menu.style.left = `${Math.max(
      8,
      Math.min(bounds.right + 4, window.innerWidth - menu.offsetWidth - 8),
    )}px`;
    menu.style.top = `${Math.max(
      8,
      Math.min(bounds.top, window.innerHeight - menu.offsetHeight - 8),
    )}px`;
    menu.querySelector("button:not(:disabled)").focus();
  }

  _setupScrollbar() {
    this.container.addEventListener("scroll", () => this._updateScrollbar());
    this.scrollbar.addEventListener("click", (event) => {
      if (event.target === this.scrollbarThumb) return;
      const track = this.scrollbar.getBoundingClientRect();
      const maxScroll =
        this.container.scrollHeight - this.container.clientHeight;
      const maxThumbTop = track.height - this.scrollbarThumb.offsetHeight;
      this.container.scrollTop =
        ((event.clientY - track.top - this.scrollbarThumb.offsetHeight / 2) /
          maxThumbTop) *
        maxScroll;
    });
    this.scrollbarThumb.addEventListener("pointerdown", (event) => {
      event.preventDefault();
      const startY = event.clientY;
      const startScrollTop = this.container.scrollTop;
      const maxScroll =
        this.container.scrollHeight - this.container.clientHeight;
      const maxThumbTop =
        this.scrollbar.clientHeight - this.scrollbarThumb.offsetHeight;
      this.scrollbarThumb.setPointerCapture(event.pointerId);
      const onPointerMove = (moveEvent) => {
        this.container.scrollTop =
          startScrollTop +
          ((moveEvent.clientY - startY) / maxThumbTop) * maxScroll;
      };
      this.scrollbarThumb.addEventListener("pointermove", onPointerMove);
      this.scrollbarThumb.addEventListener(
        "pointerup",
        () =>
          this.scrollbarThumb.removeEventListener("pointermove", onPointerMove),
        { once: true },
      );
    });

    this._resizeObserver = new ResizeObserver(() => this._updateScrollbar());
    this._resizeObserver.observe(this.container);
    this._mutationObserver = new MutationObserver(() =>
      this._updateScrollbar(),
    );
    this._mutationObserver.observe(this.container, { childList: true });
  }

  _updateScrollbar() {
    const maxScroll = this.container.scrollHeight - this.container.clientHeight;
    this.scrollbar.classList.toggle("hidden", maxScroll <= 0);
    if (maxScroll <= 0) return;

    const thumbHeight = Math.max(
      32,
      (this.scrollbar.clientHeight * this.container.clientHeight) /
        this.container.scrollHeight,
    );
    const thumbTop =
      (this.container.scrollTop / maxScroll) *
      (this.scrollbar.clientHeight - thumbHeight);
    this.scrollbarThumb.style.height = `${thumbHeight}px`;
    this.scrollbarThumb.style.transform = `translateY(${thumbTop}px)`;
  }

  destroy() {
    document.removeEventListener("pointerdown", this._dismissTabMenu);
    this._closeTabMenu(false);
    this._resizeObserver.disconnect();
    this._mutationObserver.disconnect();
  }
}

// Export for use in app.js
window.TabManager = TabManager;
