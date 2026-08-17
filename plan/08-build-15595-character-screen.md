# Plan 8: typed empty character enumeration and stable build 15595 screen

Status: ready for implementation. Plan 7 is merged and supplies the entry evidence.

Branch: `plan/08-build-15595-character-screen`, created from the latest merged `master`. Do not stack
this work on another plan branch.

## Outcome

Move the Cataclysm character-enumeration request and response into AzerothCore's typed packet layer,
prove the successful empty response body is exactly `000001000000`, and make the real build 15595
client remain connected at the zero-character screen.

Plan 8 passes only when two fresh isolated generations prove all of the following:

- the owned characters database has zero active rows for the synthetic account;
- the client sends `CMSG_CHAR_ENUM` and reports `COP_GET_CHARACTERS ... Completed ... TRUE`;
- the server sends the exact typed empty `SMSG_CHAR_ENUM` response;
- the world connection and PID-owned client window remain present for a ten-second hold;
- a human confirms that the captured PID-owned window shows the automatic character-creation screen;
- the full transcript contains no character mutation, selection, or player-login request;
- both generations have the same sanitized semantic result and reset cleanly.

The visible race, class, name, and Accept controls are automatic client UI for an account with no
characters. They are not evidence that the client sent `CMSG_CHAR_CREATE`.

This plan does not prove a populated character row, create or modify a character, select a character,
load player data, or enter the world.

## Entry evidence

Plan 7's committed fixture already proves two build 15595 runs through world `AUTH_OK`,
`CMSG_CHAR_ENUM`, and `SMSG_CHAR_ENUM`. Its retained client evidence shows the zero-character creation
screen, and its client log reports character retrieval completed successfully.

That is only pre-conversion evidence. The current implementation still serializes `SMSG_CHAR_ENUM`
inline in `CharacterHandler.cpp`, has no exact packet fixture, and does not require a stable visual
state. Its non-empty mapping is also known to be wrong: query field 24 is `c.extra_flags`, but the
handler consumes that field as the character's list position. Plan 8 must leave this non-empty defect
open for Plan 9.

## Authorities

| Authority | Pinned input | Use |
| --- | --- | --- |
| AzerothCore | this branch's merge base | ownership, session policy, async queries, hooks |
| TrinityCore | `c699217775d90794158422387b07a917e161b582` | 4.3.4 packet model and write order |
| cata-js | `ab964a0e8dfe50a44fa92716ed05438f4a14dfd3` | protocol corroboration only |
| client | build 15595, SHA-256 `0ea9cc0458cb137f4e0767e8a1896e2042480a4a22397480d7ee64c2e696192a` | acceptance judge |

Read `.agents/docs/cpp-guidelines.md` before changing C++. Read `.agents/docs/build.md` before any
build or test. Repository policy requires explicit user authorization before configuring, building,
starting Docker, or launching the client.

## Fixed architecture

### Packet ownership

Add these types to `WorldPackets::Character` in `CharacterPackets.h`:

```cpp
class EnumCharacters final : public ClientPacket
{
public:
    EnumCharacters(WorldPacket&& packet) : ClientPacket(CMSG_CHAR_ENUM, std::move(packet)) { }
    void Read() override;
};

class EnumCharactersResult final : public ServerPacket
{
public:
    struct CharacterInfo
    {
        struct VisualItemInfo
        {
            uint32 DisplayID = 0;
            uint32 DisplayEnchantID = 0;
            uint8 InvType = 0;
        };

        Position PreloadPos;
        ObjectGuid Guid;
        ObjectGuid GuildGUID;
        uint32 Flags = 0;
        uint32 Flags2 = 0;
        int32 MapID = 0;
        uint32 PetCreatureDisplayID = 0;
        uint32 PetCreatureFamilyID = 0;
        uint32 PetExperienceLevel = 0;
        int32 ZoneID = 0;
        uint8 ClassID = 0;
        uint8 ExperienceLevel = 0;
        uint8 FaceID = 0;
        uint8 FacialHair = 0;
        uint8 HairColor = 0;
        uint8 HairStyle = 0;
        uint8 ListPosition = 0;
        uint8 RaceID = 0;
        uint8 SexID = 0;
        uint8 SkinID = 0;
        bool FirstLogin = false;
        std::string Name;
        std::array<VisualItemInfo, 23> VisualItems = { };
    };

    struct RestrictedFactionChangeRuleInfo
    {
        int32 Mask = 0;
        uint8 Race = 0;
    };

    EnumCharactersResult() : ServerPacket(SMSG_CHAR_ENUM) { }
    WorldPacket const* Write() override;

    bool Success = false;
    std::vector<CharacterInfo> Characters;
    std::vector<RestrictedFactionChangeRuleInfo> FactionChangeRestrictions;
};
```

