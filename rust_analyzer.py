#!/usr/bin/env python3
"""
Rust File Analyzer

This program analyzes a Rust file and extracts information about
structs, enums, traits, their methods, and standalone functions
using tree-sitter-rust.
"""

import os
import sys
from typing import Any

try:
    import tree_sitter_rust as tsrust
    from tree_sitter import Language
    from tree_sitter import Parser

    TREE_SITTER_AVAILABLE = True
except ImportError:
    TREE_SITTER_AVAILABLE = False


def get_node_text(node, source_bytes: bytes) -> str:
    """Extract the text content of a node from source bytes."""
    return source_bytes[node.start_byte : node.end_byte].decode("utf-8")


def extract_visibility(node, source_bytes: bytes) -> str:
    """Extract visibility modifier (pub, pub(crate), etc.) from a node."""
    for child in node.children:
        if child.type == "visibility_modifier":
            return get_node_text(child, source_bytes)
    return ""


def extract_function_signature(node, source_bytes: bytes) -> dict:
    """
    Extract function signature information from a function_item node.

    Returns:
        dict with name, parameters, return_type, is_async, visibility, generics
    """
    name = ""
    parameters = ""
    return_type = ""
    is_async = False
    visibility = extract_visibility(node, source_bytes)
    generics = ""

    for child in node.children:
        if child.type == "identifier":
            name = get_node_text(child, source_bytes)
        elif child.type == "type_parameters":
            generics = get_node_text(child, source_bytes)
        elif child.type == "parameters":
            # Extract parameters, cleaning up formatting
            params_text = get_node_text(child, source_bytes)
            # Remove outer parentheses and clean whitespace
            params_text = params_text[1:-1].strip()
            parameters = params_text
        elif child.type in (
            "type_identifier",
            "generic_type",
            "reference_type",
            "primitive_type",
            "unit_type",
            "tuple_type",
            "array_type",
            "function_type",
            "pointer_type",
            "scoped_type_identifier",
        ):
            # This is the return type (appears after ->)
            return_type = get_node_text(child, source_bytes)

    # Check for async keyword
    source_text = get_node_text(node, source_bytes)
    if source_text.strip().startswith("async ") or " async fn " in source_text:
        is_async = True

    # Extract return type more reliably by looking for -> pattern
    if not return_type:
        full_sig = get_node_text(node, source_bytes)
        if " -> " in full_sig:
            # Find the return type between -> and the block/semicolon
            arrow_pos = full_sig.find(" -> ")
            after_arrow = full_sig[arrow_pos + 4 :]
            # Find where the return type ends (at { or ;)
            brace_pos = after_arrow.find("{")
            semi_pos = after_arrow.find(";")
            if brace_pos >= 0 and (semi_pos < 0 or brace_pos < semi_pos):
                return_type = after_arrow[:brace_pos].strip()
            elif semi_pos >= 0:
                return_type = after_arrow[:semi_pos].strip()
            else:
                return_type = after_arrow.strip()
            # Handle where clauses
            if "\nwhere" in return_type or "\n    where" in return_type:
                return_type = return_type.split("\n")[0].strip()
            if return_type.startswith("where "):
                return_type = ""

    return {
        "name": name,
        "parameters": parameters,
        "return_type": return_type,
        "is_async": is_async,
        "visibility": visibility,
        "generics": generics,
    }


def extract_struct_info(node, source_bytes: bytes) -> dict:
    """Extract struct information from a struct_item node."""
    name = ""
    visibility = extract_visibility(node, source_bytes)
    generics = ""
    fields = []

    for child in node.children:
        if child.type == "type_identifier":
            name = get_node_text(child, source_bytes)
        elif child.type == "type_parameters":
            generics = get_node_text(child, source_bytes)
        elif child.type == "field_declaration_list":
            for field_child in child.children:
                if field_child.type == "field_declaration":
                    field_name = ""
                    field_type = ""
                    for fc in field_child.children:
                        if fc.type == "field_identifier":
                            field_name = get_node_text(fc, source_bytes)
                        elif fc.type in (
                            "type_identifier",
                            "generic_type",
                            "reference_type",
                            "primitive_type",
                            "array_type",
                            "tuple_type",
                            "scoped_type_identifier",
                        ):
                            field_type = get_node_text(fc, source_bytes)
                    if field_name:
                        fields.append({"name": field_name, "type": field_type})

    return {
        "name": name,
        "visibility": visibility,
        "generics": generics,
        "fields": fields,
    }


