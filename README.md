# Alpaca Assist

A desktop chat application that provides a convenient interface for interacting with LLM APIs.

## Features

### Core Chat Interface
- Multi-tab chat interface with syntax highlighting
- Code block detection and copying
- Customizable tab summaries
- Keyboard shortcuts for common operations
- Streaming chat responses with continuation support

### Model Context Protocol (MCP) Integration
- **MCP Server Support**: Extensible tool functionality through the Model Context Protocol
- **Server Configuration UI**: Easy management and configuration of MCP servers
- **Automatic Tool Detection**: Seamless detection and execution of tool calls in chat responses
- **Built-in Simple MCP Server**: Includes basic file and text operations out of the box
- **Streaming Tool Execution**: Tool calls execute without interrupting the conversation flow

### File Operations & Autocompletion
- File path autocompletion with `/file:` and `/file` triggers
- File management through MCP tools
- Directory listing and file reading capabilities

### API Integration
- Ollama API support for local LLM inference
- Claude API integration via included emulator
- Modular chat components for better maintainability

## Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/carlosdcastillo/alpaca-assist.git
   cd alpaca-assist
   ```

2. Install the required dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Run the application:
   ```bash
   python alpaca_assist.py
   ```

## Using the Claude Emulator

The repository includes an Ollama API emulator that routes requests to Claude via the Anthropic API. To use it:

1. Set your Anthropic API key as an environment variable:
   ```bash
   export ANTHROPIC_API_KEY=your_api_key_here
   ```

2. Run the Claude server:
   ```bash
   python anthropic_ollama_server.py
   ```

3. Start the Alpaca Assist application in another terminal window.

## MCP Server Configuration

The application includes built-in MCP server support:

1. Access the MCP server configuration through the application's UI
2. Configure and manage multiple MCP servers
3. The included simple MCP server provides basic file operations:
   - File reading and writing
   - Directory listing
   - Text processing utilities

## File Completions

The application supports file path autocompletion. You can:

1. Add files to the completion list via the "Manage File Completions" option in the Edit menu
2. Trigger completions by typing `/file:` or `/file` in the input field
3. Use MCP tools for advanced file operations

## License

[MIT License](LICENSE)
