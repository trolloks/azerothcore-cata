# Plan 10: select the enumerated build 15595 character

Status: execution-ready after Plan 9 merges. Create this branch from the new `master`; do not stack it
on the Plan 9 branch.

Branch: `plan/10-build-15595-character-selection`.

## Outcome

Select the sole `Cataplan` character through the real build 15595 client and prove the resulting
`CMSG_PLAYER_LOGIN` crosses the existing session admission boundary:

```text
seeded GUID = enumerated GUID = selected GUID = callback GUID = 16909060
```

The selected GUID must pass `_legitCharacters`, cause the existing login query holder to complete,
and reach the first line of `WorldSession::HandlePlayerLoginFromDB` in two fresh isolated generations.

Plan 10 ends at callback entry, before `Player::LoadFromDB`. Record the first later result as
diagnostic evidence, but do not make a successful load, loading screen, outbound packet, disconnect,
or timeout part of the pass predicate.

This plan does not prove character loading, initial packet order, `SMSG_LOGIN_VERIFY_WORLD`, map
addition, update objects, movement, phasing, visibility, world entry, or player control. It also does
not implement character creation, deletion, rename, customization, race change, or faction change.

## Why this boundary

Plan 9 produces the exact database-backed character and inserts its GUID into the authenticated
session's `_legitCharacters` set. `PlayerLogin::Read()` already uses the pinned Cataclysm packed-GUID
order, and `HandlePlayerLoginOpcode` already owns all legitimacy and async-query policy. Selection is
therefore the next narrow dependency in the actual login flow.

Everything after `HandlePlayerLoginFromDB` is much wider and remains partly WotLK-shaped:

- `Player::LoadFromDB` initializes old update fields and crosses many DBC-backed subsystems;
- update-object outer framing lacks Cataclysm's map id;
- player movement serialization remains the older layout;
- phase handling and several initial packet payloads/order are unresolved;
- `SMSG_LOGIN_VERIFY_WORLD` currently appears at a different point than the pinned sequence.

A visible loading screen proves only that the client sent or accepted part of the transition. It is
not world-entry evidence.

## Entry requirements

Plan 9 must be merged and its checker must still prove:

- one owned `Cataplan` row with low GUID `16909060` for account `900000`;
- exact populated enumeration and list position `7`;
- that GUID admitted into `_legitCharacters` in the same session;
- a stable one-character build 15595 screen with no prior selection;
- two accepted/reset generations and unchanged protected inputs.

Reuse the Plan 9 character row unchanged. Do not seed `character_homebind` or any related login table:
callback entry occurs before `Player::LoadFromDB`, so additional rows would only push into explicitly
deferred work.

Read `.agents/docs/cpp-guidelines.md` before changing C++. Read `.agents/docs/build.md` before any
build or test. Obtain explicit user authorization before configuring, building, starting Docker, or
launching the client.

## Authorities

| Authority | Pinned input | Use |
| --- | --- | --- |
| AzerothCore | latest master after Plan 9 | legitimacy, query, callback, and lifecycle ownership |
| TrinityCore | `c699217775d90794158422387b07a917e161b582` | `CMSG_PLAYER_LOGIN` GUID order |
| cata-js | runtime-proven commit `3a697ce` | request/order corroboration and later failure notes |
| client | build 15595, SHA-256 `0ea9cc0458cb137f4e0767e8a1896e2042480a4a22397480d7ee64c2e696192a` | acceptance judge |

## Exact request contract

Keep the existing `WorldPackets::Character::PlayerLogin` type and parser. Its mask order is:

```text
[2, 3, 0, 6, 4, 5, 1, 7]
```

Its present-byte order is:

```text
[2, 7, 0, 3, 5, 6, 1, 4]
```

For player low GUID `0x01020304`, the exact request body is:

```text
E2 03 05 00 02
```

Mask `E2` selects bytes 2, 0, 3, and 1. The encoded bytes are XORed with `1`, producing
`03 05 00 02` in the parser's present-byte order.

Extend `CharacterPacketsTest.cpp` with these cases:

1. `E203050002` decodes `ObjectGuid::Create<HighGuid::Player>(16909060)`;
2. the decoded GUID is a player GUID with low counter `16909060`;
3. empty, `E2`, and `E2030500` bodies throw `ByteBufferException` as truncated input;
4. mutating any mask/data byte cannot produce the expected admitted GUID.

Do not add a Plan 10 trailing-byte rule. The current parser and generic typed dispatch tolerate and
log an unprocessed tail, and changing that policy is unrelated to proving this valid selection.
Do not modify generic packet dispatch.

## Existing session flow to preserve

`WorldSession::HandlePlayerLoginOpcode` must retain every existing check:

- realm disabled;
- duplicate or already-loading player;
- non-player GUID;
- account/session ownership;
- GUID absent from `_legitCharacters` outside the existing cluster-mode behavior.

After admission, retain its current creation of `LoginQueryHolder`, loading-state change,
`Initialize`, and asynchronous `DelayQueryHolder` scheduling. Callback entry itself proves that the
query was scheduled and completed; do not add another production scheduling event or a test-only
bypass.

At the first line of `HandlePlayerLoginFromDB`, before `Player::LoadFromDB`, add at most one bounded
`network.opcode` event if current evidence cannot prove the holder GUID. Keep unrelated existing
login diagnostics unchanged. Do not add packet hex dumps or logs inside `Player::LoadFromDB`.

