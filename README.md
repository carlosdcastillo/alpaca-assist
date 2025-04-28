# Alpaca Assist

Alpaca Assist is a Python-based chat application that provides an interface for interacting with AI language models. It uses a custom API that emulates the Ollama API format but connects to the Claude AI model in the backend.

## Features

- Multi-tab chat interface
- Syntax highlighting for chat messages
- Ability to copy and paste text
- Code block copying functionality
- File content expansion in prompts

## Files in the Repository

1. `main.py`: The main application file containing the GUI implementation using tkinter.
2. `expansion_language.py`: A module for expanding file references in the input prompts.
3. `anthropic_ollama_server.py`: A Flask-based API emulator that interfaces with the Claude AI model.

## Setup and Installation

1. Clone the repository:
   ```
   git clone https://github.com/yourusername/alpaca-assist.git
   cd alpaca-assist
   ```

2. Install the required dependencies:
   ```
   pip install -r requirements.txt
   ```

3. Set up your Anthropic API key:
   - Either set it as an environment variable:
     ```
     export ANTHROPIC_API_KEY=your_api_key_here
     ```
   - Or modify the `ClaudeClient` class in `anthropic_ollama_server.py` to include your API key.

## Usage

1. Start the API emulator:
   ```
   python anthropic_ollama_server.py
   ```

2. In a separate terminal, run the main application:
   ```
   python main.py
   ```

3. Use the GUI to interact with the AI model:
   - Create new chat tabs
   - Type prompts and submit them
   - Copy and paste text as needed
   - Use the "/file:" syntax to expand file contents in your prompts

## Contributing

Contributions to Alpaca Assist are welcome! Please feel free to submit a Pull Request.

## License

This project is open source and available under the [MIT License](LICENSE).
