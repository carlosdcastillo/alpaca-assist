require("../../../web/js/app.js");
require("../../../web/js/components/TabManager.js");
require("../../../web/js/components/InputArea.js");

describe("loading earlier messages", () => {
  it("keeps the scroll offset, including user scrolling between loaded turns", async () => {
    document.body.innerHTML =
      '<div id="chat-container"><div>Latest turn</div></div>';
    const container = document.getElementById("chat-container");
    // jsdom has no layout; model growth so height-based scroll compensation
    // would move the viewport and fail this regression test.
    Object.defineProperty(container, "scrollHeight", {
      get: () => container.childElementCount * 200,
    });
    const app = Object.create(window.AlpacaApp.prototype);
    app.chatDisplay = { container };
    app._updateFindCount = () => {};
    const page = {
      start: 2,
      render(index) {
        expect(container.scrollTop).toBe(index === 0 ? 0 : 73);
        const turn = document.createElement("div");
        turn.textContent = `Earlier turn ${index}`;
        container.append(turn);
        if (index === 0)
          setTimeout(() => {
            container.scrollTop = 73;
          }, 0);
      },
    };
    app._historyPage = page;
    app._updateHistoryControl(page);

    await app._loadEarlierMessages(page);

    expect(container.scrollTop).toBe(73);
    expect(
      Array.from(container.children)
        .slice(1)
        .map((el) => el.textContent),
    ).toEqual(["Earlier turn 0", "Earlier turn 1", "Latest turn"]);
    expect(page.button).not.toBeVisible();
    expect(page.label).toHaveTextContent("All earlier messages loaded");
  });
});

describe("AlpacaApp conversation display loading", () => {
  let app;
  let pending;

  beforeEach(() => {
    pending = [];
    document.body.innerHTML = '<div id="chat-container"></div>';
    app = Object.create(window.AlpacaApp.prototype);
    app.currentTabId = "pack-1";
    app._tabSwitchSeq = 0;
    app.api = {
      get_conversation_state: jest.fn(
        () =>
          new Promise((resolve) => {
            pending.push(resolve);
          }),
      ),
    };
    const container = document.getElementById("chat-container");
    app.chatDisplay = {
      container,
      clear: jest.fn(() => container.replaceChildren()),
    };
    app._renderConversationState = jest.fn();
    app._updateStatusBar = jest.fn();
    app._updateWorkspaceHeader = jest.fn();
    app.tabManager = {
      setTabOffline: jest.fn(),
      getTab: jest.fn(() => ({ title: "New Task" })),
      isTabStreaming: jest.fn(() => false),
    };
    app.inputArea = { setStreaming: jest.fn() };
  });

  afterEach(() => jest.useRealTimers());

  it("shows loading only after 500ms and removes it when finished", async () => {
    jest.useFakeTimers();
    const fast = app._onTabSwitched("fast");
    jest.advanceTimersByTime(499);
    expect(document.querySelector('[role="status"]')).toBeNull();
    pending.shift()({ success: true, state: {} });
    await fast;
    jest.advanceTimersByTime(1);
    expect(document.querySelector('[role="status"]')).toBeNull();

    const slow = app._onTabSwitched("slow");
    jest.advanceTimersByTime(499);
    expect(document.querySelector('[role="status"]')).toBeNull();
    jest.advanceTimersByTime(1);
    expect(document.querySelector('[role="status"]')).toHaveTextContent(
      "Loading…",
    );
    pending.shift()({ success: true, state: {} });
    await slow;
    expect(document.querySelector('[role="status"]')).toBeNull();
  });

  it("keeps a newer tab's loading status when a stale load finishes", async () => {
    jest.useFakeTimers();
    const older = app._onTabSwitched("older");
    jest.advanceTimersByTime(500);
    const newer = app._onTabSwitched("newer");
    expect(document.querySelector('[role="status"]')).toBeNull();
    jest.advanceTimersByTime(500);
    pending.shift()({ success: true, state: {} });
    await older;
    expect(document.querySelectorAll('[role="status"]')).toHaveLength(1);
    pending.shift()({ success: false });
    await newer;
    expect(document.querySelector('[role="status"]')).toBeNull();
  });

  it("removes loading if the backend rejects", async () => {
    jest.useFakeTimers();
    app.api.get_conversation_state = () => Promise.reject(new Error("offline"));
    const load = app._onTabSwitched("offline");
    jest.advanceTimersByTime(500);
    expect(document.querySelector('[role="status"]')).toHaveTextContent(
      "Loading…",
    );
    await expect(load).rejects.toThrow("offline");
    expect(document.querySelector('[role="status"]')).toBeNull();
  });

  it.each(["local-new", "pack-new"])(
    "clears the previous conversation gap anchor for an empty %s tab",
    async (tabId) => {
      app.currentTabId = tabId;
      app._lastTurnEnd = Date.now() / 1000 - 3 * 86400;

      const load = app._onTabSwitched(tabId);
      expect(app._lastTurnEnd).toBeNull();

      pending.shift()({
        success: true,
        state: {
          chat_state: { graph: { nodes: {}, active_node_id: null } },
        },
      });
      await load;

      expect(app._lastTurnEnd).toBeNull();
    },
  );

  it("lets a Pack sync supersede the initial tab-switch render", async () => {
    const initialLoad = app._onTabSwitched("pack-1");
    const syncedLoad = app.onPackStateSynced("pack-1");
    const syncedState = {
      chat_state: { questions: ["complete remote history"], answers: [] },
    };

    pending[1]({ success: true, state: syncedState });
    await syncedLoad;
    pending[0]({
      success: true,
      state: {
        chat_state: { questions: ["stale saved history"], answers: [] },
      },
    });
    await initialLoad;

    expect(app._renderConversationState).toHaveBeenCalledTimes(1);
    expect(app._renderConversationState).toHaveBeenCalledWith(syncedState);
  });

  it("discards an older Pack reload that resolves after a newer one", async () => {
    const olderLoad = app._reloadConversationDisplay("pack-1");
    const newerLoad = app._reloadConversationDisplay("pack-1");
    const newerState = { chat_state: { questions: ["latest"], answers: [] } };

    pending[1]({ success: true, state: newerState });
    await newerLoad;

    expect(app.chatDisplay.clear).toHaveBeenCalledTimes(1);
    expect(app._renderConversationState).toHaveBeenCalledWith(newerState);

    pending[0]({
      success: true,
      state: { chat_state: { questions: ["stale"], answers: [] } },
    });
    await olderLoad;

    expect(app.chatDisplay.clear).toHaveBeenCalledTimes(1);
    expect(app._renderConversationState).toHaveBeenCalledTimes(1);
  });

  it("does not clear the active conversation for a stale tab reload", async () => {
    await app._reloadConversationDisplay("pack-2");

    expect(app.api.get_conversation_state).not.toHaveBeenCalled();
    expect(app.chatDisplay.clear).not.toHaveBeenCalled();
  });
});

