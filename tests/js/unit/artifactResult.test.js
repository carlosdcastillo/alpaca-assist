require("../../../web/js/utils/artifactResult.js");

function descriptor(overrides = {}) {
  const manifest = {
    version: 1,
    artifact_id: "art_1a2b3c4d",
    kind: "html",
    title: "Parser architecture",
    revision: 1,
    renderer: "client_html",
    capabilities: { backend: false, network: false, user_input: true },
    ...overrides,
  };
  const token = btoa(unescape(encodeURIComponent(JSON.stringify(manifest))))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
  return `@@ALPACA_ARTIFACT_RESULT@@${token}`;
}

describe("ArtifactResultUtils", () => {
  it("round-trips a manifest through an MCP storage envelope", () => {
    const marker = descriptor();
    const wrapped = JSON.stringify({
      content: [{ type: "text", text: marker }],
    });
    expect(window.ArtifactResultUtils.parse(wrapped)).toMatchObject({
      artifact_id: "art_1a2b3c4d",
      title: "Parser architecture",
      renderer: "client_html",
    });
  });

  it.each([
    { version: 2 },
    { artifact_id: "../../secret" },
    { renderer: "remote" },
    { capabilities: { backend: false, network: true, user_input: true } },
  ])("rejects unsupported descriptors", (change) => {
    expect(window.ArtifactResultUtils.parse(descriptor(change))).toBeNull();
  });
});
