#!/usr/bin/env python3

import ast
import os

def find_imports_in_file(filepath):
    """
    Parse a Python file and return a list of modules/packages it imports.
    """
    with open(filepath, 'r', encoding='utf-8') as f:
        source = f.read()

    try:
        tree = ast.parse(source, filename=filepath)
    except SyntaxError as e:
        # If there's a syntax error in the file, skip it or print a warning
        return []

    imports_found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            # e.g. "import x, y, z"
            for alias in node.names:
                imports_found.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            # e.g. "from x import y"
            # node.module could be None in case of "from . import x", so check
            module_name = node.module if node.module else "(relative import)"
            imports_found.append(module_name)

    return imports_found

def main():
    root_dir = os.path.dirname(os.path.abspath(__file__))
    print("Scanning for imports in all .py files under:", root_dir)
    print("")

    for dirpath, dirnames, filenames in os.walk(root_dir):
        # Skip the __pycache__ or hidden directories
        if "__pycache__" in dirpath or dirpath.startswith("."):
            continue

        for filename in filenames:
            if filename.endswith(".py"):
                fullpath = os.path.join(dirpath, filename)
                imports = find_imports_in_file(fullpath)
                if imports:
                    relative_path = os.path.relpath(fullpath, root_dir)
                    print(f"In {relative_path}:")
                    for imp in imports:
                        print(f"  - imports: {imp}")
                    print("")

if __name__ == "__main__":
    main()
