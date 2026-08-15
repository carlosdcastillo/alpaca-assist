global.marked = {
  parse: jest.fn((markdown) => `<h3>${markdown}</h3>`),
};

global.DOMPurify = {
  sanitize: jest.fn((html) => html),
};

require("../../../web/js/app.js");

describe("history preview", () => {
  let app;

  beforeEach(() => {
    document.body.innerHTML = '<aside id="history-preview"></aside>';
    app = Object.create(window.AlpacaApp.prototype);
    jest.clearAllMocks();
  });

  it("renders sanitized markdown and polished user-facing metadata", () => {
    app._renderHistoryPreview({
      title: "Migration plan",
      pinned: true,
      created_date: "2026-08-14T10:00:00Z",
      closed_date: "2026-08-15T11:00:00Z",
      folder: "Work",
      tags: ["planning"],
      preview: "plain fallback",
      preview_markdown: "### You\n\nMake a **list**",
    });

    expect(marked.parse).toHaveBeenCalledWith("### You\n\nMake a **list**");
    expect(DOMPurify.sanitize).toHaveBeenCalledWith(expect.any(String), {
      FORBID_ATTR: ["href"],
    });
    expect(document.querySelector(".history-preview-title")).toHaveTextContent(
      "Migration plan",
    );
    expect(
      document.querySelector(".history-preview-eyebrow"),
    ).toHaveTextContent("Pinned conversation");
    expect(document.querySelector(".history-preview-labels")).toHaveTextContent(
      "Workplanning",
    );
    expect(document.querySelector(".history-preview-text h3")).toBeTruthy();
  });
});
