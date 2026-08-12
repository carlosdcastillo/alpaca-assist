/**
 * Markdown utilities
 */
const MarkdownUtils = {
  /**
   * Configure marked.js with custom settings
   */
  configureMarked() {
    marked.setOptions({
      breaks: true,
      gfm: true,
      headerIds: false,
      mangle: false,
    });
  },

  /**
   * Render markdown to HTML
   */
  render(text) {
    return marked.parse(text);
  },

  /**
   * Sanitize HTML using DOMPurify
   */
  sanitize(html) {
    return DOMPurify.sanitize(html, {
      ALLOWED_TAGS: [
        "p",
        "br",
        "strong",
        "em",
        "u",
        "s",
        "del",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "ul",
        "ol",
        "li",
        "blockquote",
        "code",
        "pre",
        "a",
        "img",
        "table",
        "thead",
        "tbody",
        "tr",
        "th",
        "td",
        "div",
        "span",
        "hr",
        "input", // For checkboxes
      ],
      ALLOWED_ATTR: [
        "href",
        "title",
        "target",
        "rel",
        "src",
        "alt",
        "width",
        "height",
        "class",
        "id",
        "data-*",
        "checked",
        "disabled",
        "type",
      ],
    });
  },

  /**
   * Render and sanitize markdown
   */
  renderSafe(text) {
    const html = this.render(text);
    return this.sanitize(html);
  },

  /**
   * Extract code blocks from markdown text
   */
  extractCodeBlocks(text) {
    const codeBlockRegex = /```(\w+)?\n([\s\S]*?)```/g;
    const blocks = [];
    let match;

    while ((match = codeBlockRegex.exec(text)) !== null) {
      blocks.push({
        language: match[1] || "text",
        code: match[2].trim(),
      });
    }

    return blocks;
  },

  /**
   * Strip markdown formatting for plain text preview
   */
  stripMarkdown(text) {
    return text
      .replace(/```[\s\S]*?```/g, "[code]")
      .replace(/`([^`]+)`/g, "$1")
      .replace(/\*\*([^*]+)\*\*/g, "$1")
      .replace(/\*([^*]+)\*/g, "$1")
      .replace(/__([^_]+)__/g, "$1")
      .replace(/_([^_]+)_/g, "$1")
      .replace(/#+ /g, "")
      .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
      .replace(/\n+/g, " ")
      .trim();
  },
};

// Export
window.MarkdownUtils = MarkdownUtils;
