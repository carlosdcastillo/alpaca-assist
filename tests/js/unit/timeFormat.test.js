/**
 * TimeFormat Unit Tests
 *
 * These deliberately assert the same boundaries as tests/test_timing.py. The
 * two implementations are independent, and a duration that reads differently
 * in the chat than in the model's context is a real bug, not cosmetics.
 */

require("../../../web/js/utils/timeFormat.js");

describe("TimeFormat", () => {
  let T;

  beforeAll(() => {
    T = window.TimeFormat;
    if (!T) throw new Error("window.TimeFormat is undefined");
  });

  describe("formatDurationMs", () => {
    test("sub-second reads in milliseconds", () => {
      expect(T.formatDurationMs(0)).toBe("0ms");
      expect(T.formatDurationMs(999)).toBe("999ms");
    });

    test("seconds keep one decimal", () => {
      expect(T.formatDurationMs(1000)).toBe("1.0s");
      expect(T.formatDurationMs(47300)).toBe("47.3s");
    });

    test("minutes and hours switch units", () => {
      expect(T.formatDurationMs(60000)).toBe("1m 00s");
      expect(T.formatDurationMs(194000)).toBe("3m 14s");
      expect(T.formatDurationMs(3600000)).toBe("1h 00m");
      expect(T.formatDurationMs(5460000)).toBe("1h 31m");
    });

    test("missing values render as an em dash", () => {
      expect(T.formatDurationMs(null)).toBe("—");
      expect(T.formatDurationMs(undefined)).toBe("—");
      expect(T.formatDurationMs(NaN)).toBe("—");
    });
  });

  describe("formatStopwatch", () => {
    test("counts in m:ss with a padded seconds field", () => {
      expect(T.formatStopwatch(0)).toBe("0:00");
      expect(T.formatStopwatch(9000)).toBe("0:09");
      expect(T.formatStopwatch(65000)).toBe("1:05");
      expect(T.formatStopwatch(3725000)).toBe("62:05");
    });

    test("negative elapsed clamps to zero rather than showing a minus", () => {
      expect(T.formatStopwatch(-500)).toBe("0:00");
    });
  });

  describe("formatGap", () => {
    test("units escalate at the same boundaries as core.timing", () => {
      expect(T.formatGap(30)).toBe("30 seconds later");
      expect(T.formatGap(600)).toBe("10 minutes later");
      expect(T.formatGap(3540)).toBe("59 minutes later");
      expect(T.formatGap(7200)).toBe("2 hours later");
      expect(T.formatGap(86400 * 3)).toBe("3 days later");
      expect(T.formatGap(86400 * 21)).toBe("3 weeks later");
      expect(T.formatGap(86400 * 90)).toBe("3 months later");
    });

    test("singular units are not pluralized", () => {
      expect(T.formatGap(3600)).toBe("1 hour later");
      expect(T.formatGap(86400)).toBe("1 day later");
    });

    test("negative and missing gaps produce nothing", () => {
      expect(T.formatGap(-1)).toBe("");
      expect(T.formatGap(null)).toBe("");
      expect(T.formatGap(undefined)).toBe("");
    });

    test("threshold matches the Python constant", () => {
      expect(T.GAP_THRESHOLD_SECONDS).toBe(600);
    });
  });

  describe("toDate / toEpoch", () => {
    test("accepts epoch seconds from Python", () => {
      const epoch = 1755347520;
      expect(T.toDate(epoch).getTime()).toBe(epoch * 1000);
      expect(T.toEpoch(epoch)).toBe(epoch);
    });

    test("accepts ISO strings from MessageNode.created_at", () => {
      const iso = "2026-08-16T14:32:00";
      expect(T.toDate(iso).getHours()).toBe(14);
      expect(T.toEpoch(iso)).toBe(new Date(iso).getTime() / 1000);
    });

    test("missing and unparseable values are null", () => {
      expect(T.toDate(null)).toBeNull();
      expect(T.toDate(undefined)).toBeNull();
      expect(T.toDate("")).toBeNull();
      expect(T.toDate("yesterday-ish")).toBeNull();
      expect(T.toEpoch(null)).toBeNull();
    });
  });

  describe("formatClock", () => {
    test("today shows time only", () => {
      const now = new Date();
      expect(T.formatClock(now.getTime() / 1000)).not.toMatch(/,/);
    });

    test("yesterday is named", () => {
      const yesterday = new Date();
      yesterday.setDate(yesterday.getDate() - 1);
      expect(T.formatClock(yesterday.getTime() / 1000)).toMatch(/^Yesterday /);
    });

    test("older messages carry a date so the time is not ambiguous", () => {
      const old = new Date();
      old.setDate(old.getDate() - 40);
      expect(T.formatClock(old.getTime() / 1000)).toMatch(/,/);
    });

    test("no timestamp renders nothing", () => {
      expect(T.formatClock(null)).toBe("");
    });
  });

  describe("describeTurn", () => {
    test("breaks the wall time into model, tools, and overhead", () => {
      const described = T.describeTurn({
        wall_ms: 47000,
        llm_ms: 12000,
        tool_ms: 31000,
        invocations: 4,
        tool_count: 3,
      });
      expect(described).toContain("Total 47.0s");
      expect(described).toContain("model 12.0s over 4 calls");
      expect(described).toContain("tools 31.0s over 3");
      expect(described).toContain("overhead 4.0s");
    });

    test("a single invocation is not described as a loop", () => {
      const described = T.describeTurn({
        wall_ms: 2000,
        llm_ms: 1900,
        invocations: 1,
      });
      expect(described).toContain("model 1.9s");
      expect(described).not.toContain("calls");
    });

    test("trivial overhead is left unmentioned", () => {
      const described = T.describeTurn({
        wall_ms: 10100,
        llm_ms: 10000,
        invocations: 1,
      });
      expect(described).not.toContain("overhead");
    });

    test("time to first token is reported when a token arrived", () => {
      const described = T.describeTurn({
        wall_ms: 5000,
        llm_ms: 4800,
        invocations: 1,
        started_at: 1000,
        first_token_at: 1000.85,
      });
      expect(described).toContain("first token 850ms");
    });

    test("no timing describes nothing", () => {
      expect(T.describeTurn(null)).toBe("");
    });
  });
});
