# Plan 1: conversion baseline and protocol contracts

Status: ready for review. Execution has not started.

## Outcome

Turn the existing `feature/cata` experiment into a trustworthy, rerunnable conversion baseline.
Every fork change, protocol declaration, and known WotLK anchor will be classified before more
production behavior is changed.

This plan proves what exists; it does not finish authentication or character selection. Production
packet changes, builds, server startup, database mutation, and client launch belong to later plans.

## Why this is Plan 1

The branch contains useful Cataclysm work, but it mixes proven wire details with unsafe primitives,
WotLK payload handlers, temporary diagnostics, and incomplete serializers. Adding more packets before
classifying that delta would make later upstream rebases and correctness claims harder to audit.

## Execution boundary

Do not configure, build, run tests, launch the client, change databases, or rewrite published history
merely because this plan exists. Any later C++ or SQL work must first follow the matching repository
guidance. Plan 1 owns inventory, deterministic source checks, and evidence records only.

## Pinned inputs

| Input | Pin |
| --- | --- |
| Fork branch at planning time | `feature/cata` at `c72015fee9726305ecc757e0ac341d61d6ec2095` |
| Planning upstream | `5fa7cb00fa04814c4afe6701f0c6c09e9fb96cea` |
| Canonical Cataclysm C++ | TrinityCore `c699217775d90794158422387b07a917e161b582` |
| Prior protocol evidence | `cata-js` `ab964a0e8dfe50a44fa92716ed05438f4a14dfd3` |
| Proven `cata-js` runtime boundary | `3a697ce` |
| Full build 15595 client SHA-256 | `0ea9cc0458cb137f4e0767e8a1896e2042480a4a22397480d7ee64c2e696192a` |

Refresh only the upstream pin when Plan 1 starts. Read canonical TrinityCore files from the pinned
commit, not its dirty working tree. Record and justify any other pin change.

## Baseline findings to reproduce

| Area | Planning observation |
| --- | --- |
| Upstream ancestry | Zero behind and six commits ahead |
| Fork delta | 18 files, 1,076 insertions, 316 deletions |
| Opcode agreement | 111 of 867 shared names match pinned Cata values |
| Opcode ownership | One shared table cannot represent at least six direction collisions |
| Wire primitives | Bit state and compression lifetime are incomplete |
| Authentication | Success, error, and queue serializers disagree |
| Character list | Useful wire work exists, but query mapping and ownership need correction |
| World entry | Update fields, client data, object updates, phasing, maps, and movement remain open |

These are hypotheses until the Plan 1 checker reproduces them.

## Deliverables

- `apps/cata/check_conversion.py`: one standard-library, read-only checker.
- `plan/conversion-status.tsv`: the authoritative conversion ledger.
- A machine-readable opcode comparison emitted by the checker.
- A final audit of all current fork changes, summarized in the ledger and decision log.
- Updated roadmap boundaries based on the reproduced dependency graph. Do not create Plan 2 yet.

Do not add a framework, dependency, generated source tree, or copied reference data.

## Unit 1: freeze provenance and ancestry

Work:

1. Fetch upstream without changing the branch, then record execution-time upstream and fork heads.
2. Verify upstream is an ancestor of the fork.
3. Verify the pinned TrinityCore and `cata-js` commits exist.
4. Record reference access as `git show <pin>:<path>` so dirty worktrees cannot affect results.
5. Recheck the full client executable hash without launching or copying the client.

Gate:

- Every pin resolves and has an exact evidence pointer.
- Ancestry and ahead/behind counts are recorded.
- Client hash is recorded as an observation, not runtime compatibility proof.

## Unit 2: classify the entire fork delta

Work:

1. Compare `upstream/master...feature/cata` and inventory every changed file and changed block.
2. Classify each block as required Cata behavior, upstream-preserving adaptation, accidental drift,
   or unverified work in progress.
3. Record its current AzerothCore owner, pinned Cata reference, corroborating `cata-js` evidence when
   available, and intended future plan family.
4. Identify dead blocks, temporary logging, empty handlers, duplicate serializers, and the empty
   rebased commit. Do not rewrite history to remove the empty commit.

Gate:

- No fork-only file or changed block is unclassified.
- Each retained block has a Cata requirement and an AzerothCore owner.
- Historical attribution never substitutes for behavioral proof.

## Unit 3: establish the protocol ledger

Work:

1. Parse client and server opcode declarations from the fork and pinned Cata reference.
2. Track value and direction independently from handler registration, payload conversion, fixture
   proof, and real-client proof.
3. Report same-direction duplicates and opposite-direction collisions separately.
4. Identify every Cata value that currently activates an unverified WotLK parser or serializer.
5. Inventory build, expansion, level-cap, DBC/DB2, update-field, GUID, header, compression, and other
   named WotLK anchors without pretending this plan converts them.

Gate:

- The checker reproduces or corrects the planning counts.
- Every active opcode is represented in the ledger with direction and proof state.
- Unverified payload handlers are visibly unsafe or inactive; no numeric match is called implemented.

## Unit 4: make the checks rerunnable

Work:

1. Accept reference repositories and pins as command-line inputs; do not hardcode mounted paths.
2. Emit deterministic text or JSON suitable for comparing results after an upstream rebase.
3. Fail on unresolved pins, lost upstream ancestry, unclassified fork files, malformed ledger rows,
   or contradictions in entries marked converted.
4. Report known open WotLK anchors without failing until their ledger state is explicitly converted.
5. Add the smallest runnable self-check for parsing, direction collisions, and deterministic output.

Gate:

- Two identical runs produce byte-identical output.
- The self-check catches a synthetic direction collision and a missing classification.
- The checker is read-only and works without configuring or building AzerothCore.

## Unit 5: close the audit

Work:

1. Review every ledger decision against current upstream structure and the pinned Cata behavior.
2. Decide which experimental blocks later plans should retain, relocate, rewrite, disable, or delete.
3. Update the high-level roadmap only where the reproduced dependency graph differs.
4. Record unresolved choices as experiments for the future plan that owns them.

Gate:

- Every current fork block and known protocol anchor has an owner and state.
- The next implementation slice can be selected without assuming character selection or world entry
  already works.
- No production code, database, client state, or personal Bottle was changed.

## Completion predicate

Plan 1 is complete when the checker and ledger reproduce the branch state deterministically, every
fork change is classified, every active opcode has independent direction and proof states, known
WotLK anchors are explicit, and the next small implementation plan can be written from evidence.

Plan 1 does not claim that the branch builds, authenticates, renders characters, or enters the world.
