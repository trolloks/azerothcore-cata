# Cataclysm conversion goal

## Goal

Completely convert the upstream AzerothCore project from World of Warcraft 3.3.5a (WotLK) to
Cataclysm 4.3.4.

Keep the fork's design, framework, interfaces, and code structure as close to upstream as practical
so upstream updates remain straightforward to integrate. Prefer the smallest Cata-specific delta;
depart from upstream only where Cataclysm protocol, data, or gameplay differences require it.

## Reference precedence for fixtures and acceptance criteria

**TrinityCore 4.3.4 at the pinned commit
(`c699217775d90794158422387b07a917e161b582`) is the source of truth for Cataclysm behaviour.**
When our code, data or a fixture disagrees with the pinned TC-Cata reference about a 4.3.4
player-visible outcome, wire format, packet layout or Cata-specific data schema, the reference wins —
except where the AC representation produces the same outcome (next paragraph). Only the real
build-15595 client is above it: where TC and the client disagree, the client is the acceptance judge
and the deviation is recorded.

AzerothCore's own structure, ownership, interfaces and hooks stay in place. When AC and TC-Cata
represent the *same* player-visible outcome through different internal representations — persisted
rows versus recomputed-on-login data, for example — keep the AC-native representation and do not
change core AC logic to match a TC-Cata on-disk layout. Worked example: issue #76 (starting cast
spell 2457 — AC recomputes it from `character_skills` at login; a TC-Cata-derived fixture wrongly
expected a persisted `character_spell` row).

Protocol fixtures under `apps/cata/fixtures/` that pin wire layouts follow TC-Cata: it is the
legitimate reference for a Cata-specific format AC has no prior version of.

When the pinned TC-Cata source and TDB 434 lack data the client requires (an authored `spell_area`
detection row, a missing spawn fix), adding it is allowed only when the row is marked as authored in
the code that adds it, the gameplay justification (the quest flow where applicable) is recorded on
the issue, and the change lands under the normal deterministic checks; in-world validation is picked
up later under the 2026-10-28 policy. Authored data is never presented as TC-Cata data.

## Real-client acceptance is human, not a merge gate

The client harness only launches the client and gets into the world (`run --hand-off`). In-world
testing is done by a human; agents must not script in-world input or report an in-world acceptance
result. Human sign-off is not a merge or closure gate (revised 2026-10-28): land a change on its
deterministic checks (importer gates, load diff, tests, review) and pick the in-world validation up
when the issue is revisited; a finding becomes a new issue. When the client is exercised it remains
the acceptance judge and wins over TC. See `plan/client-automation.md`.

## GM commands for test setup

GM commands are the quickest way to reach a client-testing state that would otherwise take a long
play session (spawning fixtures, teleporting, granting items/spells/talents, setting reputation or
flags). Use them freely as *setup* in local, run-owned testing. The full list is on the AzerothCore
wiki (https://www.azerothcore.org/wiki/gm-commands), and the world DB `command` table is the
runtime source of truth for what a given server actually exposes and at which security level.

They must not stand in for the mechanic under test. When an acceptance criterion is about a normal
player path, drive that path and assert on it (earn level-up XP through a quest or kill, not
`.levelup`; take loot from a corpse, not `.additem`). Use GM commands only for preconditions, then
assert the real player-visible result. This matches `.agents/docs/e2e-policy.md`: GM setup is fine;
the oracle must observe the real player path. A harness account needs its own GM/RBAC grant before
it can use restricted commands.

## Branch workflow

Use Graphify when tracing conversion subsystems, callers, and ownership. Query an existing graph before
rebuilding it, then verify findings against current source and the pinned reference. Record missing graph
coverage rather than treating a declaration or inferred edge as proof of runtime behavior.

A fresh checkout has no graph. Build it once from the repo root: the tracked `.graphifyignore` keeps the
scope to `src/`, `apps/`, `plan/` and `.agents/` (SQL data, `deps/` and e2e scratch are noise for code
questions), and `graphify-out/` is gitignored so the ~100 MB artifact is never committed.

```bash
graphify .                                   # full build, ~107k nodes
graphify query "where is quest XP awarded"   # query instead of grepping
```

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

Splitting also applies below the issue level. A large, intricate change (a 900-line import pipeline, a
packet family, a schema port) is where mistakes hide, because one review cannot hold it all.
Land it as small slices, each with its own commit, check and peer review, rather than one big PR:

- Cut along data or responsibility seams (one table family, one packet, one zone), not by line count.
  Each slice must be runnable and verifiable alone.
- Verify every slice on a clean, disposable baseline and record the counts, so a later slice cannot
  silently change an earlier one. Keep the baseline reproducible: a run dump, or a documented rebuild.
- Get an independent review (a second model or a human) per slice, before stacking the next one. #209's
  single-pass review found three high-severity native-row overwrite bugs that its own Elwynn replay missed.
- Record accepted deferrals as issues (`Parent: #NN`) instead of leaving them in a WIP PR body.

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