def extract_enum_info(node, source_bytes: bytes) -> dict:
    """Extract enum information from an enum_item node."""
    name = ""
    visibility = extract_visibility(node, source_bytes)
    generics = ""
    variants = []

    for child in node.children:
        if child.type == "type_identifier":
            name = get_node_text(child, source_bytes)
        elif child.type == "type_parameters":
            generics = get_node_text(child, source_bytes)
        elif child.type == "enum_variant_list":
            for variant_child in child.children:
                if variant_child.type == "enum_variant":
                    variant_name = ""
                    for vc in variant_child.children:
                        if vc.type == "identifier":
                            variant_name = get_node_text(vc, source_bytes)
                            break
                    if variant_name:
                        variants.append(variant_name)

    return {
        "name": name,
        "visibility": visibility,
        "generics": generics,
        "variants": variants,
    }


def extract_trait_info(node, source_bytes: bytes) -> dict:
    """Extract trait information from a trait_item node."""
    name = ""
    visibility = extract_visibility(node, source_bytes)
    generics = ""
    methods = []

    for child in node.children:
        if child.type == "type_identifier":
            name = get_node_text(child, source_bytes)
        elif child.type == "type_parameters":
            generics = get_node_text(child, source_bytes)
        elif child.type == "declaration_list":
            for decl_child in child.children:
                if decl_child.type == "function_signature_item":
                    sig = extract_function_signature(decl_child, source_bytes)
                    sig["type"] = "trait_method"
                    methods.append(sig)
                elif decl_child.type == "function_item":
                    sig = extract_function_signature(decl_child, source_bytes)
                    sig["type"] = "trait_method_default"
                    methods.append(sig)

    return {
        "name": name,
        "visibility": visibility,
        "generics": generics,
        "methods": methods,
    }


def extract_impl_info(node, source_bytes: bytes) -> dict:
    """Extract impl block information from an impl_item node."""
    type_name = ""
    trait_name = ""
    generics = ""
    methods = []

    # Parse the impl block to find type and trait
    found_for = False
    for child in node.children:
        if child.type == "type_parameters":
            generics = get_node_text(child, source_bytes)
        elif child.type in (
            "type_identifier",
            "generic_type",
            "scoped_type_identifier",
        ):
            text = get_node_text(child, source_bytes)
            if found_for:
                type_name = text
            elif trait_name == "":
                # Could be either trait or type, depends on "for" keyword
                trait_name = text
            else:
                type_name = text
        elif child.type == "declaration_list":
            for decl_child in child.children:
                if decl_child.type == "function_item":
                    sig = extract_function_signature(decl_child, source_bytes)
                    sig["type"] = "async_method" if sig["is_async"] else "method"
                    methods.append(sig)

    # Check if this is a trait impl or inherent impl
    full_text = get_node_text(node, source_bytes)
    if " for " in full_text:
        found_for = True
        # Re-parse to get correct trait/type assignment
        parts = full_text.split(" for ")
        if len(parts) >= 2:
            # trait_name stays as is (first type found)
            # type_name is after "for"
            pass
    else:
        # Inherent impl - no trait, just type
        type_name = trait_name
        trait_name = ""

    return {
        "type_name": type_name,
        "trait_name": trait_name,
        "generics": generics,
        "methods": methods,
    }


