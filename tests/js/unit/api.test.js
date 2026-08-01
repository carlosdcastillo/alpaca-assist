/**
 * PythonAPI Unit Tests
 *
 * PREREQUISITE: web/js/api.js must have this line added:
 *   window.PythonAPI = PythonAPI;
 */

// Load the module - this populates window.PythonAPI
require("../../../web/js/api.js");

describe("PythonAPI", () => {
  let PythonAPI;
  let api;
  let mockPythonAPI;

  beforeAll(() => {
    // Get the class from window after require() populated it
    PythonAPI = window.PythonAPI;

    // Verify the source file was modified correctly
    if (!PythonAPI) {
      throw new Error(
        "window.PythonAPI is undefined. " +
          'Add "window.PythonAPI = PythonAPI;" to the end of web/js/api.js',
      );
    }
  });

  beforeEach(() => {
    jest.clearAllMocks();

    // Create mock API object
    mockPythonAPI = {
      isAvailable: jest.fn().mockReturnValue(true),
      create_tab: jest.fn().mockResolvedValue({
        success: true,
        tab_id: "tab-1-abc123", // String ID like real implementation
      }),
      create_pack_tab: jest.fn().mockResolvedValue({
        success: true,
        tab_id: "tab-2-def456",
      }),
      resolve_pack_session_lost: jest.fn().mockResolvedValue({ success: true }),
      close_tab: jest.fn().mockResolvedValue({ success: true }),
      get_tabs: jest.fn().mockResolvedValue({ success: true, tabs: [] }),
      switch_tab: jest.fn().mockResolvedValue({ success: true }),
      send_message: jest.fn().mockResolvedValue({ success: true }),
      stop_streaming: jest.fn().mockResolvedValue({ success: true }),
      get_conversation_state: jest
        .fn()
        .mockResolvedValue({ success: true, state: {} }),
      get_preferences: jest.fn().mockResolvedValue({
        success: true,
        preferences: { theme: "dark" },
      }),
      save_preferences: jest.fn().mockResolvedValue({ success: true }),
      get_models: jest.fn().mockResolvedValue({
        success: true,
        models: ["llama2", "codellama"],
      }),
      set_model: jest.fn().mockResolvedValue({ success: true }),
      get_mcp_tools: jest.fn().mockResolvedValue({ success: true, tools: [] }),
      get_mcp_config: jest
        .fn()
        .mockResolvedValue({ success: true, servers: {} }),
      add_mcp_server: jest.fn().mockResolvedValue({ success: true }),
      update_mcp_server: jest.fn().mockResolvedValue({ success: true }),
      remove_mcp_server: jest.fn().mockResolvedValue({ success: true }),
      toggle_mcp_tool: jest.fn().mockResolvedValue({ success: true }),
      test_mcp_connection: jest
        .fn()
        .mockResolvedValue({ success: true, tool_count: 5 }),
      reload_mcp_config: jest.fn().mockResolvedValue({ success: true }),
      call_mcp_tool_direct: jest
        .fn()
        .mockResolvedValue({ success: true, result: "{}" }),
      open_file_dialog_mcp: jest.fn().mockResolvedValue({ path: "/test/path" }),
      get_agent_skills_config: jest.fn().mockResolvedValue({
        success: true,
        enabled: true,
        directories: [],
      }),
      save_agent_skills_config: jest.fn().mockResolvedValue({ success: true }),
      refresh_skills: jest
        .fn()
        .mockResolvedValue({ success: true, skills: [] }),
      export_conversation: jest.fn().mockResolvedValue({ success: true }),
      compact_conversation: jest.fn().mockResolvedValue({ success: true }),
      truncate_conversation: jest.fn().mockResolvedValue({ success: true }),
      get_status_info: jest.fn().mockResolvedValue({
        success: true,
        char_count: 100,
        line_count: 10,
        input_tokens: 50,
        session_output_tokens: 100,
        token_estimate: 150,
        skill_count: 3,
      }),
      get_history: jest
        .fn()
        .mockResolvedValue({ success: true, conversations: [] }),
      revive_conversation: jest.fn().mockResolvedValue({ success: true }),
      delete_history_entry: jest.fn().mockResolvedValue({ success: true }),
      get_pending_js: jest.fn().mockResolvedValue([]),
      on_fold_rendered: jest.fn().mockResolvedValue({ success: true }),
    };

    // Set up window.pywebview
    window.pywebview = { api: mockPythonAPI };

    // Create instance
    api = new PythonAPI();
  });

  afterEach(() => {
    delete window.pywebview;
    delete window.pythonAPI;
  });

  describe("initialization", () => {
    it("should check for pywebview on construction", () => {
      // api.py should be the mock we set in window.pywebview.api
      expect(api.py).toBe(mockPythonAPI);
    });

    it("should have requestId counter starting at 0", () => {
      expect(api._requestId).toBe(0);
    });

    it("should have empty pending requests map", () => {
      expect(api._pendingRequests.size).toBe(0);
    });
  });

  describe("isAvailable()", () => {
    it("should return true when pywebview and expected method exist", () => {
      // get_pending_js is a jest.fn(), which is typeof === 'function'
      expect(api.isAvailable()).toBe(true);
    });

    it("should return false when pywebview is not available", () => {
      delete window.pywebview;
      api._checkForPywebview();
      expect(api.isAvailable()).toBe(false);
    });

    it("should return false when expected method is missing", () => {
      window.pywebview = {
        api: {
          other_method: jest.fn(),
          // get_pending_js is intentionally missing
        },
      };

      api._checkForPywebview();
      expect(api.isAvailable()).toBe(false);
    });

    it("should return false when get_pending_js is not a function", () => {
      window.pywebview = {
        api: {
          get_pending_js: "not a function", // String instead of function
        },
      };

      api._checkForPywebview();
      expect(api.isAvailable()).toBe(false);
    });
  });

  describe("call()", () => {
    it("should call Python method with arguments", async () => {
      const result = await api.call("create_tab", "Test Tab");

      expect(mockPythonAPI.create_tab).toHaveBeenCalledWith("Test Tab");
      expect(result).toEqual({
        success: true,
        tab_id: "tab-1-abc123",
      });
    });

    it("should throw error when Python API unavailable", async () => {
      delete window.pywebview;
      api._checkForPywebview();

      await expect(api.call("create_tab")).rejects.toThrow(
        "Python API not available",
      );
    });

    it("should throw error when method not found", async () => {
      await expect(api.call("nonexistent_method")).rejects.toThrow(
        "Method nonexistent_method not found",
      );
    });

    it("should propagate Python errors", async () => {
      mockPythonAPI.send_message.mockRejectedValueOnce(
        new Error("Network error"),
      );

      await expect(api.call("send_message", 1, "hello")).rejects.toThrow(
        "Network error",
      );
    });
  });

  describe("tab management", () => {
    it("create_tab() should call Python with title", async () => {
      const result = await api.create_tab("New Chat");

      expect(mockPythonAPI.create_tab).toHaveBeenCalledWith("New Chat");
      expect(result.tab_id).toBe("tab-1-abc123");
      expect(typeof result.tab_id).toBe("string");
    });

    it("create_tab() should pass through whatever title is provided", async () => {
      await api.create_tab();
      expect(mockPythonAPI.create_tab).toHaveBeenCalledWith("New Chat");

      await api.create_tab("Custom Title");
      expect(mockPythonAPI.create_tab).toHaveBeenCalledWith("Custom Title");
    });

    it("create_pack_tab() should call Python with host and title", async () => {
      const result = await api.create_pack_tab("user@host", "My Pack");

      expect(mockPythonAPI.create_pack_tab).toHaveBeenCalledWith(
        "user@host",
        "My Pack",
      );
      expect(result.success).toBe(true);
    });

    it("create_pack_tab() should default the title when omitted", async () => {
      await api.create_pack_tab("user@host");

      expect(mockPythonAPI.create_pack_tab).toHaveBeenCalledWith(
        "user@host",
        "Pack Tab",
      );
    });

    it("resolve_pack_session_lost() should call Python with tabId and the choice", async () => {
      const result = await api.resolve_pack_session_lost("tab-2-def456", true);

      expect(mockPythonAPI.resolve_pack_session_lost).toHaveBeenCalledWith(
        "tab-2-def456",
        true,
      );
      expect(result.success).toBe(true);
    });

    it("close_tab() should call Python with string tabId", async () => {
      const tabId = "tab-5-xyz789";
      await api.close_tab(tabId);

      expect(mockPythonAPI.close_tab).toHaveBeenCalledWith(tabId);

      const callArg = mockPythonAPI.close_tab.mock.calls[0][0];
      expect(typeof callArg).toBe("string");
    });

    it("get_tabs() should return tabs list", async () => {
      mockPythonAPI.get_tabs.mockResolvedValueOnce({
        success: true,
        tabs: [
          { id: "tab-1-aaa", title: "Chat 1" },
          { id: "tab-2-bbb", title: "Chat 2" },
        ],
      });

      const result = await api.get_tabs();

      expect(result.tabs).toHaveLength(2);
      expect(result.tabs[0].id).toMatch(/^tab-/);
    });

    it("switch_tab() should call Python with string tabId", async () => {
      const tabId = "tab-3-ccc123";
      await api.switch_tab(tabId);

      expect(mockPythonAPI.switch_tab).toHaveBeenCalledWith(tabId);
    });
  });

  describe("messaging", () => {
    it("send_message() should pass tabId, message, and images", async () => {
      const tabId = "tab-1-abc123";
      const images = ["data:image/png;base64,abc123"];

      await api.send_message(tabId, "Hello", images);

      expect(mockPythonAPI.send_message).toHaveBeenCalledWith(
        tabId,
        "Hello",
        images,
      );
    });

    it("send_message() should use empty images array by default", async () => {
      const tabId = "tab-1-abc123";
      await api.send_message(tabId, "Hello");

      expect(mockPythonAPI.send_message).toHaveBeenCalledWith(
        tabId,
        "Hello",
        [],
      );
    });

    it("stop_streaming() should call Python with string tabId", async () => {
      const tabId = "tab-1-abc123";
      await api.stop_streaming(tabId);

      expect(mockPythonAPI.stop_streaming).toHaveBeenCalledWith(tabId);
    });
  });

  describe("preferences", () => {
    it("get_preferences() should return preferences object", async () => {
      const result = await api.get_preferences();

      expect(result.preferences.theme).toBe("dark");
    });

    it("save_preferences() should send preferences to Python", async () => {
      const prefs = { theme: "light", font_size: 14 };
      await api.save_preferences(prefs);

      expect(mockPythonAPI.save_preferences).toHaveBeenCalledWith(prefs);
    });
  });

  describe("models", () => {
    it("get_models() should return model list", async () => {
      const result = await api.get_models();

      expect(result.models).toContain("llama2");
      expect(result.models).toContain("codellama");
    });

    it("set_model() should call Python with model name", async () => {
      await api.set_model("llama2");

      expect(mockPythonAPI.set_model).toHaveBeenCalledWith("llama2");
    });
  });

  describe("polling", () => {
    it("get_pending_js() should return pending updates", async () => {
      mockPythonAPI.get_pending_js.mockResolvedValueOnce([
        { type: "content", tab_id: "tab-1-abc", content: "Hello" },
      ]);

      const result = await api.get_pending_js();

      expect(result).toHaveLength(1);
      expect(result[0].tab_id).toMatch(/^tab-/);
    });

    it("get_pending_js() should handle errors gracefully", async () => {
      mockPythonAPI.get_pending_js.mockRejectedValueOnce(
        new Error("Poll failed"),
      );

      const result = await api.get_pending_js();

      expect(result).toEqual([]);
      expect(console.error).toHaveBeenCalled();
    });
  });
});
