# Cataclysm conversion roadmap

Status: working roadmap. Revise it as each numbered plan produces better evidence.

## Goal

Convert current AzerothCore from World of Warcraft 3.3.5a to Cataclysm 4.3.4 build 15595 without
replacing AzerothCore's design with TrinityCore or a separate compatibility framework.

The rule for every change is simple:

1. Keep current AzerothCore ownership, interfaces, hooks, security checks, and file placement.
2. Take required 4.3.4 behavior from the pinned Cataclysm TrinityCore source.
3. Use `cata-js` as corroborating protocol notes and runtime evidence, never as an architecture to
   transplant.
4. Let the real build 15595 client decide whether a completed slice works.
5. **Important:** where AC's own design and TC-Cata's produce the same player-visible outcome
   through a different internal mechanism (e.g. persisted vs. recomputed-on-login data), keep the
   AC way. This is an AzerothCore project; TC-Cata is a source for required 4.3.4 behavior/wire
   format, not a template to converge AC's architecture toward. Only follow TC-Cata's approach
   where AC has no prior mechanism to defer to. See
   [#76](https://github.com/trolloks/azerothcore-cata/issues/76) for a concrete example (starting
   spells: AC recomputes from `character_skills` at login; a TC-Cata-derived fixture wrongly
   expected a persisted `character_spell` row). Agent-facing rule:
   `.agents/docs/cataclysm-conversion-goal.md` (Reference precedence for fixtures and acceptance
   criteria).

## Clean world database

World content is built from TDB 434 only — no 3.3.5a baseline — by
`apps/cata/build_clean_world.py` plus `apps/cata/import_clean_zones.sh`. `item_template` is not part
of the builder: `apps/cata/convert_items_from_db2.py --all` converts the 4.3.4 client's Item.db2 and
Item-sparse.db2 (with `--dbc-dir` for the derived armor and weapon columns) and its SQL is imported
after the build. The clean schema
(`acore_world_cata`) is now the golden reference (`acore_world_gold`): 315 tables, 221,728 creature
spawns, 2,086 starter-zone quests, 54,086 client-derived items, and real phase data from
`phase_area`/`spell_area`/`conditions` matched against the client DBCs. The previous hybrid golden is
backed up and remains reproducible from `data/sql/base` plus its pending updates.