Include `Position.h` and `<array>` explicitly. Do not give `CharacterInfo` a database `Field*`
constructor: the handler owns row mapping and the packet owns bytes.

`EnumCharacters::Read()` accepts only an empty body. If `rpos() != size()`, throw
`ByteBufferInvalidValueException("character enumeration", "trailing bytes")`. Pinned Trinity leaves
this empty `Read()` unguarded; the local stricter rule is intentional because this is a network trust
boundary and the request has no legal fields.

Move the current full response serializer mechanically into `EnumCharactersResult::Write()`. Preserve
the existing non-empty bit and byte order while adding the pinned faction-restriction vector shape.
Do not make an empty-only serializer, change enum field semantics, or query the database from the
packet class.

### Handler ownership

Change the session signature to:

```cpp
void HandleCharEnumOpcode(WorldPackets::Character::EnumCharacters& packet);
```

Keep all of this in `CharacterHandler.cpp`:

- prepared-statement choice and authenticated account binding;
- asynchronous callback scheduling;
- race, class, gender, pet, and equipment validation;
- current flag derivation and `_legitCharacters` population;
- the final decision to send the result.

Replace the local `std::vector<CharacterInfo>` and raw `WorldPacket` with one
`EnumCharactersResult`, set `Success = true`, populate `Characters`, and send `result.Write()`.
Delete the global `CharacterInfo` from `WorldSession.h` after all users move to the nested packet type.
Remove the temporary per-field/equipment/list-position `LOG_INFO` dump. Retain normal errors, warnings,
and one bounded count-level log if the runner needs it.

Do not change either opcode value, the prepared query, field 24, `Player::BuildEnumData`, or any
character policy in this plan.

## Exact empty contract

The successful empty body is six bytes:

```text
00 00 01 00 00 00
```

It encodes 23 zero faction-restriction-count bits, success bit `1`, 17 zero character-count bits,
and a bit flush. The compact fixture is `000001000000`. The corresponding failed empty result is
`000000000000`.

Add `src/test/server/game/Server/Packets/CharacterPacketsTest.cpp` with four focused cases:

1. an empty `CMSG_CHAR_ENUM` body is fully consumed;
2. body `00` throws `ByteBufferInvalidValueException`;
3. a successful empty result has opcode `SMSG_CHAR_ENUM` and body `000001000000`;
4. a failed empty result has opcode `SMSG_CHAR_ENUM` and body `000000000000`.

Reuse the payload-to-hex style from `AuthenticationPacketsTest.cpp`. Do not add sockets, sessions,
database mocks, or a second test framework. The recursive source glob discovers the new test only
when CMake configures; do not edit `src/test/CMakeLists.txt`, but do re-run the established out-of-tree
configure command after explicit build authorization.

## Existing runner extension

Extend `apps/cata/run_real_client_authentication.py`; do not create another lifecycle harness.

Add exactly these CLI fields:

- `prepare --mode character-screen`;
- `run --stability-seconds`, default `10`, rejecting values below `5`;
- `verify --confirm-expected-screen`.

