/**
 * InputArea - Handles user input, model selection, and message submission
 */
class InputArea {
  constructor(api) {
    this.api = api;

    // DOM elements
    this.inputContainer =
      document.getElementById("message-input").parentElement;
    this.sendBtn = document.getElementById("send-btn");
    this.stopBtn = document.getElementById("stop-btn");
    this.attachBtn = document.getElementById("attach-btn");
    this.modelSelect = document.getElementById("toolbar-model");
    this.statusEl = document.getElementById("input-status");

    // Image attachment strip elements
    this.imageStrip = document.getElementById("image-attachment-strip");
    this.imageCountLabel = document.getElementById("image-count-label");
    this.clearImagesBtn = document.getElementById("clear-images-btn");

    // Replace textarea with markdown input
    this.markdownInput = null;
    this._initMarkdownInput();

    // State
    this.isStreaming = false;
    this.isConnected = false;
    this.attachedImages = [];
    this.onSendCallback = null;
    this.onStopCallback = null;
    this._connectionIndicator = document.getElementById("connection-indicator");

    this._bindEvents();
  }

  /**
   * Initialize the markdown input component
   */
  _initMarkdownInput() {
    // Remove the old textarea
    const oldTextarea = document.getElementById("message-input");
    if (oldTextarea) {
      oldTextarea.remove();
    }

    // Create a container for the markdown input
    const mdContainer = document.createElement("div");
    mdContainer.id = "markdown-input-container";
    this.inputContainer.insertBefore(mdContainer, this.statusEl);

    // Initialize the markdown input
    this.markdownInput = new MarkdownInput("markdown-input-container", {
      placeholder: "Type your message... (Ctrl+Enter to send)",
      onSend: (text) => {
        if (this.onSendCallback) {
          this.onSendCallback(text, [...this.attachedImages]);
        }
        this.markdownInput.clear();
        this.attachedImages = [];
        this._updateAttachButton();
      },
    });
  }

  /**
   * Bind event handlers
   */
  _bindEvents() {
    // Send button click
    this.sendBtn.addEventListener("click", () => {
      this._sendMessage();
    });

    // Stop button click
    this.stopBtn.addEventListener("click", () => {
      if (this.onStopCallback) {
        this.onStopCallback();
      }
    });

    // Attach button - open file dialog for images
    this.attachBtn.addEventListener("click", () => {
      this._attachImages();
    });

    // Clear images button
    if (this.clearImagesBtn) {
      this.clearImagesBtn.addEventListener("click", () => {
        this._clearAttachedImages();
      });
    }

    // Model selection change
    this.modelSelect.addEventListener("change", async () => {
      const model = this.modelSelect.value;
      if (model) {
        await this.api.set_model(model);
      }
    });
  }

  /**
   * Send the current message
   */
  _sendMessage() {
    const text = this.markdownInput.getValue().trim();
    if (!text) return;

    // Note: We don't check isStreaming here - that's the caller's responsibility
    // to know if the current tab is streaming or not

    // Clear input
    this.markdownInput.clear();

    // Call send callback
    if (this.onSendCallback) {
      this.onSendCallback(text, [...this.attachedImages]);
    }

    // Clear attached images
    this.attachedImages = [];
    this._updateAttachButton();
  }

  /**
   * Attach images - opens native file dialog and adds selected image to pending list
   */
  async _attachImages() {
    // Don't allow attaching while streaming
    if (this.isStreaming) {
      this.setStatus("Cannot attach images while streaming");
      setTimeout(() => this.setStatus(""), 3000);
      return;
    }

    try {
      const result = await this.api.attach_image();

      // User cancelled the dialog
      if (result.cancelled) {
        return;
      }

      if (!result.success) {
        this.setStatus(`Error: ${result.error || "Failed to attach image"}`);
        setTimeout(() => this.setStatus(""), 3000);
        return;
      }

      // Add the image to the pending list
      this.attachedImages.push({
        data: result.data,
        mime_type: result.mime_type,
        filename: result.filename,
      });

      this._updateAttachButton();
      this.setStatus(`Attached: ${result.filename}`);
      setTimeout(() => this.setStatus(""), 3000);
    } catch (e) {
      console.error("Error attaching image:", e);
      this.setStatus("Error attaching image");
      setTimeout(() => this.setStatus(""), 3000);
    }
  }

