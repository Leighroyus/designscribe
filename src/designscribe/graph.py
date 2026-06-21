"""Dependency graph — build, maintain, and query code dependencies using NetworkX.

Nodes are "file:symbol" keys. Edges represent imports and calls.
"""
from __future__ import annotations

import json
from pathlib import Path

import networkx as nx

from .differ import _extract_symbols


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------

class Graph:
    """Directed graph of code dependencies."""

    def __init__(self, path: str = ".designscribe/graph.json"):
        self.path = path
        self.g = nx.DiGraph()
        self._file_symbols: dict[str, dict] = {}  # file -> {symbol_name: info}

    # -- Persistence --------------------------------------------------------

    def save(self):
        """Persist graph to disk as JSON."""
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        data = {
            "nodes": list(self.g.nodes(data=True)),
            "edges": list(self.g.edges(data=True)),
            "file_symbols": self._file_symbols,
        }
        # Convert node/edge data to serializable form
        serializable_nodes = []
        for node, attrs in data["nodes"]:
            serializable_nodes.append([node, attrs])
        serializable_edges = []
        for u, v, attrs in data["edges"]:
            serializable_edges.append([u, v, attrs])

        with open(self.path, "w") as f:
            json.dump({
                "nodes": serializable_nodes,
                "edges": serializable_edges,
                "file_symbols": self._file_symbols,
            }, f, indent=2)

    def load(self):
        """Load graph from disk."""
        with open(self.path) as f:
            data = json.load(f)
        self.g = nx.DiGraph()
        for node, attrs in data.get("nodes", []):
            self.g.add_node(node, **attrs)
        for u, v, attrs in data.get("edges", []):
            self.g.add_edge(u, v, **attrs)
        self._file_symbols = data.get("file_symbols", {})

    # -- Scanning -----------------------------------------------------------

    def scan(self, root: str):
        """Full scan of codebase, rebuild graph from scratch."""
        self.g = nx.DiGraph()
        self._file_symbols = {}
        root_path = Path(root).resolve()

        for py_file in root_path.rglob("*.py"):
            # Skip common non-source dirs
            rel = str(py_file.relative_to(root_path))
            if any(skip in rel for skip in ("__pycache__", ".git", "node_modules", ".venv", "venv", ".designscribe")):
                continue
            self._parse_file(py_file, root_path)

        # Build edges from imports
        self._resolve_imports(root_path)

    def update(self, files: list[str]):
        """Incremental update — re-parse only changed files."""
        root_path = Path(self.path).parent.parent.resolve()  # best guess

        for fpath in files:
            p = Path(fpath).resolve()
            if not p.suffix == ".py" or not p.exists():
                continue
            # Remove old nodes for this file
            file_key = str(p)
            old_nodes = [n for n in self.g.nodes if n.startswith(file_key + ":")]
            self.g.remove_nodes_from(old_nodes)
            if file_key in self._file_symbols:
                del self._file_symbols[file_key]

            # Re-parse
            self._parse_file(p, root_path)

        # Rebuild edges for changed files
        self._resolve_imports(root_path)

    # -- Queries ------------------------------------------------------------

    def dependencies(self, symbol: str) -> list[str]:
        """What does this symbol depend on?"""
        if symbol in self.g:
            return list(self.g.successors(symbol))
        return []

    def dependents(self, symbol: str) -> list[str]:
        """What depends on this symbol?"""
        if symbol in self.g:
            return list(self.g.predecessors(symbol))
        return []

    def impact(self, files: list[str]) -> list[str]:
        """BFS to find downstream affected files."""
        affected = set()
        for fpath in files:
            # Find all nodes in this file
            file_prefix = str(Path(fpath).resolve())
            file_nodes = [n for n in self.g.nodes if n.startswith(file_prefix + ":")]
            # BFS from these nodes
            for start in file_nodes:
                for node in nx.descendants(self.g, start):
                    # Extract file path from node key
                    if ":" in node:
                        affected.add(node.split(":")[0])
        return sorted(affected)

    def get_all_symbols(self) -> dict:
        """Return all file -> symbols mapping."""
        return dict(self._file_symbols)

    def node_count(self) -> int:
        return self.g.number_of_nodes()

    def edge_count(self) -> int:
        return self.g.number_of_edges()

    # -- Internal -----------------------------------------------------------

    def _parse_file(self, file_path: Path, root_path: Path):
        """Parse a single Python file and add nodes to the graph."""
        try:
            source = file_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return

        file_key = str(file_path)
        rel_path = str(file_path.relative_to(root_path))
        symbols = _extract_symbols(source, rel_path)

        self._file_symbols[file_key] = symbols

        for sym_name, info in symbols.items():
            node_key = f"{file_key}:{sym_name}"
            self.g.add_node(node_key, **info)

    def _resolve_imports(self, root_path: Path):
        """Build edges by resolving imports to known symbols."""
        # Build a lookup: symbol_name -> node_key
        sym_lookup: dict[str, list[str]] = {}
        for node_key in self.g.nodes:
            if ":" in node_key:
                _, sym = node_key.split(":", 1)
                sym_lookup.setdefault(sym, []).append(node_key)

        # For each import node, try to find the target
        for node_key, attrs in list(self.g.nodes(data=True)):
            if attrs.get("kind") != "import":
                continue

            import_text = attrs.get("name", "")

            # Parse "from X import Y" or "import X"
            target_name = None
            if import_text.startswith("from "):
                parts = import_text.split(" import ", 1)
                if len(parts) == 2:
                    # "from foo.bar import baz" -> look for "baz"
                    targets = [t.strip().split(" as ")[0].strip() for t in parts[1].split(",")]
                    for t in targets:
                        if t == "*":
                            continue
                        candidates = sym_lookup.get(t, [])
                        for c in candidates:
                            if c != node_key:
                                self.g.add_edge(node_key, c, type="import")
            elif import_text.startswith("import "):
                modules = import_text[7:].split(",")
                for mod in modules:
                    mod = mod.strip().split(" as ")[0].strip()
                    # The module name itself might be a symbol
                    candidates = sym_lookup.get(mod, [])
                    for c in candidates:
                        if c != node_key:
                            self.g.add_edge(node_key, c, type="import")
