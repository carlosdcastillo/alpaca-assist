require("../../../web/js/app.js");

describe("AlpacaApp Pack workspace header", () => {
  let app;

  beforeEach(() => {
    document.body.innerHTML = `
      <section class="workspace-header hidden" id="workspace-header">
        <strong id="workspace-header-project"></strong>
        <span id="workspace-header-location"></span>
        <span id="workspace-header-branch"></span>
        <span id="workspace-header-changes"></span>
        <span id="workspace-header-sync"></span>
      </section>
    `;
    app = Object.create(window.AlpacaApp.prototype);
  });

  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("renders the offload promise rather than worker infrastructure", () => {
    app._updateWorkspaceHeader({
      is_pack: true,
      project: "alpaca",
      connected: true,
      workspace_path: "/work/alpaca",
      workspace_status: {
        is_git: true,
        branch: "feature/header",
        dirty: 4,
        unpushed: 2,
      },
    });

    expect(document.getElementById("workspace-header")).not.toHaveClass(
      "hidden",
    );
    expect(
      document.getElementById("workspace-header-project"),
    ).toHaveTextContent("alpaca");
    expect(
      document.getElementById("workspace-header-branch"),
    ).toHaveTextContent("");
    expect(
      document.getElementById("workspace-header-changes"),
    ).toHaveTextContent("Changes ready to review");
    expect(
      document.getElementById("workspace-header-location"),
    ).toHaveTextContent("Runs on Atreides — without tying up your computer");
    expect(
      document.getElementById("workspace-header-location"),
    ).not.toHaveTextContent("/work/alpaca");
  });

  it("hides the repository header for local tabs", () => {
    app._updateWorkspaceHeader({ is_pack: false });

    expect(document.getElementById("workspace-header")).toHaveClass("hidden");
  });
});
