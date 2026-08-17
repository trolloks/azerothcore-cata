# Plan 9: one database-backed build 15595 character

Status: execution-ready after Plan 8 merges. Create this branch from the new `master`; do not stack it
on the Plan 8 branch.

Branch: `plan/09-build-15595-populated-character-list`.

## Outcome

Make the build 15595 client enumerate and visibly display exactly one deterministic, unequipped human
warrior named `Cataplan`. Prove every supported response field agrees with the run-owned database
seed, keep the character screen stable, and stop before selection.

Plan 9 passes only when two fresh isolated generations prove:

- exactly one active character row belongs to the synthetic account;
- `realmcharacters.numchars` and the actual character row count both equal one;
- the exact populated `SMSG_CHAR_ENUM` body matches an independently derived literal fixture;
- the enumerated GUID and list position equal the database seed;
- the PID-owned client window visibly shows the one intended character for the stability hold;
- no `CMSG_PLAYER_LOGIN` or character-mutation request occurs;
- the sanitized semantic results match and both generations reset cleanly.

This plan does not create a character through the client, validate multiple-character ordering,
exercise nonzero character flags, select or load the character, or enter the world.

## Entry requirements

Plan 8 must be merged and its checker must still prove:

- typed `EnumCharacters` and `EnumCharactersResult` ownership;
- exact empty response `000001000000`;
- a stable zero-row real-client screen;
- no selection or mutation opcode;
- two accepted/reset generations.

Reuse Plan 8's packet types, packet test file, runner, stability predicate, protected-input checks,
database cache, and client lifecycle. Do not create a second serializer or harness.

Read `.agents/docs/cpp-guidelines.md` before changing C++. Read `.agents/docs/build.md` before any
build or test. Obtain explicit user authorization before configuring, building, starting Docker, or
launching the client.

## Authorities

| Authority | Pinned input | Use |
| --- | --- | --- |
| AzerothCore | latest master after Plan 8 | handler, DB, session, and runner ownership |
| TrinityCore | `c699217775d90794158422387b07a917e161b582` | populated 4.3.4 write layout |
| cata-js | `ab964a0e8dfe50a44fa92716ed05438f4a14dfd3` | populated-layout corroboration only |
| client | build 15595, SHA-256 `0ea9cc0458cb137f4e0767e8a1896e2042480a4a22397480d7ee64c2e696192a` | acceptance judge |

## Fixed synthetic character

Use this identity unchanged in Plan 9 and Plan 10:

| Field | Value |
| --- | --- |
| account id | `900000` |
| realm id | `42` |
| character low GUID | `16909060` / `0x01020304` |
| name | `Cataplan` |
| race / class / gender | `1 / 1 / 0` (human, warrior, male) |
| skin / face / hair style / hair color / facial hair | `0 / 0 / 0 / 0 / 0` |
| level | `1` |
| map / zone | `0 / 12` |
| x / y / z / orientation | `-8949.95 / -132.493 / 83.5312 / 0` |
| list order | `7` |
| player flags / at-login flags / extra flags | `0 / 0 / 0` |
| guild, pet, ban, declined name | absent |
| equipment, enchants, titles, exploration, taxi mask | empty |

Human warrior is deliberate: `(race=1, class=1)` already exists in the current `playercreateinfo`,
so the fixture exercises enumeration without introducing a client-data or gameplay dependency. The
multi-byte low GUID exercises the packed GUID mask and byte order; do not replace it with `0` or `1`.

List order `7` deliberately differs from `extra_flags=0`. If the query still consumes `extra_flags`,
the exact packet and live evidence must fail with list position zero.

## Database correction

In both prepared statements `CHAR_SEL_ENUM` and `CHAR_SEL_ENUM_DECLINED_NAME`, replace selected field
24, `c.extra_flags`, with:

```sql
COALESCE(c.order, 0)
```

Keep it at result index 24. The optional declined-name genitive remains index 25. Keep the existing
`ORDER BY COALESCE(c.order, c.guid)`. Update the field-index comment and remove the stale note that
the handler is using an incorrect list field.

Do not add TrinityCore's `slot`, `characterFlags`, or `characterFlags2` columns. For this one normal
row, retain AzerothCore's current zero-valued flag derivation. Nonzero flag semantics remain open.

