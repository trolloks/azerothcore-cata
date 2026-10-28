---
name: graphify
description: Query the repository knowledge graph before reading code for architecture, call-path, or "where is X handled" questions. Use when a graphify-out/graph.json exists for the checkout or the question spans many files.
metadata:
  version: "1.1"
---

# Graphify

Use the built knowledge graph for architecture, call-path, data-flow and "where is this handled"
questions before blind greps.

## Find the graph

Graphify reads `graphify-out/graph.json` from the **current directory**, so run the commands from the
checkout that contains `graphify-out/` — the project root, not `graphify-out` itself:

```bash
ls graphify-out/graph.json
```

A worktree usually has no graph of its own. Use the checkout that owns one; keep that path in a
local `GRAPHIFY_ROOT` variable (for example the root project on this machine) instead of editing
this file:

```bash
cd "${GRAPHIFY_ROOT:?set to the checkout that owns graphify-out}"
```

## Commands

```bash
graphify query "<question>"            # broad BFS context
graphify query "<question>" --dfs      # trace a specific path
graphify explain "<Symbol>"            # node plus its neighbours
graphify path "SymbolA" "SymbolB"      # shortest connection
```

Narrow a noisy `query` result with `--dfs`, a symbol name, or `--budget`.

## Rules

- Cite the node's `source_location` when a graph hit informs a change.
- The graph can lag the working tree; verify the cited file and line before acting.
- Refresh after a batch of merges, not on every question; follow the update reference in the
  installed graphify skill.
- `graphify-out/` is generated output; never commit it and never edit it by hand.
