/**
 * UpdatePoller - JavaScript polling for UI updates from Python
 *
 * This implements Pattern C from the spec: JavaScript polls Python for pending
 * UI updates and executes them directly. This is the most portable approach
 * that avoids thread-safety concerns with evaluate_js.
 */
class UpdatePoller {
  constructor(api, intervalMs = 50) {
    this.api = api;
    this.intervalMs = intervalMs;
    this._running = false;
    this._pollPromise = null;
  }

  /**
   * Start the polling loop
   */
  start() {
    if (this._running) return;
    this._running = true;
    this._pollLoop();
  }

  /**
   * Stop the polling loop
   */
  stop() {
    this._running = false;
  }

  /**
   * Main polling loop
   */
  async _pollLoop() {
    while (this._running) {
      try {
        await this._pollOnce();
      } catch (e) {
        console.error("Polling error:", e);
      }

      // Wait before next poll
      await new Promise((resolve) => setTimeout(resolve, this.intervalMs));
    }
  }

  /**
   * Single poll iteration
   */
  async _pollOnce() {
    // Get pending updates from Python
    const updates = await this.api.get_pending_js();

    if (!updates || updates.length === 0) {
      return;
    }

    // Execute each update
    for (const jsCode of updates) {
      try {
        this._executeUpdate(jsCode);
      } catch (execError) {
        console.error("Failed to execute update:", execError, jsCode);
      }
    }
  }

  /**
   * Execute a JavaScript update
   */
  _executeUpdate(jsCode) {
    // Use Function constructor for cleaner scope than eval
    // This creates a function in global scope and executes it
    const fn = new Function(jsCode);
    fn.call(window);
  }
}

// Export for use in app.js
window.UpdatePoller = UpdatePoller;
