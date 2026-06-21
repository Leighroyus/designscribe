"""AST diffing — structural comparison of code changes using tree-sitter.

Parses Python files with tree-sitter, extracts symbols (functions, classes,
methods, imports), and compares two versions to produce a structural diff.
"""
from __future__ import annotations

import re

from tree_sitter_language_pack import get_parser


# ---------------------------------------------------------------------------
# Parser setup
# ---------------------------------------------------------------------------

def _make_parser():
    return get_parser("python")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _node_text(node, source: str) -> str:
    """Extract text for a node from source string."""
    return source[node.start_byte():node.end_byte()]


def _node_children(node):
    """Generator of all children."""
    for i in range(node.child_count()):
        yield node.child(i)


def _node_line(node) -> int:
    """Get 1-indexed line number for a node."""
    return node.start_position().row + 1


def _is_identifier(s: str) -> bool:
    """Check if a string is a valid Python identifier."""
    return bool(re.fullmatch(r'\w+', s))


# ---------------------------------------------------------------------------
# Symbol extraction
# ---------------------------------------------------------------------------

def _extract_symbols(source: str, file_path: str = "<unknown>") -> dict[str, dict]:
    """Parse Python source and return a dict of symbol_name -> info."""
    parser = _make_parser()
    # Tree-sitter works with byte offsets, so use parse_bytes
    source_bytes = source.encode("utf-8")
    tree = parser.parse_bytes(source_bytes)
    root = tree.root_node()

    symbols: dict[str, dict] = {}

    def _all_identifiers(node):
        """Yield all identifier nodes under this node (DFS)."""
        if node.kind() == "identifier":
            yield node
        for child in _node_children(node):
            yield from _all_identifiers(child)

    def _node_text_b(node) -> str:
        """Extract text for a node using byte offsets."""
        return source_bytes[node.start_byte():node.end_byte()].decode("utf-8", errors="replace")

    def _handle_function(node, class_context: str | None = None):
        """Handle a function_definition node. child[1] is always the identifier."""
        children = list(_node_children(node))
        if len(children) < 2 or children[1].kind() != "identifier":
            return

        name = _node_text_b(children[1])
        if not _is_identifier(name):
            return

        qualname = f"{class_context}.{name}" if class_context else name
        sym_kind = "method" if class_context else "function"

        # Parameters are at index 2
        params_text = ""
        if len(children) > 2 and children[2].kind() == "parameters":
            params_text = _node_text_b(children[2])

        # Return type annotation: look for "->" token
        ret_type = ""
        for i, child in enumerate(children):
            if child.kind() == "->" and i + 1 < len(children):
                ret_type = _node_text_b(children[i + 1])
                break

        signature = f"({params_text})"
        if ret_type:
            signature = f"({params_text}) -> {ret_type}"

        symbols[qualname] = {
            "kind": sym_kind,
            "name": name,
            "qualified_name": qualname,
            "line": _node_line(node),
            "signature": signature,
            "file": file_path,
        }

    def _handle_class(node):
        """Handle a class_definition node. child[1] is always the identifier."""
        children = list(_node_children(node))
        if len(children) < 2 or children[1].kind() != "identifier":
            return

        name = _node_text_b(children[1])
        if not _is_identifier(name):
            return

        symbols[name] = {
            "kind": "class",
            "name": name,
            "qualified_name": name,
            "line": _node_line(node),
            "signature": "",
            "file": file_path,
        }

        # Visit children to find methods
        for child in _node_children(node):
            if child.kind() == "function_definition":
                _handle_function(child, class_context=name)

    def _handle_import(node):
        """Handle an import statement."""
        kind = node.kind()

        if kind == "import_statement":
            # "import X.Y.Z" — find the last dotted_name
            dotted_names = [c for c in _node_children(node) if c.kind() == "dotted_name"]
            if dotted_names:
                target = _node_text_b(dotted_names[-1])
                symbols[f"__import__:import {target}"] = {
                    "kind": "import",
                    "name": f"import {target}",
                    "qualified_name": f"import {target}",
                    "line": _node_line(node),
                    "signature": "",
                    "file": file_path,
                }

        elif kind == "import_from_statement":
            # "from X.Y import A, B" — find the last identifier as the imported name(s)
            all_ids = list(_all_identifiers(node))
            if len(all_ids) >= 2:
                # First identifier is the module, rest are imported names
                module_parts = []
                imported_parts = []
                for i, id_node in enumerate(all_ids):
                    id_text = _node_text_b(id_node)
                    if i == 0:
                        module_parts.append(id_text)
                    elif any(
                        c.kind() in ("comma", "import", "from")
                        for c in _node_children(node)
                        if c.start_byte() > all_ids[i - 1].end_byte()
                        and c.start_byte() < id_node.start_byte()
                    ):
                        # This is a module part
                        module_parts.append(id_text)
                    else:
                        imported_parts.append(id_text)

                # Actually: just grab the last 1-2 identifiers as imported names
                if len(all_ids) >= 2:
                    # module is everything except the last identifier(s)
                    module = ".".join(_node_text_b(id_node) for id_node in all_ids[:-1])
                    imported = _node_text_b(all_ids[-1])
                    symbols[f"__import__:from {module} import {imported}"] = {
                        "kind": "import",
                        "name": f"from {module} import {imported}",
                        "qualified_name": f"from {module} import {imported}",
                        "line": _node_line(node),
                        "signature": "",
                        "file": file_path,
                    }

    def _visit(node, class_context: str | None = None):
        kind = node.kind()

        if kind == "function_definition":
            _handle_function(node, class_context)
            return

        elif kind == "class_definition":
            _handle_class(node)
            return

        elif kind == "decorated_definition":
            # Unwrap: visit the wrapped definition
            for child in _node_children(node):
                _visit(child, class_context=None)
            return

        elif kind in ("import_statement", "import_from_statement"):
            _handle_import(node)
            return

        for child in _node_children(node):
            _visit(child, class_context=class_context)

    _visit(root)
    return symbols


