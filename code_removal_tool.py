"""Tool for removing functions and methods from Python files."""
import ast
from typing import Optional
from typing import Tuple

import astor


class PythonCodeRemover:
    """Removes functions and methods from Python code using AST manipulation."""

    def parse_code(self, code: str) -> ast.AST:
        """Parse Python code into an AST."""
        return ast.parse(code)

    def find_and_remove_function(
        self,
        code: str,
        function_name: str,
        parameter_list: str | None = None,
    ) -> tuple[str, str]:
        """Remove a function or method from Python code.

        Args:
            code: The Python source code
            function_name: Name of the function/method to remove
            parameter_list: Optional parameter list to disambiguate overloaded functions (e.g., "self, x, y")

        Returns:
            Tuple of (modified_code, location_description)
        """
        tree = self.parse_code(code)

        # Track what was removed for reporting
        removed_location = None
        found_candidates = []

        # Helper to check if parameters match
        def params_match(func_node: ast.FunctionDef, param_list_str: str) -> bool:
            if not param_list_str:
                return True

            # Parse the parameter list string
            expected_params = [p.strip() for p in param_list_str.split(",")]

            # Get actual parameters from the function
            actual_params = []
            for arg in func_node.args.args:
                actual_params.append(arg.arg)

            return actual_params == expected_params

        # Search in top-level functions
        new_body = []
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == function_name:
                found_candidates.append(("top-level", node))
                if params_match(node, parameter_list):
                    removed_location = f"top-level function '{function_name}'"
                    continue  # Skip this node (remove it)
            elif isinstance(node, ast.AsyncFunctionDef) and node.name == function_name:
                found_candidates.append(("top-level", node))
                if params_match(node, parameter_list):
                    removed_location = f"top-level async function '{function_name}'"
                    continue  # Skip this node (remove it)
            elif isinstance(node, ast.ClassDef):
                # Search within class methods
                new_class_body = []
                for class_node in node.body:
                    if (
                        isinstance(class_node, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and class_node.name == function_name
                    ):
                        found_candidates.append((f"class {node.name}", class_node))
                        if params_match(class_node, parameter_list):
                            async_prefix = (
                                "async "
                                if isinstance(class_node, ast.AsyncFunctionDef)
                                else ""
                            )
                            removed_location = f"{async_prefix}method '{function_name}' from class '{node.name}'"
                            continue  # Skip this method (remove it)
                    new_class_body.append(class_node)

                # Update the class body
                node.body = new_class_body

            new_body.append(node)

        # Update the tree body
        tree.body = new_body

        # Check if we found the function but couldn't remove due to ambiguity
        if not removed_location and found_candidates:
            if len(found_candidates) > 1:
                locations = []
                for loc, func_node in found_candidates:
                    params = ", ".join(arg.arg for arg in func_node.args.args)
                    locations.append(f"  - {loc}: {function_name}({params})")

                error_msg = f"Multiple functions named '{function_name}' found. Please specify parameter_list to disambiguate:\n"
                error_msg += "\n".join(locations)
                raise ValueError(error_msg)
            else:
                # Single candidate but parameters didn't match
                loc, func_node = found_candidates[0]
                actual_params = ", ".join(arg.arg for arg in func_node.args.args)
                raise ValueError(
                    f"Function '{function_name}' found at {loc} with parameters ({actual_params}), but parameter_list '{parameter_list}' did not match",
                )

        if not removed_location:
            raise ValueError(
                f"Function or method '{function_name}' not found in the code",
            )

        # Convert AST back to code
        modified_code = astor.to_source(tree)

        return modified_code, removed_location


def remove_function_from_file(
    filepath: str,
    function_name: str,
    parameter_list: str | None = None,
) -> tuple[str, str]:
    """Remove a function or method from a Python file.

    Args:
        filepath: Path to the Python file
        function_name: Name of the function/method to remove
        parameter_list: Optional parameter list to disambiguate overloaded functions

    Returns:
        Tuple of (modified_code, location_description)
    """
    with open(filepath, encoding="utf-8") as f:
        code = f.read()

    remover = PythonCodeRemover()
    modified_code, location = remover.find_and_remove_function(
        code,
        function_name,
        parameter_list,
    )

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(modified_code)

    return modified_code, location


if __name__ == "__main__":
    # Test the remover
    test_code = """
class MyClass:
    def method1(self):
        pass

    def method2(self, x, y):
        return x + y

    def method2(self, a):
        return a * 2

def standalone_func():
    print("Hello")

def another_func(x, y, z):
    return x + y + z
"""

    remover = PythonCodeRemover()

    # Test removing a method
    try:
        result, location = remover.find_and_remove_function(test_code, "method1")
        print(f"Removed: {location}")
        print("Result:")
        print(result)
    except ValueError as e:
        print(f"Error: {e}")
