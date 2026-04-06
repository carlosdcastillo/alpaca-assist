#!/usr/bin/env python3
"""
Python File Analyzer

This program analyzes a Python file and extracts information about
classes, their methods (including async), and standalone functions (including async)
with their parameters.
"""
import ast
import os
import sys


def extract_parameters(node):
    """
    Extract parameter information from a function or method node.

    Args:
        node: ast.FunctionDef or ast.AsyncFunctionDef node

    Returns:
        str: Formatted parameter string
    """
    params = []
    args = node.args

    # Regular positional arguments
    for arg in args.args:
        param_str = arg.arg
        if arg.annotation:
            param_str += f": {ast.unparse(arg.annotation)}"
        params.append(param_str)

    # *args parameter
    if args.vararg:
        vararg_str = f"*{args.vararg.arg}"
        if args.vararg.annotation:
            vararg_str += f": {ast.unparse(args.vararg.annotation)}"
        params.append(vararg_str)

    # Keyword-only arguments
    for arg in args.kwonlyargs:
        param_str = arg.arg
        if arg.annotation:
            param_str += f": {ast.unparse(arg.annotation)}"
        params.append(param_str)

    # **kwargs parameter
    if args.kwarg:
        kwarg_str = f"**{args.kwarg.arg}"
        if args.kwarg.annotation:
            kwarg_str += f": {ast.unparse(args.kwarg.annotation)}"
        params.append(kwarg_str)

    # Handle default values
    if args.defaults:
        defaults_offset = len(args.args) - len(args.defaults)
        for i, default in enumerate(args.defaults):
            param_index = defaults_offset + i
            if param_index < len(params):
                try:
                    default_value = ast.unparse(default)
                    params[param_index] += f" = {default_value}"
                except:
                    params[param_index] += " = <default>"

    # Handle keyword-only defaults
    if args.kw_defaults:
        kwonly_start = len(args.args)
        if args.vararg:
            kwonly_start += 1

        for i, default in enumerate(args.kw_defaults):
            if default is not None:
                param_index = kwonly_start + i
                if param_index < len(params):
                    try:
                        default_value = ast.unparse(default)
                        params[param_index] += f" = {default_value}"
                    except:
                        params[param_index] += " = <default>"

    return ", ".join(params)


def extract_return_type(node):
    """
    Extract return type annotation from a function node.

    Args:
        node: ast.FunctionDef or ast.AsyncFunctionDef node

    Returns:
        str: Return type annotation or empty string
    """
    if node.returns:
        try:
            return f" -> {ast.unparse(node.returns)}"
        except:
            return " -> <return_type>"
    return ""


def summarize_file(filename):
    """
    Analyze a Python file and extract classes with their methods and standalone functions.

    Args:
        filename (str): Path to the Python file to analyze

    Returns:
        dict: Dictionary containing classes with methods and standalone functions
    """
    try:
        with open(filename, encoding="utf-8") as f:
            tree = ast.parse(f.read())

        classes = {}
        standalone_functions = []

        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                class_name = node.name
                methods = []

                # Find methods within this class (both sync and async)
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        params = extract_parameters(item)
                        return_type = extract_return_type(item)

                        methods.append(
                            {
                                "name": item.name,
                                "parameters": params,
                                "return_type": return_type,
                                "type": "async_method"
                                if isinstance(item, ast.AsyncFunctionDef)
                                else "method",
                                "is_async": isinstance(item, ast.AsyncFunctionDef),
                            },
                        )

                classes[class_name] = methods

            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # Check if this function is not inside a class
                is_method = False

                # Walk up the tree to see if we're inside a class
                for ancestor in ast.walk(tree):
                    if isinstance(ancestor, ast.ClassDef):
                        if node in ast.walk(ancestor):
                            is_method = True
                            break

                if not is_method:
                    params = extract_parameters(node)
                    return_type = extract_return_type(node)

                    standalone_functions.append(
                        {
                            "name": node.name,
                            "parameters": params,
                            "return_type": return_type,
                            "type": "async_function"
                            if isinstance(node, ast.AsyncFunctionDef)
                            else "function",
                            "is_async": isinstance(node, ast.AsyncFunctionDef),
                        },
                    )

        return {
            "classes": classes,
            "standalone_functions": standalone_functions,
        }

    except FileNotFoundError:
        return {"error": f"File '{filename}' not found."}
    except SyntaxError as e:
        return {"error": f"Invalid Python syntax in '{filename}': {e}"}
    except Exception as e:
        return {"error": f"Error analyzing '{filename}': {e}"}