Still being filled in: world-wide conditions and quest greetings, the remaining quest-state variants
(#282), and the goblin/worgen starts (deferred behind phasing/scenario work). The harness `prepare`
imports this schema with `--world-dump` into a freshly created world schema, and the dump hash is part
of the database cache key, so a new generation reproduces the clean world without editing its config.

## Reference order

| Priority | Source | Role |
| --- | --- | --- |
| 1 | Current `upstream/master` | Structure, framework, maintainability, and merge compatibility |
| 2 | TrinityCore commit `c699217775d90794158422387b07a917e161b582` | Canonical 4.3.4 behavior and wire format |
| 3 | `cata-js` commit `ab964a0e8dfe50a44fa92716ed05438f4a14dfd3` | Prior discoveries and client-visible failure notes |
| 4 | Full `Wow-64.exe` build 15595 | Runtime acceptance judge |

The TrinityCore working tree is dirty. Read the pinned commit, not uncommitted files. The `cata-js`
runtime log proves a useful vertical slice only through commit `3a697ce`; later aura work is
unverified and the log must not be committed because it contains account and packet data.

## Current state

Audited on 2026-10-15 at merged master `7be5f4bf9da6b23c2addb55628febc4f6db2ae82`, after
PRs #130-#132. Plans 1-30 are closed. Existing Plan 31 / [#102][plan-31] remains the next
implementation task. Five new plans follow it below. Closed plans prove their bounded contracts,
not complete gameplay systems or content coverage.

The audit record is [audit.json](evidence/conversion-audit-2026-10-15/audit.json). It separates
source inspection and checks of recorded evidence from fresh runtime acceptance.

### What has reviewable proof

- Authentication, character enumeration/selection, world entry and normal logout/relogin through
  AC's existing session/player ownership.
- Cata opcode directions, shared codecs, object field families and bounded create/update paths.
  A mapped opcode still needs its own payload and recipient/state proof.
- Creation/deletion checks across the 124 profiles in the current `playercreateinfo`
  matrix, with the documented Death Knight eligibility restriction. Exact starting-kit/action
  comparison is bounded to Human Warrior. Goblin and Worgen starting campaigns are absent from
  that accepted matrix.
- Keyboard ground movement, walk/run switching and jump/land routing. Full movement modes,
  fall-damage rules and all acknowledgement variants remain separate.
- The phase-125 area scenario with selected NPC/chest visibility, interaction and login restoration,
  recorded in [phase acceptance](phase-97-client-acceptance.md). Terrain swaps and broad quest phases
  are not implied by this scenario.
- Bounded action-bar self/target casts, cancellation and invalid-target handling. Native split
  Spell/SpellEffect/auxiliary stores and native starting-skill consumers are already implemented.
  The old roadmap claim that active spell loading still requires a flattened WotLK root is obsolete.
- [Quest 2158](evidence/plan26-quest2158/README.md), normal reward delivery and persistence. It has
  no kill/collect objective or intended phase transition, so it does not prove those systems.
- [Inventory/item use](evidence/plan27-inventory/README.md), [solo loot](evidence/plan28-loot/README.md),
  [one melee encounter](evidence/plan29-melee/README.md), and
  [death/reclaim/spirit-healer recovery](evidence/plan30-death/README.md). These include exact item,
  money, damage, corpse, durability and relog checks for the declared fixtures.
- Plan 30's final native replay matches 63 incoming swings and 60 damage, restores 30/60 HP through
  the healer, preserves durability 17/25 across the living relog and records zero UI errors.
  Its generic launcher failed during teardown after the final save; the independent scene checks
  and subsequent ownership-checked reset pass. The evidence retains that distinction.
- #75's Cata ScalingStatValues layout/accessors are already implemented. PR #132 adds a static
  comparison, rerun by this audit against the actual 85-record, 47-field extraction. A native
  scaling-item confirmation was transferred to Plan 31/#102 in #75 comments. #75 closed after
  PR #132; its static closure is not native acceptance. Do not repeat the obsolete port proposal.

### Remaining gates and tracker corrections

The functionality matrix now has **29 partial and 51 unassessed** rows. The data inventory has
**48 partial, 473 unassessed and one blocked** row. Neither has a verified parent row. These are
coverage categories, not equal-sized units of work, and they do not support a completion percentage.
The pinned table/store inventory check covers 516 declared entries; six additional artifact/content
rows bring the data tracker to 522. Record-level quest/spawn/encounter denominators are still missing.

This audit reconciles existing proof, without new native acceptance:

- Close the stale Plan 23-26/30 entries in the issue index and include existing Plan 31.
- Link accepted phase-125 visibility and same-map graveyard teleport evidence. Keep broader
  visibility and teleport variants unproven.
- Replace five obsolete data-blocker descriptions with partial status where native outfit,
  split-spell and starting-skill implementations already exist and bounded runtime proof is recorded.
  Record the native class-power table's existing Plan 30 proof as partial. These corrections do not
  establish complete table, join, profile or mechanic parity.
- Keep the full-content coverage gate blocked until per-zone/quest/chain/spawn/loot/encounter
  inventories exist. The source release being pinned does not supply that denominator.

The most immediate functional/data gaps are ordinary progression beyond creation, server geometry,
NPC commerce/training, mana-class combat and kill-objective credit. Current acceptance defaults
explicitly disable VMAP LOS/height. Vendor and trainer layouts still differ from the Cata pin.
Distributed defaults now say `Expansion = 3` and `MaxPlayerLevel = 85` (#172); level-85 progression
beyond the first transition and release defaults remain unfinished. Plan 31 proves only the first
normal level transition.

Other races/classes and exact starter profiles, all other map/terrain/transport variants, social
and economy systems, parties/instances/PvP, new resources/talents and broad world/encounter content
remain partial or unassessed. The native input, schema, record, relation and runtime gates below
continue to apply independently to each.

## Next five new plans

Plan 31 / [#102][plan-31], first normal level-up and persisted progression, already exists and stays
first. It is not duplicated. The five newly created issues are sequenced after it:

| Plan | Canonical issue | Completion boundary |
| --- | --- | --- |
| 32 | [#134][plan-32]: Northshire geometry | Pinned geometry, enabled LOS/height, legal chase path |
| 33 | [#135][plan-33]: Vendor transactions | Buy/sell/buyback/repair; exact saved item and money changes |
| 34 | [#136][plan-34]: Trainer learning | Trainer identity, legitimate learning, spell/action relog |
| 35 | [#137][plan-35]: Mana-class combat | Mage starter inputs, cast/cost/regen and stable relog |
| 36 | [#138][plan-36]: Kill-objective quest | Eligible kills, objective saves and exactly-once rewards |

[plan-31]: https://github.com/trolloks/azerothcore-cata/issues/102
[plan-32]: https://github.com/trolloks/azerothcore-cata/issues/134
[plan-33]: https://github.com/trolloks/azerothcore-cata/issues/135
[plan-34]: https://github.com/trolloks/azerothcore-cata/issues/136
[plan-35]: https://github.com/trolloks/azerothcore-cata/issues/137
[plan-36]: https://github.com/trolloks/azerothcore-cata/issues/138

Each issue has its own source/data preflight, deterministic and native predicates, rejection cases,
exclusions, dependency and merge gate. Creating this queue does not authorize builds or mean every
input is already available. Start each implementation branch from current merged master after its
predecessor merges. Split an unavailable data/mechanics prerequisite into a blocking child rather
than weakening the contract. Keep #75's transferred native scaling-item companion visible in #102,
separate from its normal-XP proof.

### Historical starter-data work

Plans 22-26 and #57-#60 are closed. Their original source comparisons, TDB 434.22011 world/hotfix
identity, update replay and native spell/item/skill extraction records remain under
`apps/cata/fixtures/plan22-*`. Those records are useful source evidence, not whole-world or all-class
runtime acceptance. The earlier queue and pending-loader descriptions are historical and have been
replaced above with the merged state.

### Validation budget

Run focused deterministic checks before real-client acceptance. When a build is authorized, use
the existing ccache/PCH build tree and build the required targets once. Reuse those binaries and
cached prepare inputs. Default to one isolated client acceptance session per plan, combining that
plan's scenarios. A repeat needs a concrete failure or relevant change and a stated purpose;
there is no blanket two-build or two-run requirement. Keep protected inputs immutable and use
only the owned disposable database and cleanup procedure.

A data-dependent plan must define its source/provenance, tables, output formats, out-of-repository
artifacts, and disposable migration checks before extraction or broad imports. Pull that prerequisite
forward when needed; do not invent retail values or add a second compatibility framework to bypass
an unavailable store.

## Functional and data parity tracking

Use these records together; none replaces the others:

- [Canonical plan issues](github-issues.tsv): implementation ownership, dependencies, decisions,
  and completion evidence for each bounded issue.
- [Functionality matrix](parity-functionality.tsv): capability scope, pinned TC reference, status,
  issue links, acceptance predicate, evidence, and remaining gaps. The initial 80 rows are coverage
  categories; inventory their individual handlers, variants, and scenarios before marking them done.
- [Data inventory](parity-data.tsv): one row per SQL table and named client store referenced by the
  pinned TC sources, plus explicit content-source, record-coverage, and map-artifact gates.
- [Existing conversion ledger](conversion-status.tsv): packet/file/block contracts and upstream
  delta auditing. A mapped opcode is not a verified payload or a completed capability.

The reference inventory contains 345 SQL table declarations and 171 named DBC/DB2 inputs, plus six
content/artifact gates. It includes tables introduced by current migrations because the base schema
alone missed 15 names. Historical base declarations remain tracked even when an update removes them;
SQL replay must establish whether each table should exist. These counts cover declared names, not
record parity. Maps, locales, script registrations, per-quest chains, spawns, and encounters need
expanded inventories. Uninventoried work remains unassessed, never complete.

### Source and comparison contract

Keep AC's ownership, security checks, hooks, and architecture. The pinned TC commit defines the
functional/data comparison target; cata-js corroborates protocol findings. A difference in code
structure is acceptable when behavior is equivalent and the proof is recorded. Any intended
behavioral deviation needs a named issue, rationale, and explicit approval; it remains visible and
must not be silently counted as parity. TC parity is not a guarantee of bug-free retail behavior.

The TC code pin is not a complete content snapshot. Its world and hotfix base SQL contain schemas
but no content rows. Before data parity can pass, pin the compatible world/hotfix release, applied
update sequence, locale coverage, source hashes, and required build-15595 client data. Store assets,
dumps, credentials, and raw captures outside Git. Track their sanitized manifests and reproducible
comparison commands. A populated local database or an unchanged row count is not source verification.

Every data row needs proof of:

1. Source identity: build/version, release and updates, locale, hashes, and documented provenance.
2. Schema/layout: types, signedness, widths, keys, defaults, indexes, DBC/DB2 columns, and loader joins.
3. Values: compare required records and fields by stable domain keys, including missing/extra IDs,
   defaults, enum/bit meanings, strings/locales, and any explicit AC-to-TC representation mapping.
4. Relations: detect dangling IDs and invalid cross-table references, including quest chains,
   conditions, phase membership, spawns, scripts, loot, spells/effects, outfits, and item templates.
5. Runtime use: the actual AC loader and consumer use the converted values correctly; fresh install,
   supported upgrade, restart, and relevant player-visible behavior agree.

Runtime-owned character/account tables need schema and behavior comparisons, not copied TC player
records. Use synthetic records to prove migration, ownership, constraints, round trips, and recovery.
Never require rewriting AC tables to match TC naming when an explicit semantic mapping suffices.

### Functionality proof and coverage

Before starting an issue, expand the relevant capability into a concrete inventory: opcode/callers,
state transitions, data dependencies, supported variants, and negative cases. For content, generate
per-zone, per-quest/chain, per-spawn/template, and per-map/encounter entries from the pinned data and
script registrations. Every discovered entry must be mapped, intentionally deferred, or blocked
with a reason. Parent categories cannot be verified while required children remain unproven.

For each scoped behavior, record independently derived TC expectations and prove them through the
normal AC path. Compare meaningful results: state, packet fields, eligibility, resource/item deltas,
visibility, and persistence. Include invalid/unauthorized requests and the failure paths that could
lose or duplicate data. Do not weaken security, invent retail values, or force database state to
make an acceptance scenario pass.

Use codec/unit/data comparisons for deterministic coverage, an owned live stack for integration,
and the real build-15595 client for visible behavior. Multiple-account scenarios are required where
ownership, recipient routing, grouping, trade, or visibility is part of the contract. A single login,
a screenshot, or one successful spell is evidence for that scenario only. An automated INCONCLUSIVE
record stays inconclusive; later human evidence is recorded separately with its exact scope.

### Status and evidence rules

- `unassessed`: scope is listed but comparison and proof are missing.
- `partial`: some bounded cases pass; list the remaining variants and dependencies.
- `blocked`: name the unavailable source or prerequisite and the issue needed to resolve it.
- `deferred`: deliberately unscheduled scope, still required for full conversion unless explicitly excluded.
- `verified`: every declared case and required child/data dependency has current, reviewable proof.

No empty evidence field is allowed on a verified row. Evidence records must identify the AC commit,
TC pin, input manifests/hashes, exact command/scenario, expected/actual result, and artifact location.
Client evidence also identifies the run, visual observation, ownership/isolation checks, and cleanup.
When a layer is inapplicable, use a reasoned `n/a: ...` entry instead of an empty cell or a claimed pass.
The checker enforces structure and required fields; a reviewer must assess the evidence itself.

Update the relevant rows in the same PR as the implementation, linking its canonical issue. Keep
prior failures and limitations. A later code/data change that invalidates an expectation makes the
related row partial again until reverified. On upstream/reference updates, compare inventories,
identify affected capability/data consumers, and rerun their checks. Do not carry green status across
changed inputs without review.

Report functional and data status separately, with blocked/unassessed counts and missing inventory
denominators. Do not publish one completion percentage by averaging unrelated packets, tables,
quests, and encounters. A closed plan issue is a completed slice, not a blanket subsystem verdict.

### Runnable tracking check

`python3 apps/cata/check_parity.py` checks columns, identities, statuses, pinned references, and
required evidence fields. Add `--reference-tree /path/to/TrinityCore` to check that every named SQL
base table and active-store DBC/DB2 input at the pinned commit has a data row. The command reads the
commit, so a dirty TC checkout does not affect the comparison.

Use `--add-missing-data` with the reference tree to add newly missing rows as unassessed while keeping
existing evidence. This updates only the tracker. It does not import game data or verify parity.
`--self-test` exercises the tracker's rejection cases. Keep the existing conversion checker for its
separate packet and upstream-delta audit; do not replace it with this inventory check.

When editing these TSVs, by hand or by script, preserve the stored field values exactly: read and
write them as plain text (split/join on `\t`) rather than round-tripping through a CSV writer with an
escape character. A `QUOTE_NONE` writer escapes every quote it emits, so a note containing `"` gains
`\"`, and each repeat of the rewrite doubles it again. That silently corrupted `client:Phase.dbc`'s
note across three commits — no backslashes in its introducing commit, fourteen on master, thirty in a
later branch — and it had to be repaired from `e9bbc9c0a`. Only rows you intend to change should
differ.

The final release gate requires complete scope inventories, verified functionality and data rows,
reviewed exceptions, no required blocked/deferred work, reproducible installation/upgrades, supported
builds, representative full client journeys, and recovery/security checks. Until those conditions
hold, describe this fork as a partial conversion, even if all currently opened issues are closed.

## Completion predicate

The conversion is complete only when all of these statements are true:

- The fork is based on a current upstream commit and every fork-only path is classified and checked
  after each upstream update.
- Supported authserver, worldserver, tools, and tests configure and build using the repository's
  supported toolchains.
- No active build, expansion, opcode, packet, update-field, client-data, or level-cap contract still
  targets 3.3.5a unless it is documented historical data.
- Fresh Cataclysm databases can be created and upgraded through a documented, repeatable process.
- The build 15595 client can complete automated smoke paths for authentication, character lifecycle,
  world entry, movement, combat, persistence, grouping, instances, and representative content.
- Each converted subsystem has deterministic local checks and a real-client acceptance trace.
- Upstream updates can be merged without replacing whole subsystems or reapplying hand-maintained
  patches.

## Plan families

These are areas, not promised plan numbers. Each area will split into as many small plans as needed.
Issues may be queued in advance at the user's request. Start implementation only when the
predecessor is green and the required inputs are known.

| Area | Exit condition before moving on |
| --- | --- |
| Protocol foundation | Direction-safe opcodes, wire primitives, auth, enumeration, and selection admission are proven |
| Client data extraction | Build 15595 DBC, DB2, map, VMAP, and MMAP inputs are reproducible |
| Client data stores | Cata layouts and loaders replace active WotLK contracts |
| Database strategy | Auth, character, and world schemas have a legal, repeatable Cata migration path |
| Object model | GUIDs, type IDs, update fields, visibility masks, and object updates agree |
| World entry | Phasing and the complete loading sequence reach the world reliably |
| Movement | Movement packets, acknowledgements, speed, spline, and teleport behavior agree |
| Maps and visibility | Grids, transports, maps, collision, phasing, and nearby-object updates agree |
| Character lifecycle | Creation, deletion, customization, races, classes, login, logout, and saves agree |
| Player progression | Level 85 stats, resources, regeneration, experience, and skills agree |
| Items and currencies | Equipment, inventory, bags, bank, currencies, and item persistence agree |
| Talents and customization | Specs, talents, glyphs, reforging, transmog, and professions agree |
| Spells and auras | Casting, auras, procs, cooldowns, dispels, and power costs agree |
| Combat | Damage, healing, threat, death, resurrection, and combat state agree |
| Creatures and AI | Spawning, respawn, AI, evade, pathfinding, and difficulty behavior agree |
| Pets and vehicles | Pets, guardians, summons, possession, and vehicles agree |
| Quests and phasing | Objectives, conditions, quest phases, rewards, and persistence agree |
| World interaction | Loot, vendors, trainers, gossip, reputation, factions, and events agree |
| Social | Chat, friends, ignores, groups, guilds, and calendar agree |
| Economy | Trade, mail, auction house, and economy rules agree |
| Instances and matching | LFG, instances, lockouts, and raid-finder behavior where applicable agree |
| PvP | Battlegrounds, arenas, honor, rated PvP, and rewards agree |
| Secondary systems | Achievements, archaeology, collections, and Cata-specific systems agree |
| World content | Eastern Kingdoms, Kalimdor, expansion zones, and outdoor content agree |
| Instance content | Dungeon and raid scripts, encounters, conditions, and rewards agree |
| Operations | Config, migrations, observability, deployment, and admin commands agree |
| Hardening | Long sessions, restart recovery, performance, security, and upstream rebase checks pass |

## Real-client integration strategy

Later vertical plans use the full build 15595 client as an acceptance judge. Client launch is allowed
only after the relevant plan has explicit build/test authorization and all of these isolation gates
are met:

- Create a fully run-owned Bottles prefix. Never launch through the personal `Battle.NET` Bottle.
- Use a fully run-owned client copy or a run-owned symlink/copy-on-write overlay whose isolation has
  been proven first. An overlay may link only read-only source files; its writable directories and
  locale `realmlist.wtf` must be local. Never launch the source client tree directly.
- Capture pre-run and post-run manifests for the source client and personal Bottle, and require no
  changes.
- Store logs, screenshots, manifests, and packet evidence outside the Git worktree. Sanitize them
  before deliberately copying any summary into the repository.
- Seed disposable server, database, account, and character state. Record exact owned resources and
  process IDs so reset can touch only those resources.
- Run databases in run-owned Docker containers and volumes bound to `127.0.0.1` on confirmed unused
  host ports. Never reuse or modify an existing database, container, or volume.

The eventual harness should expose five idempotent operations: `prepare`, `start`, `observe`,
`collect`, and `reset`. Automate hashes, server readiness, ports, protocol milestones, disconnects,
sanitization, and cleanup. Keep credential entry and visual confirmation manual until the isolated
flow is stable; an addon cannot observe authentication or character selection.

### Packet capture and replay

Use real-client sessions to create deterministic fixtures, then run most protocol checks without the
client. Capture logical `WorldPacket` data at the server boundary:

- Incoming: after TCP framing and header decryption, before opcode dispatch.
- Outgoing: after packet serialization, before compression, header creation, and encryption.
- Record only build, sequence, direction, connection type, opcode, and payload.
- Exclude wall-clock time, addresses, ports, credentials, session keys, and personal identifiers.

Raw PCAP remains optional forensic evidence. It is a poor primary fixture because authenticated
world headers use session-specific crypto, TCP segmentation varies, and outbound compression keeps
stream state across packets.

Never redact sensitive bytes in place. Synthesize a new transcript using dummy accounts, fixed test
keys and challenges, remapped GUIDs, fixed clocks and counters, and recomputed proofs. Keep three
small test layers:

1. Typed codec fixtures compare parsed fields or serialized opcode and payload.
2. Session-flow replay feeds logical client packets through dispatch against fixed server state and
   captures logical responses.
3. Wire vectors test initializer framing, normal and large headers, crypto, and full ordered zlib
   streams separately.

This replaces repeated client runs for parser, serializer, dispatch, and login-sequence regressions.
It does not replace real-client acceptance checks for crypto activation, connection redirects,
packet ordering, streaming compression, world loading, movement, UI behavior, crashes, or silent
disconnects.

## Planning rules

- One numbered GitHub issue owns one verifiable slice. Split it again when the real dependency graph
  is wider than expected.
- Give each numbered issue its own `plan/NN-short-name` branch. Create it from the latest `master`
  after the previous plan is merged; never stack plan branches.
- Start each plan from updated upstream and end it with a repeatable check.
- Port semantic differences into existing AzerothCore layers. Do not bulk-copy TrinityCore files.
- An opcode number is not implemented until its parser or serializer and client behavior are proven.
- Add SQL only under the permitted pending-update directories unless the user explicitly authorizes a
  broader database migration.
- Do not start client-data extraction until the user confirms the source data's provenance and
  intended use, and the plan defines an out-of-repository storage and ignore boundary.
- Keep experimental tracing behind existing logging facilities and remove it before a plan closes.
- Record negative results. `INCONCLUSIVE` is not a pass.
- Do not commit raw packet captures. Commit only reviewed synthetic fixtures with dummy identities
  and reproducible generation inputs.
- Treat existing database data as immutable. A plan that needs a database must define its disposable
  Docker container, volume, unused-port check, ownership manifest, and bounded cleanup first.

## Plan issues

GitHub issues hold the complete plans and progress. `github-issues.tsv` is the repository index; the
numbered Markdown files are stable-path stubs for historical ledger references.

- [Plan 1: conversion baseline and protocol contracts](https://github.com/trolloks/azerothcore-cata/issues/11)
- [Plan 2: deterministic bit-buffer contract](https://github.com/trolloks/azerothcore-cata/issues/12)
- [Plan 3: world compression stream lifetime](https://github.com/trolloks/azerothcore-cata/issues/13)
- [Plan 4: direction-safe opcode model](https://github.com/trolloks/azerothcore-cata/issues/14)
- [Plan 5: world authentication packet contract](https://github.com/trolloks/azerothcore-cata/issues/15)
- [Plan 6: build 15595 authentication handoff](https://github.com/trolloks/azerothcore-cata/issues/16)
- [Plan 7: isolated build 15595 client authentication](https://github.com/trolloks/azerothcore-cata/issues/17)
- [Plan 8: typed empty character enumeration](https://github.com/trolloks/azerothcore-cata/issues/18)
- [Plan 9: one database-backed character](https://github.com/trolloks/azerothcore-cata/issues/19)
- [Plan 10: select the enumerated character](https://github.com/trolloks/azerothcore-cata/issues/20)
- [Plan 11: build 15595 pre-map initial-packet contract](https://github.com/trolloks/azerothcore-cata/issues/25)
- [Plan 12: build 15595 map insertion and object bootstrap](https://github.com/trolloks/azerothcore-cata/issues/26)
- [Plan 13: build 15595 in-world control bootstrap](https://github.com/trolloks/azerothcore-cata/issues/27)
- [Plan 14: build 15595 post-map-insertion client survival](https://github.com/trolloks/azerothcore-cata/issues/32)
- [Plan 15: build 15595 basic movement packet contract](https://github.com/trolloks/azerothcore-cata/issues/33)
- [Plan 16: run-speed acknowledgement contract](https://github.com/trolloks/azerothcore-cata/issues/34)
- [Plan 17: Object/Unit/Player update fields](https://github.com/trolloks/azerothcore-cata/issues/36)
- [Plan 18: remaining object update fields](https://github.com/trolloks/azerothcore-cata/issues/37)
- [Plan 19: bit-packed movement/create block](https://github.com/trolloks/azerothcore-cata/issues/40)
- [Plan 20: opcode-table realignment](https://github.com/trolloks/azerothcore-cata/issues/41)
- [Plan 21: login packet sequence parity](https://github.com/trolloks/azerothcore-cata/issues/43)
- [Plan 22: character creation and starting state](https://github.com/trolloks/azerothcore-cata/issues/52)
- [Plan 23: on-foot movement](https://github.com/trolloks/azerothcore-cata/issues/53)
- [Plan 24: initial phasing and visibility](https://github.com/trolloks/azerothcore-cata/issues/54)
- [Plan 25: basic spell casting](https://github.com/trolloks/azerothcore-cata/issues/55)
- [Plan 26: first complete quest loop](https://github.com/trolloks/azerothcore-cata/issues/56)

Plans 8-10 established enumeration and selection admission. Plans 11-14 and 21 completed the
bounded world-entry sequence; Plans 17-20 supplied the field, create-block, and opcode contracts.
Plans 15-16 established idle heartbeat and run-speed acknowledgement acceptance. See each canonical
issue for its exact proof and exclusions. The next five plans above begin where those proofs stop.
