import os

from anthropic_ollama_server import ClaudeClient


def determine_merge_location(python_file_content, code_block):
    """
    Determines where a small code block should be inserted into a Python file
    using Claude API.

    Args:
        python_file_content (str): The content of the Python file
        code_block (str): The small block of code to be merged

    Returns:
        str: The location where the code should be inserted
    """

    # Get API key from environment variable
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        # Try loading from .env file as fallback
        try:
            from dotenv import load_dotenv

            load_dotenv()
            api_key = os.environ.get("ANTHROPIC_API_KEY")
        except ImportError:
            pass

    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY environment variable not found")
    # Initialize Claude client
    client = ClaudeClient(api_key=api_key)

    # Construct the prompt
    prompt = f"""You are a helpful coding assistant. Follow these instructions precisely:

1. I will provide you with exactly two code blocks:
   - One python file
   - One small block of code

2. Your task: Determine where the small block of code should be inserted into the python file so that the resulting merged code will compile successfully.

3. Response format: Answer with ONLY one of the following:
   - "toplevel" - if the small block should be inserted at the top level (not inside any class, function, or other structure)
   - The exact class name - if the small block should be inserted inside a specific class
   - The exact function name - if the small block should be inserted inside a specific function

4. Constraints:
   - Provide ONLY the location answer
   - No explanations
   - No comments
   - No additional text
   - No code examples

Python file:
```python
{python_file_content}
```

Small block of code:
```python
{code_block}
```"""

    try:
        # Make API call using ClaudeClient
        response = client.complete(
            prompt=prompt,
            model="claude-sonnet-4-20250514",
            max_tokens=100,
            temperature=0.1,  # Low temperature for consistent responses
        )

        # Extract the response text from Claude's response format
        response_text = response["content"][0]["text"].strip()
        return response_text

    except Exception as e:
        raise Exception(f"Error calling Claude API: {str(e)}")


def main():
    """
    Example usage of the determine_merge_location function.
    """

    # Example Python file content
    python_file = """
class Calculator:
    def __init__(self):
        self.result = 0

    def add(self, x, y):
        return x + y

def main():
    calc = Calculator()
    print(calc.add(2, 3))

if __name__ == "__main__":
    main()
"""

    # Example code block to merge
    code_block = """
def __init__(self):
    self.result = 0
    self.history = []
"""

    try:
        location = determine_merge_location(python_file, code_block)
        print(f"The code should be inserted in: {location}")
    except Exception as e:
        print(f"Error: {e}")


if __name__ == "__main__":
    main()
