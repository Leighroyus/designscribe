"""MCP Server — Model Context Protocol server for DesignScribe.

Exposes DesignScribe tools to coding agents via the MCP protocol.

Usage:
    designscribe mcp                    # Start MCP server (stdio)
    designscribe mcp --port 3000        # Start MCP server (HTTP)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# MCP tool definitions
TOOLS = [
    {
        "name": "designscribe_record",
        "description": "Record file changes and update the architecture documentation. Call this after writing or modifying code files.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of file paths that changed"
                },
                "task": {
                    "type": "string",
                    "description": "What the agent was doing (e.g., 'Added OAuth2 login flow')"
                }
            },
            "required": ["files"]
        }
    },
    {
        "name": "designscribe_narrate",
        "description": "Generate an LLM narration of pending changes — summary, rationale, data flow, impact analysis, and architecture diagram.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "Task context for the narration"
                },
                "model": {
                    "type": "string",
                    "description": "LLM model to use (default: openai/gpt-4o-mini)"
                }
            }
        }
    },
    {
        "name": "designscribe_graph",
        "description": "Query the dependency graph. Ask what depends on a symbol, what a symbol depends on, or get impact analysis for files.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["dependencies", "dependents", "impact", "stats"],
                    "description": "What to query"
                },
                "symbol": {
                    "type": "string",
                    "description": "Symbol or file to query (for dependencies/dependents/impact)"
                }
            },
            "required": ["action"]
        }
    },
    {
        "name": "designscribe_architecture",
        "description": "Retrieve the living architecture document — all recorded design decisions, data flows, and diagrams.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "since": {
                    "type": "string",
                    "description": "Only include entries since this date (ISO format, e.g., 2026-06-01)"
                },
                "limit": {
                    "type": "integer",
                    "description": "Max number of entries to return (default: 10)"
                }
            }
        }
    },
    {
        "name": "designscribe_diff",
        "description": "Show structural changes in files — what functions, classes, and imports were added, removed, or modified.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Files to diff"
                }
            },
            "required": ["files"]
        }
    }
]


def _handle_record(args: dict) -> dict:
    """Handle designscribe_record tool call."""
    import subprocess
    files = args.get("files", [])
    task = args.get("task", "")
    cmd = ["designscribe", "record"] + files
    if task:
        cmd += ["--task", task]
    cmd += ["--no-narrate"]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    return {"content": [{"type": "text", "text": result.stdout or result.stderr}]}


def _handle_narrate(args: dict) -> dict:
    """Handle designscribe_narrate tool call."""
    import subprocess
    cmd = ["designscribe", "narrate"]
    task = args.get("task")
    if task:
        cmd += ["--task", task]
    model = args.get("model")
    if model:
        cmd += ["--model", model]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    return {"content": [{"type": "text", "text": result.stdout or result.stderr}]}


def _handle_graph(args: dict) -> dict:
    """Handle designscribe_graph tool call."""
    import subprocess
    action = args.get("action", "stats")
    symbol = args.get("symbol", "")
    cmd = ["designscribe", "graph", action]
    if symbol:
        cmd.append(symbol)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    return {"content": [{"type": "text", "text": result.stdout or result.stderr}]}


def _handle_architecture(args: dict) -> dict:
    """Handle designscribe_architecture tool call."""
    arch_file = Path("living-arch.md")
    if not arch_file.exists():
        return {"content": [{"type": "text", "text": "No architecture document found. Run `designscribe run` first."}]}

    content = arch_file.read_text(encoding="utf-8")
    limit = args.get("limit", 10)
    since = args.get("since")

    # Simple filtering — just return the file
    if since:
        lines = content.split("\n")
        filtered = []
        include = False
        for line in lines:
            if line.startswith("## 📝") and since in line:
                include = True
            if include:
                filtered.append(line)
        content = "\n".join(filtered) if filtered else f"No entries found since {since}"

    # Truncate if too long
    if len(content) > 10000:
        content = content[:10000] + "\n\n... (truncated)"

    return {"content": [{"type": "text", "text": content}]}


def _handle_diff(args: dict) -> dict:
    """Handle designscribe_diff tool call."""
    import subprocess
    files = args.get("files", [])
    cmd = ["designscribe", "diff"] + [f for f in files]
    if not files:
        return {"content": [{"type": "text", "text": "No files specified."}]}
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    return {"content": [{"type": "text", "text": result.stdout or result.stderr}]}


HANDLERS = {
    "designscribe_record": _handle_record,
    "designscribe_narrate": _handle_narrate,
    "designscribe_graph": _handle_graph,
    "designscribe_architecture": _handle_architecture,
    "designscribe_diff": _handle_diff,
}


def _make_response(id, result):
    return {"jsonrpc": "2.0", "id": id, "result": result}


def _make_error(id, code, message):
    return {"jsonrpc": "2.0", "id": id, "error": {"code": code, "message": message}}


def run_stdio():
    """Run MCP server on stdin/stdout (stdio mode)."""
    # Send server info
    server_info = {
        "name": "designscribe",
        "version": "0.1.0",
    }

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue

        method = request.get("method", "")
        id = request.get("id")
        params = request.get("params", {})

        if method == "initialize":
            result = {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": server_info,
            }
            print(json.dumps(_make_response(id, result)), flush=True)

        elif method == "tools/list":
            result = {"tools": TOOLS}
            print(json.dumps(_make_response(id, result)), flush=True)

        elif method == "tools/call":
            tool_name = params.get("name", "")
            tool_args = params.get("arguments", {})
            handler = HANDLERS.get(tool_name)
            if handler:
                try:
                    result = handler(tool_args)
                    print(json.dumps(_make_response(id, result)), flush=True)
                except Exception as e:
                    print(json.dumps(_make_error(id, -32000, str(e))), flush=True)
            else:
                print(json.dumps(_make_error(id, -32601, f"Unknown tool: {tool_name}")), flush=True)

        elif method == "notifications/initialized":
            # Client acknowledged initialization
            pass

        else:
            if id is not None:
                print(json.dumps(_make_error(id, -32601, f"Unknown method: {method}")), flush=True)


if __name__ == "__main__":
    run_stdio()
