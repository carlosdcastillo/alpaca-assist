"""
PyWebView Markdown Viewer Demo

A simple desktop application for viewing Markdown files using PyWebView.
"""
import json
import os
from pathlib import Path

import webview


class MarkdownAPI:
    """API class exposed to the JavaScript frontend."""

    def __init__(self, initial_file=None):
        self.current_file = initial_file
        # Store as string to avoid serialization issues with Path objects
        if initial_file:
            self.current_directory = str(Path(initial_file).parent)
        else:
            self.current_directory = str(Path.cwd())

    def test_api(self):
        """Test method to verify API is working."""
        print("TEST_API CALLED - API is working!")
        return json.dumps({"status": "ok", "directory": self.current_directory})

    def get_markdown_content(self):
        """Return the content of the current markdown file."""
        print(f"get_markdown_content called, current_file: {self.current_file}")
        if self.current_file and os.path.exists(self.current_file):
            try:
                with open(self.current_file, encoding="utf-8") as f:
                    return f.read()
            except Exception as e:
                return f"# Error\n\nCould not read file: {str(e)}"
        return "# No file selected\n\nUse the sidebar to open a markdown file."

    def get_current_file_info(self):
        """Return information about the current file."""
        if self.current_file:
            return json.dumps(
                {
                    "path": self.current_file,
                    "name": os.path.basename(self.current_file),
                },
            )
        return json.dumps({"path": None, "name": None})

    def list_markdown_files(self):
        """List all markdown files in the current directory."""
        files = []
        try:
            print(f"Listing files in: {self.current_directory}")
            path_obj = Path(self.current_directory)
            print(f"Path object created: {path_obj}")
            print(f"Path exists: {path_obj.exists()}")
            print(f"Path is_dir: {path_obj.is_dir()}")
            for item in sorted(path_obj.iterdir()):
                if item.is_file() and item.suffix.lower() in [".md", ".markdown"]:
                    path_str = str(item.absolute())
                    files.append(
                        {
                            "name": item.name,
                            "path": path_str,
                            "escapedPath": path_str.replace('"', "&quot;"),
                        },
                    )
            print(f"Found {len(files)} markdown files")
        except Exception as e:
            print(f"Error listing files: {e}")
            import traceback

            traceback.print_exc()
        return json.dumps(files)

    def open_file(self, file_path):
        """Open a specific markdown file."""
        if os.path.exists(file_path):
            self.current_file = file_path
            self.current_directory = str(Path(file_path).parent)
            return True
        return False

    def open_file_dialog(self):
        """Open a file dialog to select a markdown file."""
        result = webview.windows[0].create_file_dialog(
            webview.OPEN_DIALOG,
            directory=self.current_directory,
            file_types=("Markdown Files (*.md;*.markdown)",),
        )
        if result and len(result) > 0:
            self.current_file = result[0]
            self.current_directory = str(Path(self.current_file).parent)
            return json.dumps(
                {
                    "success": True,
                    "path": self.current_file,
                    "name": os.path.basename(self.current_file),
                },
            )
        return json.dumps({"success": False})

    def change_directory(self, new_directory):
        """Change the current directory."""
        if os.path.isdir(new_directory):
            self.current_directory = str(Path(new_directory))
            return json.dumps({"success": True, "path": self.current_directory})
        return json.dumps({"success": False, "error": "Invalid directory"})

    def open_directory_dialog(self):
        """Open a directory dialog to select a folder."""
        result = webview.windows[0].create_file_dialog(
            webview.FOLDER_DIALOG,
            directory=self.current_directory,
        )
        if result and len(result) > 0:
            self.current_directory = str(Path(result[0]))
            return json.dumps(
                {
                    "success": True,
                    "path": self.current_directory,
                },
            )
        return json.dumps({"success": False})