def summarize_file(filename: str) -> dict:
    """
    Analyze a Rust file and extract structs, enums, traits, impls, and functions.

    Args:
        filename: Path to the Rust file to analyze

    Returns:
        dict containing all extracted information
    """
    if not TREE_SITTER_AVAILABLE:
        return {
            "error": "tree-sitter and tree-sitter-rust are not installed. "
            "Install with: pip install tree-sitter tree-sitter-rust",
        }

    try:
        with open(filename, "rb") as f:
            source_bytes = f.read()

        parser = Parser(Language(tsrust.language()))
        tree = parser.parse(source_bytes)
        root = tree.root_node

        structs = []
        enums = []
        traits = []
        impls = []
        standalone_functions = []

        def visit_node(node):
            if node.type == "struct_item":
                structs.append(extract_struct_info(node, source_bytes))
            elif node.type == "enum_item":
                enums.append(extract_enum_info(node, source_bytes))
            elif node.type == "trait_item":
                traits.append(extract_trait_info(node, source_bytes))
            elif node.type == "impl_item":
                impls.append(extract_impl_info(node, source_bytes))
            elif node.type == "function_item":
                # Only top-level functions (not inside impl blocks)
                if node.parent and node.parent.type == "source_file":
                    sig = extract_function_signature(node, source_bytes)
                    sig["type"] = "async_function" if sig["is_async"] else "function"
                    standalone_functions.append(sig)

            for child in node.children:
                visit_node(child)

        visit_node(root)

        return {
            "structs": structs,
            "enums": enums,
            "traits": traits,
            "impls": impls,
            "standalone_functions": standalone_functions,
        }

    except FileNotFoundError:
        return {"error": f"File '{filename}' not found."}
    except Exception as e:
        return {"error": f"Error analyzing '{filename}': {e}"}


