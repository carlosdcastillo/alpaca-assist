/**
 * ChatDisplay time-awareness rendering tests.
 *
 * Covers the three chat-surface affordances: the clock on message headers, the
 * divider marking a pause between sessions, and the turn duration chip with
 * its model/tools proportion bar.
 */

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

require("../../../web/js/utils/imageResult.js");
require("../../../web/js/utils/timeFormat.js");
require("../../../web/js/components/ChatDisplay.js");

describe("ChatDisplay time awareness", () => {
  let chatDisplay;
  let container;

  const NOW = Math.floor(Date.now() / 1000);

  beforeEach(() => {
    document.body.innerHTML = '<div id="chat-container"></div>';
    container = document.getElementById("chat-container");
    chatDisplay = new window.ChatDisplay("chat-container");
  });

  afterEach(() => {
    chatDisplay.stopAnswerTimer();
  });

  describe("question timestamps", () => {
    test("a timestamp renders a clock in the header", () => {
      chatDisplay.addQuestion("hello", [], { timestamp: NOW });

      const time = container.querySelector(".message-time");
      expect(time).not.toBeNull();
      expect(time.textContent).not.toBe("");
      expect(time.title).not.toBe("");
    });

    test("no timestamp leaves the header exactly as it was", () => {
      chatDisplay.addQuestion("hello");

      expect(container.querySelector(".message-time")).toBeNull();
      expect(container.querySelector(".message-role").textContent).toBe("User");
    });
  });

  describe("gap dividers", () => {
    test("a long pause inserts a divider before the question", () => {
      chatDisplay.addQuestion("back again", [], {
        timestamp: NOW,
        previousEnd: NOW - 7200,
      });

      const gap = container.querySelector(".time-gap");
      expect(gap).not.toBeNull();
      expect(gap.textContent).toBe("2 hours later");
      // Must precede the message it introduces.
      expect(gap.nextElementSibling.classList.contains("question")).toBe(true);
    });

    test("a prompt follow-up gets no divider", () => {
      chatDisplay.addQuestion("and another thing", [], {
        timestamp: NOW,
        previousEnd: NOW - 30,
      });

      expect(container.querySelector(".time-gap")).toBeNull();
    });

    test("the boundary is the shared threshold", () => {
      const threshold = window.TimeFormat.GAP_THRESHOLD_SECONDS;
      chatDisplay.addQuestion("under", [], {
        timestamp: NOW,
        previousEnd: NOW - (threshold - 1),
      });
      expect(container.querySelectorAll(".time-gap")).toHaveLength(0);

      chatDisplay.addQuestion("over", [], {
        timestamp: NOW,
        previousEnd: NOW - (threshold + 1),
      });
      expect(container.querySelectorAll(".time-gap")).toHaveLength(1);
    });

    test("the first question of a conversation has nothing to measure", () => {
      chatDisplay.addQuestion("first ever", [], { timestamp: NOW });
      expect(container.querySelector(".time-gap")).toBeNull();
    });
  });

  describe("turn duration", () => {
    const timing = {
      wall_ms: 47000,
      llm_ms: 12000,
      tool_ms: 31000,
      invocations: 4,
      tool_count: 3,
    };

    test("the duration lands in the answer header with a breakdown tooltip", () => {
      chatDisplay.appendToAnswerBuffer(0, "answer text", true);
      chatDisplay.setAnswerTiming(0, timing);

      const el = container.querySelector(".answer-time");
      expect(el.textContent).toBe("47.0s");
      expect(el.title).toContain("model 12.0s");
      expect(el.title).toContain("tools 31.0s");
    });

    test("timing known before the header exists is applied when it appears", () => {
      // The re-render path hands over timing before any content is appended.
      chatDisplay.setAnswerTiming(0, timing);
      expect(container.querySelector(".answer-time")).toBeNull();

      chatDisplay.appendToAnswerBuffer(0, "answer text", true);

      expect(container.querySelector(".answer-time").textContent).toBe("47.0s");
    });

    test("the split bar is proportional to model, tools, and overhead", () => {
      chatDisplay.appendToAnswerBuffer(0, "answer text", true);
      chatDisplay.setAnswerTiming(0, timing);

      const segments = container.querySelectorAll(".turn-split-seg");
      expect(segments).toHaveLength(3);
      expect(segments[0].style.width).toBe(`${(12000 / 47000) * 100}%`);
      expect(segments[1].style.width).toBe(`${(31000 / 47000) * 100}%`);
      expect(segments[2].style.width).toBe(`${(4000 / 47000) * 100}%`);
    });

    test("a segment with no time is omitted rather than drawn at zero width", () => {
      chatDisplay.appendToAnswerBuffer(0, "answer text", true);
      chatDisplay.setAnswerTiming(0, {
        wall_ms: 2000,
        llm_ms: 2000,
        tool_ms: 0,
        invocations: 1,
      });

      const segments = container.querySelectorAll(".turn-split-seg");
      expect(segments).toHaveLength(1);
      expect(segments[0].classList.contains("turn-split-seg--llm")).toBe(true);
    });

    test("sub-second turns get no bar", () => {
      chatDisplay.appendToAnswerBuffer(0, "quick", true);
      chatDisplay.setAnswerTiming(0, { wall_ms: 400, llm_ms: 400 });

      expect(container.querySelector(".turn-split")).toBeNull();
      expect(container.querySelector(".answer-time").textContent).toBe("400ms");
    });

    test("re-applying timing does not stack duplicate bars", () => {
      chatDisplay.appendToAnswerBuffer(0, "answer text", true);
      chatDisplay.setAnswerTiming(0, timing);
      chatDisplay.setAnswerTiming(0, timing);

      expect(container.querySelectorAll(".turn-split")).toHaveLength(1);
    });
  });

  describe("live turn stopwatch", () => {
    beforeEach(() => jest.useFakeTimers());
    afterEach(() => jest.useRealTimers());

    test("counts up in the answer header while a turn runs", () => {
      chatDisplay.appendToAnswerBuffer(0, "streaming...", false);
      chatDisplay.startAnswerTimer(0);

      jest.advanceTimersByTime(3000);

      const el = container.querySelector(".answer-time");
      expect(el.textContent).toBe("0:03");
      expect(el.classList.contains("answer-time--live")).toBe(true);
    });

    test("a tool-loop continuation does not reset the count", () => {
      chatDisplay.appendToAnswerBuffer(0, "streaming...", false);
      chatDisplay.startAnswerTimer(0);
      jest.advanceTimersByTime(5000);

      // onStreamingStart fires again for the continuation after a tool call.
      chatDisplay.startAnswerTimer(0);
      jest.advanceTimersByTime(1000);

      expect(container.querySelector(".answer-time").textContent).toBe("0:06");
    });

    test("a new turn starts its own count", () => {
      chatDisplay.appendToAnswerBuffer(0, "first", false);
      chatDisplay.startAnswerTimer(0);
      jest.advanceTimersByTime(5000);

      chatDisplay.appendToAnswerBuffer(1, "second", false);
      chatDisplay.startAnswerTimer(1);
      jest.advanceTimersByTime(2000);

      const headers = container.querySelectorAll(".answer-time");
      expect(headers[1].textContent).toBe("0:02");
    });

    test("elapsed is reported for the status bar and cleared when stopped", () => {
      chatDisplay.startAnswerTimer(0);
      jest.advanceTimersByTime(4000);
      expect(chatDisplay.liveTurnElapsedMs()).toBe(4000);

      chatDisplay.stopAnswerTimer();
      expect(chatDisplay.liveTurnElapsedMs()).toBeNull();
    });

    test("the final duration replaces the running count", () => {
      chatDisplay.appendToAnswerBuffer(0, "streaming...", false);
      chatDisplay.startAnswerTimer(0);
      jest.advanceTimersByTime(3000);

      chatDisplay.stopAnswerTimer();
      chatDisplay.setAnswerTiming(0, { wall_ms: 3200, llm_ms: 3000 });

      const el = container.querySelector(".answer-time");
      expect(el.textContent).toBe("3.2s");
      expect(el.classList.contains("answer-time--live")).toBe(false);
    });

    test("a finished turn cannot be restarted into a runaway count", () => {
      chatDisplay.appendToAnswerBuffer(0, "streaming...", false);
      chatDisplay.startAnswerTimer(0);
      jest.advanceTimersByTime(3000);
      chatDisplay.stopAnswerTimer();
      chatDisplay.setAnswerTiming(0, { wall_ms: 3200, llm_ms: 3000 });

      // A late onStreamingStart for the same answer must not put the header
      // back on the clock: nothing would ever stop it again.
      chatDisplay.startAnswerTimer(0);
      jest.advanceTimersByTime(9000);

      expect(container.querySelector(".answer-time").textContent).toBe("3.2s");
      expect(chatDisplay.liveTurnElapsedMs()).toBeNull();
    });

    test("clear() stops the ticker so it cannot write into a wiped DOM", () => {
      chatDisplay.appendToAnswerBuffer(0, "streaming...", false);
      chatDisplay.startAnswerTimer(0);

      chatDisplay.clear();
      jest.advanceTimersByTime(5000);

      expect(chatDisplay.liveTurnElapsedMs()).toBeNull();
      expect(container.querySelector(".answer-time")).toBeNull();
    });
  });
});
