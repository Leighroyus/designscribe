# DesignScribe — Agent Integration

Instructions for integrating DesignScribe with coding agents.

---

## CLAUDE.md

Add this to your project's `CLAUDE.md`:

```markdown
## Architecture Documentation

This project uses DesignScribe to maintain a living architecture document.

After making significant code changes, run:
  designscribe record <changed_files> --task "what you did"

For a full pipeline run (diff + narrate + diagram + render):
  designscribe run --task "what you did"

To watch for changes continuously:
  designscribe watch ./src --task "feature name"

The living architecture doc is at: living-arch.md
```

---

## AGENTS.md

Add this to your project's `AGENTS.md`:

```markdown
## Architecture Documentation

After writing or modifying code, record the changes for architecture documentation:

  designscribe record src/file1.py src/file2.py --task "Added OAuth2 login flow"

This will:
1. Analyze the structural changes (AST diff)
2. Update the dependency graph
3. Generate an LLM narration of the design decisions
4. Render an architecture diagram
5. Update living-arch.md

The living architecture document tracks all design decisions and data flows.
```

---

## Git Hooks

### post-commit

Add to `.git/hooks/post-commit`:

```bash
#!/bin/bash
# Auto-document architecture changes on commit
cd "$(git rev-parse --show-toplevel)"

# Get changed .py files in the last commit
CHANGED=$(git diff --name-only HEAD~1 HEAD -- '*.py')

if [ -n "$CHANGED" ]; then
    designscribe record $CHANGED --task "$(git log -1 --pretty=%B)" --no-narrate
fi
```

### post-merge

Add to `.git/hooks/post-merge`:

```bash
#!/bin/bash
cd "$(git rev-parse --show-toplevel)"
CHANGED=$(git diff --name-only HEAD~1 HEAD -- '*.py')
if [ -n "$CHANGED" ]; then
    designscribe record $CHANGED --task "Merge: $(git log -1 --pretty=%B)" --no-narrate
fi
```

---

## Cursor / VS Code

Add to `.cursorrules` or `.vscode/settings.json`:

```
After making code changes, run: designscribe record <files> --task "description"
The living architecture doc is at living-arch.md — read it for context.
```

---

## MCP Server (Coming Soon)

DesignScribe will expose MCP tools for agent integration:

- `designscribe_record` — record file changes
- `designscribe_narrate` — generate narration
- `designscribe_graph` — query the dependency graph
- `designscribe_architecture` — retrieve the living architecture doc

Stay tuned for Phase 2 completion.