def format_results(results, filename):
    """
    Format the analysis results into a string.

    Args:
        results (dict): Results from summarize_file
        filename (str): Name of the analyzed file

    Returns:
        str: Formatted analysis results
    """
    if results is None:
        return "No results to display."

    if "error" in results:
        return f"Error: {results['error']}"

    output_lines = []
    output_lines.append(f"=== Analysis Results for '{filename}' ===")

    # Format classes and their methods
    classes = results["classes"]
    output_lines.append(f"\nClasses found ({len(classes)}):")

    if classes:
        for class_name, methods in classes.items():
            output_lines.append(f"\n  📁 Class: {class_name}")
            if methods:
                sync_methods = [m for m in methods if not m["is_async"]]
                async_methods = [m for m in methods if m["is_async"]]

                output_lines.append(f"     Methods ({len(methods)} total):")

                if sync_methods:
                    output_lines.append(f"       Synchronous ({len(sync_methods)}):")
                    for method in sync_methods:
                        signature = f"{method['name']}({method['parameters']}){method['return_type']}"
                        output_lines.append(f"         - {signature}")

                if async_methods:
                    output_lines.append(f"       Asynchronous ({len(async_methods)}):")
                    for method in async_methods:
                        signature = f"async {method['name']}({method['parameters']}){method['return_type']}"
                        output_lines.append(f"         - {signature} ⚡")
            else:
                output_lines.append("     (No methods found)")
    else:
        output_lines.append("  (No classes found)")

    # Format standalone functions
    standalone_functions = results["standalone_functions"]
    sync_functions = [f for f in standalone_functions if not f["is_async"]]
    async_functions = [f for f in standalone_functions if f["is_async"]]

    output_lines.append(
        f"\n🔧 Standalone Functions ({len(standalone_functions)} total):",
    )

    if sync_functions:
        output_lines.append(f"\n   Synchronous Functions ({len(sync_functions)}):")
        for function in sync_functions:
            signature = (
                f"{function['name']}({function['parameters']}){function['return_type']}"
            )
            output_lines.append(f"     - {signature}")

    if async_functions:
        output_lines.append(f"\n   Asynchronous Functions ({len(async_functions)}):")
        for function in async_functions:
            signature = f"async {function['name']}({function['parameters']}){function['return_type']}"
            output_lines.append(f"     - {signature} ⚡")

    if not sync_functions and not async_functions:
        output_lines.append("  (No standalone functions found)")

    # Format summary
    total_sync_methods = sum(
        len([m for m in methods if not m["is_async"]]) for methods in classes.values()
    )
    total_async_methods = sum(
        len([m for m in methods if m["is_async"]]) for methods in classes.values()
    )
    total_sync_functions = len(sync_functions)
    total_async_functions = len(async_functions)
    total_classes = len(classes)

    output_lines.append(f"\n📊 Summary:")
    output_lines.append(f"  - Classes: {total_classes}")
    output_lines.append(f"  - Synchronous Methods: {total_sync_methods}")
    output_lines.append(f"  - Asynchronous Methods: {total_async_methods} ⚡")
    output_lines.append(f"  - Synchronous Functions: {total_sync_functions}")
    output_lines.append(f"  - Asynchronous Functions: {total_async_functions} ⚡")
    output_lines.append(
        f"  - Total Methods: {total_sync_methods + total_async_methods}",
    )
    output_lines.append(
        f"  - Total Functions: {total_sync_functions + total_async_functions}",
    )
    output_lines.append(
        f"  - Total Callable Items: {total_sync_methods + total_async_methods + total_sync_functions + total_async_functions}",
    )

    output_lines.append("\n" + "=" * 80)

    return "\n".join(output_lines)


def analyze_python_file(filename):
    """
    Main analysis function that returns formatted results as a string.

    Args:
        filename (str): Path to the Python file to analyze

    Returns:
        str: Formatted analysis results
    """
    results = summarize_file(filename)
    return format_results(results, filename)


def main():
    """
    Main function to handle command line arguments and run the analysis.
    """
    if len(sys.argv) != 2:
        print("Usage: python python_analyzer.py <python_file>")
        print("Example: python python_analyzer.py my_script.py")
        print("\nThis tool will show:")
        print("  - Classes and their methods with parameters (sync & async)")
        print("  - Standalone functions with parameters (sync & async)")
        print("  - Type annotations and return types")
        print("  - Summary statistics")
        sys.exit(1)

    filename = sys.argv[1]

    # Check if file exists and is a Python file
    if not os.path.exists(filename):
        print(f"Error: File '{filename}' does not exist.")
        sys.exit(1)

    if not filename.endswith(".py"):
        print(
            f"Warning: '{filename}' doesn't have a .py extension. Proceeding anyway...",
        )

    # Analyze the file and print results
    result_string = analyze_python_file(filename)
    print(result_string)


if __name__ == "__main__":
    main()