def create_html_template():
    """Create the HTML template for the application."""
    return """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Markdown Viewer</title>
    <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github.min.css">
    <script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"></script>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
            background: #f5f5f5;
            height: 100vh;
            display: flex;
            overflow: hidden;
        }

        /* Sidebar */
        .sidebar {
            width: 280px;
            background: #2c3e50;
            color: white;
            display: flex;
            flex-direction: column;
            flex-shrink: 0;
        }

        .sidebar-header {
            padding: 20px;
            border-bottom: 1px solid #34495e;
        }

        .sidebar-header h1 {
            font-size: 18px;
            font-weight: 600;
            margin-bottom: 15px;
        }

        .btn {
            background: #3498db;
            color: white;
            border: none;
            padding: 10px 15px;
            border-radius: 4px;
            cursor: pointer;
            font-size: 13px;
            width: 100%;
            margin-bottom: 8px;
            transition: background 0.2s;
        }

        .btn:hover {
            background: #2980b9;
        }

        .btn-secondary {
            background: #7f8c8d;
        }

        .btn-secondary:hover {
            background: #6c7a7d;
        }

        .current-dir {
            font-size: 11px;
            color: #bdc3c7;
            word-break: break-all;
            margin-top: 10px;
            padding: 8px;
            background: rgba(0,0,0,0.2);
            border-radius: 4px;
        }

        .file-list {
            flex: 1;
            overflow-y: auto;
            padding: 10px;
        }

        .file-item {
            padding: 10px 12px;
            margin-bottom: 4px;
            border-radius: 4px;
            cursor: pointer;
            font-size: 13px;
            display: flex;
            align-items: center;
            gap: 8px;
            transition: background 0.2s;
        }

        .file-item:hover {
            background: rgba(255,255,255,0.1);
        }

        .file-item.active {
            background: #3498db;
        }

        .file-icon {
            font-size: 14px;
        }

        .no-files {
            text-align: center;
            color: #7f8c8d;
            padding: 20px;
            font-size: 13px;
        }

        /* Main Content */
        .main-content {
            flex: 1;
            display: flex;
            flex-direction: column;
            overflow: hidden;
        }

        .toolbar {
            background: white;
            padding: 12px 20px;
            border-bottom: 1px solid #e0e0e0;
            display: flex;
            align-items: center;
            justify-content: space-between;
        }

        .file-info {
            display: flex;
            align-items: center;
            gap: 10px;
        }

        .file-name {
            font-weight: 600;
            color: #2c3e50;
            font-size: 14px;
        }

        .file-path {
            color: #7f8c8d;
            font-size: 12px;
        }

        .refresh-btn {
            background: none;
            border: none;
            color: #7f8c8d;
            cursor: pointer;
            font-size: 16px;
            padding: 5px;
            border-radius: 4px;
        }

        .refresh-btn:hover {
            background: #f0f0f0;
            color: #2c3e50;
        }

        .content-area {
            flex: 1;
            overflow-y: auto;
            padding: 30px 40px;
        }

        .markdown-content {
            max-width: 900px;
            margin: 0 auto;
            background: white;
            padding: 40px;
            border-radius: 8px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
            user-select: text;
            -webkit-user-select: text;
            cursor: text;
            caret-color: #3498db;
            caret-width: 2px;
        }

        .markdown-content:focus {
            outline: none;
        }

        /* Markdown Styles */
        .markdown-content h1 {
            font-size: 32px;
            margin-bottom: 20px;
            padding-bottom: 10px;
            border-bottom: 2px solid #e0e0e0;
            color: #2c3e50;
        }

        .markdown-content h2 {
            font-size: 24px;
            margin-top: 30px;
            margin-bottom: 15px;
            color: #34495e;
        }

        .markdown-content h3 {
            font-size: 20px;
            margin-top: 25px;
            margin-bottom: 12px;
            color: #34495e;
        }

        .markdown-content p {
            margin-bottom: 16px;
            line-height: 1.6;
            color: #333;
        }

        .markdown-content code {
            background: #f4f4f4;
            padding: 2px 6px;
            border-radius: 3px;
            font-family: 'Consolas', 'Monaco', monospace;
            font-size: 14px;
        }

        .markdown-content pre {
            background: #f8f8f8;
            padding: 16px;
            border-radius: 6px;
            overflow-x: auto;
            margin-bottom: 16px;
        }

        .markdown-content pre code {
            background: none;
            padding: 0;
        }

        .markdown-content ul, .markdown-content ol {
            margin-bottom: 16px;
            padding-left: 24px;
        }

        .markdown-content li {
            margin-bottom: 8px;
            line-height: 1.5;
        }

        .markdown-content a {
            color: #3498db;
            text-decoration: none;
        }

        .markdown-content a:hover {
            text-decoration: underline;
        }

        .markdown-content blockquote {
            border-left: 4px solid #3498db;
            padding-left: 16px;
            margin-left: 0;
            color: #555;
            font-style: italic;
        }

        .markdown-content table {
            width: 100%;
            border-collapse: collapse;
            margin-bottom: 16px;
        }

        .markdown-content th, .markdown-content td {
            border: 1px solid #ddd;
            padding: 12px;
            text-align: left;
        }

        .markdown-content th {
            background: #f8f8f8;
            font-weight: 600;
        }

        .markdown-content img {
            max-width: 100%;
            height: auto;
            border-radius: 4px;
        }

        .loading {
            text-align: center;
            color: #7f8c8d;
            padding: 40px;
        }
    </style>
</head>
<body>
    <div class="sidebar">
        <div class="sidebar-header">
            <h1>📄 Markdown Viewer</h1>
            <button class="btn" onclick="openFileDialog()">📂 Open File</button>
            <button class="btn btn-secondary" onclick="openDirectoryDialog()">📁 Change Folder</button>
            <div class="current-dir" id="currentDir">Loading...</div>
        </div>
        <div class="file-list" id="fileList">
            <div class="loading">Loading files...</div>
        </div>
    </div>

    <div class="main-content">
        <div class="toolbar">
            <div class="file-info">
                <span class="file-name" id="fileName">No file selected</span>
                <span class="file-path" id="filePath"></span>
            </div>
            <button class="refresh-btn" onclick="refreshContent()" title="Refresh">🔄</button>
        </div>
        <div class="content-area">
            <div class="markdown-content" id="markdownContent" contenteditable="true" spellcheck="false">
                <div class="loading">Select a markdown file to view</div>
            </div>
        </div>
    </div>

    <script>
        let currentFile = null;

        // Initialize the app
        async function init() {
            console.log('init() called');
            console.log('pywebview object:', window.pywebview);
            console.log('api object:', window.pywebview ? window.pywebview.api : 'no pywebview');
            try {
                // Test API first
                console.log('Testing API...');
                const testResult = await window.pywebview.api.test_api();
                console.log('Test API result:', testResult);
                await loadFileList();
                await loadCurrentFile();
            } catch (err) {
                console.error('Error in init():', err);
                document.getElementById('fileList').innerHTML = '<div class="no-files">Error initializing: ' + err.message + '</div>';
            }
        }

        // Load the list of markdown files
        async function loadFileList() {
            console.log('loadFileList() called');
            try {
                const filesJson = await window.pywebview.api.list_markdown_files();
                const files = JSON.parse(filesJson);
                const fileList = document.getElementById('fileList');

                if (files.length === 0) {
                    fileList.innerHTML = '<div class="no-files">No markdown files found in this directory</div>';
                } else {
                    fileList.innerHTML = files.map(file => {
                        return '<div class="file-item ' + (file.path === currentFile ? 'active' : '') + '"' +
                               ' data-path="' + file.escapedPath + '">' +
                               '<span class="file-icon">📄</span>' +
                               '<span>' + escapeHtml(file.name) + '</span>' +
                               '</div>';
                    }).join('');
                    // Attach click handlers
                    document.querySelectorAll('.file-item').forEach(item => {
                        item.addEventListener('click', function() {
                            selectFile(this.getAttribute('data-path'));
                        });
                    });
                }

                // Update current directory display
                const infoJson = await window.pywebview.api.get_current_file_info();
                const info = JSON.parse(infoJson);
                if (info.path) {
                    document.getElementById('currentDir').textContent = info.path;
                }
            } catch (error) {
                console.error('Error loading file list:', error);
                document.getElementById('fileList').innerHTML =
                    '<div class="no-files">Error loading files</div>';
            }
        }

        // Load and render the current markdown file
        async function loadCurrentFile() {
            try {
                const content = await window.pywebview.api.get_markdown_content();
                const infoJson = await window.pywebview.api.get_current_file_info();
                const info = JSON.parse(infoJson);

                currentFile = info.path;

                // Update file info display
                if (info.name) {
                    document.getElementById('fileName').textContent = info.name;
                    document.getElementById('filePath').textContent = info.path;
                    document.title = info.name + ' - Markdown Viewer';
                } else {
                    document.getElementById('fileName').textContent = 'No file selected';
                    document.getElementById('filePath').textContent = '';
                    document.title = 'Markdown Viewer';
                }

                // Render markdown
                const htmlContent = marked.parse(content);
                document.getElementById('markdownContent').innerHTML = htmlContent;

                // Apply syntax highlighting
                document.querySelectorAll('pre code').forEach((block) => {
                    hljs.highlightElement(block);
                });

                // Update active file in sidebar
                loadFileList();
            } catch (error) {
                console.error('Error loading file:', error);
                document.getElementById('markdownContent').innerHTML =
                    '<div class="loading">Error loading file content</div>';
            }
        }

        // Select a file from the sidebar
        async function selectFile(filePath) {
            try {
                const result = await window.pywebview.api.open_file(filePath);
                if (result) {
                    await loadCurrentFile();
                }
            } catch (error) {
                console.error('Error selecting file:', error);
            }
        }

        // Open file dialog
        async function openFileDialog() {
            try {
                const resultJson = await window.pywebview.api.open_file_dialog();
                const result = JSON.parse(resultJson);
                if (result.success) {
                    await loadCurrentFile();
                    await loadFileList();
                }
            } catch (error) {
                console.error('Error opening file dialog:', error);
            }
        }

        // Open directory dialog
        async function openDirectoryDialog() {
            try {
                const resultJson = await window.pywebview.api.open_directory_dialog();
                const result = JSON.parse(resultJson);
                if (result.success) {
                    await loadFileList();
                }
            } catch (error) {
                console.error('Error opening directory dialog:', error);
            }
        }

        // Refresh the current content
        async function refreshContent() {
            await loadCurrentFile();
        }

        // Escape HTML special characters
        function escapeHtml(text) {
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }

        // Initialize when page loads
        console.log('Setting up pywebviewready listener');
        window.addEventListener('pywebviewready', function() {
            console.log('pywebviewready event fired!');
            init();
        });

        // Fallback: try init after a short delay in case event already fired
        setTimeout(function() {
            if (window.pywebview && window.pywebview.api) {
                console.log('Fallback init triggered');
                init();
            }
        }, 500);
    </script>
</body>
</html>"""