def format_results(results: dict, filename: str) -> str:
    """
    Format the analysis results into a string.

    Args:
        results: Results from summarize_file
        filename: Name of the analyzed file

    Returns:
        Formatted analysis results
    """
    if results is None:
        return "No results to display."

    if "error" in results:
        return f"Error: {results['error']}"

    output_lines = []
    output_lines.append(f"=== Analysis Results for '{filename}' ===")

    # Format structs
    structs = results.get("structs", [])
    output_lines.append(f"\n📦 Structs ({len(structs)}):")
    if structs:
        for struct in structs:
            vis = f"{struct['visibility']} " if struct["visibility"] else ""
            gen = struct["generics"] if struct["generics"] else ""
            output_lines.append(f"  {vis}struct {struct['name']}{gen}")
            if struct["fields"]:
                for field in struct["fields"]:
                    output_lines.append(f"    - {field['name']}: {field['type']}")
    else:
        output_lines.append("  (No structs found)")

    # Format enums
    enums = results.get("enums", [])
    output_lines.append(f"\n🔢 Enums ({len(enums)}):")
    if enums:
        for enum in enums:
            vis = f"{enum['visibility']} " if enum["visibility"] else ""
            gen = enum["generics"] if enum["generics"] else ""
            output_lines.append(f"  {vis}enum {enum['name']}{gen}")
            if enum["variants"]:
                for variant in enum["variants"]:
                    output_lines.append(f"    - {variant}")
    else:
        output_lines.append("  (No enums found)")

    # Format traits
    traits = results.get("traits", [])
    output_lines.append(f"\n🎭 Traits ({len(traits)}):")
    if traits:
        for trait in traits:
            vis = f"{trait['visibility']} " if trait["visibility"] else ""
            gen = trait["generics"] if trait["generics"] else ""
            output_lines.append(f"  {vis}trait {trait['name']}{gen}")
            if trait["methods"]:
                for method in trait["methods"]:
                    async_prefix = "async " if method["is_async"] else ""
                    ret = (
                        f" -> {method['return_type']}" if method["return_type"] else ""
                    )
                    gen_m = method["generics"] if method["generics"] else ""
                    output_lines.append(
                        f"    - {async_prefix}fn {method['name']}{gen_m}({method['parameters']}){ret}",
                    )
    else:
        output_lines.append("  (No traits found)")

    # Format impl blocks grouped by type
    impls = results.get("impls", [])
    output_lines.append(f"\n🔧 Implementations ({len(impls)} impl blocks):")
    if impls:
        # Group by type_name
        impl_by_type: dict[str, list[dict[str, Any]]] = {}
        for impl in impls:
            type_name = impl["type_name"] or "Unknown"
            if type_name not in impl_by_type:
                impl_by_type[type_name] = []
            impl_by_type[type_name].append(impl)

        for type_name, type_impls in impl_by_type.items():
            output_lines.append(f"\n  📁 {type_name}:")
            for impl in type_impls:
                if impl["trait_name"]:
                    output_lines.append(
                        f"    impl {impl['trait_name']} for {type_name}:",
                    )
                else:
                    output_lines.append(f"    impl {type_name}:")

                sync_methods = [m for m in impl["methods"] if not m["is_async"]]
                async_methods = [m for m in impl["methods"] if m["is_async"]]

                if sync_methods:
                    for method in sync_methods:
                        vis = f"{method['visibility']} " if method["visibility"] else ""
                        ret = (
                            f" -> {method['return_type']}"
                            if method["return_type"]
                            else ""
                        )
                        gen = method["generics"] if method["generics"] else ""
                        output_lines.append(
                            f"      - {vis}fn {method['name']}{gen}({method['parameters']}){ret}",
                        )

                if async_methods:
                    for method in async_methods:
                        vis = f"{method['visibility']} " if method["visibility"] else ""
                        ret = (
                            f" -> {method['return_type']}"
                            if method["return_type"]
                            else ""
                        )
                        gen = method["generics"] if method["generics"] else ""
                        output_lines.append(
                            f"      - {vis}async fn {method['name']}{gen}({method['parameters']}){ret} ⚡",
                        )
    else:
        output_lines.append("  (No impl blocks found)")

    # Format standalone functions
    functions = results.get("standalone_functions", [])
    sync_functions = [f for f in functions if not f["is_async"]]
    async_functions = [f for f in functions if f["is_async"]]

    output_lines.append(f"\n🔧 Standalone Functions ({len(functions)} total):")

    if sync_functions:
        output_lines.append(f"\n  Synchronous Functions ({len(sync_functions)}):")
        for func in sync_functions:
            vis = f"{func['visibility']} " if func["visibility"] else ""
            ret = f" -> {func['return_type']}" if func["return_type"] else ""
            gen = func["generics"] if func["generics"] else ""
            output_lines.append(
                f"    - {vis}fn {func['name']}{gen}({func['parameters']}){ret}",
            )

    if async_functions:
        output_lines.append(f"\n  Asynchronous Functions ({len(async_functions)}):")
        for func in async_functions:
            vis = f"{func['visibility']} " if func["visibility"] else ""
            ret = f" -> {func['return_type']}" if func["return_type"] else ""
            gen = func["generics"] if func["generics"] else ""
            output_lines.append(
                f"    - {vis}async fn {func['name']}{gen}({func['parameters']}){ret} ⚡",
            )

    if not sync_functions and not async_functions:
        output_lines.append("  (No standalone functions found)")

    # Summary
    total_methods = sum(len(impl["methods"]) for impl in impls)
    total_trait_methods = sum(len(trait["methods"]) for trait in traits)

    output_lines.append(f"\n📊 Summary:")
    output_lines.append(f"  - Structs: {len(structs)}")
    output_lines.append(f"  - Enums: {len(enums)}")
    output_lines.append(f"  - Traits: {len(traits)}")
    output_lines.append(f"  - Trait Methods: {total_trait_methods}")
    output_lines.append(f"  - Impl Blocks: {len(impls)}")
    output_lines.append(f"  - Impl Methods: {total_methods}")
    output_lines.append(f"  - Standalone Functions: {len(functions)}")
    output_lines.append(
        f"  - Total Callable Items: {total_methods + total_trait_methods + len(functions)}",
    )

    output_lines.append("\n" + "=" * 80)

    return "\n".join(output_lines)


def analyze_rust_file(filename: str) -> str:
    """
    Main analysis function that returns formatted results as a string.

    Args:
        filename: Path to the Rust file to analyze

    Returns:
        Formatted analysis results
    """
    results = summarize_file(filename)
    return format_results(results, filename)


def main():
    """Main function to handle command line arguments and run the analysis."""
    if len(sys.argv) != 2:
        print("Usage: python rust_analyzer.py <rust_file>")
        print("Example: python rust_analyzer.py main.rs")
        print("\nThis tool will show:")
        print("  - Structs with their fields")
        print("  - Enums with their variants")
        print("  - Traits with their method signatures")
        print("  - Impl blocks with their methods")
        print("  - Standalone functions (sync & async)")
        print("  - Summary statistics")
        sys.exit(1)

    filename = sys.argv[1]

    if not os.path.exists(filename):
        print(f"Error: File '{filename}' does not exist.")
        sys.exit(1)

    if not filename.endswith(".rs"):
        print(
            f"Warning: '{filename}' doesn't have a .rs extension. Proceeding anyway...",
        )

    result_string = analyze_rust_file(filename)
    print(result_string)


if __name__ == "__main__":
    main()
