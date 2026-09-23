# Cataclysm conversion goal

## Goal

Completely convert the upstream AzerothCore project from World of Warcraft 3.3.5a (WotLK) to
Cataclysm 4.3.4.

Keep the fork's design, framework, interfaces, and code structure as close to upstream as practical
so upstream updates remain straightforward to integrate. Prefer the smallest Cata-specific delta;
depart from upstream only where Cataclysm protocol, data, or gameplay differences require it.

## Branch workflow

GitHub issues in `trolloks/azerothcore-cata` are the canonical numbered plans and progress records.
Keep `plan/github-issues.tsv` as the repository index. Numbered Markdown files may retain short
issue-reference stubs for stable historical paths, but must not duplicate complete issue bodies.
Close a plan issue only after its completion predicate and recorded checks pass.

All conversion work is connected to a GitHub issue. Before starting non-trivial work, find or open
the tracking issue. Post significant progress (new findings, ruled-out theories, reproductions,
fixes, merged PRs) as comments on that issue as you go, not only at the end — treat the issue
thread as the running record of the investigation or change.

Each numbered plan uses its own `plan/NN-short-name` branch. Create that branch from the latest
`master`, never from the previous plan branch. Merge the completed plan into `master` before creating
the next plan branch. Do not implement numbered plans directly on `master` or `feature/cata`.

When a plan's scope is too large for one bounded acceptance pass, split it into sub-issues rather
than weakening its acceptance criteria (e.g. Plan 22/#52 split off starter-data prerequisite #57,
which itself split into implementation children #58-#60; Plan 23/#53 split into ground-movement
#77 and jump/fall-land #78, ordered so #78 depends on #77). Each sub-issue states `Parent: #NN`
and, if it depends on a sibling, says so explicitly. Edit the parent issue's body to list its
children as a dependency checklist and keep it open until all children close and its own
completion predicate passes. Sub-issues use the same numbered-plan branch workflow as any other
plan; they do not get their own `plan/NN-*.md` stub unless promoted to a standalone plan.

## Database safety

Never point conversion work, tests, or client smoke runs at an existing database. Treat existing
schemas, data, containers, and volumes as immutable.

When a database is required, use a disposable run-owned Docker container and volume. Bind it to
`127.0.0.1` on a confirmed unused host port, record the exact resources in the run manifest, and
delete only those recorded resources during cleanup. Reuse an existing suitable image; build a
separate test image only when the existing images cannot provide the required Cataclysm schema.

## Disassembling the client when a crash address is the only lead

`Wow-64.exe` is a plain PE binary, so `objdump` reads it directly without Wine or a debugger
attached — no separate tool install needed on top of `binutils`. This is useful when a Wine
crash-dialog backtrace (`plan/client-automation.md`'s evidence capture) gives only
`Wow-64.exe + <RVA>` offsets and the packet-level evidence has been exhausted:

```bash
CLIENT="/path/to/Cataclysm-4.3.4.15595-enUS-x64/Wow-64.exe"
objdump -p "$CLIENT" | grep ImageBase                 # confirm the load base (e.g. 0000000140000000)
# VA = ImageBase + RVA from the crash backtrace
objdump -d --start-address=0x<VA-40> --stop-address=0x<VA+40> \
  -b pei-x86-64 -m i386:x86-64 "$CLIENT"
```

This build ships no PDB, so functions/classes can't be named directly, but the raw
disassembly still confirms or refutes a hypothesis about the fault (e.g. a specific
`mov reg, [reg+offset]` matches a null-pointer read reported independently in two different
crash traces) without needing to reproduce the crash again. Treat this as a last resort after
server-log/packet evidence, not a replacement for it -- it tells you what instruction faulted,
not why the object it dereferences is null.