def main():
    """Main entry point for the application."""
    # Create a sample markdown file if none exists
    demo_dir = Path("C:/Users/Carlos/Desktop/pywebview_demo")
    sample_file = demo_dir / "sample.md"

    if not sample_file.exists():
        sample_content = """# Welcome to PyWebView Markdown Viewer

This is a sample markdown file to demonstrate the viewer's capabilities.

## Features

- **Live rendering** of markdown files
- **File browser** sidebar showing all markdown files in the directory
- **Syntax highlighting** for code blocks
- **Clean, modern interface**

## Code Example

Here is some Python code:

```python
def hello_world():
    print("Hello from PyWebView!")
    return True
```

And here is some JavaScript:

```javascript
function initApp() {
    console.log("Application initialized");
    window.pywebview.api.get_markdown_content();
}
```

## Tables

| Feature | Status | Notes |
|---------|--------|-------|
| Markdown rendering | ✅ Working | Using marked.js |
| File browser | ✅ Working | Lists .md files |
| Syntax highlighting | ✅ Working | Using highlight.js |
| File dialogs | ✅ Working | Native dialogs |

## Getting Started

1. Click **"Open File"** to select a markdown file
2. Click **"Change Folder"** to browse a different directory
3. Use the sidebar to quickly switch between files
4. Click the refresh button to reload the current file

## Links

- [PyWebView Documentation](https://pywebview.flowrl.com/)
- [Marked.js Documentation](https://marked.js.org/)

> **Note:** This application requires an internet connection for the first load to download the JavaScript libraries.
"""
        with open(sample_file, "w", encoding="utf-8") as f:
            f.write(sample_content)
        print(f"Created sample file: {sample_file}")
        initial_file = str(sample_file)
    else:
        # Find first markdown file in the directory
        md_files = list(demo_dir.glob("*.md")) + list(demo_dir.glob("*.markdown"))
        initial_file = str(md_files[0]) if md_files else None

    # Create the API instance
    api = MarkdownAPI(initial_file)

    # Create the HTML content
    html = create_html_template()

    # Create and start the webview
    window = webview.create_window(
        title="Markdown Viewer",
        html=html,
        js_api=api,
        width=1200,
        height=800,
        min_size=(800, 600),
    )

    print("Starting PyWebView Markdown Viewer...")
    print(f"Initial file: {initial_file or 'None'}")
    webview.start(debug=False)


if __name__ == "__main__":
    main()
