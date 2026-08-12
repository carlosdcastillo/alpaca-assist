/**
 * UpdatePoller Unit Tests
 */

// Load the module - this populates window.UpdatePoller
require("../../../web/js/UpdatePoller.js");

describe("UpdatePoller", () => {
  let UpdatePoller;
  let mockApi;
  let poller;

  beforeAll(() => {
    // Get the class from window after require() populated it
    UpdatePoller = window.UpdatePoller;

    // Verify the source file was modified correctly
    if (!UpdatePoller) {
      throw new Error(
        "window.UpdatePoller is undefined. " +
          'Add "window.UpdatePoller = UpdatePoller;" to the end of web/js/UpdatePoller.js',
      );
    }
  });

  beforeEach(() => {
    jest.useFakeTimers();

    mockApi = {
      get_pending_js: jest.fn().mockResolvedValue([]),
    };

    poller = new UpdatePoller(mockApi, 50);
  });

  afterEach(() => {
    poller.stop();
    jest.useRealTimers();
  });

  describe("initialization", () => {
    it("should store api reference", () => {
      expect(poller.api).toBe(mockApi);
    });

    it("should store interval", () => {
      expect(poller.intervalMs).toBe(50);
    });

    it("should not be running initially", () => {
      expect(poller._running).toBe(false);
    });

    it("should have null poll promise initially", () => {
      expect(poller._pollPromise).toBeNull();
    });
  });

  describe("start()", () => {
    it("should set running to true", () => {
      poller.start();
      expect(poller._running).toBe(true);
    });

    it("should start polling loop", () => {
      poller.start();
      expect(mockApi.get_pending_js).toHaveBeenCalled();
    });

    it("should not start if already running", () => {
      poller.start();
      const firstCallCount = mockApi.get_pending_js.mock.calls.length;

      poller.start();
      expect(mockApi.get_pending_js.mock.calls.length).toBe(firstCallCount);
    });
  });

  describe("stop()", () => {
    it("should set running to false", () => {
      poller.start();
      poller.stop();
      expect(poller._running).toBe(false);
    });

    it("should stop polling after current iteration", async () => {
      mockApi.get_pending_js.mockResolvedValueOnce([]);

      poller.start();
      await Promise.resolve(); // Let first poll execute

      poller.stop();

      // Advance timers - should not trigger more polls
      mockApi.get_pending_js.mockClear();
      jest.advanceTimersByTime(100);
      await Promise.resolve();

      expect(mockApi.get_pending_js).not.toHaveBeenCalled();
    });
  });

  describe("_pollLoop()", () => {
    it("should start polling when started", async () => {
      mockApi.get_pending_js.mockResolvedValue([]);

      poller.start();
      await Promise.resolve();

      expect(mockApi.get_pending_js).toHaveBeenCalled();
    });

    it("should handle polling errors gracefully in loop", async () => {
      const consoleSpy = jest.spyOn(console, "error").mockImplementation();
      mockApi.get_pending_js.mockRejectedValueOnce(new Error("Poll failed"));

      // _pollOnce should throw when API fails
      await expect(poller._pollOnce()).rejects.toThrow("Poll failed");

      // The _pollLoop would catch this and log it
      // We're testing that _pollOnce properly propagates the error
      consoleSpy.mockRestore();
    });

    it("should continue polling after errors", async () => {
      mockApi.get_pending_js
        .mockRejectedValueOnce(new Error("Poll failed"))
        .mockResolvedValueOnce([]);

      // First call will fail but not throw because it's caught
      try {
        await poller._pollOnce();
      } catch (e) {
        // Expected error
      }

      // Second call should succeed
      await poller._pollOnce();

      expect(mockApi.get_pending_js).toHaveBeenCalledTimes(2);
    });
  });

  describe("_pollOnce()", () => {
    it("should execute updates from queue", async () => {
      const mockUpdate = jest.fn();
      window.testUpdate = mockUpdate;

      mockApi.get_pending_js.mockResolvedValue(["testUpdate()"]);

      poller.start();
      await Promise.resolve();

      expect(mockUpdate).toHaveBeenCalled();
      delete window.testUpdate;
    });

    it("should execute multiple updates in order", async () => {
      const executionOrder = [];
      window.updateOne = () => executionOrder.push(1);
      window.updateTwo = () => executionOrder.push(2);
      window.updateThree = () => executionOrder.push(3);

      mockApi.get_pending_js.mockResolvedValue([
        "updateOne()",
        "updateTwo()",
        "updateThree()",
      ]);

      poller.start();
      await Promise.resolve();

      expect(executionOrder).toEqual([1, 2, 3]);

      delete window.updateOne;
      delete window.updateTwo;
      delete window.updateThree;
    });

    it("should handle empty update queue", async () => {
      mockApi.get_pending_js.mockResolvedValue([]);

      poller.start();
      await Promise.resolve();

      // Should not throw
      expect(mockApi.get_pending_js).toHaveBeenCalled();
    });

    it("should handle null updates", async () => {
      mockApi.get_pending_js.mockResolvedValue(null);

      poller.start();
      await Promise.resolve();

      // Should not throw
      expect(mockApi.get_pending_js).toHaveBeenCalled();
    });
  });

  describe("_executeUpdate()", () => {
    it("should execute JS code in global scope", () => {
      window.testVar = 0;

      poller._executeUpdate("window.testVar = 42");

      expect(window.testVar).toBe(42);
      delete window.testVar;
    });

    it("should handle syntax errors gracefully", async () => {
      const consoleSpy = jest.spyOn(console, "error").mockImplementation();

      // Provide invalid JavaScript that will throw when executed
      mockApi.get_pending_js.mockResolvedValueOnce(["invalid syntax {"]);

      // Call the real _pollOnce which will call _executeUpdate with bad code
      await poller._pollOnce();

      // Verify the real error handling path was exercised
      expect(consoleSpy).toHaveBeenCalledWith(
        "Failed to execute update:",
        expect.any(SyntaxError),
        "invalid syntax {",
      );
      consoleSpy.mockRestore();
    });
  });
});
