document.body.innerHTML =
  '<div class="hidden" id="surface-splitter"></div>' +
  '<div class="hidden" id="surface-dock"></div>';
window.SurfaceDock = { count: jest.fn(() => 0) };
require("../../../web/js/components/ArtifactPanel.js");

const manifest = {
  artifact_id: "art_12345678",
  title: "Tiny game",
  revision: 1,
};

describe("ArtifactPanel", () => {
  beforeEach(() => {
    document.getElementById("surface-dock").replaceChildren();
    window.ArtifactDock._panels.clear();
    window.ArtifactDock._activeTabId = null;
    window.pythonAPI = { artifact_attach: jest.fn() };
  });

  it("renders HTML in a script-only opaque sandbox", async () => {
    const panel = await window.ArtifactDock.show("tab-1", {
      manifest,
      html: "<button>Play</button><script>window.game = 1</script>",
    });
    const iframe = panel.shadowRoot.querySelector("iframe");

    expect(iframe.getAttribute("sandbox")).toBe("allow-scripts");
    expect(iframe.getAttribute("sandbox")).not.toContain("allow-same-origin");
    expect(iframe.getAttribute("sandbox")).not.toContain("allow-popups");
    expect(iframe.srcdoc).toContain("window.game = 1");
  });

  it("preserves a panel while switching tabs", async () => {
    const panel = await window.ArtifactDock.show("tab-1", {
      manifest,
      html: "<h1>Demo</h1>",
    });
    window.ArtifactDock.setActiveTab("tab-2");
    expect(panel.style.display).toBe("none");
    window.ArtifactDock.setActiveTab("tab-1");
    expect(panel.style.display).toBe("flex");
    expect(panel.shadowRoot.querySelector("iframe")).not.toBeNull();
  });

  it("reloads from the Pack host and close removes only the panel", async () => {
    const panel = await window.ArtifactDock.show("tab-1", {
      manifest,
      html: "old",
    });
    window.pythonAPI.artifact_attach.mockResolvedValue({
      success: true,
      manifest,
      html: "new",
    });
    await panel.reload();
    expect(panel.shadowRoot.querySelector("iframe").srcdoc).toBe("new");

    window.ArtifactDock.remove("tab-1", manifest.artifact_id);
    expect(document.body.contains(panel)).toBe(false);
  });
});
