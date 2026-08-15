/**
 * MarkdownInput Unit Tests
 */

require("../../../web/js/components/MarkdownInput.js");

describe("MarkdownInput", () => {
  let input;

  beforeEach(() => {
    document.body.innerHTML = '<div id="markdown-input"></div>';
    input = new window.MarkdownInput("markdown-input");
    input.setValue("first line\nsecond line\nthird line");
  });

  afterEach(() => {
    document.body.innerHTML = "";
  });

  function pressCtrl(key) {
    input.editor.dispatchEvent(
      new KeyboardEvent("keydown", {
        key,
        ctrlKey: true,
        bubbles: true,
        cancelable: true,
      }),
    );
  }

  it("moves the caret to the start of the current line with Ctrl+A", () => {
    input.editor.setSelectionRange(18, 18);

    pressCtrl("a");

    expect(input.editor.selectionStart).toBe(11);
    expect(input.editor.selectionEnd).toBe(11);
  });

  it("moves the caret to the end of the current line with Ctrl+E", () => {
    input.editor.setSelectionRange(14, 14);

    pressCtrl("e");

    expect(input.editor.selectionStart).toBe(22);
    expect(input.editor.selectionEnd).toBe(22);
  });

  it("moves to the end of the text when Ctrl+E is used on the last line", () => {
    input.editor.setSelectionRange(25, 25);

    pressCtrl("e");

    expect(input.editor.selectionStart).toBe(input.getValue().length);
    expect(input.editor.selectionEnd).toBe(input.getValue().length);
  });
});
