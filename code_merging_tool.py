import ast
import textwrap
from typing import List
from typing import Optional
from typing import Union


class ASTMerger:
    def __init__(self):
        self.large_tree = None
        self.small_tree = None
        self.large_code_original = None
        self.small_code_original = None

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

    def extract_imports_from_top_level(self, tree: ast.AST) -> list[ast.stmt]:
        """Extract import statements from module top level."""
        imports = []
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                imports.append(node)
        return imports

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

    def _import_signature(self, import_node: ast.stmt) -> str:
        """Generate a unique signature for an import statement for deduplication."""
        if isinstance(import_node, ast.Import):
            names = sorted([alias.name for alias in import_node.names])
            return f"import:{','.join(names)}"
        elif isinstance(import_node, ast.ImportFrom):
            module = import_node.module or ""
            names = sorted([alias.name for alias in import_node.names])
            level = import_node.level or 0
            return f"from:{level}:{module}:{','.join(names)}"
        return ""

    def _find_function_in_source(
        self,
        code: str,
        func_name: str,
        tree: ast.AST,
    ) -> str | None:
        """
        Find and extract a function from source code by name.
        Only searches top-level functions.
        """
        lines = code.split("\n")
        # Only look at top-level body, not nested nodes
        for node in tree.body:
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == func_name
            ):
                start_line = node.lineno - 1
                end_line = node.end_lineno
                extracted_lines = lines[start_line:end_line]
                return "\n".join(extracted_lines)
        return None

    def _find_method_in_source(
        self,
        code: str,
        class_name: str,
        method_name: str,
        tree: ast.AST,
    ) -> str | None:
        """
        Find and extract a method from source code by class and method name.
        """
        lines = code.split("\n")
        class_node = self.find_class_node(tree, class_name)
        if class_node:
            for method in class_node.body:
                if (
                    isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and method.name == method_name
                ):
                    start_line = method.lineno - 1
                    end_line = method.end_lineno
                    extracted_lines = lines[start_line:end_line]
                    return "\n".join(extracted_lines)
        return None

    def _find_import_in_source(
        self,
        code: str,
        import_node: ast.stmt,
        tree: ast.AST,
    ) -> str | None:
        """
        Find and extract an import from source code by matching signature.
        """
        lines = code.split("\n")
        target_sig = self._import_signature(import_node)
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                if self._import_signature(node) == target_sig:
                    start_line = node.lineno - 1
                    end_line = node.end_lineno
                    extracted_lines = lines[start_line:end_line]
                    return "\n".join(extracted_lines)
        return None

    def _merge_imports(self, small_imports: list[ast.stmt]) -> None:
        """Merge imports from small code into large code, avoiding duplicates."""
        if not small_imports:
            return
        existing_imports = self.extract_imports_from_top_level(self.large_tree)
        existing_signatures = {self._import_signature(imp) for imp in existing_imports}
        insert_position = 0
        for i, node in enumerate(self.large_tree.body):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                insert_position = i + 1
            else:
                break
        new_imports = []
        for import_node in small_imports:
            signature = self._import_signature(import_node)
            if signature not in existing_signatures:
                new_imports.append(import_node)
                existing_signatures.add(signature)
        if new_imports:
            self.large_tree.body = (
                self.large_tree.body[:insert_position]
                + new_imports
                + self.large_tree.body[insert_position:]
            )

    def merge_ast(
        self,
        large_code: str,
        small_code: str,
        target: str | None = None,
    ) -> str:
        """
        Merge small code into large code, preserving comments.

        Args:
            large_code: The main code to merge into
            small_code: The code containing functions and imports to merge
            target: None, "top level", or class name to merge into

        Returns:
            Merged code as string with comments preserved
        """
        # Store original code for comment preservation
        self.large_code_original = textwrap.dedent(large_code).strip()
        self.small_code_original = textwrap.dedent(small_code).strip()

        self.large_tree = self.parse_code(large_code)
        self.small_tree = self.parse_code(small_code)

        small_imports = self.extract_imports_from_top_level(self.small_tree)
        self._merge_imports(small_imports)

        if self._is_top_level_target(target):
            small_functions = self.extract_functions_from_top_level(self.small_tree)
            if small_functions:
                small_function_names = {func.name for func in small_functions}
                self._merge_at_top_level(small_functions, small_function_names)
        else:
            small_functions = self.extract_functions_from_top_level(self.small_tree)
            if small_functions:
                small_function_names = {func.name for func in small_functions}
                self._merge_into_class(target, small_functions, small_function_names)

        # Reconstruct code with comments preserved
        return self._reconstruct_code_with_comments()

    def _reconstruct_class_with_comments(
        self,
        class_node: ast.ClassDef,
    ) -> str | None:
        """
        Reconstruct a class while preserving comments and applying method updates.
        """
        lines = self.large_code_original.split("\n")
        class_start = class_node.lineno - 1
        class_end = class_node.end_lineno

        # Get the methods that were merged from small_code
        small_methods = self.extract_functions_from_class(
            self.small_tree,
            class_node.name,
        )
        merged_method_names = {method.name for method in small_methods}

        # If no methods found in small_code class, check for top-level functions in small_code
        # (common pattern when small_code contains just method implementations)
        if not merged_method_names:
            small_top_level_functions = self.extract_functions_from_top_level(
                self.small_tree,
            )
            merged_method_names = {func.name for func in small_top_level_functions}

        # Reconstruct the class line by line
        result_lines = []
        i = class_start

        # Add class definition line
        result_lines.append(lines[i])
        i += 1

        # Process class body
        for method_node in class_node.body:
            if isinstance(method_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # If this method was merged from small_code, extract from small_code
                if method_node.name in merged_method_names:
                    # First try to find it as a method in a class in small_code
                    source = self._find_method_in_source(
                        self.small_code_original,
                        class_node.name,
                        method_node.name,
                        self.small_tree,
                    )
                    # If not found as a class method, try as a top-level function
                    if not source:
                        source = self._find_function_in_source(
                            self.small_code_original,
                            method_node.name,
                            self.small_tree,
                        )
                        # If extracted from top-level (small_code), add indentation
                        if source:
                            source_lines = source.split("\n")
                            # Check if first line is not indented (top-level function)
                            if (
                                source_lines
                                and source_lines[0]
                                and source_lines[0][0] not in (" ", "\t")
                            ):
                                # Add 4-space indentation to all lines
                                source = "\n".join(
                                    "    " + line if line.strip() else line
                                    for line in source_lines
                                )
                else:
                    # Method is from large_code, extract from large_code
                    source = self._find_method_in_source(
                        self.large_code_original,
                        class_node.name,
                        method_node.name,
                        self.large_tree,
                    )

                if source:
                    result_lines.append(source)
                    # Add blank line after method for spacing
                    result_lines.append("")
            else:
                # For other class members (attributes, etc.), extract from original
                start_line = method_node.lineno - 1
                end_line = method_node.end_lineno
                extracted_lines = lines[start_line:end_line]
                result_lines.extend(extracted_lines)

        # Remove trailing blank line if present
        if result_lines and result_lines[-1] == "":
            result_lines.pop()

        return "\n".join(result_lines) if result_lines else None

    def _find_class_in_source(
        self,
        code: str,
        class_name: str,
        tree: ast.AST,
    ) -> str | None:
        """Find and extract a class from source code by name."""
        lines = code.split("\n")
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == class_name:
                start_line = node.lineno - 1
                end_line = node.end_lineno
                extracted_lines = lines[start_line:end_line]
                return "\n".join(extracted_lines)
        return None

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

    def _reconstruct_code_with_comments(self) -> str:
        """Reconstruct the merged code while preserving comments from original sources."""
        result_lines = []

        # Get the set of functions that were merged from small_code
        small_functions = self.extract_functions_from_top_level(self.small_tree)
        merged_function_names = {func.name for func in small_functions}

        # Get all lines from large_code for reference
        large_lines = self.large_code_original.split("\n")

        # Process all nodes in order, preserving everything
        for node in self.large_tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                # Try to find import in small_code first (since new imports come from there)
                source = self._find_import_in_source(
                    self.small_code_original,
                    node,
                    self.small_tree,
                )
                if not source:
                    # Then try large_code
                    source = self._find_import_in_source(
                        self.large_code_original,
                        node,
                        self.large_tree,
                    )
                if not source:
                    # If still not found, reconstruct from AST (fallback for new imports)
                    source = ast.unparse(node)
                result_lines.append(source)

            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # If this function was merged from small_code, extract from small_code
                if node.name in merged_function_names:
                    source = self._find_function_in_source(
                        self.small_code_original,
                        node.name,
                        self.small_tree,
                    )
                else:
                    # Function is from large_code, extract from large_code
                    source = self._find_function_in_source(
                        self.large_code_original,
                        node.name,
                        self.large_tree,
                    )

                if source:
                    result_lines.append(source)

            elif isinstance(node, ast.ClassDef):
                # For classes, reconstruct with updated methods
                source = self._reconstruct_class_with_comments(node)
                if source:
                    result_lines.append(source)

            else:
                # Preserve all other top-level statements (for loops, while loops, try-except, if, assignments, etc.)
                start_line = node.lineno - 1
                end_line = node.end_lineno
                if (
                    end_line is not None
                    and start_line >= 0
                    and start_line < len(large_lines)
                ):
                    # end_lineno is exclusive in Python 3.8+, so we need to include it
                    extracted_lines = large_lines[start_line:end_line]
                    if extracted_lines:
                        source = "\n".join(extracted_lines)
                        result_lines.append(source)

        return "\n\n".join(result_lines)


def validate_imports_and_functions(code: str) -> tuple[bool, str]:
    """
    Validate that code contains imports and/or functions.
    Returns (is_method, function_name) tuple.

    Raises ValueError if code is invalid.
    """
    code = textwrap.dedent(code).strip()

    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        raise ValueError(f"Invalid Python syntax: {e}")

    # Extract functions and imports
    functions = []
    imports = []

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.append(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            imports.append(node)

    # Check if we have at least functions or imports
    if not functions and not imports:
        raise ValueError("Code must contain at least one function or import statement")

    # If we have functions, return the first one
    if functions:
        # Check if it's a method (has 'self' as first parameter)
        for node in tree.body:
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == functions[0]
            ):
                is_method = False
                if node.args.args:
                    first_arg = node.args.args[0].arg
                    is_method = first_arg == "self"
                return (is_method, functions[0])

    # If only imports, return empty function name
    return (False, "")


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


def test_type_signature_changes():
    """Test that type signature changes are correctly applied."""
    print("=" * 60)
    print("=== TYPE SIGNATURE CHANGE TESTS ===")
    print("=" * 60)

    # Test 1: Adding return type annotation
    print("\n=== Test 1: Adding return type annotation ===")
    merger = ASTMerger()
    large_code = """
def calculate(x, y):
    # This function adds two numbers
    return x + y
"""
    small_code = """
def calculate(x, y) -> int:
    # This function adds two numbers
    return x + y
"""
    result = merger.merge_ast(large_code, small_code, target="top level")
    print("RESULT:")
    print(result)
    assert "-> int:" in result, "Return type annotation not applied!"
    assert result.count("def calculate") == 1, "Function duplicated!"
    print("✓ Return type annotation correctly applied")
    print()

    # Test 2: Adding parameter type annotations
    print("=== Test 2: Adding parameter type annotations ===")
    merger = ASTMerger()
    large_code = """
def greet(name):
    # Greet someone
    return f"Hello, {name}!"
"""
    small_code = """
def greet(name: str) -> str:
    # Greet someone
    return f"Hello, {name}!"
"""
    result = merger.merge_ast(large_code, small_code, target="top level")
    print("RESULT:")
    print(result)
    assert "name: str" in result, "Parameter type annotation not applied!"
    assert "-> str:" in result, "Return type annotation not applied!"
    assert result.count("def greet") == 1, "Function duplicated!"
    print("✓ Parameter and return type annotations correctly applied")
    print()

    # Test 3: Changing parameter types
    print("=== Test 3: Changing parameter types ===")
    merger = ASTMerger()
    large_code = """
def process(value):
    # Process a value
    return value * 2
"""
    small_code = """
def process(value: float) -> float:
    # Process a value
    return value * 2
"""
    result = merger.merge_ast(large_code, small_code, target="top level")
    print("RESULT:")
    print(result)
    assert "value: float" in result, "Parameter type change not applied!"
    assert "-> float:" in result, "Return type change not applied!"
    assert result.count("def process") == 1, "Function duplicated!"
    print("✓ Parameter type changes correctly applied")
    print()

    # Test 4: Complex type annotations with generics
    print("=== Test 4: Complex type annotations with generics ===")
    merger = ASTMerger()
    large_code = """
def filter_items(items):
    # Filter items from a list
    return [x for x in items if x]
"""
    small_code = """
from typing import List

def filter_items(items: List[int]) -> List[int]:
    # Filter items from a list
    return [x for x in items if x]
"""
    result = merger.merge_ast(large_code, small_code, target="top level")
    print("RESULT:")
    print(result)
    assert "items: List[int]" in result, "Complex parameter type not applied!"
    assert "-> List[int]:" in result, "Complex return type not applied!"
    assert "from typing import List" in result, "Import not added!"
    assert result.count("def filter_items") == 1, "Function duplicated!"
    print("✓ Complex type annotations correctly applied")
    print()

    # Test 5: Changing from no types to Optional types
    print("=== Test 5: Changing to Optional types ===")
    merger = ASTMerger()
    large_code = """
def find_user(user_id):
    # Find a user by ID
    return None
"""
    small_code = """
from typing import Optional

def find_user(user_id: int) -> Optional[dict]:
    # Find a user by ID
    return None
"""
    result = merger.merge_ast(large_code, small_code, target="top level")
    print("RESULT:")
    print(result)
    assert "user_id: int" in result, "Parameter type not applied!"
    assert "-> Optional[dict]:" in result, "Optional return type not applied!"
    assert "from typing import Optional" in result, "Optional import not added!"
    assert result.count("def find_user") == 1, "Function duplicated!"
    print("✓ Optional type annotations correctly applied")
    print()

    # Test 6: Method with generic return type change
    print("=== Test 6: Method with generic return type change ===")
    merger = ASTMerger()
    large_code = """
class DataProcessor:
    def get_items(self, items):
        # Get items from collection
        return items
"""
    small_code = """

def get_items(self, items) -> List[str]:
    # Get items from collection
    return items
"""
    result = merger.merge_ast(large_code, small_code, target="DataProcessor")
    print("RESULT:")
    print(result)
    assert "-> List[str]:" in result, "Method return type not applied!"
    assert "def get_items(self, items)" in result, "Method signature not preserved!"
    assert result.count("def get_items") == 1, "Method duplicated!"
    print("✓ Method generic return type correctly applied")
    print()

    # Test 7: Method with parameter type changes
    print("=== Test 7: Method with parameter type changes ===")
    merger = ASTMerger()
    large_code = """
class Calculator:
    def multiply(self, x, y):
        # Multiply two numbers
        return x * y
"""
    small_code = """

def multiply(self, x: int, y: int) -> int:
    # Multiply two numbers
    return x * y
"""
    result = merger.merge_ast(large_code, small_code, target="Calculator")
    print("RESULT:")
    print(result)
    assert "x: int" in result, "Parameter x type not applied!"
    assert "y: int" in result, "Parameter y type not applied!"
    assert "-> int:" in result, "Return type not applied!"
    assert result.count("def multiply") == 1, "Method duplicated!"
    print("✓ Method parameter types correctly applied")
    print()

    # Test 8: Class with multiple methods - some updated, some unchanged
    print("=== Test 8: Class with multiple methods - selective updates ===")
    merger = ASTMerger()
    large_code = """
class Service:
    def process(self, data):
        # Process data
        return data

    def validate(self, value):
        # Validate value
        return True
"""
    small_code = """
def process(self, data: Any) -> Any:
    # Process data
    return data
"""
    result = merger.merge_ast(large_code, small_code, target="Service")
    print("RESULT:")
    print(result)
    assert "data: Any" in result, "Process method parameter type not applied!"
    assert "-> Any:" in result, "Process method return type not applied!"
    assert (
        "def validate(self, value):" in result
    ), "Validate method should be unchanged!"
    assert result.count("def process") == 1, "Process method duplicated!"
    assert result.count("def validate") == 1, "Validate method duplicated!"
    print("✓ Multiple methods correctly merged with selective updates")
    print()

    # Test 9: Method with complex generic types
    print("=== Test 9: Method with complex generic types ===")
    merger = ASTMerger()
    large_code = """
class DataStore:
    def get_mapping(self, keys):
        # Get mapping of keys
        return {}
"""
    small_code = """
from typing import Dict, List

class DataStore:
    def get_mapping(self, keys: List[str]) -> Dict[str, int]:
        # Get mapping of keys
        return {}
"""
    result = merger.merge_ast(large_code, small_code, target="DataStore")
    print("RESULT:")
    print(result)
    assert "keys: List[str]" in result, "Complex parameter type not applied!"
    assert "-> Dict[str, int]:" in result, "Complex return type not applied!"
    assert "from typing import Dict, List" in result, "Imports not added!"
    assert result.count("def get_mapping") == 1, "Method duplicated!"
    print("✓ Method with complex generic types correctly applied")
    print()

    # Test 10: Method comments preserved with type changes
    print("=== Test 10: Method comments preserved with type changes ===")
    merger = ASTMerger()
    large_code = """
class Logger:
    def log_message(self, msg):
        # Log a message to console
        # This is important for debugging
        print(msg)
"""
    small_code = """
class Logger:
    def log_message(self, msg: str) -> None:
        # Log a message to console
        # This is important for debugging
        print(msg)
"""
    result = merger.merge_ast(large_code, small_code, target="Logger")
    print("RESULT:")
    print(result)
    assert "msg: str" in result, "Parameter type not applied!"
    assert "-> None:" in result, "Return type not applied!"
    assert "# Log a message to console" in result, "First comment not preserved!"
    assert (
        "# This is important for debugging" in result
    ), "Second comment not preserved!"
    assert result.count("def log_message") == 1, "Method duplicated!"
    print("✓ Method comments preserved with type changes")
    print()

    print("=" * 60)
    print("=== ALL TYPE SIGNATURE CHANGE TESTS PASSED ===")
    print("=" * 60)


def test_validate():
    # Test case 1: Invalid syntax
    code = """
def g(x):
    re turn x+1
"""
    try:
        validate_imports_and_functions(code)
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
        is_method, func_name = validate_imports_and_functions(
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
        is_method, func_name = validate_imports_and_functions(
            code_with_import_outside,
        )
        print(
            f"Test 3 - Import outside function: SUCCESS - got '{func_name}'",
        )
    except ValueError as e:
        print(
            f"Test 3 - Import outside function: FAILED - raised ValueError: {e}",
        )

    # Test case 4: From import outside function (should raise ValueError)
    code_with_from_import_outside = """
from pathlib import Path

def my_function():
    return Path('file.txt').exists()
"""
    try:
        is_method, func_name = validate_imports_and_functions(
            code_with_from_import_outside,
        )
        print(
            f"Test 4 - From import outside function: SUCCESS -  got '{func_name}'",
        )
    except ValueError as e:
        print(
            f"Test 4 - From import outside function: FAILED - raised ValueError: {e}",
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
        is_method, func_name = validate_imports_and_functions(code_mixed_valid)
        print(
            f"Test 5 - Mixed valid with imports inside: SUCCESS - Method '{func_name}' is valid (is_method={is_method})",
        )
    except ValueError as e:
        print(f"Test 5 - Mixed valid with imports inside: FAILED - {e}")

    print("\n" + "=" * 50)


def test_if_main_and_globals():
    """
    Test merging with if __main__ and global variables.

    IMPORTANT: The merger should preserve ALL content from large_code that is not
    explicitly being replaced by small_code. This includes:
    - Global variables
    - if __name__ == '__main__' blocks
    - Any other top-level statements
    - Functions that are not being updated

    Only functions/methods that appear in small_code should be replaced in large_code.
    Everything else should remain unchanged.
    """
    print("=" * 80)
    print("=== TEST: MERGING WITH if __main__ AND GLOBAL VARIABLES ===")
    print("=" * 80)
    print()

    # Test 1: Global variables ARE PRESERVED (not dropped)
    print("=== Test 1: Global variables ARE PRESERVED during merge ===")
    merger = ASTMerger()
    large_code = """
# Global variable in large code
DEBUG = False
CONFIG = {"timeout": 30}

def process():
    # Process function
    return "processing"
"""
    small_code = """
def process():
    # Updated process function
    return "processing with updates"
"""
    result = merger.merge_ast(large_code, small_code, target="top level")
    print("LARGE CODE:")
    print(large_code)
    print("\nSMALL CODE:")
    print(small_code)
    print("\nMERGED RESULT:")
    print(result)
    print("\nOBSERVATION:")
    print(
        "✓ Global variables from large_code (DEBUG, CONFIG) are PRESERVED in the result",
    )
    print("✓ The function 'process' was updated with new implementation")
    print()

    # Verify that globals are preserved
    assert "DEBUG = False" in result, "Global variables should be preserved!"
    assert "CONFIG" in result, "Global variables should be preserved!"
    assert "def process():" in result, "Function should be merged"
    assert "processing with updates" in result, "Function body should be updated"
    print("✓ Test 1 PASSED: Global variables are preserved during merge")
    print()

    # Test 2: if __main__ blocks ARE PRESERVED (not dropped)
    print("=== Test 2: if __main__ blocks ARE PRESERVED during merge ===")
    merger = ASTMerger()
    large_code = """
def main():
    # Main function
    return "main"

if __name__ == "__main__":
    print("Running large code")
    main()
"""
    small_code = """
def main():
    # Updated main function
    return "main updated"
"""
    result = merger.merge_ast(large_code, small_code, target="top level")
    print("LARGE CODE:")
    print(large_code)
    print("\nSMALL CODE:")
    print(small_code)
    print("\nMERGED RESULT:")
    print(result)
    print("\nOBSERVATION:")
    print(
        "✓ if __name__ == '__main__' block from large_code is PRESERVED in the result",
    )
    print("✓ The function 'main' was updated with new implementation")
    print()

    # Verify that if __main__ block is preserved
    assert result.count("if __name__") >= 1, "if __main__ block should be preserved"
    assert (
        "Running large code" in result
    ), "if __main__ block content should be preserved"
    assert "main updated" in result, "Function body was updated"
    print("✓ Test 2 PASSED: if __main__ blocks are preserved during merge")
    print()

    # Test 3: Complex scenario with globals, functions, and if __main__
    print("=== Test 3: Complex scenario - globals, functions, and if __main__ ===")
    merger = ASTMerger()
    large_code = """
import sys

# Global configuration
APP_NAME = "MyApp"
VERSION = "1.0.0"
SETTINGS = {"debug": False}

def initialize():
    # Initialize the app
    print(f"Initializing {APP_NAME}")
    return True

def run():
    # Run the app
    if initialize():
        print("App started")

if __name__ == "__main__":
    print(f"Starting {APP_NAME} v{VERSION}")
    run()
"""
    small_code = """
def initialize():
    # Updated initialization
    print(f"Initializing {APP_NAME} with logging")
    return True

def cleanup():
    # New cleanup function
    print("Cleaning up resources")
"""
    result = merger.merge_ast(large_code, small_code, target="top level")
    print("LARGE CODE:")
    print(large_code)
    print("\nSMALL CODE:")
    print(small_code)
    print("\nMERGED RESULT:")
    print(result)
    print("\nOBSERVATION:")
    print(
        "✓ Global variables from large_code (APP_NAME, VERSION, SETTINGS) are PRESERVED",
    )
    print("✓ if __main__ block from large_code is PRESERVED")
    print("✓ Imports are preserved (sys)")
    print("✓ Function 'initialize' was updated with new implementation")
    print("✓ Function 'cleanup' was added from small_code")
    print("✓ Function 'run' is preserved (not in small_code)")
    print()

    # Verify the behavior
    assert "APP_NAME = " in result, "Global variables should be preserved"
    assert "VERSION = " in result, "Global variables should be preserved"
    assert "SETTINGS = " in result, "Global variables should be preserved"
    assert "import sys" in result, "Imports should be preserved"
    assert "def initialize():" in result, "Function merged"
    assert "with logging" in result, "Function updated"
    assert "def cleanup():" in result, "New function added"
    assert "def run():" in result, "Existing function preserved"
    assert "if __name__" in result, "if __main__ block should be preserved"
    print("✓ Test 3 PASSED: Complex scenario - all content preserved correctly")
    print()

    # Test 4: Functions that reference globals work correctly
    print("=== Test 4: Functions that reference globals work correctly ===")
    merger = ASTMerger()
    large_code = """
DEBUG = False
for i in range(100):
    print(i)

def log_message(msg):
    # Log with debug flag
    if DEBUG:
        print(f"DEBUG: {msg}")
    else:
        print(msg)
"""
    small_code = """
def log_message(msg):
    # Updated log with more details
    if DEBUG:
        print(f"DEBUG [UPDATED]: {msg}")
    else:
        print(f"INFO: {msg}")
"""
    result = merger.merge_ast(large_code, small_code, target="top level")
    print("LARGE CODE:")
    print(large_code)
    print("\nSMALL CODE:")
    print(small_code)
    print("\nMERGED RESULT:")
    print(result)
    print("\nOBSERVATION:")
    print("✓ Global variable 'DEBUG' is PRESERVED from large_code")
    print("✓ Function 'log_message' was updated with new implementation")
    print("✓ Function can safely reference DEBUG global variable at runtime")
    print()

    # Verify
    assert "DEBUG = False" in result, "Global variable should be preserved"
    assert "DEBUG [UPDATED]" in result, "Function updated with new logic"
    assert "for i" in result, "for loop preserved"
    assert result.count("def log_message") == 1, "Function not duplicated"
    print("✓ Test 4 PASSED: Functions reference globals that are preserved")
    print()

    # Test 5: Multiple functions, only some updated
    print("=== Test 5: Multiple functions - selective updates ===")
    merger = ASTMerger()
    large_code = """
TIMEOUT = 30

def fetch_data():
    # Original fetch
    return "data"

def process_data(data):
    # Original process
    return data.upper()

def save_data(data):
    # Original save
    return True
"""
    small_code = """
def fetch_data():
    # Updated fetch with timeout
    return "new data"
"""
    result = merger.merge_ast(large_code, small_code, target="top level")
    print("LARGE CODE:")
    print(large_code)
    print("\nSMALL CODE:")
    print(small_code)
    print("\nMERGED RESULT:")
    print(result)
    print("\nOBSERVATION:")
    print("✓ Global variable 'TIMEOUT' is PRESERVED")
    print("✓ Function 'fetch_data' was updated")
    print("✓ Function 'process_data' is unchanged (not in small_code)")
    print("✓ Function 'save_data' is unchanged (not in small_code)")
    print()

    # Verify
    assert "TIMEOUT = 30" in result, "Global variable should be preserved"
    assert "new data" in result, "Updated function should be in result"
    assert "def process_data(data):" in result, "Unchanged function should be preserved"
    assert "def save_data(data):" in result, "Unchanged function should be preserved"
    assert result.count("def fetch_data") == 1, "Function not duplicated"
    print("✓ Test 5 PASSED: Selective function updates work correctly")
    print()

    print("=" * 80)
    print("=== SUMMARY: MERGING BEHAVIOR ===")
    print("=" * 80)
    print()
    print("WHAT IS PRESERVED FROM LARGE CODE:")
    print("  • All global variable assignments (ast.Assign nodes)")
    print("  • All if __name__ == '__main__' blocks (ast.If nodes)")
    print("  • All other non-function, non-import, non-class top-level statements")
    print("  • All functions NOT mentioned in small_code")
    print("  • All imports (merged with new imports from small_code)")
    print("  • All class definitions (with methods updated if specified)")
    print()
    print("WHAT IS REPLACED FROM SMALL CODE:")
    print("  • Only functions/methods that appear in small_code")
    print("  • These replace the corresponding functions in large_code")
    print("  • New functions from small_code are added if they don't exist")
    print()
    print("DESIGN PRINCIPLE:")
    print("  The ASTMerger is designed to merge small_code INTO large_code.")
    print("  Large code is the base, small code provides updates.")
    print("  Everything from large_code is preserved unless explicitly replaced.")
    print("  This ensures no data loss and safe merging of code updates.")
    print()
    print("=" * 80)
    print("=== ALL if __main__ AND GLOBALS TESTS PASSED ===")
    print("=" * 80)


if __name__ == "__main__":
    test_ast_merger()
    test_type_signature_changes()
    test_validate()
    test_if_main_and_globals()
