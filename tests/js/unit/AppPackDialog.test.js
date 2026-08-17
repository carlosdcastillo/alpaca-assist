require("../../../web/js/app.js");

describe("Offload Task dialog", () => {
  let app;

  beforeEach(() => {
    document.body.innerHTML = `
      <div id="dialog-overlay">
        <div class="dialog" id="pack-tab-dialog">
          <div id="pack-host-choices"></div>
          <label id="pack-custom-host-choice">
            <input type="radio" name="pack-host" value="__custom__">
            <input id="pack-custom-host">
          </label>
          <div id="pack-project-choices"></div>
          <button id="pack-tab-create">Offload Task</button>
        </div>
      </div>`;
    app = Object.create(window.AlpacaApp.prototype);
    app.api = {
      get_pack_hosts: jest.fn().mockResolvedValue({
        success: true,
        hosts: [{ hostname: "dev@example.test", display_name: "Development" }],
      }),
      get_projects: jest.fn().mockResolvedValue({
        success: true,
        projects: [
          {
            name: "alpaca-assist",
            repo_url: "git@example.test:alpaca-assist.git",
            branch: "main",
          },
        ],
      }),
    };
    app.tabManager = { createPackTab: jest.fn().mockResolvedValue("tab-1") };
    app._showToast = jest.fn();
  });

  it("presents host and project choices together with useful context", async () => {
    await app._showPackTabDialog();

    expect(document.getElementById("pack-tab-dialog")).toHaveClass("active");
    expect(document.getElementById("pack-host-choices")).toHaveTextContent(
      "Development",
    );
    expect(document.getElementById("pack-host-choices")).toHaveTextContent(
      "dev@example.test",
    );
    expect(document.getElementById("pack-project-choices")).toHaveTextContent(
      "No project",
    );
    expect(document.getElementById("pack-project-choices")).toHaveTextContent(
      "alpaca-assist",
    );
    expect(document.getElementById("pack-project-choices")).toHaveTextContent(
      "git@example.test:alpaca-assist.git · main",
    );
    expect(
      document.querySelector('input[name="pack-host"]:checked').value,
    ).toBe("dev@example.test");
    expect(
      document.querySelector('input[name="pack-project"]:checked').value,
    ).toBe("");
  });

  it("creates the tab from the selected host and project", async () => {
    await app._showPackTabDialog();
    document.querySelector(
      'input[name="pack-project"][value="alpaca-assist"]',
    ).checked = true;

    await app._createPackTabFromDialog();

    expect(app.tabManager.createPackTab).toHaveBeenCalledWith(
      "dev@example.test",
      "alpaca-assist",
    );
    expect(document.getElementById("dialog-overlay")).not.toHaveClass("active");
  });

  it("falls back to a required custom host when none are configured", async () => {
    app.api.get_pack_hosts.mockResolvedValue({ success: true, hosts: [] });
    await app._showPackTabDialog();

    await app._createPackTabFromDialog();

    expect(document.getElementById("pack-custom-host")).toHaveAttribute(
      "aria-invalid",
      "true",
    );
    expect(app.tabManager.createPackTab).not.toHaveBeenCalled();
  });
});
