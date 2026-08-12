/**
 * Helper utilities
 */
const Helpers = {
  /**
   * Copy text to clipboard.
   *
   * Prefers the pywebview API bridge (GTK clipboard on the Python side) —
   * WebKit2GTK's navigator.clipboard permission handling isn't implemented
   * by pywebview, so that browser API silently rejects in this app. Falls
   * back to navigator.clipboard for contexts without the bridge (e.g. the
   * frontend opened directly in a regular browser during development).
   */
  async copyToClipboard(text) {
    if (window.pywebview?.api?.copy_to_clipboard) {
      try {
        const result = await window.pywebview.api.copy_to_clipboard(text);
        if (result?.success) return true;
        console.error("pywebview clipboard copy failed:", result?.error);
      } catch (err) {
        console.error("pywebview clipboard bridge threw:", err);
      }
    }
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch (err) {
      console.error("Failed to copy:", err);
      return false;
    }
  },
};

// Export
window.Helpers = Helpers;
