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
        self.large_tree = self.parse_code(large_code)
        self.small_tree = self.parse_code(small_code)
        if self._is_top_level_target(target):
            small_functions = self.extract_functions_from_top_level(self.small_tree)
            if not small_functions:
                return large_code
            small_function_names = {func.name for func in small_functions}
            self._merge_at_top_level(small_functions, small_function_names)
        else:
            small_functions = self.extract_functions_from_top_level(self.small_tree)
            if not small_functions:
                return large_code
            small_function_names = {func.name for func in small_functions}
            self._merge_into_class(target, small_functions, small_function_names)
        return ast.unparse(self.large_tree)

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
        target_class.body = self.remove_existing_functions(
            target_class.body,
            small_function_names,
        )
        target_class.body.extend(small_functions)

    def _merge_at_top_level(
        self,
        small_functions: list[ast.stmt],
        small_function_names: set,
    ):
        """Merge functions at the module top level, preserving approximate location."""
        new_functions_map = {func.name: func for func in small_functions}
        replaced_functions = set()
        new_body = []
        for node in self.large_tree.body:
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name in small_function_names
            ):
                new_body.append(new_functions_map[node.name])
                replaced_functions.add(node.name)
            else:
                new_body.append(node)
        for func in small_functions:
            if func.name not in replaced_functions:
                new_body.append(func)
        self.large_tree.body = new_body


def validate_single_function_or_method(code: str) -> tuple[bool, str]:
    """
    Evaluate the AST of code to determine if it contains only a single function or method.

    Args:
        code: Python code string to validate

    Returns:
        tuple: (is_method, function_name) where is_method indicates if it's a method (has 'self' parameter)

    Raises:
        ValueError: If the code contains anything other than a single function/method definition"""
    try:
        code = textwrap.dedent(code).strip()
        tree = ast.parse(code)
    except SyntaxError as e:
        raise ValueError(f"Invalid Python syntax: {e}")

    # Check for top-level imports (imports outside of function definitions)
    top_level_imports = [
        node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))
    ]
    if top_level_imports:
        raise ValueError(
            "Code cannot contain import statements outside of function definitions",
        )

    classes = [node for node in tree.body if isinstance(node, ast.ClassDef)]
    if classes:
        raise ValueError("Code cannot contain class definitions")

    functions = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    if len(functions) == 0:
        raise ValueError("Code must contain exactly one function or method definition")
    if len(functions) > 1:
        function_names = [f.name for f in functions]
        raise ValueError(
            f"Code must contain exactly one function or method definition, found {len(functions)}: {', '.join(function_names)}",
        )

    other_statements = [
        node
        for node in tree.body
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    if other_statements:
        statement_types = [type(node).__name__ for node in other_statements]
        raise ValueError(
            f"Code can only contain a single function or method definition, found other statements: {', '.join(statement_types)}",
        )

    function = functions[0]
    function_name = function.name
    is_method = False
    if function.args.args:
        first_param = function.args.args[0].arg
        is_method = first_param in ("self", "cls")
    return (is_method, function_name)


def test_ast_merger():
    merger = ASTMerger()
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
    # Test case 1: Invalid syntax
    code = """
def g(x):
    re turn x+1
"""
    try:
        validate_single_function_or_method(code)
    except ValueError as e:
        print(f"Test 1 - Invalid syntax: {e}")
    except Exception:
        print("asdf")

    # Test case 2: Import inside function (should be allowed)
    code_with_import_inside = """
def my_function():
    import os
    import sys
    from pathlib import Path
    return os.path.exists('file.txt')
"""
    try:
        is_method, func_name = validate_single_function_or_method(
            code_with_import_inside,
        )
        print(
            f"Test 2 - Import inside function: SUCCESS - Function '{func_name}' is valid (is_method={is_method})",
        )
    except ValueError as e:
        print(f"Test 2 - Import inside function: FAILED - {e}")

    # Test case 3: Import outside function (should raise ValueError)
    code_with_import_outside = """
import os

def my_function():
    return os.path.exists('file.txt')
"""
    try:
        is_method, func_name = validate_single_function_or_method(
            code_with_import_outside,
        )
        print(
            f"Test 3 - Import outside function: FAILED - Should have raised ValueError but got '{func_name}'",
        )
    except ValueError as e:
        print(
            f"Test 3 - Import outside function: SUCCESS - Correctly raised ValueError: {e}",
        )

    # Test case 4: From import outside function (should raise ValueError)
    code_with_from_import_outside = """
from pathlib import Path

def my_function():
    return Path('file.txt').exists()
"""
    try:
        is_method, func_name = validate_single_function_or_method(
            code_with_from_import_outside,
        )
        print(
            f"Test 4 - From import outside function: FAILED - Should have raised ValueError but got '{func_name}'",
        )
    except ValueError as e:
        print(
            f"Test 4 - From import outside function: SUCCESS - Correctly raised ValueError: {e}",
        )

    # Test case 5: Mixed - import inside and valid function
    code_mixed_valid = """
def process_file(self, filename):
    import json
    import os
    if os.path.exists(filename):
        with open(filename) as f:
            return json.load(f)
    return None
"""
    try:
        is_method, func_name = validate_single_function_or_method(code_mixed_valid)
        print(
            f"Test 5 - Mixed valid with imports inside: SUCCESS - Method '{func_name}' is valid (is_method={is_method})",
        )
    except ValueError as e:
        print(f"Test 5 - Mixed valid with imports inside: FAILED - {e}")

    print("\n" + "=" * 50)
    test_ast_merger()
