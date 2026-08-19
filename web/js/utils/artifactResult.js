/** Parse versioned interactive-artifact descriptors from tool results. */
const ArtifactResultUtils = {
  SENTINEL: "@@ALPACA_ARTIFACT_RESULT@@",

  parse(content) {
    if (!content) return null;
    const idx = content.indexOf(this.SENTINEL);
    if (idx === -1) return null;
    const match = /^[A-Za-z0-9_-]+/.exec(
      content.slice(idx + this.SENTINEL.length),
    );
    if (!match) return null;
    try {
      const token = match[0].replace(/-/g, "+").replace(/_/g, "/");
      const bytes = atob(token + "=".repeat((4 - (token.length % 4)) % 4));
      const escaped = Array.from(
        bytes,
        (c) => `%${c.charCodeAt(0).toString(16).padStart(2, "0")}`,
      ).join("");
      const manifest = JSON.parse(decodeURIComponent(escaped));
      const capabilities = manifest.capabilities;
      if (
        manifest.version !== 1 ||
        !/^art_[0-9a-f]{8}$/.test(manifest.artifact_id) ||
        manifest.kind !== "html" ||
        typeof manifest.title !== "string" ||
        !manifest.title.trim() ||
        !Number.isInteger(manifest.revision) ||
        manifest.revision < 1 ||
        manifest.renderer !== "client_html" ||
        !capabilities ||
        capabilities.backend !== false ||
        capabilities.network !== false ||
        typeof capabilities.user_input !== "boolean"
      ) {
        return null;
      }
      return manifest;
    } catch (error) {
      return null;
    }
  },
};

window.ArtifactResultUtils = ArtifactResultUtils;
