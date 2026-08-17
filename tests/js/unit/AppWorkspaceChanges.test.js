require("../../../web/js/app.js");

const DIFF = [
  "diff --git a/core/app.py b/core/app.py",
  "index 1111111..2222222 100644",
  "--- a/core/app.py",
  "+++ b/core/app.py",
  "@@ -1,3 +1,3 @@",
  " context",
  "-old line",
  "+new line",
  "",
].join("\n");

describe("AlpacaApp workspace changes panel", () => {
  let app;

  beforeEach(() => {
    document.body.innerHTML = `
      <p id="workspace-changes-subtitle"></p>
      <div id="workspace-changes-files"></div>
      <div id="workspace-changes-diff"></div>
      <span id="workspace-changes-summary"></span>
    `;
    app = Object.create(window.AlpacaApp.prototype);
  });

  afterEach(() => {
    document.body.innerHTML = "";
  });

  const render = (overrides = {}) =>
    app._renderWorkspaceChanges({
      success: true,
      is_git: true,
      exists: true,
      branch: "main",
      head: "486afb5 Promote Pack workspace status",
      workspace_path: "/work/alpaca",
      omitted_files: 0,
      truncated: false,
      entries: [],
      ...overrides,
    });

  it("lists changed files with their status and line counts", () => {
    render({
      entries: [
        {
          path: "core/app.py",
          index: "M",
          worktree: " ",
          untracked: false,
          renamed_from: null,
          diff: DIFF,
        },
        {
          path: "notes.md",
          index: "?",
          worktree: "?",
          untracked: true,
          renamed_from: null,
          diff: "@@ -0,0 +1 @@\n+fresh\n",
        },
      ],
    });

    const rows = document.querySelectorAll(".workspace-change-row");
    // "All changes" plus one row per file.
    expect(rows).toHaveLength(3);
    expect(rows[1]).toHaveTextContent("core/app.py");
    expect(rows[1]).toHaveTextContent("+1 −1");
    expect(rows[2].querySelector(".workspace-change-badge")).toHaveTextContent(
      "?",
    );
    expect(document.getElementById("workspace-changes-summary")).toHaveTextContent(
      "2 files",
    );
    expect(document.getElementById("workspace-changes-subtitle")).toHaveTextContent(
      "main",
    );
  });

  it("colourizes diff lines and shows every file by default", () => {
    render({
      entries: [
        { path: "a.py", index: "M", worktree: " ", diff: DIFF },
        { path: "b.py", index: "M", worktree: " ", diff: DIFF },
      ],
    });

    const diff = document.getElementById("workspace-changes-diff");
    expect(diff.querySelectorAll(".workspace-diff-file")).toHaveLength(2);
    expect(diff.querySelectorAll(".diff-line--add")).toHaveLength(2);
    expect(diff.querySelectorAll(".diff-line--del")).toHaveLength(2);
    // The ---/+++ file headers are metadata, not removed/added lines.
    expect(diff.querySelectorAll(".diff-line--meta").length).toBeGreaterThan(0);
    expect(diff.querySelector(".diff-line--hunk")).toHaveTextContent("@@ -1,3 +1,3 @@");
  });

  it("shows only the selected file after a click", () => {
    render({
      entries: [
        { path: "a.py", index: "M", worktree: " ", diff: DIFF },
        { path: "b.py", index: "M", worktree: " ", diff: "@@\n+only b\n" },
      ],
    });

    document.querySelectorAll(".workspace-change-row")[2].click();

    const diff = document.getElementById("workspace-changes-diff");
    expect(diff).toHaveTextContent("only b");
    expect(diff).not.toHaveTextContent("new line");
    expect(diff.querySelectorAll(".workspace-diff-file")).toHaveLength(0);
  });

  it("renders diff content as text, never as markup", () => {
    render({
      entries: [
        {
          path: "evil.html",
          index: "?",
          worktree: "?",
          untracked: true,
          diff: "@@ -0,0 +1 @@\n+<img src=x onerror=alert(1)>\n",
        },
      ],
    });

    const diff = document.getElementById("workspace-changes-diff");
    expect(diff.querySelector("img")).toBeNull();
    expect(diff).toHaveTextContent("<img src=x onerror=alert(1)>");
  });

  it("reports a clean tree instead of an empty panel", () => {
    render({ entries: [] });

    expect(document.getElementById("workspace-changes-summary")).toHaveTextContent(
      "Working tree clean",
    );
    expect(document.getElementById("workspace-changes-diff")).toHaveTextContent(
      "the tree is clean",
    );
  });

  it("surfaces bounded results honestly", () => {
    render({
      omitted_files: 3,
      truncated: true,
      entries: [
        { path: "big.bin", index: "M", worktree: " ", diff: "", truncated: true },
      ],
    });

    expect(document.getElementById("workspace-changes-summary")).toHaveTextContent(
      "3 more not shown",
    );
    expect(document.getElementById("workspace-changes-diff")).toHaveTextContent(
      "Diff too large",
    );
  });

  it("shows the error when the workspace cannot be read", () => {
    app._renderWorkspaceChanges({ success: false, error: "Pack tab offline" });

    expect(document.getElementById("workspace-changes-diff")).toHaveTextContent(
      "Pack tab offline",
    );
  });

  it("explains a workspace that is not a Git repository", () => {
    render({ is_git: false });

    expect(document.getElementById("workspace-changes-diff")).toHaveTextContent(
      "not a Git repository",
    );
  });
});