No SQL migration is required. This is one query projection fix in
`CharacterDatabase.cpp`; base, archived, released, and pending SQL remain unchanged.

## Disposable seed

Extend the existing runner's per-generation seed step. Apply the character only after restoring the
immutable base/released database cache and applying the current pending migrations.

Use an `INSERT` with an explicit column list. Set the fixed fields above plus the current schema's
required `name`, `taximask=''`, and `innTriggerId=0`; use deterministic empty strings for serialized
text fields read by enumeration. Let unrelated columns use declared schema defaults. Do not use a
position-dependent `INSERT INTO characters VALUES (...)`.

Set the owned `realmcharacters` row for `(realmid=42, acctid=900000)` to `numchars=1`. Before starting
the servers, query the owned database back and require:

- exactly one active `characters` row for account `900000`;
- GUID, name, race, class, gender, appearance, level, map, zone, coordinates, flags, and `order`
  equal the fixed table above;
- `realmcharacters.numchars=1` for realm `42` and account `900000`.

The actual `characters` row drives enumeration; `realmcharacters.numchars` alone is not proof. Seed
SQL stays inside the Python runner and may execute only against the manifest-owned container. Never
write it to a repository SQL update or connect it to an operator database.

## Packet contract

Reuse `WorldPackets::Character::EnumCharactersResult`. Construct one result with:

- `Success=true`, one character, and no faction restrictions;
- player GUID low counter `0x01020304` and zero guild GUID;
- the fixed name, appearance, level, map, zone, and three float32 coordinates;
- `ListPosition=7`, `Flags=0`, `Flags2=0`, and `FirstLogin=false`;
- all 23 visual-item triples and all pet values zero.

Extend `CharacterPacketsTest.cpp` with a literal full-body byte vector. The expected vector must be
derived independently from the pinned Trinity bit/byte order and reviewed once; it must not be
generated by `EnumCharactersResult::Write()` or copied from a live capture. Record its expected size
(`278` bytes for the fixed values) and compare every byte. Keep Plan 8's empty vector unchanged.

Before freezing the literal, have the reviewer check these calculation inputs independently:

1. character count `1`, restriction count `0`, and success bit `1`;
2. player GUID bytes for low value `04 03 02 01` and zero guild mask;
3. seven-bit name length `8` followed by `Cataplan` without a terminator;
4. 23 zero visual triples in the pinned byte order;
5. list-position byte `07`;
6. IEEE-754 little-endian bytes for all three coordinates;
7. zero pet, flag, guild, and customization values.

Mutation checks must fail when the count, any present GUID mask/data byte, name length/name bytes, or
list-position byte changes. Do not create a database mock solely for query text: the checker requires
`COALESCE(c.order, 0)` in both statements, and the deliberately distinct live position proves the
consumer.

## Handler and evidence

Keep row mapping, validation, `_legitCharacters`, flag derivation, pet/equipment lookup, and send
policy in `CharacterHandler.cpp`. Do not move them into the packet layer.

If current logs do not expose the normalized row, retain one bounded `network.opcode` event after
`_legitCharacters.insert` with account id, player GUID, and list position. Do not log the character
name, raw packet, equipment string, or every field. The runner must require the event's GUID and
position to equal the seed.

Do not revive the unused WotLK-shaped `Player::BuildEnumData`.

## Existing runner extension

Add mode `populated-character-list` to `run_real_client_authentication.py`. Reuse the Plan 8
`--stability-seconds` and `--confirm-expected-screen` arguments.

For this mode:

1. Apply and read back the fixed one-row seed before server launch.
2. Require `COP_GET_CHARACTERS ... Completed ... TRUE` and the populated response.
3. Require one enumerated legitimate GUID `16909060` with list position `7`.
4. Hold the owned connection for ten seconds and capture the PID-owned window afterward.
5. Ask the operator to confirm that exactly one visible character named `Cataplan` is present.
6. Inspect the complete transcript before sanitizing it. Reject `CMSG_PLAYER_LOGIN` and every Plan 8
   mutation opcode.
7. Compare semantic projections rather than screenshot hashes.