# ---------------------------------------------------------------------------
# Diff
# ---------------------------------------------------------------------------

def diff_content(old_source: str, new_source: str, file_path: str = "<unknown>") -> list[dict]:
    """Compare two Python source strings and return structural changes."""
    old_syms = _extract_symbols(old_source, file_path) if old_source.strip() else {}
    new_syms = _extract_symbols(new_source, file_path) if new_source.strip() else {}

    changes: list[dict] = []

    for key, info in old_syms.items():
        if key not in new_syms:
            changes.append({
                "type": "import_removed" if info["kind"] == "import" else "symbol_removed",
                "file": file_path,
                "symbol": info["qualified_name"],
                "kind": info["kind"],
                "line": info["line"],
            })

    for key, info in new_syms.items():
        if key not in old_syms:
            changes.append({
                "type": "import_added" if info["kind"] == "import" else "symbol_added",
                "file": file_path,
                "symbol": info["qualified_name"],
                "kind": info["kind"],
                "line": info["line"],
            })

    for key in old_syms:
        if key in new_syms:
            old_info = old_syms[key]
            new_info = new_syms[key]
            if old_info["kind"] != "import" and old_info.get("signature") != new_info.get("signature"):
                changes.append({
                    "type": "signature_changed",
                    "file": file_path,
                    "symbol": old_info["qualified_name"],
                    "kind": old_info["kind"],
                    "old_sig": old_info["signature"],
                    "new_sig": new_info["signature"],
                })

    return changes


def diff_files(old_path: str, new_path: str) -> list[dict]:
    """Compare two file versions structurally by reading from disk."""
    with open(old_path) as f:
        old_source = f.read()
    with open(new_path) as f:
        new_source = f.read()
    return diff_content(old_source, new_source, file_path=new_path)
