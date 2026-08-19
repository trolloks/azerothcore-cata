# Plan 19: bit-packed movement/create block Cata conversion

Canonical issue: [#40](https://github.com/trolloks/azerothcore-cata/issues/40)

Status: complete, merged in #39 (commit 471ef218c). Continues the "Object model" plan family started by Plan 17 (#36).
Converts `Object::BuildMovementUpdate` (`src/server/game/Entities/Object/Object.cpp`) from WotLK's
plain byte-stream movement format (`uint16 flags` followed by sequential field writes) to Cata
4.3.4 build 15595's bit-packed movement/create block.

## Evidence this is the next real blocker

Plan 17 converted the values-block field layout and, verified live, measurably improved client
behavior: the real build 15595 client began sending `CMSG_PING` and receiving `SMSG_PONG` (a
liveness signal never observed in any prior test run) and sustained a full stability window of
streamed world content with no early death. Despite that, the client still does not dismiss the
90% loading screen -- confirmed by direct visual observation of the running client, not just log
inspection. Since the values-block layout is now correct but the client still cannot construct its
own player object, the next most likely culprit is the *other* half of `SMSG_UPDATE_OBJECT`'s
wire format: the movement/create block that precedes the values block in every create-type update.

Our current `Object::BuildMovementUpdate` writes a flat `uint16` flags field followed by
unconditional sequential writes (position, orientation, speeds, etc.) -- this is WotLK's format.
`cata-js`'s reference documentation (`docs/smsg-update-object.md` in that project, and the matching
implementation in `src/server/game/packet/login_packet.ts`) describes Cata's real format in exact
bit-level detail: a 57-bit accumulated header (`CreateObjectBits`, 38 outer bits plus 19 inner
`MovementUpdate` bits when the `MovementUpdate`/`LIVING` bit is set) flushed to 8 bytes, followed by
an interleaved data section where GUID byte-presence bits gate `WriteByteSeq` emissions (with an
XOR-1 obfuscation) and float/uint32 fields are written in a specific, non-sequential order. This is
a fundamentally different bit-packing scheme, not a field-by-field rename -- every
`UPDATEFLAG_*`-driven code path in `Object::BuildMovementUpdate`/`BuildMovementUpdateBlock`-adjacent
code (living units, stationary objects, transports, corpses) needs auditing against the real
format, not just the living/player path this investigation happened to exercise.

## Reference sources

1. `cata-js` commit `ab964a0e8dfe50a44fa92716ed05438f4a14dfd3`, `docs/smsg-update-object.md` and
   `docs/world-login-flow.md` for the exact bit-level layout proven against a real client.
2. Pinned TrinityCore commit `c699217775d90794158422387b07a917e161b582`
   (`/mnt/f79365ff-6a68-45da-925e-b9ddc6d5da6c/Fun/TrinityCore/TrinityCore`),
   `Object::BuildMovementUpdate` in `src/server/game/Entities/Object/Object.cpp`, as the canonical
   C++ implementation to port from (real TrinityCore Cata source, not a reimplementation).

## Scope

In scope: the movement/create block bit-packing itself (`Object::BuildMovementUpdate` and any
`UPDATEFLAG_*` branches it contains) for at minimum the living-unit/player path that gates world
entry. Extending full correctness to every object type (transports, corpses, stationary objects)
is acceptable to defer to a follow-up if the living/player path alone is sufficient to clear the
90% hang -- confirm with a real client run before deciding whether to split further.

Out of scope: the values-block field layout (Plan 17, done) and Item/Container/GameObject/
DynamicObject/Corpse field content (Plan 18).

## Acceptance

- `Object::BuildMovementUpdate`'s living-unit path matches the pinned TrinityCore reference exactly,
  cross-checked against cata-js's documented bit layout.
- `worldserver` builds clean.
- A real build 15595 client run dismisses the 90% loading screen and reaches the in-world-control
  marker (`CMSG_TIME_SYNC_RESP` observed) across at least two consecutive clean runs.
