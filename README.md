# Alpaca Assist

A desktop chat application that provides a convenient interface for interacting with LLM APIs.

## Features

- Multi-tab chat interface with syntax highlighting
- Code block detection and copying
- File path autocompletion
- API integration with Ollama (or Claude via the included emulator)
- Customizable tab summaries
- Keyboard shortcuts for common operations

## Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/yourusername/alpaca-assist.git
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

2. Run the emulator:
   ```bash
   python ollama_emulator.py
   ```

3. Start the Alpaca Assist application in another terminal window.

## File Completions

The application supports file path autocompletion. You can:

1. Add files to the completion list via the "Manage File Completions" option in the Edit menu
2. Trigger completions by typing `/file:` or `/file` in the input field

## License

[MIT License](LICENSE)
