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
    // Set up DOM (scroll buttons must exist for TabManager constructor)
    document.body.innerHTML = `
      <button id="tab-scroll-left" class="tab-scroll-btn hidden"></button>
      <div id="tab-container"></div>
      <button id="tab-scroll-right" class="tab-scroll-btn hidden"></button>
      <button id="toolbar-tab-back"></button>
      <button id="toolbar-tab-forward"></button>
    `;
    container = document.getElementById("tab-container");

    mockApi = {
      create_tab: jest.fn(),
      create_pack_tab: jest.fn(),
      close_tab: jest.fn(),
      switch_tab: jest.fn(),
    };

    tabManager = new TabManager("tab-container", mockApi);
  });

  afterEach(() => {
    document.body.innerHTML = "";
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

  describe("vertical overflow", () => {
    it("scroll buttons move the tab list vertically", () => {
      container.scrollBy = jest.fn();

      document.getElementById("tab-scroll-right").click();
      document.getElementById("tab-scroll-left").click();

      expect(container.scrollBy).toHaveBeenNthCalledWith(1, {
        top: 44,
        behavior: "smooth",
      });
      expect(container.scrollBy).toHaveBeenNthCalledWith(2, {
        top: -44,
        behavior: "smooth",
      });
    });

    it("shows overflow controls when tabs exceed the available height", () => {
      Object.defineProperties(container, {
        clientHeight: { configurable: true, value: 200 },
        scrollHeight: { configurable: true, value: 300 },
      });

      tabManager._checkOverflow();

      expect(document.getElementById("tab-scroll-left")).not.toHaveClass(
        "hidden",
      );
      expect(document.getElementById("tab-scroll-right")).not.toHaveClass(
        "hidden",
      );
    });
  });
});
