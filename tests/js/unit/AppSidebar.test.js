require("../../../web/js/app.js");

describe("conversation sidebar toggle", () => {
  let app;

  beforeEach(() => {
    document.body.innerHTML = `
      <div class="app-body">
        <aside id="tab-bar">
          <button id="sidebar-hide-toggle"></button>
        </aside>
        <button id="sidebar-show-toggle" hidden></button>
      </div>`;
    localStorage.clear();
    app = Object.create(window.AlpacaApp.prototype);
  });

  it("collapses and restores the sidebar from the persistent toolbar control", () => {
    app._setupSidebarToggle();
    const body = document.querySelector(".app-body");
    const sidebar = document.getElementById("tab-bar");
    const hideToggle = document.getElementById("sidebar-hide-toggle");
    const showToggle = document.getElementById("sidebar-show-toggle");

    hideToggle.click();

    expect(body).toHaveClass("sidebar-collapsed");
    expect(sidebar).toHaveAttribute("aria-hidden", "true");
    expect(sidebar.inert).toBe(true);
    expect(hideToggle).toHaveAttribute("aria-expanded", "false");
    expect(showToggle).not.toHaveAttribute("hidden");
    expect(localStorage.getItem("sidebar-collapsed")).toBe("true");

    showToggle.click();

    expect(body).not.toHaveClass("sidebar-collapsed");
    expect(sidebar).toHaveAttribute("aria-hidden", "false");
    expect(showToggle).toHaveAttribute("hidden");
  });

  it("restores the collapsed state on startup", () => {
    localStorage.setItem("sidebar-collapsed", "true");

    app._setupSidebarToggle();

    expect(document.querySelector(".app-body")).toHaveClass(
      "sidebar-collapsed",
    );
    expect(document.getElementById("sidebar-show-toggle")).not.toHaveAttribute(
      "hidden",
    );
  });
});