The sanitized fixture `apps/cata/fixtures/plan9-populated-character-list.json` records the common
Plan 8 fields plus:

```json
{
  "character_rows": 1,
  "realm_character_count": 1,
  "enumerated": {
    "guid_low": 16909060,
    "name": "Cataplan",
    "race": 1,
    "class": 1,
    "gender": 0,
    "level": 1,
    "map": 0,
    "zone": 12,
    "list_position": 7,
    "flags": 0,
    "flags2": 0,
    "visual_items_nonzero": 0
  }
}
```

It also records the reviewed exact response body, two distinct fresh generations, equal normalized
results, unchanged protected inputs, and reset `PASS`. Create the `PASS` fixture only after both live
runs and manual screen reviews succeed. Keep raw screenshots and logs outside Git.

## Files to change

- `src/server/database/Database/Implementation/CharacterDatabase.cpp`
- `src/server/game/Handlers/CharacterHandler.cpp` only if one bounded semantic event is required
- `src/test/server/game/Server/Packets/CharacterPacketsTest.cpp`
- `apps/cata/run_real_client_authentication.py`
- `apps/cata/check_conversion.py`
- `apps/cata/fixtures/plan9-populated-character-list.json` only after acceptance
- this plan, `plan/conversion-status.tsv`, and `plan/decision-log.tsv`

Do not change schema SQL, packet types, opcode values, or CMake lists.

## Execution order

1. Create the branch from merged Plan 8/master and rerun Plan 8's source-only checks.
2. Fix field 24 in both prepared statements and its consumer comment.
3. Add the exact one-character packet fixture and mutation checks; retain the empty fixture.
4. Add runner mode, seed/readback, one-row acceptance, denylist, and self-check mutations.
5. Extend the existing `protocol.character-enumeration` checker with a distinct Plan 9 evidence gate.
   Do not make historical Plan 8 evidence depend on a future Plan 9 fixture.
6. After explicit authorization, rebuild the affected targets and run the focused packet test.
7. Run two fresh client generations, review each PID-owned screen, reset each, and compare results.
8. Create the sanitized fixture, update only the proven query/enum ledger rows, run the checker, and
   purge the run-owned caches.

## Commands

Source-only checks:

```bash
python3 apps/cata/run_real_client_authentication.py self-check
python3 apps/cata/check_conversion.py --self-check
python3 apps/cata/check_conversion.py \
  --base-ref upstream/master \
  --head-ref HEAD \
  --trinity-repo "$TRINITY_ROOT" \
  --cata-js-repo "$CATA_JS_ROOT" \
  --client-exe "$CLIENT_EXE"
git diff --check
```

Use the same absolute `TRINITY_ROOT`, `CATA_JS_ROOT`, and `CLIENT_EXE` inputs verified in Plan 8.

After explicit build authorization:

```bash
cmake --build "$BUILD_ROOT" --target game worldserver unit_tests -j2
"$BUILD_ROOT/src/test/unit_tests" --gtest_filter='CharacterPacketsTest.*'
```

Use Plan 8's full `prepare -> run -> verify -> reset` procedure twice, changing only the mode:

```bash
--mode populated-character-list
```

Then run `compare-last-two` and a final `reset --purge-client-base --purge-database-cache` exactly as
documented in Plan 8.

## Failure classification

`FAIL` means wrong exact bytes, field 24 still sourced from `extra_flags`, seed/readback mismatch,
wrong GUID or list position, wrong or missing visible character, selection/mutation opcode, unstable
connection, unequal repeats, protected-input mutation, or unsafe reset.

`INCONCLUSIVE` is limited to an environment/preflight stop before the protocol boundary: occupied
port, stale binary, wrong client hash, missing data, display/Wine failure, insufficient space, or
unowned process/window/endpoint.

## Completion predicate

Plan 9 is complete only when the empty fixture still passes, the independently reviewed exact
one-character fixture passes, both queries source field 24 from `characters.order`, and two fresh
build 15595 generations display the same one-row character without selection or mutation before
clean reset and cache purge.

The next branch is [Plan 10](10-build-15595-character-selection.md), which selects this exact GUID and
stops at database-load callback admission.