For this mode:

1. Retain all Plan 7 client hash, Docker label, volume identity, unused-port, PID, source-client,
   personal-Bottle, protected-input, and bounded-reset checks.
2. Match a client `Completed` line for `COP_GET_CHARACTERS` only when its result is `TRUE`. The
   existing `Initiating` line cannot satisfy Plan 8.
3. Start the stability timer only after that completion milestone.
4. After the hold, require the manifest-owned client PID and established owned world TCP connection.
5. Capture non-empty `window.xwd` and `window.xprop` from the PID-owned window after the hold.
6. Query the actual owned characters schema and require zero active rows for account id `900000`.
   `realmcharacters.numchars=0` is supporting state, not a substitute for this query.
7. Inspect the complete unsanitized transcript before allowlisting. Fail if it contains
   `CMSG_CHAR_CREATE`, `CMSG_CHAR_DELETE`, `CMSG_CHAR_CUSTOMIZE`, `CMSG_CHAR_FACTION_CHANGE`,
   `CMSG_CHAR_RACE_CHANGE`, `CMSG_CHAR_RENAME`, or `CMSG_PLAYER_LOGIN`.
8. Ask the operator to inspect the PID-bound capture. `verify --confirm-expected-screen` records only
   the boolean confirmation, never the image.

The normalized comparison must include the mode, completed milestone, exact empty payload, database
row count, stability interval, connection/window booleans, screen confirmation, forbidden-opcode
list, protected-input result, and reset result. Exclude paths, endpoints, ports, PIDs, timestamps,
raw log lines, credentials, screenshots, and screenshot hashes.

Extend the runner's no-Docker `self-check` with one mutation for each new acceptance field. Keep the
existing Plan 7 fixture and authentication-only replay behavior unchanged.

## Sanitized fixture

Create `apps/cata/fixtures/plan8-character-screen.json` only after two live generations pass. It must
record:

- schema version, plan `8`, build `15595`, and the pinned client hash;
- verdict `PASS` and mode `character-screen`;
- ordered character-completion and enum opcode milestones;
- response body `000001000000` and `character_rows: 0`;
- `stability_seconds: 10`, stable connection, PID-bound evidence, and screen confirmation;
- an empty forbidden-opcode list;
- two distinct fresh generations with equal normalized results;
- protected inputs unchanged and reset `PASS`.

Never commit a placeholder `PASS` fixture. Raw logs, XWD/xprop data, manifests, credentials, paths,
endpoints, ports, account names, timestamps, and crypto material stay outside Git.

## Files to change

- `src/server/game/Server/Packets/CharacterPackets.h`
- `src/server/game/Server/Packets/CharacterPackets.cpp`
- `src/server/game/Server/WorldSession.h`
- `src/server/game/Handlers/CharacterHandler.cpp`
- `src/test/server/game/Server/Packets/CharacterPacketsTest.cpp` (new)
- `apps/cata/run_real_client_authentication.py`
- `apps/cata/check_conversion.py`
- `apps/cata/fixtures/plan8-character-screen.json` (only after acceptance)
- this plan, `plan/conversion-status.tsv`, and `plan/decision-log.tsv`

No CMake list or SQL file changes are needed.

## Execution order

1. Confirm this branch is based on the merged Plan 7/master state and the worktree is clean.
2. Add the request/response packet types and the four focused tests.
3. Move the value type and serializer mechanically; keep the empty fixture green.
4. Move typed dispatch and handler sending; grep out the raw serializer and global DTO.
5. Add the runner mode, stability predicate, database count, denylist, and self-check mutations.
6. Add a `protocol.character-enumeration` checker anchor. Its Plan 8 gate must require the typed
   source shape, local empty fixture, zero-row live fixture, two fresh runs, stability, protected
   inputs, and clean reset. Leave it open while the live fixture is absent.
