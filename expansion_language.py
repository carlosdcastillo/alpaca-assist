import os
import re


def expand(input_string):
    def replace_file_content(match):
        filename = match.group(1)
        if os.path.exists(filename):
            try:
                with open(filename, "r", encoding="utf-8") as file:
                    return file.read()
            except IOError:
                return f"[Error: Unable to read file {filename}]"
        else:
            return f"[Error: File {filename} not found]"

    pattern = r"/file:([^\s]+)"
    expanded_string = re.sub(pattern, replace_file_content, input_string)
    return expanded_string


# Example usage
if __name__ == "__main__":
    test_string = (
        "This is a test. /file:example.txt And this is after the file content."
    )
    result = expand(test_string)
    print(result)
