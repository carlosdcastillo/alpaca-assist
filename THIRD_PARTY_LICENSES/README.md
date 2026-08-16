# Third-party licenses

The application includes the following vendored browser assets under
`web/js/lib`:

| Component | Version | Vendored files | License |
| --- | --- | --- | --- |
| KaTeX | 0.16.22 | `katex.min.js`, `katex-auto-render.min.js`, `katex.min.css` | MIT ([KaTeX.txt](KaTeX.txt)) |
| KaTeX fonts | 0.16.22 | `katex-fonts/*.woff2` | SIL Open Font License 1.1 ([KaTeX-fonts.txt](KaTeX-fonts.txt)) |
| Marked | 15.0.12 | `marked.min.js` | MIT and bundled Markdown notice ([Marked.txt](Marked.txt)) |
| Highlight.js | 11.11.1 | `highlight.min.js`, `github.min.css`, `nord.min.css` | BSD-3-Clause ([highlight.js.txt](highlight.js.txt)) |
| DOMPurify | 3.4.12 | `purify.min.js` | Apache-2.0 OR MPL-2.0; distributed here under Apache-2.0 ([DOMPurify.txt](DOMPurify.txt)) |
| noVNC | 1.7.0 | `novnc/core/**`, `novnc/vendor/pako/**` | MPL-2.0, with MIT-licensed bundled pako ([noVNC.txt](noVNC.txt)) |

These notices cover the files committed directly to this repository. Python
and npm packages installed by package managers have their own licenses. A
binary application bundle that includes those packages must also include the
licenses for the versions in that bundle.
