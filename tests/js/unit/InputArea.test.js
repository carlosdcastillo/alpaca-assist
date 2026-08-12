/**
 * InputArea Unit Tests
 */

// Mock MarkdownInput before loading InputArea
class MockMarkdownInput {
  constructor(containerId, options) {
    this.options = options;
    this.value = "";
    this.disabled = false;
    this.placeholder = options?.placeholder || "";
    this.container = document.createElement("div");
  }

  getValue() {
    return this.value;
  }

  setValue(text) {
    this.value = text;
  }

  clear() {
    this.value = "";
  }

  setDisabled(disabled) {
    this.disabled = disabled;
  }

  setPlaceholder(text) {
    this.placeholder = text;
  }

  focus() {
    // Mock focus
  }
}

// Set up mock before requiring InputArea
window.MarkdownInput = MockMarkdownInput;

// Load the module - this populates window.InputArea
require("../../../web/js/components/InputArea.js");

describe("InputArea", () => {
  let InputArea;
  let mockApi;
  let inputArea;

  beforeAll(() => {
    // Get the class from window after require() populated it
    InputArea = window.InputArea;

    // Verify the source file was modified correctly
    if (!InputArea) {
      throw new Error(
        "window.InputArea is undefined. " +
          'Add "window.InputArea = InputArea;" to the end of web/js/components/InputArea.js',
      );
    }
  });

  beforeEach(() => {
    // Set up DOM
    document.body.innerHTML = `
      <div id="input-container">
        <div id="markdown-input-container"></div>
        <div id="input-status"></div>
      </div>
      <button id="send-btn"></button>
      <button id="stop-btn"></button>
      <button id="attach-btn"></button>
      <select id="toolbar-model"></select>
    `;

    mockApi = {
      get_models: jest.fn(),
      set_model: jest.fn(),
    };

    inputArea = new InputArea(mockApi);
  });

  afterEach(() => {
    document.body.innerHTML = "";
  });

  describe("initialization", () => {
    it("should store api reference", () => {
      expect(inputArea.api).toBe(mockApi);
    });

    it("should initialize markdown input", () => {
      expect(inputArea.markdownInput).not.toBeNull();
    });

    it("should not be streaming initially", () => {
      expect(inputArea.isStreaming).toBe(false);
    });

    it("should have empty attached images", () => {
      expect(inputArea.attachedImages).toHaveLength(0);
    });
  });

  describe("callbacks", () => {
    it("should set send callback", () => {
      const callback = jest.fn();
      inputArea.onSend(callback);

      expect(inputArea.onSendCallback).toBe(callback);
    });

    it("should set stop callback", () => {
      const callback = jest.fn();
      inputArea.onStop(callback);

      expect(inputArea.onStopCallback).toBe(callback);
    });
  });

  describe("streaming state", () => {
    it("setStreaming(true) should disable send button", () => {
      inputArea.setStreaming(true);

      expect(inputArea.sendBtn.disabled).toBe(true);
    });

    it("setStreaming(true) should enable stop button", () => {
      inputArea.setStreaming(true);

      expect(inputArea.stopBtn.disabled).toBe(false);
    });

    it("setStreaming(false) should enable send button when connected", () => {
      inputArea.setConnectionStatus(true); // must be connected to allow sends
      inputArea.setStreaming(true);
      inputArea.setStreaming(false);

      expect(inputArea.sendBtn.disabled).toBe(false);
    });

    it("setStreaming(false) should disable stop button", () => {
      inputArea.setStreaming(false);

      expect(inputArea.stopBtn.disabled).toBe(true);
    });

    it("should update markdown input disabled state", () => {
      inputArea.setStreaming(true);

      expect(inputArea.markdownInput.disabled).toBe(true);
    });

    it("should update placeholder when streaming", () => {
      inputArea.setStreaming(true);

      expect(inputArea.markdownInput.placeholder).toBe("Streaming...");
    });

    it("should restore placeholder when not streaming", () => {
      inputArea.setStreaming(true);
      inputArea.setStreaming(false);

      expect(inputArea.markdownInput.placeholder).toContain(
        "Type your message",
      );
    });
  });

  describe("status messages", () => {
    it("setStatus should update status element", () => {
      inputArea.setStatus("Test message");

      expect(inputArea.statusEl.textContent).toBe("Test message");
    });
  });

  describe("model loading", () => {
    it("loadModels should call API", async () => {
      mockApi.get_models.mockResolvedValue({
        success: true,
        models: ["llama2", "codellama"],
      });

      await inputArea.loadModels();

      expect(mockApi.get_models).toHaveBeenCalled();
    });

    it("should populate dropdown on success", async () => {
      mockApi.get_models.mockResolvedValue({
        success: true,
        models: ["llama2", "codellama"],
      });

      await inputArea.loadModels();

      const options = inputArea.modelSelect.querySelectorAll("option");
      expect(options.length).toBe(2);
    });

    it('should show "No models available" when list is empty', async () => {
      mockApi.get_models.mockResolvedValue({
        success: true,
        models: [],
      });

      await inputArea.loadModels();

      const option = inputArea.modelSelect.querySelector("option");
      expect(option.textContent).toBe("No models available");
    });

    it("should restore previous selection if available", async () => {
      // First load with model
      mockApi.get_models.mockResolvedValue({
        success: true,
        models: ["llama2", "codellama"],
      });
      await inputArea.loadModels();
      inputArea.modelSelect.value = "llama2";

      // Second load with same models
      await inputArea.loadModels();

      expect(inputArea.modelSelect.value).toBe("llama2");
    });
  });

  describe("model selection", () => {
    it("setSelectedModel should set dropdown value", () => {
      // Add option first
      const option = document.createElement("option");
      option.value = "llama2";
      inputArea.modelSelect.appendChild(option);

      inputArea.setSelectedModel("llama2");

      expect(inputArea.modelSelect.value).toBe("llama2");
    });

    it("should not set value for non-existent model", () => {
      inputArea.modelSelect.innerHTML = "";
      const option = document.createElement("option");
      option.value = "other-model";
      inputArea.modelSelect.appendChild(option);

      inputArea.setSelectedModel("llama2");

      expect(inputArea.modelSelect.value).not.toBe("llama2");
    });
  });

  describe("input operations", () => {
    it("getValue should return markdown input value", () => {
      inputArea.markdownInput.value = "test message";

      expect(inputArea.getValue()).toBe("test message");
    });

    it("setValue should set markdown input value", () => {
      inputArea.setValue("test message");

      expect(inputArea.markdownInput.value).toBe("test message");
    });

    it("clear should clear markdown input", () => {
      inputArea.markdownInput.value = "test";
      inputArea.clear();

      expect(inputArea.markdownInput.value).toBe("");
    });

    it("clear should clear attached images", () => {
      inputArea.attachedImages = ["image1.png"];
      inputArea.clear();

      expect(inputArea.attachedImages).toHaveLength(0);
    });

    it("focus should call markdownInput.focus", () => {
      const focusSpy = jest.spyOn(inputArea.markdownInput, "focus");
      inputArea.focus();

      expect(focusSpy).toHaveBeenCalled();
    });
  });

  describe("send button", () => {
    it("should trigger send callback with text and images", () => {
      const sendCallback = jest.fn();
      inputArea.onSend(sendCallback);
      inputArea.markdownInput.value = "Hello world";
      inputArea.attachedImages = ["image1.png"];

      inputArea.sendBtn.click();

      expect(sendCallback).toHaveBeenCalledWith("Hello world", ["image1.png"]);
    });

    it("should not send empty messages", () => {
      const sendCallback = jest.fn();
      inputArea.onSend(sendCallback);
      inputArea.markdownInput.value = "   ";

      inputArea.sendBtn.click();

      expect(sendCallback).not.toHaveBeenCalled();
    });

    it("should clear input after send", () => {
      inputArea.onSend(() => {});
      inputArea.markdownInput.value = "Hello";

      inputArea.sendBtn.click();

      expect(inputArea.markdownInput.value).toBe("");
    });

    it("should clear images after send", () => {
      inputArea.onSend(() => {});
      inputArea.markdownInput.value = "Hello";
      inputArea.attachedImages = ["image1.png"];

      inputArea.sendBtn.click();

      expect(inputArea.attachedImages).toHaveLength(0);
    });
  });

  describe("stop button", () => {
    it("should trigger stop callback", () => {
      const stopCallback = jest.fn();
      inputArea.onStop(stopCallback);

      inputArea.stopBtn.click();

      expect(stopCallback).toHaveBeenCalled();
    });
  });
});
