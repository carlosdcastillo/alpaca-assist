import ast
import textwrap
from typing import List
from typing import Optional
from typing import Union


class ASTMerger:
    def __init__(self):
        self.large_tree = None
        self.small_tree = None

    def parse_code(self, code: str) -> ast.AST:
        """Parse Python code into an AST."""
        # Clean up the code by removing common indentation
        code = textwrap.dedent(code).strip()
        return ast.parse(code)

    def find_class_node(self, tree: ast.AST, class_name: str) -> ast.ClassDef | None:
        """Find a class definition by name in the AST."""
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == class_name:
                return node
        return None

    def get_function_names(self, nodes: list[ast.stmt]) -> set:
        """Get all function/method names from a list of AST nodes."""
        names = set()
        for node in nodes:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                names.add(node.name)
        return names

    def remove_existing_functions(
        self,
        target_nodes: list[ast.stmt],
        function_names: set,
    ) -> list[ast.stmt]:
        """Remove functions with names that exist in function_names set."""
        return [
            node
            for node in target_nodes
            if not (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name in function_names
            )
        ]

    def extract_functions_from_top_level(self, tree: ast.AST) -> list[ast.stmt]:
        """Extract function definitions from module top level."""
        functions = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions.append(node)
        return functions

    def extract_functions_from_class(
        self,
        tree: ast.AST,
        class_name: str,
    ) -> list[ast.stmt]:
        """Extract function definitions from a specific class."""
        functions = []
        class_node = self.find_class_node(tree, class_name)
        if class_node:
            for node in class_node.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    functions.append(node)
        return functions

    def _is_top_level_target(self, target: str | None) -> bool:
        """Check if target refers to top level (None or 'top level')."""
        return (
            target is None
            or target.lower() == "top level"
            or target.lower() == "toplevel"
        )

    def merge_ast(
        self,
        large_code: str,
        small_code: str,
        target: str | None = None,
    ) -> str:
        """
        Merge small code into large code.

        Args:
            large_code: The main code to merge into
            small_code: The code containing functions to merge
            target: None, "top level", or class name to merge into

        Returns:
            Merged code as string
        """
        # Parse both code snippets
        self.large_tree = self.parse_code(large_code)
        self.small_tree = self.parse_code(small_code)

        # Determine if merging at top level or into a class
        if self._is_top_level_target(target):
            # Extract functions from small code at top level
            small_functions = self.extract_functions_from_top_level(self.small_tree)
            if not small_functions:
                return large_code  # Nothing to merge

            # Get names of functions to be merged
            small_function_names = {func.name for func in small_functions}

            # Merge at top level
            self._merge_at_top_level(small_functions, small_function_names)
        else:
            # For class targets, extract functions from top level of small code
            # and merge them into the specified class in large code
            small_functions = self.extract_functions_from_top_level(self.small_tree)
            if not small_functions:
                return large_code  # Nothing to merge

            # Get names of functions to be merged
            small_function_names = {func.name for func in small_functions}

            # Merge into specific class
            self._merge_into_class(target, small_functions, small_function_names)

        # Convert back to code
        return ast.unparse(self.large_tree)

    def _merge_at_top_level(
        self,
        small_functions: list[ast.stmt],
        small_function_names: set,
    ):
        """Merge functions at the module top level."""
        # Remove existing functions with same names
        self.large_tree.body = self.remove_existing_functions(
            self.large_tree.body,
            small_function_names,
        )

        # Add new functions
        self.large_tree.body.extend(small_functions)

    def _merge_into_class(
        self,
        class_name: str,
        small_functions: list[ast.stmt],
        small_function_names: set,
    ):
        """Merge functions into a specific class."""
        target_class = self.find_class_node(self.large_tree, class_name)

        if target_class is None:
            raise ValueError(f"Class '{class_name}' not found in large code")

        # Remove existing methods with same names
        target_class.body = self.remove_existing_functions(
            target_class.body,
            small_function_names,
        )

        # Add new methods
        target_class.body.extend(small_functions)


# Usage example and test
def test_ast_merger():
    merger = ASTMerger()

    # Test case 1: Merge at top level
    large_code1 = """
def existing_func():
    return "original"

def keep_this():
    return "unchanged"

class MyClass:
    def class_method(self):
        return "class original"
"""

    small_code1 = """
def existing_func():
    return "overwritten"

def new_func():
    return "brand new"
"""

    print("=== Test 1: Top-level merge ===")
    result1 = merger.merge_ast(large_code1, small_code1, target="top level")
    print(result1)
    print()

    # Test case 2: Merge into class (your test case)
    large_code2 = """
def top_level():
    return "top"

class MyClass:
    def existing_method(self):
        return "original method"

    def keep_method(self):
        return "keep this"

class OtherClass:
    def other_method(self):
        return "other"
"""

    small_code2 = """
def existing_method(self):
    return "overwritten method"

def new_method(self):
    return "brand new method"
"""

    print("=== Test 2: Class-level merge ===")
    result2 = merger.merge_ast(large_code2, small_code2, target="MyClass")
    print(result2)
    print()


if __name__ == "__main__":
    test_ast_merger()
