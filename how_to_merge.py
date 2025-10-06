import os


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

    client = None
    model = None
    errors_tried = []

    # Try 1: Environment variable API_KEY with anthropic_ollama_server
    try:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if api_key:
            from anthropic_ollama_server import ClaudeClient

            client = ClaudeClient(api_key=api_key)
            model = "claude-sonnet-4-20250514"
        else:
            errors_tried.append("ANTHROPIC_API_KEY environment variable not found")
    except Exception as e:
        errors_tried.append(
            f"Failed to initialize anthropic_ollama_server with API_KEY: {str(e)}",
        )

    # Try 2: Load from .env file for API_KEY with anthropic_ollama_server
    if client is None:
        try:
            from dotenv import load_dotenv

            load_dotenv()
            api_key = os.environ.get("ANTHROPIC_API_KEY")
            if api_key:
                from anthropic_ollama_server import ClaudeClient

                client = ClaudeClient(api_key=api_key)
                model = "claude-sonnet-4-20250514"
            else:
                errors_tried.append("ANTHROPIC_API_KEY not found in .env file")
        except ImportError:
            errors_tried.append("dotenv package not available")
        except Exception as e:
            errors_tried.append(
                f"Failed to load .env or initialize anthropic_ollama_server: {str(e)}",
            )

    # Try 3: Fall back to bedrock_server
    if client is None:
        try:
            from bedrock_server import ClaudeClient

            client = ClaudeClient()
            model = "us.anthropic.claude-sonnet-4-20250514-v1:0"
        except Exception as e:
            errors_tried.append(f"Failed to initialize bedrock_server: {str(e)}")

    # If all attempts failed, raise error with details
    if client is None:
        error_msg = "Failed to initialize any ClaudeClient. Attempts made:\n"
        for i, error in enumerate(errors_tried, 1):
            error_msg += f"{i}. {error}\n"
        raise ValueError(error_msg)

    # Construct the prompt
    prompt = f"""You are a helpful coding assistant. Follow these instructions precisely:

1. I will provide you with exactly two code blocks:
   - One python file
   - One small block of code

2. Your task: Determine where the small block of code should be inserted into the python file so that the resulting merged code will compile successfully.

3. Response format: Answer with ONLY one of the following:
   - "toplevel" - if the small block should be inserted at the module top level
   - The exact class name - if the small block should be inserted inside a specific class

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
            model=model,
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
