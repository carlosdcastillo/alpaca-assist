/**
 * TimeFormat - rendering of durations, clock times, and conversation gaps.
 *
 * Mirrors the thresholds and wording in core/timing.py so a duration reads the
 * same whether it was formatted for the model, the status bar, or a message
 * header. Keep the two in step: GAP_THRESHOLD_SECONDS in particular decides
 * both which history messages the model sees a [Sent ...] note on and which
 * turns get a visible divider, and a mismatch would make the chat show a gap
 * the model was never told about.
 */
(function () {
  const GAP_THRESHOLD_SECONDS = 600;

  /**
   * Render a duration at roughly two significant figures.
   * @param {number|null|undefined} ms
   * @returns {string}
   */
  function formatDurationMs(ms) {
    if (ms === null || ms === undefined || Number.isNaN(ms)) return "—";
    ms = Math.round(ms);
    if (ms < 1000) return `${ms}ms`;
    const seconds = ms / 1000;
    if (seconds < 60) return `${seconds.toFixed(1)}s`;
    const totalSeconds = Math.round(seconds);
    const minutes = Math.floor(totalSeconds / 60);
    const secs = totalSeconds % 60;
    if (minutes < 60) return `${minutes}m ${String(secs).padStart(2, "0")}s`;
    const hours = Math.floor(minutes / 60);
    const mins = minutes % 60;
    return `${hours}h ${String(mins).padStart(2, "0")}m`;
  }

  /**
   * Render a running duration as a stopwatch, for the live streaming counter.
   * Counts in m:ss so the digits stay put instead of reflowing every tick.
   * @param {number} ms
   * @returns {string}
   */
  function formatStopwatch(ms) {
    const totalSeconds = Math.max(0, Math.floor(ms / 1000));
    const minutes = Math.floor(totalSeconds / 60);
    const secs = totalSeconds % 60;
    return `${minutes}:${String(secs).padStart(2, "0")}`;
  }

  /**
   * Render a between-messages pause the way a person would say it.
   * @param {number|null|undefined} seconds
   * @returns {string}
   */
  function formatGap(seconds) {
    if (seconds === null || seconds === undefined || seconds < 0) return "";
    if (seconds < 90) return `${Math.floor(seconds)} seconds later`;
    const minutes = seconds / 60;
    // Units change at their own boundary rather than at 1.5x, so an hour reads
    // as "1 hour later" instead of "60 minutes later".
    if (minutes < 60) return `${Math.round(minutes)} minutes later`;
    const hours = minutes / 60;
    if (hours < 24) {
      const n = Math.round(hours);
      return `${n} hour${n === 1 ? "" : "s"} later`;
    }
    const days = hours / 24;
    if (days < 14) {
      const n = Math.round(days);
      return `${n} day${n === 1 ? "" : "s"} later`;
    }
    const weeks = days / 7;
    if (weeks < 9) {
      const n = Math.round(weeks);
      return `${n} week${n === 1 ? "" : "s"} later`;
    }
    const n = Math.round(days / 30.44);
    return `${n} month${n === 1 ? "" : "s"} later`;
  }

  /**
   * Wall-clock label for a message header: time of day, plus the date once the
   * message is old enough that the time alone would be ambiguous.
   * @param {number|string|null} value - epoch seconds, or an ISO string
   * @returns {string}
   */
  function formatClock(value) {
    const date = toDate(value);
    if (!date) return "";
    const time = date.toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
    });
    const now = new Date();
    const sameDay = date.toDateString() === now.toDateString();
    if (sameDay) return time;
    const yesterday = new Date(now);
    yesterday.setDate(now.getDate() - 1);
    if (date.toDateString() === yesterday.toDateString())
      return `Yesterday ${time}`;
    const sameYear = date.getFullYear() === now.getFullYear();
    const day = date.toLocaleDateString([], {
      month: "short",
      day: "numeric",
      ...(sameYear ? {} : { year: "numeric" }),
    });
    return `${day}, ${time}`;
  }

  /**
   * Full timestamp for tooltips, where ambiguity matters more than brevity.
   * @param {number|string|null} value
   * @returns {string}
   */
  function formatAbsolute(value) {
    const date = toDate(value);
    return date ? date.toLocaleString() : "";
  }

  /**
   * Accept either epoch seconds (Python's time.time) or an ISO string
   * (MessageNode.created_at), since both reach the frontend.
   * @param {number|string|null|undefined} value
   * @returns {Date|null}
   */
  function toDate(value) {
    if (value === null || value === undefined || value === "") return null;
    const date =
      typeof value === "number" ? new Date(value * 1000) : new Date(value);
    return Number.isNaN(date.getTime()) ? null : date;
  }

  /**
   * Convert either representation to epoch seconds for arithmetic.
   * @param {number|string|null|undefined} value
   * @returns {number|null}
   */
  function toEpoch(value) {
    const date = toDate(value);
    return date ? date.getTime() / 1000 : null;
  }

  /**
   * Compose the hover breakdown for a turn: how the wall time split between
   * the model, tools, and everything else.
   * @param {object|null} timing - a serialized core.timing.TurnTiming
   * @returns {string}
   */
  function describeTurn(timing) {
    if (!timing) return "";
    const parts = [`Total ${formatDurationMs(timing.wall_ms)}`];
    if (timing.llm_ms)
      parts.push(
        `model ${formatDurationMs(timing.llm_ms)}` +
          (timing.invocations > 1 ? ` over ${timing.invocations} calls` : ""),
      );
    if (timing.tool_ms)
      parts.push(
        `tools ${formatDurationMs(timing.tool_ms)}` +
          (timing.tool_count ? ` over ${timing.tool_count}` : ""),
      );
    // Overhead is only worth naming when it is a real share of the turn —
    // below that it is measurement noise between two different clocks.
    const overhead =
      (timing.wall_ms || 0) - (timing.llm_ms || 0) - (timing.tool_ms || 0);
    if (overhead > 500 && overhead > (timing.wall_ms || 0) * 0.05)
      parts.push(`overhead ${formatDurationMs(overhead)}`);
    if (timing.first_token_at && timing.started_at) {
      const ttft = (timing.first_token_at - timing.started_at) * 1000;
      parts.push(`first token ${formatDurationMs(ttft)}`);
    }
    return parts.join(" · ");
  }

  window.TimeFormat = {
    GAP_THRESHOLD_SECONDS,
    formatDurationMs,
    formatStopwatch,
    formatGap,
    formatClock,
    formatAbsolute,
    toDate,
    toEpoch,
    describeTurn,
  };
})();
