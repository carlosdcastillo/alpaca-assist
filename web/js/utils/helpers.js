/**
 * Helper utilities
 */
const Helpers = {
  /**
   * Debounce function calls
   */
  debounce(fn, ms = 100) {
    let timeout;
    return (...args) => {
      clearTimeout(timeout);
      timeout = setTimeout(() => fn(...args), ms);
    };
  },

  /**
   * Throttle function calls
   */
  throttle(fn, ms = 100) {
    let lastTime = 0;
    return (...args) => {
      const now = Date.now();
      if (now - lastTime >= ms) {
        lastTime = now;
        fn(...args);
      }
    };
  },

  /**
   * Generate a unique ID
   */
  generateId(prefix = "id") {
    return `${prefix}-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
  },

  /**
   * Format bytes to human-readable string
   */
  formatBytes(bytes, decimals = 2) {
    if (bytes === 0) return "0 Bytes";

    const k = 1024;
    const dm = decimals < 0 ? 0 : decimals;
    const sizes = ["Bytes", "KB", "MB", "GB", "TB"];

    const i = Math.floor(Math.log(bytes) / Math.log(k));

    return parseFloat((bytes / Math.pow(k, i)).toFixed(dm)) + " " + sizes[i];
  },

  /**
   * Format a timestamp
   */
  formatTime(date) {
    if (typeof date === "string") {
      date = new Date(date);
    }
    return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  },

  /**
   * Format a date
   */
  formatDate(date) {
    if (typeof date === "string") {
      date = new Date(date);
    }
    return date.toLocaleDateString([], {
      year: "numeric",
      month: "short",
      day: "numeric",
    });
  },

  /**
   * Copy text to clipboard
   */
  async copyToClipboard(text) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch (err) {
      console.error("Failed to copy:", err);
      return false;
    }
  },

  /**
   * Escape HTML special characters
   */
  escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
  },

  /**
   * Truncate text with ellipsis
   */
  truncate(text, maxLength = 100) {
    if (text.length <= maxLength) return text;
    return text.slice(0, maxLength - 3) + "...";
  },

  /**
   * Wait for a specified duration
   */
  sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  },

  /**
   * Retry an async operation with exponential backoff
   */
  async retry(fn, maxAttempts = 3, baseDelay = 1000) {
    for (let attempt = 1; attempt <= maxAttempts; attempt++) {
      try {
        return await fn();
      } catch (err) {
        if (attempt === maxAttempts) throw err;
        const delay = baseDelay * Math.pow(2, attempt - 1);
        await this.sleep(delay);
      }
    }
  },
};

// Export
window.Helpers = Helpers;