7. After explicit authorization, reconfigure the existing out-of-tree build so its plain source glob
   sees the new test, then build `game`, `worldserver`, and `unit_tests` and run the focused test once.
8. Run two fresh client generations. Manually inspect each PID-bound capture, reset after each, and
   compare the sanitized projections.
9. Create the reviewed fixture, close only the empty-enumeration evidence gate, run the checker, and
   purge the manifest-owned caches.

## Commands

These source-only checks are safe before build authorization:

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

Set `TRINITY_ROOT`, `CATA_JS_ROOT`, and `CLIENT_EXE` to the absolute pinned reference checkout paths
and the pinned source `Wow-64.exe` before running the full audit.

After explicit authorization, recover and rerun the established configure invocation for the
operator-provided absolute `BUILD_ROOT`; do not invent different options. Then run:

```bash
cmake --build "$BUILD_ROOT" --target game worldserver unit_tests -j2
"$BUILD_ROOT/src/test/unit_tests" --gtest_filter='CharacterPacketsTest.*'
```

Set absolute paths for `RUN_ROOT`, `BUILD_ROOT`, `CLIENT_ROOT`, `DATA_ROOT`, `SERVER_DBC_ROOT`,
`WINE_RUNNER`, `PERSONAL_BOTTLE`, and `DISPLAY`. `RUN_ROOT` must be outside the repository. Set
`XAUTHORITY` only when the display requires it; omit the argument entirely otherwise.

Run this sequence twice against the same manifest:

```bash
PLAN_MANIFEST="$RUN_ROOT/manifest.json"
python3 apps/cata/run_real_client_authentication.py prepare \
  --manifest "$PLAN_MANIFEST" \
  --authserver "$BUILD_ROOT/src/server/apps/authserver" \
  --worldserver "$BUILD_ROOT/src/server/apps/worldserver" \
  --unit-tests "$BUILD_ROOT/src/test/unit_tests" \
  --client-root "$CLIENT_ROOT" \
  --data-root "$DATA_ROOT" \
  --server-dbc-root "$SERVER_DBC_ROOT" \
  --wine-runner "$WINE_RUNNER" \
  --personal-bottle "$PERSONAL_BOTTLE" \
  --display "$DISPLAY" \
  --mode character-screen
python3 apps/cata/run_real_client_authentication.py run \
  --manifest "$PLAN_MANIFEST" --timeout 300 --stability-seconds 10
python3 apps/cata/run_real_client_authentication.py verify \
  --manifest "$PLAN_MANIFEST" --confirm-expected-screen
python3 apps/cata/run_real_client_authentication.py reset --manifest "$PLAN_MANIFEST"
```

After the second reset:

```bash
python3 apps/cata/run_real_client_authentication.py compare-last-two --manifest "$PLAN_MANIFEST"
python3 apps/cata/run_real_client_authentication.py reset \
  --manifest "$PLAN_MANIFEST" --purge-client-base --purge-database-cache
```

## Failure classification

`FAIL` means the implementation or owned run contradicted the contract: wrong opcode or bytes,
malformed input accepted, nonzero owned character count, forbidden opcode, lost connection during the
hold, wrong visual state, mismatched repeats, protected-input mutation, or unsafe reset.

`INCONCLUSIVE` means the environment prevented reaching the boundary: occupied port, missing or stale
binary, wrong client hash, insufficient space, unusable display/Wine, unowned window/endpoint, or server
startup failure. Do not patch protocol code to turn an environmental stop into a pass.

## Completion predicate

Plan 8 is complete only when the four local packet cases pass, two fresh build 15595 generations
reach and hold the verified zero-character screen with zero owned rows and no forbidden opcode, the
sanitized results match, protected inputs remain unchanged, reset and cache purge pass, and the
conversion checker closes only the typed empty-enumeration evidence.

The next branch is [Plan 9](09-build-15595-populated-character-list.md), which fixes field 24 and
proves one deterministic populated response without selecting it.