## Admission evidence

The runner must establish this ordered, same-session predicate:

1. the owned database seed contains GUID `16909060` for account `900000`;
2. successful character enumeration reports and admits GUID `16909060`;
3. the real client sends one `CMSG_PLAYER_LOGIN` which decodes GUID `16909060`;
4. no legitimacy rejection or kick occurs before admission;
5. `HandlePlayerLoginFromDB` begins with holder GUID `16909060`;
6. no client or server crash occurs before callback entry.

Add a runner self-check case whose transcript starts a fresh authenticated session and sends the same
selection without a successful enumeration. It must not satisfy the Plan 10 predicate. This proves
the evidence does not bypass `_legitCharacters`.

## Existing runner extension

Add mode `character-selection` to `run_real_client_authentication.py`. Reuse the Plan 9 seed,
database readback, character-completion milestone, protected-input checks, and manifest-owned
lifecycle.

For this mode:

1. Wait for the one-character screen and confirm the Plan 9 row/evidence.
2. Prompt the operator to select only `Cataplan` by Enter or double-click. Do not add a general UI
   automation framework.
3. Wait for one `CMSG_PLAYER_LOGIN` and the callback-entry evidence.
4. Require all four GUID values to equal `16909060` in one session.
5. Stop the acceptance clock at callback entry and immediately classify the next observation as one
   of `LOAD_RETURNED_FALSE`, `FIRST_OUTBOUND_<SYMBOL>`, `DISCONNECTED`, `TIMEOUT`, or another explicit
   diagnostic value.
6. Reset the owned generation without waiting for a successful load or world entry.

The boundary projection compared across generations ends at callback entry. Retain each run's first
downstream diagnostic for review, but exclude it from equality because downstream conversion is not
part of Plan 10. Do not let the diagnostic replace or weaken any admission field.

Commit `apps/cata/fixtures/plan10-character-selection.json` only after two live generations pass.
It records:

- schema version, plan `10`, build `15595`, and the pinned client hash;
- exact request body `E203050002`;
- seeded, enumerated, selected, and callback GUID `16909060`;
- ordered enumeration, selection, legitimacy, and callback milestones;
- `legitimate_character: true` and `login_callback_entered: true`;
- two distinct fresh generations and equal through-callback projections;
- each run's separately labeled downstream diagnostic;
- protected inputs unchanged and reset `PASS`.

Never commit a placeholder `PASS` fixture, raw log, packet capture, account name, endpoint, path,
timestamp, screenshot, PID, credential, session key, or crypto material.

## Files to change

- `src/test/server/game/Server/Packets/CharacterPacketsTest.cpp`
- `src/server/game/Handlers/CharacterHandler.cpp` only if one callback-entry event is necessary
- `apps/cata/run_real_client_authentication.py`
- `apps/cata/check_conversion.py`
- `apps/cata/fixtures/plan10-character-selection.json` only after acceptance
- this plan, `plan/conversion-status.tsv`, and `plan/decision-log.tsv`

Do not change character seed values, schema SQL, packet opcodes, generic dispatch, `Player::LoadFromDB`,
initial packets, update fields, or map code.

## Execution order

1. Create the branch from merged Plan 9/master and rerun Plan 9's source-only checks.
2. Add the exact valid/truncated packed-GUID parser cases.
3. Add the runner's selection mode, four-way equality predicate, no-enumeration negative self-check,
   callback boundary, and diagnostic classification.
4. Add a `protocol.player-login-admission` checker anchor. It must remain open until the exact parser
   fixture and two live same-session admission runs exist.
5. After explicit authorization, build affected targets and run the focused packet test.
6. Run two fresh client generations, select only `Cataplan`, stop at callback entry, and reset each.
7. Compare the through-callback projections, review the diagnostics, create the sanitized fixture,
   and close only the player-login admission blocks.
8. Leave `Player::LoadFromDB` and every world-entry anchor open, run the checker, and purge the
   manifest-owned caches.

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

Use Plan 8's complete two-generation `prepare -> run -> verify -> reset` procedure with:

```bash
--mode character-selection
```

The runner's selection-mode `verify` command must require callback admission rather than the Plan 8
screen-confirmation flag. After both resets, run `compare-last-two`, then final reset with
`--purge-client-base --purge-database-cache`.

## Failure classification

`FAIL` means wrong valid parser result, truncated input accepted, GUID mismatch, selection without a
same-session enumeration, legitimacy rejection, missing callback, pre-boundary crash, unequal boundary
projections, protected-input mutation, or unsafe reset.

`INCONCLUSIVE` means an environment/preflight failure prevented selection: occupied port, stale binary,
wrong client hash, missing data, unusable display/Wine, insufficient space, or an unowned
process/window/endpoint.

A post-callback load failure, packet, disconnect, or timeout is diagnostic, not Plan 10 failure. A
failure before callback entry is Plan 10 failure.

## Completion predicate

Plan 10 is complete only when exact parser tests pass and two fresh build 15595 client selections
prove four-way GUID equality, same-session `_legitCharacters` admission, and callback entry without a
pre-boundary crash, followed by matching boundary evidence, unchanged protected inputs, clean reset,
and cache purge.

Do not assign Plan 11 from assumptions. Ground the first downstream result captured by Plan 10, then
split load conversion, initial packets, update objects, and world entry into the smallest evidence-led
plans. Character creation also remains a separate later state-mutation plan.
