require("../../../web/js/app.js");

describe("AlpacaApp conversation display loading", () => {
  let app;
  let pending;

  beforeEach(() => {
    pending = [];
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
    app.chatDisplay = { clear: jest.fn() };
    app._renderConversationState = jest.fn();
    app._updateStatusBar = jest.fn();
    app._updateWorkspaceHeader = jest.fn();
    app.tabManager = { setTabOffline: jest.fn() };
  });

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
