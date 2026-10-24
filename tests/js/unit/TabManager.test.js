/**
 * TabManager Unit Tests
 */

// Load the module - this populates window.TabManager
require("../../../web/js/components/TabManager.js");

describe("TabManager", () => {
  let TabManager;
  let mockApi;
  let tabManager;
  let container;

  beforeAll(() => {
    // Get the class from window after require() populated it
    TabManager = window.TabManager;

    // Verify the source file was modified correctly
    if (!TabManager) {
      throw new Error(
        "window.TabManager is undefined. " +
          'Add "window.TabManager = TabManager;" to the end of web/js/components/TabManager.js',
      );
    }
  });

  beforeEach(() => {
    document.body.innerHTML = `
      <div id="tab-container"></div>
      <div id="tabs-scrollbar" class="hidden">
        <div id="tabs-scrollbar-thumb"></div>
      </div>
      <button id="toolbar-tab-back"></button>
      <button id="toolbar-tab-forward"></button>
    `;
    container = document.getElementById("tab-container");

    mockApi = {
      create_tab: jest.fn(),
      create_pack_tab: jest.fn(),
      close_tab: jest.fn(),
      switch_tab: jest.fn(),
      save_preferences: jest.fn().mockResolvedValue({ success: true }),
    };

    tabManager = new TabManager("tab-container", mockApi);
  });

  afterEach(() => {
    tabManager.destroy();
    document.body.innerHTML = "";
  });

  describe("tab organization", () => {
    beforeEach(() => {
      for (const [id, conv] of [
        ["a", 41],
        ["b", 73],
        ["c", 105],
        ["child", 106],
      ]) {
        tabManager.createTabUI(id, id, false);
        tabManager.setConversationId(id, conv);
      }
      tabManager.setSubagentInfo("child", {
        parent_conversation_id: 73,
        status: "running",
        task_id: "task",
      });
    });

    it("pins groups, retains independent colors, and restores using permanent IDs", async () => {
      tabManager.setTabPinned("b", true);
      tabManager.setTabColor("child", "purple");
      expect(tabManager.getAllTabIds()).toEqual(["b", "child", "a", "c"]);
      expect(tabManager.reorderTab("a", "b")).toBe(false);
      tabManager.moveTab("c", -1);
      expect(tabManager.getAllTabIds()).toEqual(["b", "child", "c", "a"]);
      await tabManager._saveLayoutQueue;
      const saved = mockApi.save_preferences.mock.calls.at(-1)[0].tab_layout;
      tabManager.destroy();
      container.replaceChildren();
      tabManager = new TabManager("tab-container", mockApi);
      tabManager.applyLayout(saved);
      // Opposite creation order and new ephemeral tab IDs, as on restart.
      for (const [id, conv] of [
        ["new-c", 105],
        ["new-a", 41],
        ["new-b", 73],
        ["new-child", 106],
      ]) {
        tabManager.createTabUI(id, id, false);
        tabManager.setConversationId(id, conv);
      }
      tabManager.setSubagentInfo("new-child", {
        parent_conversation_id: 73,
        status: "running",
        task_id: "task",
      });
      expect(tabManager.getAllTabIds()).toEqual([
        "new-b",
        "new-child",
        "new-c",
        "new-a",
      ]);
      expect(
        tabManager.getTab("new-b").button.classList.contains("pinned"),
      ).toBe(true);
      expect(tabManager.getTab("new-child").button.dataset.color).toBe(
        "purple",
      );
      tabManager.setTabPinned("new-b", false);
      tabManager.moveTab("new-b", 1);
      expect(tabManager.getAllTabIds()).toEqual([
        "new-c",
        "new-b",
        "new-child",
        "new-a",
      ]);
      tabManager.createTabUI("fresh", "Fresh task", false);
      tabManager.setConversationId("fresh", 200);
      expect(tabManager.getAllTabIds().at(-1)).toBe("fresh");
    });

    it("separates mixed groups after pinned children and removes empty boundaries", async () => {
      const divider = () => container.querySelector('[role="separator"]');
      expect(divider()).toBeNull();
      tabManager.setTabPinned("b", true);
      expect(divider().textContent).toBe("Other tasks");
      expect(divider().previousElementSibling.dataset.tabId).toBe("child");
      expect(divider().nextElementSibling.dataset.tabId).toBe("a");

      tabManager.moveTab("c", -1);
      expect(container.querySelectorAll('[role="separator"]')).toHaveLength(1);
      expect(divider().nextElementSibling.dataset.tabId).toBe("c");
      tabManager.setTabPinned("b", false);
      expect(divider()).toBeNull();

      for (const id of ["a", "b", "c"]) tabManager.setTabPinned(id, true);
      expect(divider()).toBeNull();
      tabManager.createTabUI("other", "Other", false);
      expect(divider().nextElementSibling.dataset.tabId).toBe("other");
      await tabManager.closeTab("other");
      expect(divider()).toBeNull();
    });

    it("reorders multiple pins and siblings without detaching children", () => {
      tabManager.createTabUI("sibling", "Sibling", false);
      tabManager.setConversationId("sibling", 107);
      tabManager.setSubagentInfo("sibling", {
        parent_conversation_id: 73,
        status: "running",
        task_id: "other-task",
      });
      tabManager.moveTab("sibling", -1);
      expect(tabManager.getAllTabIds()).toEqual([
        "a",
        "b",
        "sibling",
        "child",
        "c",
      ]);
      tabManager.setTabPinned("a", true);
      tabManager.setTabPinned("b", true);
      tabManager.moveTab("b", -1);
      expect(tabManager.getAllTabIds()).toEqual([
        "b",
        "sibling",
        "child",
        "a",
        "c",
      ]);
      tabManager.setSubagentInfo("child", {
        parent_conversation_id: 73,
        status: "complete",
        task_id: "task",
      });
      expect(tabManager.getAllTabIds()).toEqual([
        "b",
        "sibling",
        "child",
        "a",
        "c",
      ]);
      expect(tabManager.reorderTab("child", "c")).toBe(false);
    });

    it("reorders from keyboard without activating, respects boundaries and keeps focus", () => {
      const button = tabManager.getTab("c").button;
      button.focus();
      button.dispatchEvent(
        new KeyboardEvent("keydown", {
          key: "ArrowUp",
          altKey: true,
          shiftKey: true,
          bubbles: true,
        }),
      );
      expect(tabManager.getAllTabIds()).toEqual(["a", "c", "b", "child"]);
      expect(document.activeElement).toBe(button);
      expect(mockApi.switch_tab).not.toHaveBeenCalled();
      tabManager.moveTab("a", -1);
      expect(tabManager.getAllTabIds()).toEqual(["a", "c", "b", "child"]);
      expect(tabManager.reorderTab("child", "a")).toBe(false);
    });

    it("opens actions on right-click and keyboard, clears color, and restores tab focus", () => {
      const button = tabManager.getTab("a").button;
      expect(button.querySelector(".tab-actions")).toBeNull();
      button.dispatchEvent(new MouseEvent("contextmenu", { bubbles: true }));
      const menu = document.querySelector('[role="dialog"]');
      [...menu.querySelectorAll("button")]
        .find((b) => b.textContent === "Blue")
        .click();
      expect(button.dataset.color).toBe("blue");
      expect(document.activeElement).toBe(button);
      expect(mockApi.switch_tab).not.toHaveBeenCalled();
      button.dispatchEvent(
        new KeyboardEvent("keydown", {
          key: "F10",
          shiftKey: true,
          bubbles: true,
        }),
      );
      document.querySelector('.tab-layout-menu [data-color="none"]').click();
      expect(button.dataset.color).toBe("none");
      expect(document.activeElement).toBe(button);
    });
  });

  describe("initialization", () => {
    it("should store api reference", () => {
      expect(tabManager.api).toBe(mockApi);
    });

    it("should store container reference", () => {
      expect(tabManager.container).toBe(container);
    });

    it("should have empty tabs map", () => {
      expect(tabManager.tabs.size).toBe(0);
    });

    it("should have null active tab", () => {
      expect(tabManager.activeTabId).toBeNull();
    });

    it("should start with empty conv-id map", () => {
      expect(tabManager._convIds.size).toBe(0);
    });
  });

  describe("createTab()", () => {
    it("should call Python to create tab with no args", async () => {
      mockApi.create_tab.mockResolvedValue({
        success: true,
        tab_id: "tab-1-abc123",
        conversation_id: 42,
      });

      await tabManager.createTab();

      expect(mockApi.create_tab).toHaveBeenCalledWith();
    });

    it("should create tab UI on success", async () => {
      mockApi.create_tab.mockResolvedValue({
        success: true,
        tab_id: "tab-1-abc123",
        conversation_id: 42,
      });

      const tabId = await tabManager.createTab();

      expect(tabId).toBe("tab-1-abc123");
      expect(tabManager.tabs.has("tab-1-abc123")).toBe(true);
    });

    it("should use conversation id as initial title", async () => {
      mockApi.create_tab.mockResolvedValue({
        success: true,
        tab_id: "tab-1-abc123",
        conversation_id: 42,
      });

      await tabManager.createTab();

      const button = container.querySelector('[data-tab-id="tab-1-abc123"]');
      expect(button.querySelector(".tab-title").textContent).toBe("#42");
    });

    it("should store conversation id in _convIds map", async () => {
      mockApi.create_tab.mockResolvedValue({
        success: true,
        tab_id: "tab-1-abc123",
        conversation_id: 42,
      });

      await tabManager.createTab();

      expect(tabManager._convIds.get("tab-1-abc123")).toBe(42);
    });

    it("should return null on failure", async () => {
      mockApi.create_tab.mockResolvedValue({
        success: false,
        error: "Failed to create",
      });

      const tabId = await tabManager.createTab();

      expect(tabId).toBeNull();
    });

    it("should dispatch tabCreated event", async () => {
      const eventHandler = jest.fn();
      document.addEventListener("tabCreated", eventHandler);

      mockApi.create_tab.mockResolvedValue({
        success: true,
        tab_id: "tab-1-abc123",
        conversation_id: 42,
      });

      await tabManager.createTab();

      expect(eventHandler).toHaveBeenCalled();
      expect(eventHandler.mock.calls[0][0].detail.tabId).toBe("tab-1-abc123");

      document.removeEventListener("tabCreated", eventHandler);
    });
  });

  describe("createPackTab()", () => {
    it("should call Python with the given host", async () => {
      mockApi.create_pack_tab.mockResolvedValue({
        success: true,
        tab_id: "tab-2-def456",
        conversation_id: 7,
        display_name: "Deimos",
      });

      await tabManager.createPackTab("user@host");

      expect(mockApi.create_pack_tab).toHaveBeenCalledWith(
        "user@host",
        "Offloaded task",
      );
    });

    it("should create tab UI tagged as pack on success", async () => {
      mockApi.create_pack_tab.mockResolvedValue({
        success: true,
        tab_id: "tab-2-def456",
        conversation_id: 7,
        display_name: "Deimos",
      });

      const tabId = await tabManager.createPackTab("user@host");

      expect(tabId).toBe("tab-2-def456");
      const button = container.querySelector('[data-tab-id="tab-2-def456"]');
      expect(button.classList.contains("pack")).toBe(true);
      expect(tabManager.tabs.get("tab-2-def456").isPack).toBe(true);
      expect(tabManager.tabs.get("tab-2-def456").packHostName).toBe("Deimos");
    });

    it("should return null on failure", async () => {
      mockApi.create_pack_tab.mockResolvedValue({
        success: false,
        error: "unreachable",
      });

      const tabId = await tabManager.createPackTab("user@host");

      expect(tabId).toBeNull();
    });
  });

  describe("createTabUI()", () => {
    it("should create tab button in DOM", () => {
      tabManager.createTabUI("tab-1-abc123", "Test Tab", false);

      const button = container.querySelector('[data-tab-id="tab-1-abc123"]');
      expect(button).not.toBeNull();
      expect(button.classList.contains("tab")).toBe(true);
    });

    it("should set tab title", () => {
      tabManager.createTabUI("tab-1-abc123", "Test Tab", false);

      const titleSpan = container.querySelector(".tab-title");
      expect(titleSpan.textContent).toBe("Test Tab");
      expect(titleSpan.title).toBe("Test Tab");
    });

    it("should add close button", () => {
      tabManager.createTabUI("tab-1-abc123", "Test Tab", false);

      const closeBtn = container.querySelector(".tab-close");
      expect(closeBtn).not.toBeNull();
      expect(closeBtn.textContent).toBe("×");
    });

    it("should switch to tab when autoSwitch is true", () => {
      tabManager.createTabUI("tab-1-abc123", "Test Tab", true);

      expect(tabManager.activeTabId).toBe("tab-1-abc123");
    });

    it("should not switch to tab when autoSwitch is false", () => {
      tabManager.createTabUI("tab-1-abc123", "Test Tab", false);

      expect(tabManager.activeTabId).toBeNull();
    });

    it("should store tab data", () => {
      tabManager.createTabUI("tab-1-abc123", "Test Tab", false);

      const tabData = tabManager.tabs.get("tab-1-abc123");
      expect(tabData).toBeDefined();
      expect(tabData.id).toBe("tab-1-abc123");
      expect(tabData.title).toBe("Test Tab");
    });
  });

  describe("switchToTab()", () => {
    beforeEach(async () => {
      mockApi.create_tab.mockResolvedValue({
        success: true,
        tab_id: "tab-1-abc123",
      });
      await tabManager.createTab("Tab 1");

      mockApi.create_tab.mockResolvedValue({
        success: true,
        tab_id: "tab-2-abc456",
      });
      await tabManager.createTab("Tab 2");
    });

    it("should notify Python on switch", async () => {
      await tabManager.switchToTab("tab-2-abc456");

      expect(mockApi.switch_tab).toHaveBeenCalledWith("tab-2-abc456");
    });

    it("should update active tab", async () => {
      await tabManager.switchToTab("tab-2-abc456");

      expect(tabManager.activeTabId).toBe("tab-2-abc456");
    });

    it("should update visual active state", async () => {
      await tabManager.switchToTab("tab-2-abc456");

      const tab1 = container.querySelector('[data-tab-id="tab-1-abc123"]');
      const tab2 = container.querySelector('[data-tab-id="tab-2-abc456"]');

      expect(tab1.classList.contains("active")).toBe(false);
      expect(tab2.classList.contains("active")).toBe(true);
    });

    it("should dispatch tabSwitched event", async () => {
      const eventHandler = jest.fn();
      document.addEventListener("tabSwitched", eventHandler);

      await tabManager.switchToTab("tab-2-abc456");

      expect(eventHandler).toHaveBeenCalled();
      expect(eventHandler.mock.calls[0][0].detail.tabId).toBe("tab-2-abc456");

      document.removeEventListener("tabSwitched", eventHandler);
    });

    it("should do nothing for non-existent tab", async () => {
      const originalActive = tabManager.activeTabId;

      await tabManager.switchToTab("non-existent");

      expect(tabManager.activeTabId).toBe(originalActive);
    });
  });

  describe("closeTab()", () => {
    beforeEach(async () => {
      mockApi.create_tab.mockResolvedValue({
        success: true,
        tab_id: "tab-1-abc123",
      });
      await tabManager.createTab("Tab 1");
    });

    it("should remove tab from DOM", async () => {
      await tabManager.closeTab("tab-1-abc123");

      const button = container.querySelector('[data-tab-id="tab-1-abc123"]');
      expect(button).toBeNull();
    });

    it("should remove tab from tabs map", async () => {
      await tabManager.closeTab("tab-1-abc123");

      expect(tabManager.tabs.has("tab-1-abc123")).toBe(false);
    });

    it("should notify Python", async () => {
      await tabManager.closeTab("tab-1-abc123");

      expect(mockApi.close_tab).toHaveBeenCalledWith("tab-1-abc123");
    });

    it("should dispatch tabClosed event", async () => {
      const eventHandler = jest.fn();
      document.addEventListener("tabClosed", eventHandler);

      await tabManager.closeTab("tab-1-abc123");

      expect(eventHandler).toHaveBeenCalled();
      expect(eventHandler.mock.calls[0][0].detail.tabId).toBe("tab-1-abc123");

      document.removeEventListener("tabClosed", eventHandler);
    });

    it("should switch to another tab when closing active tab", async () => {
      mockApi.create_tab.mockResolvedValue({
        success: true,
        tab_id: "tab-2-abc456",
      });
      await tabManager.createTab("Tab 2"); // autoSwitch=true → activeTabId='tab-2-abc456'

      // Switch to tab-1 to make it the active tab
      await tabManager.switchToTab("tab-1-abc123");
      expect(tabManager.activeTabId).toBe("tab-1-abc123");

      // Now close the active tab (tab-1), should auto-switch to tab-2
      await tabManager.closeTab("tab-1-abc123");

      expect(tabManager.activeTabId).toBe("tab-2-abc456");
    });

    it("should handle close button click", async () => {
      const closeBtn = container.querySelector(".tab-close");

      // Click the close button
      closeBtn.click();

      // Wait for async operations
      await Promise.resolve();

      expect(tabManager.tabs.has("tab-1-abc123")).toBe(false);
    });
  });

  describe("setTabOffline()", () => {
    beforeEach(() => {
      tabManager.createTabUI("tab-1-abc123", "Test Tab", false, true);
    });

    it("should add the offline class when true", () => {
      tabManager.setTabOffline("tab-1-abc123", true);

      const button = container.querySelector('[data-tab-id="tab-1-abc123"]');
      expect(button.classList.contains("offline")).toBe(true);
      expect(tabManager.tabs.get("tab-1-abc123").isOffline).toBe(true);
    });

    it("should remove the offline class when false", () => {
      tabManager.setTabOffline("tab-1-abc123", true);
      tabManager.setTabOffline("tab-1-abc123", false);

      const button = container.querySelector('[data-tab-id="tab-1-abc123"]');
      expect(button.classList.contains("offline")).toBe(false);
      expect(tabManager.tabs.get("tab-1-abc123").isOffline).toBe(false);
    });

    it("should do nothing for a non-existent tab", () => {
      expect(() => tabManager.setTabOffline("nope", true)).not.toThrow();
    });
  });

  describe("setPackWorkspaceStatus()", () => {
    beforeEach(() => {
      tabManager.createTabUI(
        "pack-1",
        "Remote work",
        false,
        true,
        "alpaca",
        "Deimos",
      );
    });

    it("shows the Pack host before status is loaded", () => {
      expect(container.querySelector(".tab-workspace-meta").textContent).toBe(
        "Runs on Deimos",
      );
    });

    it("shows the outcome without exposing repository plumbing", () => {
      tabManager.setPackWorkspaceStatus("pack-1", {
        connected: true,
        workspace_path: "/work/alpaca",
        workspace_status: {
          is_git: true,
          branch: "feature/pack-status",
          dirty: 3,
          unpushed: 2,
        },
      });

      const meta = container.querySelector(".tab-workspace-meta");
      expect(meta.textContent).toBe("Changes ready to review");
      expect(meta.classList.contains("tab-workspace-meta--dirty")).toBe(true);
      expect(meta.textContent).not.toContain("feature/pack-status");
    });

    it("marks an idle clean task as ready", () => {
      tabManager.setPackWorkspaceStatus("pack-1", {
        connected: true,
        workspace_status: {
          is_git: true,
          branch: "main",
          dirty: 0,
          unpushed: 0,
        },
      });

      expect(container.querySelector(".tab-workspace-meta").textContent).toBe(
        "Ready",
      );
    });

    it("shows the Pack host name while streaming", () => {
      tabManager.setPackWorkspaceStatus("pack-1", {
        connected: true,
        display_name: "Deimos",
        host: "192.168.0.58",
        workspace_status: { dirty: 0 },
      });

      tabManager.setTabStreaming("pack-1", true);

      expect(container.querySelector(".tab-workspace-meta").textContent).toBe(
        "Runs on Deimos — without tying up your computer",
      );
    });
  });

  describe("tab navigation", () => {
    beforeEach(async () => {
      mockApi.create_tab
        .mockResolvedValueOnce({ success: true, tab_id: "tab-1" })
        .mockResolvedValueOnce({ success: true, tab_id: "tab-2" })
        .mockResolvedValueOnce({ success: true, tab_id: "tab-3" });

      await tabManager.createTab("Tab 1");
      await tabManager.createTab("Tab 2");
      await tabManager.createTab("Tab 3");
    });

    it("nextTab() should go to next tab", async () => {
      await tabManager.switchToTab("tab-1");
      tabManager.nextTab();

      expect(tabManager.activeTabId).toBe("tab-2");
    });

    it("nextTab() should wrap around", async () => {
      await tabManager.switchToTab("tab-3");
      tabManager.nextTab();

      expect(tabManager.activeTabId).toBe("tab-1");
    });

    it("prevTab() should go to previous tab", async () => {
      await tabManager.switchToTab("tab-2");
      tabManager.prevTab();

      expect(tabManager.activeTabId).toBe("tab-1");
    });

    it("prevTab() should wrap around", async () => {
      await tabManager.switchToTab("tab-1");
      tabManager.prevTab();

      expect(tabManager.activeTabId).toBe("tab-3");
    });

    it("should do nothing with single tab", async () => {
      // Close all but one
      await tabManager.closeTab("tab-2");
      await tabManager.closeTab("tab-3");

      const originalActive = tabManager.activeTabId;
      tabManager.nextTab();

      expect(tabManager.activeTabId).toBe(originalActive);
    });
  });

  describe("tab visit history", () => {
    beforeEach(() => {
      tabManager.createTabUI("tab-1", "Tab 1", false);
      tabManager.createTabUI("tab-2", "Tab 2", false);
      tabManager.createTabUI("tab-14", "Tab 14", false);
    });

    it("navigates back and forward by visit order", async () => {
      await tabManager.switchToTab("tab-1");
      await tabManager.switchToTab("tab-14");

      await tabManager.goBack();
      expect(tabManager.activeTabId).toBe("tab-1");
      expect(document.getElementById("toolbar-tab-forward").disabled).toBe(
        false,
      );

      await tabManager.goForward();
      expect(tabManager.activeTabId).toBe("tab-14");
    });

    it("skips closed tabs without removing earlier history", async () => {
      await tabManager.switchToTab("tab-2");
      await tabManager.switchToTab("tab-1");
      await tabManager.switchToTab("tab-14");
      await tabManager.closeTab("tab-1");

      await tabManager.goBack();
      expect(tabManager.activeTabId).toBe("tab-2");

      await tabManager.goForward();
      expect(tabManager.activeTabId).toBe("tab-14");
    });

    it("clears forward history after a new manual switch", async () => {
      await tabManager.switchToTab("tab-1");
      await tabManager.switchToTab("tab-14");
      await tabManager.goBack();
      await tabManager.switchToTab("tab-2");

      await tabManager.goForward();
      expect(tabManager.activeTabId).toBe("tab-2");
      expect(document.getElementById("toolbar-tab-forward").disabled).toBe(
        true,
      );
    });
  });

  describe("tab utilities", () => {
    beforeEach(async () => {
      mockApi.create_tab.mockResolvedValue({
        success: true,
        tab_id: "tab-1-abc123",
      });
      await tabManager.createTab("Tab 1");
    });

    it("getActiveTab() should return current tab data", () => {
      tabManager.switchToTab("tab-1-abc123");

      const activeTab = tabManager.getActiveTab();
      expect(activeTab.id).toBe("tab-1-abc123");
    });

    it("getActiveTabId() should return current tab ID", () => {
      tabManager.switchToTab("tab-1-abc123");

      expect(tabManager.getActiveTabId()).toBe("tab-1-abc123");
    });

    it("getTab() should return specific tab", () => {
      const tab = tabManager.getTab("tab-1-abc123");
      expect(tab.id).toBe("tab-1-abc123");
    });

    it("getAllTabIds() should return all tab IDs", () => {
      const ids = tabManager.getAllTabIds();
      expect(ids).toContain("tab-1-abc123");
    });

    it("getTabCount() should return tab count", () => {
      expect(tabManager.getTabCount()).toBe(1);
    });

    it("updateTabTitle() should update tab title", () => {
      tabManager.updateTabTitle("tab-1-abc123", "New Title");

      const tabData = tabManager.tabs.get("tab-1-abc123");
      expect(tabData.title).toBe("New Title");

      const titleSpan = container.querySelector(".tab-title");
      expect(titleSpan.textContent).toBe("New Title");
    });

    it("setTabStreaming() should set streaming state", () => {
      tabManager.setTabStreaming("tab-1-abc123", true);

      const tabData = tabManager.tabs.get("tab-1-abc123");
      expect(tabData.isStreaming).toBe(true);

      const button = container.querySelector('[data-tab-id="tab-1-abc123"]');
      expect(button.classList.contains("streaming")).toBe(true);
    });

    it("isTabStreaming() should return streaming state", () => {
      tabManager.setTabStreaming("tab-1-abc123", true);

      expect(tabManager.isTabStreaming("tab-1-abc123")).toBe(true);
    });

    it("setActiveTab() should set visual active state", () => {
      tabManager.setActiveTab("tab-1-abc123");

      const button = container.querySelector('[data-tab-id="tab-1-abc123"]');
      expect(button.classList.contains("active")).toBe(true);
    });
  });

  describe("subagent tabs", () => {
    const info = (status = "running") => ({
      parent_conversation_id: 42,
      task_id: "task-1",
      status,
    });
    const order = () =>
      Array.from(container.children, (el) => el.dataset.tabId);

    beforeEach(() => {
      for (const id of ["parent", "other", "child-1", "child-2"]) {
        tabManager.createTabUI(id, id, false);
      }
      tabManager.setConversationId("parent", 42);
    });

    it("groups siblings in creation order without switching or duplicating UI", async () => {
      await tabManager.switchToTab("other");
      mockApi.switch_tab.mockClear();
      tabManager.setSubagentInfo("child-2", info());
      tabManager.setSubagentInfo("child-1", info());
      tabManager.setSubagentInfo("child-1", info());
      expect(order()).toEqual(["parent", "child-1", "child-2", "other"]);
      expect(container.querySelectorAll(".tab-subagent-meta")).toHaveLength(2);
      expect(tabManager.activeTabId).toBe("other");
      expect(mockApi.switch_tab).not.toHaveBeenCalled();
      await tabManager.switchToTab("parent");
      tabManager.nextTab();
      expect(tabManager.activeTabId).toBe("child-1");
      await tabManager.closeTab("child-1");
      expect(tabManager.activeTabId).toBe("parent");
    });

    it.each(["running", "completed", "failed", "cancelled", "interrupted"])(
      "shows %s while keeping the conversation inspectable",
      (status) => {
        tabManager.setSubagentInfo("child-1", info());
        tabManager.setSubagentInfo("child-1", info(status));
        const child = tabManager.getTab("child-1").button;
        expect(child.querySelector(".tab-subagent-status").textContent).toBe(
          `↳ Child · ${status}`,
        );
        expect(child.querySelectorAll(".tab-subagent-parent")).toHaveLength(1);
        child.querySelector(".tab-title").click();
        expect(tabManager.activeTabId).toBe("child-1");
      },
    );

    it("navigates only to the parent and disables back when it closes", async () => {
      tabManager.setSubagentInfo("child-1", info());
      const back = tabManager
        .getTab("child-1")
        .button.querySelector(".tab-subagent-parent");
      mockApi.switch_tab.mockClear();
      back.click();
      expect(mockApi.switch_tab.mock.calls).toEqual([["parent"]]);
      await tabManager.closeTab("parent");
      expect(back.disabled).toBe(true);
      expect(back.title).toBe("Parent #42 is not open");
      mockApi.switch_tab.mockClear();
      back.click();
      expect(mockApi.switch_tab).not.toHaveBeenCalled();
    });

    it("reconciles late child IDs and a parent restored after its children", async () => {
      await tabManager.closeTab("parent");
      tabManager.setSubagentInfo("child-2", info());
      tabManager.setSubagentInfo("child-1", info());
      tabManager.setConversationId("child-1", 43);
      const back = tabManager
        .getTab("child-1")
        .button.querySelector(".tab-subagent-parent");
      expect(back.disabled).toBe(true);
      tabManager.createTabUI("restored-parent", "Parent", false);
      expect(back.disabled).toBe(true);
      tabManager.setConversationId("restored-parent", 42);
      expect(order()).toEqual([
        "other",
        "restored-parent",
        "child-1",
        "child-2",
      ]);
      expect(back.disabled).toBe(false);
      back.click();
      expect(tabManager.activeTabId).toBe("restored-parent");
    });
  });

  describe("vertical overflow", () => {
    it("shows a conventional scrollbar when tabs exceed the available height", () => {
      Object.defineProperties(container, {
        clientHeight: { configurable: true, value: 200 },
        scrollHeight: { configurable: true, value: 300 },
      });
      Object.defineProperty(tabManager.scrollbar, "clientHeight", {
        configurable: true,
        value: 200,
      });

      tabManager._updateScrollbar();

      expect(tabManager.scrollbar).not.toHaveClass("hidden");
      expect(tabManager.scrollbarThumb.style.height).toBe(
        "133.33333333333334px",
      );
      expect(tabManager.scrollbarThumb.style.transform).toBe("translateY(0px)");
    });

    it("moves the thumb with the task list scroll position", () => {
      Object.defineProperties(container, {
        clientHeight: { configurable: true, value: 200 },
        scrollHeight: { configurable: true, value: 400 },
        scrollTop: { configurable: true, value: 100 },
      });
      Object.defineProperty(tabManager.scrollbar, "clientHeight", {
        configurable: true,
        value: 200,
      });

      tabManager._updateScrollbar();

      expect(tabManager.scrollbarThumb.style.height).toBe("100px");
      expect(tabManager.scrollbarThumb.style.transform).toBe(
        "translateY(50px)",
      );
    });
  });
});
