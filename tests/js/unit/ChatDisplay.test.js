/**
 * ChatDisplay Unit Tests
 */

// Set up global mocks BEFORE requiring ChatDisplay
global.marked = {
  Renderer: jest.fn().mockImplementation(function () {
    this.code = jest.fn().mockReturnValue("<pre><code>code</code></pre>");
  }),
  parse: jest.fn().mockImplementation((text) => `<p>${text}</p>`),
  setOptions: jest.fn(),
};

global.DOMPurify = {
  sanitize: jest.fn().mockImplementation((html) => html),
  addHook: jest.fn(),
};

global.hljs = {
  highlight: jest.fn().mockReturnValue({ value: "<span>highlighted</span>" }),
  getLanguage: jest.fn().mockReturnValue(true),
};

global.renderMathInElement = jest.fn();

// Load the module - this populates window.ChatDisplay
require("../../../web/js/components/ChatDisplay.js");

describe("ChatDisplay", () => {
  let ChatDisplay;
  let chatDisplay;
  let container;

  beforeAll(() => {
    // Get the class from window after require() populated it
    ChatDisplay = window.ChatDisplay;

    // Verify the source file was modified correctly
    if (!ChatDisplay) {
      throw new Error(
        "window.ChatDisplay is undefined. " +
          'Add "window.ChatDisplay = ChatDisplay;" to the end of web/js/components/ChatDisplay.js',
      );
    }
  });

  beforeEach(() => {
    // Set up DOM
    document.body.innerHTML = '<div id="chat-container"></div>';
    container = document.getElementById("chat-container");

    chatDisplay = new ChatDisplay("chat-container");
  });

  afterEach(() => {
    document.body.innerHTML = "";
    jest.clearAllMocks();
  });

  describe("initialization", () => {
    it("should store container reference", () => {
      expect(chatDisplay.container).toBe(container);
    });

    it("should have empty answer buffers", () => {
      expect(chatDisplay.answerBuffers.size).toBe(0);
    });

    it("should have empty pending folds", () => {
      expect(chatDisplay.pendingFolds.size).toBe(0);
    });

    it("should have empty injected folds", () => {
      expect(chatDisplay.injectedFolds.size).toBe(0);
    });

    it("should not be streaming initially", () => {
      expect(chatDisplay.isStreaming).toBe(false);
    });

    it("should have current answer index of -1", () => {
      expect(chatDisplay.currentAnswerIndex).toBe(-1);
    });
  });

  describe("addQuestion", () => {
    it("should create question element", () => {
      chatDisplay.addQuestion("Hello?");

      const question = container.querySelector(".message.question");
      expect(question).not.toBeNull();
    });

    it("should set User role", () => {
      chatDisplay.addQuestion("Hello?");

      const role = container.querySelector(".message-role");
      expect(role.textContent).toBe("User");
    });

    it("should render question content", () => {
      chatDisplay.addQuestion("Hello?");

      const content = container.querySelector(".message-content");
      expect(content).not.toBeNull();
    });

    it("should render LaTeX in question content", () => {
      chatDisplay.addQuestion("Inline $x^2$ math");

      expect(renderMathInElement).toHaveBeenCalledWith(
        expect.any(HTMLElement),
        expect.objectContaining({ throwOnError: false }),
      );
    });

    it("should include images if provided", () => {
      chatDisplay.addQuestion("Hello?", ["data:image/png;base64,abc123"]);

      const images = container.querySelector(".message-images");
      expect(images).not.toBeNull();
    });
  });

  describe("appendContent", () => {
    it("should update current answer index", () => {
      chatDisplay.appendContent({
        type: "content",
        content: "Hello",
        answer_index: 5,
      });

      expect(chatDisplay.currentAnswerIndex).toBe(5);
    });

    it("should skip tool_call updates", () => {
      const consoleSpy = jest.spyOn(console, "log").mockImplementation();

      chatDisplay.appendContent({
        type: "tool_call",
        content: '{"tool": "test"}',
        answer_index: 0,
      });

      expect(chatDisplay.answerBuffers.size).toBe(0);
      consoleSpy.mockRestore();
    });

    it("should skip tool_result updates", () => {
      const consoleSpy = jest.spyOn(console, "log").mockImplementation();

      chatDisplay.appendContent({
        type: "tool_result",
        content: "Result",
        answer_index: 0,
      });

      expect(chatDisplay.answerBuffers.size).toBe(0);
      consoleSpy.mockRestore();
    });

    it("should handle progress updates", () => {
      chatDisplay.appendContent({
        type: "progress",
        content: "🔧 Working...",
        answer_index: 0,
      });

      const progress = container.querySelector(".progress-indicator");
      expect(progress).not.toBeNull();
    });

    it("should handle error updates", () => {
      chatDisplay.appendContent({
        type: "error",
        content: "Error occurred",
        answer_index: 0,
      });

      const error = container.querySelector(".error-message");
      expect(error).not.toBeNull();
      expect(error.textContent).toBe("Error occurred");
    });

    it("should create answer wrapper for content", () => {
      chatDisplay.appendContent({
        type: "content",
        content: "Hello world",
        answer_index: 0,
      });

      const wrapper = container.querySelector(".answer-wrapper");
      expect(wrapper).not.toBeNull();
    });

    it("should create answer header", () => {
      chatDisplay.appendContent({
        type: "content",
        content: "Hello world",
        answer_index: 0,
      });

      const header = container.querySelector(".answer-header");
      expect(header).not.toBeNull();
    });

    it("should set Assistant role", () => {
      chatDisplay.appendContent({
        type: "content",
        content: "Hello world",
        answer_index: 0,
      });

      const role = container.querySelector(".answer-role");
      expect(role.textContent).toBe("Assistant");
    });

    it("should create answer segment", () => {
      chatDisplay.appendContent({
        type: "content",
        content: "Hello world",
        answer_index: 0,
      });

      const segment = container.querySelector(".answer-segment");
      expect(segment).not.toBeNull();
    });
  });

  describe("appendToAnswerBuffer", () => {
    it("should create buffer for new answer index", () => {
      chatDisplay.appendToAnswerBuffer(0, "Hello", false);

      expect(chatDisplay.answerBuffers.has(0)).toBe(true);
    });

    it("should accumulate content in buffer", () => {
      chatDisplay.appendToAnswerBuffer(0, "Hello", false);
      chatDisplay.appendToAnswerBuffer(0, " World", false);

      const bufferData = chatDisplay.answerBuffers.get(0);
      expect(bufferData.buffer).toBe("Hello World");
    });

    it("should render content to DOM", () => {
      chatDisplay.appendToAnswerBuffer(0, "Hello world", true);

      const segment = container.querySelector(".answer-segment");
      expect(segment).not.toBeNull();
    });

    it("should render display and inline LaTeX in answers", () => {
      chatDisplay.appendToAnswerBuffer(
        0,
        "$$I \\propto \\frac{1}{\\lambda^4}$$ and \\(x^2\\)",
        true,
      );

      expect(renderMathInElement).toHaveBeenCalledWith(expect.any(HTMLElement), {
        delimiters: [
          { left: "$$", right: "$$", display: true },
          { left: "\\[", right: "\\]", display: true },
          { left: "\\(", right: "\\)", display: false },
          { left: "$", right: "$", display: false },
        ],
        throwOnError: false,
        strict: false,
      });
    });

    it("should retain unchanged rendered formula nodes across stream updates", () => {
      const formula = (text) => `
        <span class="math-wrapper">
          <span class="katex-display"><span class="katex">
            <annotation encoding="application/x-tex">${text}</annotation>
          </span></span>
        </span>`;
      const segment = document.createElement("div");
      segment.innerHTML = `<p>First ${formula("x^2")}</p>`;
      const originalWrapper = segment.querySelector(".math-wrapper");
      const next = document.createElement("div");
      next.innerHTML = `<p>First ${formula("x^2")} and more text</p>`;

      chatDisplay._updateRenderedContent(segment, next);

      expect(segment.querySelector(".math-wrapper")).toBe(originalWrapper);
      expect(segment.textContent).toContain("and more text");
    });

    it("should replace a formula node when its source changes", () => {
      const segment = document.createElement("div");
      segment.innerHTML =
        '<span class="old"><span class="katex"><annotation encoding="application/x-tex">x</annotation></span></span>';
      const oldWrapper = segment.querySelector(".old");
      const next = document.createElement("div");
      next.innerHTML =
        '<span class="new"><span class="katex"><annotation encoding="application/x-tex">x^2</annotation></span></span>';

      chatDisplay._updateRenderedContent(segment, next);

      expect(segment.querySelector(".old")).toBeNull();
      expect(segment.querySelector(".new")).not.toBe(oldWrapper);
    });

    it("should preserve bracketed math delimiters through Markdown rendering", () => {
      chatDisplay.appendToAnswerBuffer(0, "\\[x^2\\] and \\(y^2\\)", true);

      const segment = container.querySelector(".answer-segment");
      expect(segment.textContent).toContain("\\[x^2\\] and \\(y^2\\)");
    });

    it("should not alter math-like delimiters inside code", () => {
      const text = "`\\(inline\\)`\n```text\n\\[block\\]\n```";

      expect(chatDisplay._protectMath(text)).toEqual({
        protectedText: text,
        formulas: new Map(),
      });
    });

    it("should protect multiline display math before Markdown adds breaks", () => {
      const math = "$$\ne^{ix} = \\cos(x) + i\\sin(x)\n$$";
      const { protectedText, formulas } = chatDisplay._protectMath(math);

      expect(protectedText).toBe("ALPACA_MATH_TOKEN_0_END");
      expect(formulas.get(protectedText)).toBe(math);
    });

    it("should track last render length", () => {
      chatDisplay.appendToAnswerBuffer(0, "Hello world", true);

      const bufferData = chatDisplay.answerBuffers.get(0);
      expect(bufferData.lastRenderLength).toBe(11);
    });
  });

  describe("clear", () => {
    it("should clear container innerHTML", () => {
      chatDisplay.addQuestion("Test");
      chatDisplay.clear();

      expect(container.innerHTML).toBe("");
    });

    it("should clear answer buffers", () => {
      chatDisplay.appendToAnswerBuffer(0, "Hello", false);
      chatDisplay.clear();

      expect(chatDisplay.answerBuffers.size).toBe(0);
    });

    it("should clear pending folds", () => {
      chatDisplay.pendingFolds.set("test-key", { content: "test" });
      chatDisplay.clear();

      expect(chatDisplay.pendingFolds.size).toBe(0);
    });

    it("should clear injected folds", () => {
      chatDisplay.injectedFolds.set("test-key", { content: "test" });
      chatDisplay.clear();

      expect(chatDisplay.injectedFolds.size).toBe(0);
    });

    it("should reset current answer index", () => {
      chatDisplay.appendContent({
        type: "content",
        content: "Hello",
        answer_index: 5,
      });
      chatDisplay.clear();

      expect(chatDisplay.currentAnswerIndex).toBe(-1);
    });
  });

  describe("scrollToBottom", () => {
    it("should set scrollTop to scrollHeight", () => {
      // Mock scrollHeight since JSDOM doesn't fully support it
      Object.defineProperty(container, "scrollHeight", {
        value: 1000,
        writable: true,
      });

      chatDisplay.scrollToBottom();

      expect(container.scrollTop).toBe(1000);
    });
  });

  describe("setStreaming", () => {
    it("should set streaming state", () => {
      chatDisplay.setStreaming(true);

      expect(chatDisplay.isStreaming).toBe(true);
    });

    it("should clear streaming state", () => {
      chatDisplay.setStreaming(true);
      chatDisplay.setStreaming(false);

      expect(chatDisplay.isStreaming).toBe(false);
    });
  });

  describe("replaceProgressLine", () => {
    it("should create progress indicator", () => {
      chatDisplay.replaceProgressLine("Loading...", 0);

      const progress = container.querySelector(".progress-indicator");
      expect(progress).not.toBeNull();
    });

    it("should set progress content", () => {
      chatDisplay.replaceProgressLine("Loading...", 0);

      const progress = container.querySelector(".progress-indicator");
      expect(progress.textContent).toBe("Loading...");
    });

    it("should update existing progress indicator", () => {
      chatDisplay.replaceProgressLine("Loading...", 0);
      chatDisplay.replaceProgressLine("Still loading...", 0);

      const progress = container.querySelectorAll(".progress-indicator");
      expect(progress.length).toBe(1);
      expect(progress[0].textContent).toBe("Still loading...");
    });

    it("should remove progress indicator when content is empty", () => {
      chatDisplay.replaceProgressLine("Loading...", 0);
      chatDisplay.replaceProgressLine("", 0);

      const progress = container.querySelector(".progress-indicator");
      expect(progress).toBeNull();
    });
  });

  describe("showError", () => {
    it("should create error element", () => {
      chatDisplay.showError("Something went wrong", 0);

      const error = container.querySelector(".error-message");
      expect(error).not.toBeNull();
    });

    it("should set error message", () => {
      chatDisplay.showError("Something went wrong", 0);

      const error = container.querySelector(".error-message");
      expect(error.textContent).toBe("Something went wrong");
    });

    it("should apply error styling", () => {
      chatDisplay.showError("Something went wrong", 0);

      const error = container.querySelector(".error-message");
      expect(error.style.color).toContain("var(--error-color)");
    });
  });

  describe("storeFoldData", () => {
    it("should store fold in pendingFolds", () => {
      chatDisplay.storeFoldData(0, "Tool call content", "call", "tool-1");

      expect(chatDisplay.pendingFolds.size).toBeGreaterThan(0);
    });

    it("should generate deterministic fold ID", () => {
      chatDisplay.storeFoldData(0, "Tool call content", "call", "tool-1");

      const foldData = Array.from(chatDisplay.pendingFolds.values())[0];
      expect(foldData.id).toMatch(/^fold-call-0-/);
    });

    it("should avoid duplicates for same content", () => {
      chatDisplay.storeFoldData(0, "Same content", "call", "tool-1");
      chatDisplay.storeFoldData(0, "Same content", "call", "tool-1");

      expect(chatDisplay.pendingFolds.size).toBe(1);
    });

    it("should add placeholder to buffer if exists", () => {
      chatDisplay.appendToAnswerBuffer(0, "Hello", false);
      chatDisplay.storeFoldData(0, "Tool call", "call", "tool-1");

      const bufferData = chatDisplay.answerBuffers.get(0);
      expect(bufferData.buffer).toContain("FOLD_PLACEHOLDER");
    });
  });

  describe("setPreFoldsText", () => {
    it("should create buffer with text", () => {
      chatDisplay.setPreFoldsText(0, "Pre-fold text");

      expect(chatDisplay.answerBuffers.has(0)).toBe(true);
    });

    it("should not create buffer for empty text", () => {
      chatDisplay.setPreFoldsText(0, "");

      expect(chatDisplay.answerBuffers.has(0)).toBe(false);
    });
  });

  describe("_isNaturalBreakPoint", () => {
    it("should return true for double newline", () => {
      expect(chatDisplay._isNaturalBreakPoint("Hello\n\n")).toBe(true);
    });

    it("should return true for code fence", () => {
      expect(chatDisplay._isNaturalBreakPoint("Hello\n```")).toBe(true);
    });

    it("should return true for period space", () => {
      expect(chatDisplay._isNaturalBreakPoint("Hello. ")).toBe(true);
    });

    it("should return true for question space", () => {
      expect(chatDisplay._isNaturalBreakPoint("Hello? ")).toBe(true);
    });

    it("should return true for exclamation space", () => {
      expect(chatDisplay._isNaturalBreakPoint("Hello! ")).toBe(true);
    });

    it("should return false for other endings", () => {
      expect(chatDisplay._isNaturalBreakPoint("Hello")).toBe(false);
    });
  });

  describe("_closeOpenFences", () => {
    it("should close open code fences", () => {
      const result = chatDisplay._closeOpenFences("```javascript\ncode");

      expect(result).toContain("```");
      expect(result.endsWith("```")).toBe(true);
    });

    it("should handle multiple open fences", () => {
      const result = chatDisplay._closeOpenFences("```\n```javascript\ncode");

      expect(result).toContain("```");
    });

    it("should not modify closed fences", () => {
      const input = "```javascript\ncode\n```";
      const result = chatDisplay._closeOpenFences(input);

      expect(result).toBe(input);
    });
  });

  describe("_simpleHash", () => {
    it("should return consistent hash for same string", () => {
      const hash1 = chatDisplay._simpleHash("test");
      const hash2 = chatDisplay._simpleHash("test");

      expect(hash1).toBe(hash2);
    });

    it("should return different hashes for different strings", () => {
      const hash1 = chatDisplay._simpleHash("test1");
      const hash2 = chatDisplay._simpleHash("test2");

      expect(hash1).not.toBe(hash2);
    });

    it("should return positive hash", () => {
      const hash = chatDisplay._simpleHash("test");

      // Hash is base-36 string; verify it contains only valid base-36 chars (0-9, a-z)
      expect(hash).toMatch(/^[0-9a-z]+$/);
      // Verify it's a valid base-36 number (not negative, not empty)
      expect(parseInt(hash, 36)).toBeGreaterThanOrEqual(0);
    });
  });
});
