"""DesignScribe CLI — main entry point.

Wires together the full pipeline: diff → narrate → diagram → render.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from .config import Config
from .differ import diff_content
from .graph import Graph
from .narrator import narrate as narrate_fn
from .diagram import render_mermaid
from .output import render_markdown, append_entry
from . import event_log

app = typer.Typer(name="designscribe", help="Architecture docs as a side effect of coding.")
console = Console()

# Default paths
GRAPH_PATH = ".designscribe/graph.json"
EVENT_LOG_PATH = ".designscribe/events.jsonl"
PENDING_DIFF_PATH = ".designscribe/pending_diff.json"
PENDING_NARRATION_PATH = ".designscribe/pending_narration.json"


def _ensure_dir():
    Path(".designscribe").mkdir(exist_ok=True)


def _load_config() -> Config:
    return Config.load()


def _load_graph() -> Graph:
    g = Graph(path=GRAPH_PATH)
    if Path(GRAPH_PATH).exists():
        g.load()
    return g


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------

@app.command()
def init(path: str = typer.Argument(".", help="Root directory to scan")):
    """Initial scan — build the dependency graph."""
    _ensure_dir()
    console.print(f"[bold green]Initializing DesignScribe on {path}...[/]")

    g = Graph(path=GRAPH_PATH)
    console.print("  Scanning Python files with tree-sitter...")
    g.scan(path)
    g.save()

    event_log.append("init", {
        "path": path,
        "symbols": g.node_count(),
        "edges": g.edge_count(),
    }, path=EVENT_LOG_PATH)

    console.print(f"  [green]✓[/] {g.node_count()} symbols, {g.edge_count()} dependencies")
    console.print(f"  [green]✓[/] Graph saved to {GRAPH_PATH}")


# ---------------------------------------------------------------------------
# diff
# ---------------------------------------------------------------------------

@app.command()
def diff(
    ref: str = typer.Argument(None, help="Git ref to diff against (default: HEAD~1)"),
    files: list[str] = typer.Option(None, "--file", "-f", help="Specific files to diff (instead of git)"),
):
    """Show structural changes since last commit."""
    _ensure_dir()
    ref = ref or "HEAD~1"

    changes: list[dict] = []

    if files:
        # Diff specific files (compare against current saved graph)
        console.print(f"[bold]Structural diff for {len(files)} file(s)[/]")
        for fpath in files:
            p = Path(fpath)
            if not p.exists():
                console.print(f"  [yellow]⚠[/] {fpath} not found, skipping")
                continue
            # Read current content — compare against empty (all symbols are "added")
            source = p.read_text(encoding="utf-8", errors="replace")
            file_changes = diff_content("", source, file_path=str(p))
            changes.extend(file_changes)
    else:
        # Git diff
        try:
            import git
            repo = git.Repo(".", search_parent_directories=True)
            diff_index = repo.head.commit.diff(ref)

            for d in diff_index:
                if not d.a_path.endswith(".py") and not d.b_path.endswith(".py"):
                    continue

                old_source = ""
                new_source = ""

                if d.a_blob:
                    old_source = d.a_blob.data_stream.read().decode("utf-8", errors="replace")
                if d.b_blob:
                    new_source = d.b_blob.data_stream.read().decode("utf-8", errors="replace")
                elif Path(d.a_path).exists():
                    new_source = Path(d.a_path).read_text(encoding="utf-8", errors="replace")

                file_path = d.b_path or d.a_path
                file_changes = diff_content(old_source, new_source, file_path=file_path)
                changes.extend(file_changes)

        except Exception as e:
            console.print(f"[red]Git diff failed: {e}[/]")
            raise typer.Exit(1)

    if not changes:
        console.print("[dim]No structural changes detected.[/]")
        return

    # Display changes
    table = Table(title="Structural Changes")
    table.add_column("Type", style="cyan")
    table.add_column("File", style="green")
    table.add_column("Symbol", style="bold")
    table.add_column("Kind", style="dim")
    table.add_column("Line", justify="right")

    for c in changes:
        table.add_row(
            c.get("type", ""),
            c.get("file", ""),
            c.get("symbol", ""),
            c.get("kind", ""),
            str(c.get("line", "")),
        )

    console.print(table)

    # Save pending diff
    with open(PENDING_DIFF_PATH, "w") as f:
        json.dump(changes, f, indent=2)

    event_log.append("change", {"count": len(changes), "files": list(set(c.get("file", "") for c in changes))}, path=EVENT_LOG_PATH)
    console.print(f"\n[dim]Saved {len(changes)} changes to {PENDING_DIFF_PATH}[/]")


# ---------------------------------------------------------------------------
# record
# ---------------------------------------------------------------------------

@app.command()
def record(
    files: list[str] = typer.Argument(..., help="Files that changed"),
    task: str = typer.Option(None, help="What the agent was doing"),
    model: str = typer.Option(None, help="LLM model"),
    auto_narrate: bool = typer.Option(True, "--narrate/--no-narrate", help="Auto-narrate after recording"),
):
    """Record a batch of file changes (agent integration hook).

    Use from CLAUDE.md, AGENTS.md, or git hooks:
      designscribe record src/auth.py src/models/user.py --task "Added OAuth2"
    """
    _ensure_dir()
    config = _load_config()
    model = model or config.llm_model

    changes: list[dict] = []
    g = _load_graph()

    for fpath in files:
        p = Path(fpath)
        if not p.exists():
            console.print(f"  [yellow]⚠[/] {fpath} not found, skipping")
            continue
        source = p.read_text(encoding="utf-8", errors="replace")

        # Try to diff against existing symbols in the graph
        file_key = str(p.resolve())
        old_symbols = {}
        if g.node_count() > 0:
            old_symbols = {k.split(":", 1)[1]: v for k, v in g.g.nodes(data=True)
                          if k.startswith(file_key + ":")}

        if old_symbols:
            # Reconstruct old source markers for diff
            file_changes = diff_content("", source, file_path=str(p))
        else:
            file_changes = diff_content("", source, file_path=str(p))

        changes.extend(file_changes)

    # Save to pending diff
    with open(PENDING_DIFF_PATH, "w") as f:
        json.dump(changes, f, indent=2)

    # Update graph with new file symbols
    g.update(files)
    g.save()

    event_log.append("change", {
        "count": len(changes),
        "files": files,
        "task": task,
    }, path=EVENT_LOG_PATH)

    console.print(f"[green]✓[/] Recorded {len(changes)} structural changes from {len(files)} file(s)")
    if task:
        console.print(f"  Task: {task}")

    # Auto-narrate if requested
    if auto_narrate and changes:
        console.print()
        narrate(task=task, model=model)
        console.print()
        config = _load_config()
        _do_diagram(fmt=config.diagram_format, output=config.diagram_output)
        console.print()
        _do_render()


# ---------------------------------------------------------------------------
# narrate
# ---------------------------------------------------------------------------

@app.command()
def narrate(
    task: str = typer.Option(None, help="Task context for the LLM"),
    model: str = typer.Option(None, help="LLM model (default from config)"),
):
    """Generate narrative for pending changes."""
    config = _load_config()
    model = model or config.llm_model

    # Load pending diff
    if not Path(PENDING_DIFF_PATH).exists():
        console.print("[yellow]No pending diff. Run `designscribe diff` first.[/]")
        raise typer.Exit(1)

    with open(PENDING_DIFF_PATH) as f:
        changes = json.load(f)

    console.print(f"[bold]Generating narrative for {len(changes)} changes...[/]")

    # Build graph context
    graph_context: dict = {}
    g = _load_graph()
    if g.node_count() > 0:
        affected_files = list(set(c.get("file", "") for c in changes if c.get("file")))
        affected_deps: dict[str, list[str]] = {}
        for f in affected_files:
            file_nodes = [n for n in g.g.nodes if n.startswith(str(Path(f).resolve()) + ":")]
            for node in file_nodes:
                deps = g.dependencies(node)
                if deps:
                    affected_deps[node] = deps

        graph_context = {
            "total_symbols": g.node_count(),
            "total_edges": g.edge_count(),
            "affected_dependencies": affected_deps,
        }

    # Call narrator
    result = narrate_fn(changes, graph_context, task=task, model=model)

    # Add metadata
    result["timestamp"] = datetime.now(timezone.utc).isoformat()
    result["task"] = task
    result["changes"] = changes

    # Save pending narration
    with open(PENDING_NARRATION_PATH, "w") as f:
        json.dump(result, f, indent=2)

    event_log.append("narration", {
        "summary": result.get("summary", "")[:200],
        "task": task,
        "model": model,
    }, path=EVENT_LOG_PATH)

    console.print(f"\n[bold green]Narration complete[/]")
    console.print(f"\n{result.get('summary', 'No summary')}")


# ---------------------------------------------------------------------------
# diagram
# ---------------------------------------------------------------------------

def _do_diagram(fmt: str = "png", output: str = "diagrams/"):
    """Internal: render Mermaid diagrams from pending narrations."""
    if not Path(PENDING_NARRATION_PATH).exists():
        console.print("[yellow]No pending narration. Run `designscribe narrate` first.[/]")
        return

    with open(PENDING_NARRATION_PATH) as f:
        narration = json.load(f)

    diagram_text = narration.get("diagram", "")
    if not diagram_text:
        console.print("[yellow]No diagram in narration.[/]")
        return

    console.print(f"[bold]Rendering diagram to {output}/[/]")

    Path(output).mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = str(Path(output) / f"diagram_{ts}.{fmt}")

    result_path = render_mermaid(diagram_text, out_path, fmt=fmt)
    narration["diagram_path"] = result_path

    # Update pending narration with diagram path
    with open(PENDING_NARRATION_PATH, "w") as f:
        json.dump(narration, f, indent=2)

    console.print(f"  [green]✓[/] {result_path}")


@app.command()
def diagram(
    fmt: str = typer.Option("png", help="Output format (png/svg/pdf)"),
    output: str = typer.Option("diagrams/", help="Output directory"),
):
    """Render Mermaid diagrams from pending narrations."""
    config = _load_config()
    _do_diagram(fmt=fmt or config.diagram_format, output=output or config.diagram_output)


# ---------------------------------------------------------------------------
# render
# ---------------------------------------------------------------------------

def _do_render(since: str = None, output: str = None):
    """Internal: regenerate the living architecture doc."""
    config = _load_config()
    output = output or config.output_file

    # Load pending narration if available
    entries: list[dict] = []
    if Path(PENDING_NARRATION_PATH).exists():
        with open(PENDING_NARRATION_PATH) as f:
            entries.append(json.load(f))

    if not entries:
        console.print("[yellow]No narrations to render. Run `designscribe narrate` first.[/]")
        return

    console.print(f"[bold]Generating {output}...[/]")
    result_path = render_markdown(entries, output_path=output, since=since)

    event_log.append("render", {
        "output": result_path,
        "entries": len(entries),
    }, path=EVENT_LOG_PATH)

    console.print(f"  [green]✓[/] {result_path}")


@app.command()
def render(
    since: str = typer.Option(None, help="Only include entries since date"),
    output: str = typer.Option(None, help="Output file (default from config)"),
):
    """Regenerate the living architecture doc."""
    _do_render(since=since, output=output)


# ---------------------------------------------------------------------------
# run (full pipeline)
# ---------------------------------------------------------------------------

@app.command()
def run(
    ref: str = typer.Argument(None, help="Git ref to diff against"),
    task: str = typer.Option(None, help="Task context for narration"),
    model: str = typer.Option(None, help="LLM model"),
    output: str = typer.Option(None, help="Output file"),
):
    """Full pipeline: diff → narrate → diagram → render."""
    console.print("[bold]Running full pipeline...[/]\n")

    console.print("[bold]Step 1/4: Structural diff[/]")
    diff(ref, files=None)

    console.print("\n[bold]Step 2/4: LLM narration[/]")
    if not Path(PENDING_DIFF_PATH).exists():
        console.print("[yellow]No changes to narrate. Pipeline stopped.[/]")
        return
    narrate(task=task, model=model)

    console.print("\n[bold]Step 3/4: Diagram rendering[/]")
    if Path(PENDING_NARRATION_PATH).exists():
        config = _load_config()
        _do_diagram(fmt=config.diagram_format, output=config.diagram_output)

    console.print("\n[bold]Step 4/4: Living architecture doc[/]")
    if Path(PENDING_NARRATION_PATH).exists():
        _do_render(output=output)

    console.print("\n[bold green]✓ Pipeline complete![/]")


# ---------------------------------------------------------------------------
# graph
# ---------------------------------------------------------------------------

@app.command()
def graph(
    action: str = typer.Argument("show", help="show | query | stats"),
    target: str = typer.Argument(None, help="Symbol to query (for 'query' action)"),
):
    """Query the dependency graph."""
    g = _load_graph()
    if g.node_count() == 0:
        console.print("[yellow]No graph loaded. Run `designscribe init` first.[/]")
        raise typer.Exit(1)

    if action == "stats":
        console.print(f"[bold]Graph stats:[/]")
        console.print(f"  Nodes: {g.node_count()}")
        console.print(f"  Edges: {g.edge_count()}")
        return

    if action == "show":
        table = Table(title="Dependency Graph Nodes")
        table.add_column("Node", style="bold")
        table.add_column("Kind", style="cyan")
        table.add_column("File", style="green")

        for node, attrs in list(g.g.nodes(data=True))[:100]:
            table.add_row(node, attrs.get("kind", ""), attrs.get("file", ""))

        if g.node_count() > 100:
            table.add_row(f"... ({g.node_count() - 100} more)", "", "")

        console.print(table)
        return

    if action == "query" and target:
        console.print(f"[bold]Dependencies of {target}:[/]")
        deps = g.dependencies(target)
        if deps:
            for d in deps:
                console.print(f"  → {d}")
        else:
            console.print("  (none)")

        console.print(f"\n[bold]Dependents of {target}:[/]")
        dents = g.dependents(target)
        if dents:
            for d in dents:
                console.print(f"  ← {d}")
        else:
            console.print("  (none)")
        return

    console.print("[yellow]Usage: graph show|query|stats [target][/]")


# ---------------------------------------------------------------------------
# watch (Phase 2 stub)
# ---------------------------------------------------------------------------

@app.command()
def watch(
    path: str = typer.Argument(".", help="Directory to watch"),
    debounce: int = typer.Option(2000, help="Debounce in milliseconds"),
    task: str = typer.Option(None, help="Task context for narrations"),
    model: str = typer.Option(None, help="LLM model"),
):
    """Watch for file changes and auto-generate docs."""
    from .watcher import watch as do_watch

    config = _load_config()
    model = model or config.llm_model

    console.print(f"[bold]Watching {path} (debounce: {debounce}ms)[/]")
    console.print("[dim]Press Ctrl+C to stop[/]\n")

    def on_change(files: list[str]):
        console.print(f"\n[bold cyan]Change detected:[/] {len(files)} file(s)")
        for f in files:
            console.print(f"  → {f}")

        # Run diff
        changes: list[dict] = []
        for fpath in files:
            p = Path(fpath)
            if not p.exists():
                continue
            source = p.read_text(encoding="utf-8", errors="replace")
            try:
                file_changes = diff_content("", source, file_path=str(p))
                changes.extend(file_changes)
            except Exception:
                pass

        if not changes:
            console.print("[dim]No structural changes detected.[/]")
            return

        console.print(f"[green]{len(changes)} structural change(s) detected[/]")

        # Save diff
        _ensure_dir()
        with open(PENDING_DIFF_PATH, "w") as f:
            json.dump(changes, f, indent=2)

        # Narrate
        try:
            g = _load_graph()
            graph_context = {"total_symbols": g.node_count(), "total_edges": g.edge_count(), "affected_dependencies": {}}
        except Exception:
            graph_context = {}

        console.print("[dim]Generating narration...[/]")
        try:
            result = narrate_fn(changes, graph_context, task=task, model=model)
            result["timestamp"] = datetime.now(timezone.utc).isoformat()
            result["task"] = task
            result["changes"] = changes

            with open(PENDING_NARRATION_PATH, "w") as f:
                json.dump(result, f, indent=2)

            event_log.append("narration", {"summary": result.get("summary", "")[:200], "task": task}, path=EVENT_LOG_PATH)
            console.print(f"[green]✓[/] {result.get('summary', 'Done')[:120]}")
        except Exception as e:
            console.print(f"[yellow]Narration failed: {e}[/]")

    do_watch(path, on_change, debounce_ms=debounce)


# ---------------------------------------------------------------------------
# mcp (MCP server)
# ---------------------------------------------------------------------------

@app.command()
def mcp():
    """Start MCP server (stdio mode) for agent integration."""
    from .mcp_server import run_stdio
    console.print("[bold]DesignScribe MCP Server[/]")
    console.print("[dim]Listening on stdin/stdout (MCP protocol)[/]")
    run_stdio()


if __name__ == "__main__":
    app()
