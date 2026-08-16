/**
 * Parse live-surface tool results (see core/surface_protocol.py).
 *
 * Unlike images and videos there is nothing to load: a surface result is a
 * descriptor, not content. The bytes live in a WebSocket the panel opens
 * directly to the remote x11vnc, and the transcript only ever remembers
 * which surface a turn was talking about.
 */
const SurfaceResultUtils = {
  SENTINEL: "@@ALPACA_SURFACE_RESULT@@",
  FIELD_SEP: "@@ALPACA_FIELD@@",

  parse(content) {
    if (!content) return null;
    const idx = content.indexOf(this.SENTINEL);
    if (idx === -1) return null;
    const parts = content
      .slice(idx + this.SENTINEL.length)
      .split(this.FIELD_SEP, 3);
    if (parts.length !== 3) return null;
    const [surfaceId, geometry, rawDescription] = parts;
    if (!/^srf_[0-9a-f]{8}$/.test(surfaceId)) return null;
    const match = /^(\d{1,5})x(\d{1,5})$/.exec(geometry);
    if (!match) return null;
    const width = Number(match[1]);
    const height = Number(match[2]);
    if (!width || !height) return null;
    // The description is the last field, so when this arrives wrapped in the
    // {"content": [...]} storage envelope there is no delimiter marking where
    // it ends — the envelope's own quote leaks in. Same cut as videoResult.
    const quoteIdx = rawDescription.indexOf('"');
    let description =
      quoteIdx === -1 ? rawDescription : rawDescription.slice(0, quoteIdx);
    if (description.endsWith("\\")) description = description.slice(0, -1);
    return { surfaceId, width, height, description };
  },
};

window.SurfaceResultUtils = SurfaceResultUtils;