describe("whole-turn Stop control", () => {
  let app;
  beforeEach(() => {
    document.body.innerHTML =
      '<div id="tabs"></div><div id="tabs-scrollbar"><div id="tabs-scrollbar-thumb"></div></div><button id="send"></button><button id="stop"></button>';
    app = Object.create(window.AlpacaApp.prototype);
    app.currentTabId = "parent";
    app.api = {
      switch_tab: async () => ({ success: true }),
      stop_streaming: async () => ({ success: true }),
      get_conversation_state: async () => ({ success: true, state: {} }),
    };
    app.tabManager = new window.TabManager("tabs", app.api);
    app.tabManager.createTabUI("parent", "Parent", false);
    app.tabManager.createTabUI("child", "Child", false);
    app.inputArea = Object.create(window.InputArea.prototype);
    app.inputArea.sendBtn = document.getElementById("send");
    app.inputArea.stopBtn = document.getElementById("stop");
    app.inputArea.isConnected = true;
    app.inputArea.markdownInput = { setDisabled() {}, setPlaceholder() {} };
    app.chatDisplay = {
      clear() {},
      setStreaming() {},
      startAnswerTimer() {},
      stopAnswerTimer() {},
      appendContent() {},
      setAnswerTiming() {},
    };
    for (const name of [
      "_startStatusTicker",
      "_stopStatusTicker",
      "_updateStatusBar",
      "_scheduleStatusUpdate",
      "_renderConversationState",
      "_updateWorkspaceHeader",
    ]) {
      app[name] = () => {};
    }
  });

  it("keeps Stop enabled through invocation completion and tab switches, until the whole turn ends", async () => {
    app.onStreamingStart("parent", 0);
    app.onContentUpdate("parent", { is_done: true });
    app.onStreamingEnd("parent", 0);
    expect(app.inputArea.stopBtn).not.toBeDisabled();
    expect(app.inputArea.sendBtn).toBeDisabled();
    await app._onTabSwitched("child");
    expect(app.inputArea.stopBtn).toBeDisabled();
    await app._onTabSwitched("parent");
    expect(app.inputArea.stopBtn).not.toBeDisabled();
    app.onTurnTiming("parent", 0, { completed_at: 123 });
    expect(app.inputArea.stopBtn).toBeDisabled();
    expect(app.inputArea.sendBtn).not.toBeDisabled();
  });

  it("allows stopping a waiting turn and clears completed background turns", async () => {
    app.onStreamingStart("parent", 0);
    app.onStreamingEnd("parent", 0);
    await app._onStopStreaming();
    expect(app.tabManager.isTabStreaming("parent")).toBe(false);
    expect(app.inputArea.stopBtn).toBeDisabled();
    app.onStreamingStart("child", 0);
    app.onStreamingEnd("child", 0);
    app.onTurnTiming("child", 0, { completed_at: 456 });
    await app._onTabSwitched("child");
    expect(app.inputArea.stopBtn).toBeDisabled();
  });

  it("does not mark the newly selected child stopped when a parent Stop request resolves", async () => {
    app.onStreamingStart("parent", 0);
    const stopping = app._onStopStreaming();
    app.onStreamingStart("child", 0);
    await app._onTabSwitched("child");
    await stopping;
    expect(app.tabManager.isTabStreaming("parent")).toBe(false);
    expect(app.tabManager.isTabStreaming("child")).toBe(true);
    expect(app.inputArea.stopBtn).not.toBeDisabled();
  });
});
