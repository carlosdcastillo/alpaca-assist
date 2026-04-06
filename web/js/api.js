/**
 * Python API Bridge
 * Handles all communication between JavaScript and Python via PyWebView's js_api
 */
class PythonAPI {
  constructor() {
    // In PyWebView, the python object is injected into the window
    // It may take a moment to be available after page load
    this._checkForPywebview();
    this._requestId = 0;
    this._pendingRequests = new Map();
  }

  /**
   * Check for pywebview object in window
   */
  _checkForPywebview() {
    // pywebview injects the API as window.pywebview.api
    if (window.pywebview && window.pywebview.api) {
      this.py = window.pywebview.api;
    } else if (window.pywebview) {
      // Fallback - try direct access
      this.py = window.pywebview;
    } else {
      this.py = null;
    }
  }

  /**
   * Check if Python API is available
   */
  isAvailable() {
    if (!this.py) {
      this._checkForPywebview();
    }
    // Also verify at least one expected method exists
    return this.py !== null && typeof this.py.get_pending_js === "function";
  }

  /**
   * Call a Python method
   */
  async call(method, ...args) {
    if (!this.py) {
      throw new Error("Python API not available");
    }

    if (typeof this.py[method] !== "function") {
      throw new Error(`Method ${method} not found on Python API`);
    }

    try {
      const result = await this.py[method](...args);
      return result;
    } catch (error) {
      console.error(`Error calling ${method}:`, error);
      throw error;
    }
  }

  // =======================================================================
  // Tab Management
  // =======================================================================

  async create_tab(title = "New Chat") {
    return this.call("create_tab", title);
  }

  async save_and_close() {
    return this.call("save_and_close");
  }

  async close_tab(tabId) {
    return this.call("close_tab", tabId);
  }

  async get_tabs() {
    return this.call("get_tabs");
  }

  async switch_tab(tabId) {
    return this.call("switch_tab", tabId);
  }

  // =======================================================================
  // Messaging
  // =======================================================================

  async send_message(tabId, message, images = []) {
    return this.call("send_message", tabId, message, images);
  }

  async attach_image() {
    return this.call("attach_image");
  }

  async stop_streaming(tabId) {
    return this.call("stop_streaming", tabId);
  }

  // =======================================================================
  // Conversation State
  // =======================================================================

  async get_conversation_state(tabId) {
    return this.call("get_conversation_state", tabId);
  }

  // =======================================================================
  // Preferences
  // =======================================================================

  async get_preferences() {
    return this.call("get_preferences");
  }

  async save_preferences(preferences) {
    return this.call("save_preferences", preferences);
  }

  // =======================================================================
  // Models
  // =======================================================================

  async get_models() {
    return this.call("get_models");
  }

  async set_model(model) {
    return this.call("set_model", model);
  }

  // =======================================================================
  // MCP Tools
  // =======================================================================

  async get_mcp_tools() {
    return this.call("get_mcp_tools");
  }

  // =======================================================================
  // MCP Config
  // =======================================================================

  async get_mcp_config() {
    return this.call("get_mcp_config");
  }

  async add_mcp_server(name, commandStr, argsStr) {
    return this.call("add_mcp_server", name, commandStr, argsStr);
  }

  async update_mcp_server(oldName, newName, commandStr, argsStr) {
    return this.call(
      "update_mcp_server",
      oldName,
      newName,
      commandStr,
      argsStr,
    );
  }

  async remove_mcp_server(name) {
    return this.call("remove_mcp_server", name);
  }

  async toggle_mcp_tool(serverName, toolName, enabled) {
    return this.call("toggle_mcp_tool", serverName, toolName, enabled);
  }

  async test_mcp_connection(commandStr, argsStr) {
    return this.call("test_mcp_connection", commandStr, argsStr);
  }

  async reload_mcp_config() {
    return this.call("reload_mcp_config");
  }

  async call_mcp_tool_direct(serverName, toolName, argumentsJson) {
    return this.call(
      "call_mcp_tool_direct",
      serverName,
      toolName,
      argumentsJson,
    );
  }

  async open_file_dialog_mcp() {
    return this.call("open_file_dialog_mcp");
  }

  // =======================================================================
  // Agent Skills
  // =======================================================================

  async get_agent_skills_config() {
    return this.call("get_agent_skills_config");
  }

  async save_agent_skills_config(config) {
    return this.call("save_agent_skills_config", config);
  }

  async refresh_skills() {
    return this.call("refresh_skills");
  }

  // =======================================================================
  // Actions
  // =======================================================================

  async export_conversation(tabId) {
    return this.call("export_conversation", tabId);
  }

  async compact_conversation(tabId) {
    return this.call("compact_conversation", tabId);
  }

  async truncate_conversation(tabId) {
    return this.call("truncate_conversation", tabId);
  }

  async pop_conversation(tabId) {
    return this.call("pop_conversation", tabId);
  }

  async perform_handoff(tabId) {
    return this.call("perform_handoff", tabId);
  }

  async clone_conversation(tabId) {
    return this.call("clone_conversation", tabId);
  }

  async navigate_to_tab(tabId) {
    return this.call("navigate_to_tab", tabId);
  }

  async navigate_to_conv(convId) {
    return this.call("navigate_to_conv", convId);
  }

  async navigate_qa(tabId, direction) {
    return this.call("navigate_qa", tabId, direction);
  }

  async fork_conversation(tabId, nodeId, newQuestion) {
    return this.call("fork_conversation", tabId, nodeId, newQuestion);
  }

  async regenerate_answer(tabId, nodeId) {
    return this.call("regenerate_answer", tabId, nodeId);
  }

  async edit_question(tabId, nodeId, newText) {
    return this.call("edit_question", tabId, nodeId, newText);
  }

  async navigate_sibling(tabId, nodeId, direction) {
    return this.call("navigate_sibling", tabId, nodeId, direction);
  }

  // =======================================================================
  // Status Bar
  // =======================================================================

  async get_status_info(tabId) {
    return this.call("get_status_info", tabId);
  }

  // =======================================================================
  // Conversation History
  // =======================================================================

  async get_history(searchTerm = "") {
    return this.call("get_history", searchTerm);
  }

  async revive_conversation(convId) {
    return this.call("revive_conversation", convId);
  }

  async delete_history_entry(convId) {
    return this.call("delete_history_entry", convId);
  }

  // =======================================================================
  // Polling for UI Updates (Pattern C)
  // =======================================================================

  async get_pending_js() {
    if (!this.py) return [];
    try {
      return await this.py.get_pending_js();
    } catch (e) {
      console.error("Error polling for updates:", e);
      return [];
    }
  }

  // =======================================================================
  // Fold Rendering Confirmation
  // =======================================================================

  async on_fold_rendered(tabId, foldId) {
    return this.call("on_fold_rendered", tabId, foldId);
  }
}

// Create global API instance
window.pythonAPI = new PythonAPI();

// Export class for testing
window.PythonAPI = PythonAPI;
