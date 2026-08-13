/**
 * Shared JS mirror of image_tool_result.py's sentinel encode/decode.
 *
 * Both ToolFolds.js (rendering a view_image tool-result fold as an actual
 * image instead of a wall of base64 text) and ChatDisplay.js (resolving
 * `alpaca://image/<id>` refs in an answer's own markdown to the matching
 * tool-result image) need to recognize the same sentinel — kept here once
 * so the two don't drift into subtly different parsing.
 */
const ImageResultUtils = {
  SENTINEL: "@@ALPACA_IMAGE_RESULT@@",
  FIELD_SEP: "@@ALPACA_FIELD@@",
  SAFE_MIME_TYPES: new Set([
    "image/gif",
    "image/jpeg",
    "image/png",
    "image/webp",
  ]),

  /**
   * Mirrors Python's str.split(sep, 2): at most 3 parts, with any further
   * occurrences of `sep` left intact inside the last part.
   */
  _splitMaxTwo(str, sep) {
    const first = str.indexOf(sep);
    if (first === -1) return [str];
    const second = str.indexOf(sep, first + sep.length);
    if (second === -1) {
      return [str.slice(0, first), str.slice(first + sep.length)];
    }
    return [
      str.slice(0, first),
      str.slice(first + sep.length, second),
      str.slice(second + sep.length),
    ];
  },

  /**
   * Return {mimeType, base64Data, description} if `content` embeds an
   * image result (see image_tool_result.py), else null. Searches anywhere
   * in the string since callers may have wrapped/prefixed the stored
   * content (e.g. the {"content": [...]} tool-result JSON envelope).
   */
  parse(content) {
    if (!content) return null;
    const idx = content.indexOf(this.SENTINEL);
    if (idx === -1) return null;
    const payload = content.slice(idx + this.SENTINEL.length);
    const parts = this._splitMaxTwo(payload, this.FIELD_SEP);
    if (parts.length !== 3) return null;
    const [mimeType, base64Data, rawDescription] = parts;
    if (!this.SAFE_MIME_TYPES.has(mimeType)) return null;
    if (
      !base64Data ||
      base64Data.length % 4 !== 0 ||
      !/^[A-Za-z0-9+/]+={0,2}$/.test(base64Data)
    ) {
      return null;
    }
    try {
      atob(base64Data);
    } catch (_error) {
      return null;
    }
    const quoteIdx = rawDescription.indexOf('"');
    const description =
      quoteIdx !== -1 ? rawDescription.slice(0, quoteIdx) : rawDescription;
    return { mimeType, base64Data, description };
  },
};

window.ImageResultUtils = ImageResultUtils;
