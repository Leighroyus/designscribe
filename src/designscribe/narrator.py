"""Narrator — LLM-powered design summary generation via OpenRouter.

Builds a prompt from structural diff + graph context, calls the LLM,
and parses the response into a structured narration dict.
"""
from __future__ import annotations

import json
import os
import urllib.request
import urllib.error
from typing import Any


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """You are DesignScribe, an architecture documentation assistant.
Given a structural diff of code changes and dependency graph context,
produce a concise architectural narrative.

Respond ONLY with valid JSON (no markdown fences). Schema:
{
  "summary": "One-paragraph summary of what changed and why it matters architecturally",
  "rationale": "Why these changes were made (infer from the diff)",
  "data_flow": "Describe how data flows through the changed components",
  "impact": ["list", "of", "potentially", "affected", "files"],
  "diagram": "mermaid graph syntax showing the changed relationships"
}

The diagram should use Mermaid syntax, e.g.:
graph LR
  A[ModuleA] --> B[ModuleB]
  B --> C[ModuleC]

Keep it focused and technical. No fluff."""


def _build_prompt(changes: list[dict], graph_context: dict, task: str | None = None) -> str:
    """Build the user prompt for the LLM."""
    parts = []

    if task:
        parts.append(f"## Task Context\n{task}\n")

    parts.append("## Structural Changes")
    if not changes:
        parts.append("No structural changes detected.")
    else:
        for c in changes[:50]:  # Limit to avoid huge prompts
            kind = c.get("kind", "unknown")
            symbol = c.get("symbol", "?")
            ftype = c.get("type", "unknown")
            file = c.get("file", "?")
            line = c.get("line", "")
            if ftype == "signature_changed":
                parts.append(f"- [{ftype}] {file}:{line} `{symbol}` ({kind}) — was `{c.get('old_sig')}`, now `{c.get('new_sig')}`")
            else:
                parts.append(f"- [{ftype}] {file}:{line} `{symbol}` ({kind})")

    parts.append("\n## Graph Context")
    if graph_context:
        parts.append(f"Total symbols: {graph_context.get('total_symbols', '?')}")
        parts.append(f"Total dependencies: {graph_context.get('total_edges', '?')}")
        deps = graph_context.get("affected_dependencies", {})
        if deps:
            parts.append("Affected dependency chains:")
            for sym, dep_list in list(deps.items())[:20]:
                parts.append(f"  {sym} -> {', '.join(dep_list[:10])}")
    else:
        parts.append("No graph context available.")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------------

def _call_llm(prompt: str, model: str = "xiaomi/mimo-v2.5-pro") -> str:
    """Call OpenRouter API and return the response text."""
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY environment variable not set")

    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
        "max_tokens": 2000,
    }).encode()

    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "HTTP-Referer": "https://github.com/designscribe",
            "X-Title": "DesignScribe",
        },
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = resp.read().decode().strip()
        data = json.loads(raw)

    return data["choices"][0]["message"]["content"]


def _parse_response(text: str) -> dict:
    """Parse LLM JSON response into a structured dict."""
    # Strip markdown fences if present
    text = text.strip()
    if text.startswith("```"):
        # Remove first and last lines
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Try to extract JSON from the text
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1:
            try:
                data = json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                data = None
        else:
            data = None

    if data and isinstance(data, dict):
        return {
            "summary": data.get("summary", ""),
            "rationale": data.get("rationale", ""),
            "data_flow": data.get("data_flow", ""),
            "impact": data.get("impact", []),
            "diagram": data.get("diagram", ""),
        }

    # Fallback if parsing fails
    return {
        "summary": text[:500],
        "rationale": "",
        "data_flow": "",
        "impact": [],
        "diagram": "",
    }


# ---------------------------------------------------------------------------
# Fallback (no LLM)
# ---------------------------------------------------------------------------

def _fallback_narrate(changes: list[dict], task: str | None = None) -> dict:
    """Generate a basic summary without LLM."""
    added = [c for c in changes if "added" in c.get("type", "")]
    removed = [c for c in changes if "removed" in c.get("type", "")]
    modified = [c for c in changes if "changed" in c.get("type", "")]

    parts = []
    if added:
        symbols = ", ".join(f"`{c['symbol']}`" for c in added[:10])
        parts.append(f"Added {len(added)} symbol(s): {symbols}")
    if removed:
        symbols = ", ".join(f"`{c['symbol']}`" for c in removed[:10])
        parts.append(f"Removed {len(removed)} symbol(s): {symbols}")
    if modified:
        symbols = ", ".join(f"`{c['symbol']}`" for c in modified[:10])
        parts.append(f"Modified {len(modified)} symbol(s): {symbols}")

    summary = ". ".join(parts) if parts else "No structural changes detected."
    if task:
        summary = f"[{task}] {summary}"

    # Build a simple mermaid diagram from the changes
    diagram_lines = ["graph LR"]
    affected_files = list(set(c.get("file", "") for c in changes if c.get("file")))[:10]
    for i, f in enumerate(affected_files):
        label = Path(f).stem if f else f"file{i}"
        diagram_lines.append(f"  {label}[{label}]")

    return {
        "summary": summary,
        "rationale": "Inferred from structural diff (LLM unavailable).",
        "data_flow": "",
        "impact": affected_files,
        "diagram": "\n".join(diagram_lines) if len(diagram_lines) > 1 else "",
    }


from pathlib import Path


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def narrate(
    changes: list[dict],
    graph_context: dict,
    task: str | None = None,
    model: str = "openai/gpt-4o-mini",
) -> dict:
    """Generate a narrative summary of code changes.

    Tries the LLM first; falls back to a basic summary if it fails.

    Returns:
        {
            "summary": "...",
            "rationale": "...",
            "data_flow": "...",
            "impact": ["file1.py", "file2.py"],
            "diagram": "graph LR\n  A --> B"
        }
    """
    prompt = _build_prompt(changes, graph_context, task)

    # Try primary model, then fallback
    models_to_try = [model]
    if model != "openai/gpt-4o-mini":
        models_to_try.append("openai/gpt-4o-mini")

    last_error = None
    for m in models_to_try:
        try:
            response_text = _call_llm(prompt, model=m)
            return _parse_response(response_text)
        except Exception as e:
            last_error = e
            continue

    # All LLM calls failed — use fallback
    result = _fallback_narrate(changes, task)
    result["summary"] += f" [LLM error: {last_error}]"
    return result