  /**
   * Clear all attached images
   */
  _clearAttachedImages() {
    this.attachedImages = [];
    this._updateAttachButton();
    this.setStatus("Images cleared");
    setTimeout(() => this.setStatus(""), 2000);
  }

  /**
   * Set the send callback
   */
  onSend(callback) {
    this.onSendCallback = callback;
  }

  /**
   * Set the stop callback
   */
  onStop(callback) {
    this.onStopCallback = callback;
  }

  /**
   * Set streaming state
   */
  setConnectionStatus(connected) {
    this.isConnected = connected;
    if (this._connectionIndicator) {
      this._connectionIndicator.classList.remove(
        "connection-ok",
        "connection-error",
        "connection-checking",
      );
      this._connectionIndicator.classList.add(
        connected ? "connection-ok" : "connection-error",
      );
      this._connectionIndicator.title = connected
        ? "API connected"
        : "API unreachable — check the proxy is running";
    }
    if (!this.isStreaming) {
      this.sendBtn.disabled = !connected;
    }
  }

  setStreaming(isStreaming) {
    this.isStreaming = isStreaming;
    this.sendBtn.disabled = isStreaming || !this.isConnected;
    this.stopBtn.disabled = !isStreaming;
    this.markdownInput.setDisabled(isStreaming);

    if (isStreaming) {
      this.markdownInput.setPlaceholder("Streaming...");
    } else {
      this.markdownInput.setPlaceholder(
        "Type your message... (Ctrl+Enter to send)",
      );
    }
  }

  /**
   * Set status message
   */
  setStatus(message) {
    this.statusEl.textContent = message;
  }

  /**
   * Update attach button state and image strip visibility
   */
  _updateAttachButton() {
    // Update attach button text
    if (this.attachedImages.length > 0) {
      this.attachBtn.textContent = `📎 ${this.attachedImages.length}`;
    } else {
      this.attachBtn.textContent = "📎";
    }

    // Update image attachment strip
    if (this.imageStrip && this.imageCountLabel) {
      if (this.attachedImages.length > 0) {
        this.imageCountLabel.textContent = `📎 ${this.attachedImages.length} image(s)`;
        this.imageStrip.classList.remove("hidden");
      } else {
        this.imageStrip.classList.add("hidden");
      }
    }
  }

  /**
   * Populate model dropdown
   */
  async loadModels() {
    try {
      const result = await this.api.get_models();
      this.setConnectionStatus(result.connected !== false);
      if (result.success) {
        this._populateModels(result.models);
      }
    } catch (e) {
      console.error("Failed to load models:", e);
      this.setConnectionStatus(false);
    }
  }

  /**
   * Populate model select dropdown
   */
  _populateModels(models) {
    const currentValue = this.modelSelect.value;

    this.modelSelect.innerHTML = "";

    if (models.length === 0) {
      const option = document.createElement("option");
      option.textContent = "No models available";
      option.value = "";
      this.modelSelect.appendChild(option);
      return;
    }

    for (const model of models) {
      const option = document.createElement("option");
      option.value = model;
      option.textContent = model;
      this.modelSelect.appendChild(option);
    }

    // Restore selection if possible
    if (currentValue && models.includes(currentValue)) {
      this.modelSelect.value = currentValue;
    }
  }

  /**
   * Set selected model
   */
  setSelectedModel(model) {
    if (model && this.modelSelect.querySelector(`option[value="${model}"]`)) {
      this.modelSelect.value = model;
    }
  }

  /**
   * Focus the input
   */
  focus() {
    this.markdownInput.focus();
  }

  /**
   * Get input value
   */
  getValue() {
    return this.markdownInput.getValue();
  }

  /**
   * Set input value
   */
  setValue(text) {
    this.markdownInput.setValue(text);
  }

  /**
   * Clear input
   */
  clear() {
    this.markdownInput.clear();
    this.attachedImages = [];
    this._updateAttachButton();
  }
}

// Export for use in app.js
window.InputArea = InputArea;
