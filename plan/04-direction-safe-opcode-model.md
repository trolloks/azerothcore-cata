# Plan 4: direction-safe opcode model

Status: implemented and verified.

## Outcome

Separate client and server opcode types, registration tables, and name lookup without changing any
numeric opcode, parser, serializer, handler, connection, or packet behavior. Keep the existing
AzerothCore dispatch structure and use the pinned Cataclysm reference only for direction ownership.

This plan does not correct opcode values, rename handlers, convert payloads, add instance connections,
change authentication, or claim client compatibility.

## Why this is next

After Plan 3, `protocol.opcode-model` is the next open protocol-foundation anchor. The current
`Opcodes` enum contains both directions, while `OpcodeClient` and `OpcodeServer` are aliases of that
same type. `OpcodeTable::ValidateAndSetServerOpcode` writes dummy handlers into the client table, and
both logging directions use the same lookup. This makes a valid Cataclysm client/server value overlap
look like a collision and lets a direction mistake compile.

The committed audit currently measures 1,309 active registrations, two same-direction collisions,
four opposite-direction overlaps, six registered zero values, and one declared/registered direction
contradiction. Only the opposite-direction overlaps are made representable here. Incorrect values,
same-direction collisions, zero values, and the contradiction remain evidence for later opcode and
handler plans.

## Pinned behavior

- Preserve every current numeric constant and registration status exactly.
- Preserve `WorldSession` dispatch, packet processing categories, `WorldSocket` framing, and logging
  call sites.
- Use TrinityCore `c699217775d90794158422387b07a917e161b582` only for separate `OpcodeClient`,
  `OpcodeServer`, client table, server table, and direction-specific name lookup.
- Do not import TrinityCore's dual-connection routing, instance-only opcode policy, or surrounding
  socket implementation.

## Unit 1: define direction at declaration

Work:

1. Replace the combined `Opcodes` declaration and its direction aliases with distinct client and
   server opcode types.
2. Classify every active declaration from its registration direction and pinned Cataclysm evidence.
3. Define one explicit policy for legacy `MSG_` declarations used in both directions; do not hide
   ambiguous direction with broad casts or another combined type.
4. Keep all numeric values byte-for-byte unchanged.

Gate:

- A client opcode cannot be passed to a server-only API, or the reverse, without an explicit reviewed
  conversion at a raw wire boundary.
- Every active registration has exactly one declared direction or an explicit bidirectional policy.
- A generated before/after value manifest proves that no numeric opcode changed.

## Unit 2: separate registration and lookup

Work:

1. Give `OpcodeTable` independent client and server storage, typed lookup overloads, validation, and
   destruction.
2. Store `ServerOpcodeHandler` entries directly instead of dummy client packet handlers.
3. Split opcode-name logging by direction while preserving the existing output format.
4. Keep same-direction duplicate and bounds checks fail-closed; allow the same numeric value once in
   each direction.

Gate:

- Client dispatch reads only the client table and outbound logging reads only the server table.
- One synthetic client/server pair sharing a value registers and logs independently.
- A duplicate within either direction is still rejected.

## Unit 3: migrate callers and retain proof

Work:

1. Migrate current `Opcodes` variables and signatures to the narrow direction type they actually use.
2. Extend the conversion checker so `protocol.opcode-model` is converted only with distinct
   declarations, tables, lookup functions, and a preserved numeric-value manifest.
3. Add the smallest focused registry/lookup test supported by the existing test target.
4. Compile every changed core target and update every changed ledger block.

Gate:

- No production call site uses the old combined type or direction aliases.
- Exact opcode values and registration counts match the pre-plan manifest.
- Opposite-direction overlaps are reported as valid separate entries; existing same-direction and
  zero-value findings remain visible rather than being normalized away.
- The focused test and two full audits pass with identical output.

## Safety and execution boundary

Create `plan/04-direction-safe-opcode-model` from updated `master` only after Plan 3 is merged. Read
the repository C++ and build guidance before implementation. This is a type and registry migration,
not an opcode-value port. No database, client, Bottle, packet capture, or Docker database resource is
needed or permitted.

## Completion predicate

Plan 4 is complete when client and server opcode declarations, storage, validation, lookup, and
logging are direction-distinct; every existing numeric value is preserved; active callers compile
without the combined type; opposite-direction overlaps work independently; the focused proof and
full audit are green; and no payload or client behavior changed.

## Execution results

Plan 4 started from merged `master` commit `3b110b2d80c7758d26e73a19571d45a74b140410` on
`plan/04-direction-safe-opcode-model`. The implementation keeps the existing raw `WorldPacket`
boundary and splits semantic ownership above it:

- `OpcodeClient` owns 727 declarations and client handlers.
- `OpcodeServer` owns 588 declarations and server metadata.
- All 105 legacy `MSG_` names have one explicit opposite-direction typed alias and one shared name
  registration. They do not gain a second handler.
- Client dispatch can call only `ClientOpcodeHandler`. A server-only number received from a client
  still follows the previous queue-and-drop path instead of disconnecting early.

The immutable 1,315-row value manifest is `plan/04-opcode-values.tsv`, with SHA-256
`895f39dda942c1e4bf7de2a9a80d9b3f4a4151745a52b36bf8e46b936fd26853`. It preserves all six zero
registrations, both same-direction collisions, and all four valid opposite-direction overlaps.

Verification used the isolated MySQL 8.0.46 client-header toolchain without a database process:

- `game`, `worldserver`, and `unit_tests` compiled and linked.
- `OpcodeTableTest.KeepsDirectionsIndependent` passed twice with the real `0x0014` overlap and the
  existing `0x0205` duplicate.
- The complete unit binary passed 11,396 tests; data-dependent cases kept their existing skips.
- The checker self-check passed twice, and both C++ and SQL codestyle checks passed.

No opcode value, payload parser, payload serializer, database, client file, Bottle, port, or running
service changed. Real-client testing remains for a later vertical protocol slice because this plan
changes type ownership and lookup only.
