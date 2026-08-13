/** Parse lightweight video tool results and load their bytes on demand. */
const VideoResultUtils = {
  SENTINEL: "@@ALPACA_VIDEO_RESULT@@",
  FIELD_SEP: "@@ALPACA_FIELD@@",
  _cache: new Map(),
  _epoch: 0,

  parse(content) {
    if (!content) return null;
    const idx = content.indexOf(this.SENTINEL);
    if (idx === -1) return null;
    const parts = content
      .slice(idx + this.SENTINEL.length)
      .split(this.FIELD_SEP, 4);
    if (parts.length !== 4) return null;
    const [mimeType, locator, rawSize, rawDescription] = parts;
    const size = Number(rawSize);
    const quoteIdx = rawDescription.indexOf('"');
    let description =
      quoteIdx === -1 ? rawDescription : rawDescription.slice(0, quoteIdx);
    if (description.endsWith("\\")) description = description.slice(0, -1);
    if (
      !/^video\/(mp4|webm|ogg)$/.test(mimeType) ||
      !Number.isSafeInteger(size)
    ) {
      return null;
    }
    return { mimeType, locator, size, description };
  },

  load(tabId, result) {
    const key = `${tabId}:${result.locator}:${result.size}`;
    const cached = this._cache.get(key);
    if (cached) return cached.promise;

    const epoch = this._epoch;
    const entry = { promise: null, url: null };
    entry.promise = (async () => {
      const chunks = [];
      let offset = 0;
      while (true) {
        if (epoch !== this._epoch) throw new Error("Video load cancelled");
        const response = await window.pythonAPI.get_video_chunk(
          tabId,
          result.locator,
          offset,
        );
        if (!response?.success) {
          throw new Error(response?.error || "Could not load video");
        }
        if (
          response.mime_type !== result.mimeType ||
          response.size !== result.size
        ) {
          throw new Error("Video changed after it was generated");
        }
        const binary = atob(response.data);
        const bytes = new Uint8Array(binary.length);
        for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
        chunks.push(bytes);
        offset = response.next_offset;
        if (response.done) break;
      }
      if (epoch !== this._epoch) throw new Error("Video load cancelled");
      entry.url = URL.createObjectURL(
        new Blob(chunks, { type: result.mimeType }),
      );
      return entry.url;
    })().catch((error) => {
      this._cache.delete(key);
      throw error;
    });
    this._cache.set(key, entry);
    return entry.promise;
  },

  clear() {
    this._epoch += 1;
    for (const entry of this._cache.values()) {
      if (entry.url) URL.revokeObjectURL(entry.url);
    }
    this._cache.clear();
  },
};

window.VideoResultUtils = VideoResultUtils;
