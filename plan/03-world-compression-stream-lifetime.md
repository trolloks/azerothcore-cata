# Plan 3: world compression stream lifetime

Status: ready for review. Execution has not started.

## Outcome

Give the existing Cataclysm world-packet deflate stream one deterministic lifetime without changing
which packets are compressed or their wire bytes. Keep the current AzerothCore socket and queue
design, and use the smallest ownership fix compatible with the pinned Cataclysm reference.

This plan does not redesign packet compression, change thresholds, framing, opcodes, encryption,
authentication, packet handlers, databases, or client behavior.

## Why this is next

The Plan 2 audit is green and its shared bit-buffer prerequisite is converted. The remaining
`protocol.compression` anchor reports two local lifetime defects in the next-smallest protocol
foundation slice: `_compressionStream` is allocated with `new`, the constructor does not initialize
the pointer, and the default destructor neither calls `deflateEnd` nor deletes it.

Direction-safe opcode tables are a larger independent plan. Compression byte compatibility is also
broader than resource lifetime and will need ordered multi-packet fixtures later. Mixing either into
this ownership repair would make the result harder to review against upstream.

## Pinned behavior

- Preserve AzerothCore's `WorldSocket`, queue, logging, and close paths.
- Preserve the current persistent `Z_SYNC_FLUSH` stream, compression threshold, compressed-opcode
  mask, uncompressed-size prefix, and packet ordering exactly.
- Use TrinityCore `c699217775d90794158422387b07a917e161b582` only as evidence for the Cataclysm
  stream lifetime.
- Do not copy its surrounding socket implementation or add a general compression framework.

## Unit 1: establish one owner

Work:

1. Initialize `_compressionStream` to null in every construction path.
2. Allocate and initialize it only after the client initializer is accepted.
3. On `deflateInit` failure, release the allocation, restore null state, and close through the
   existing error path.
4. Prevent a second initializer pass from overwriting an owned stream.

Gate:

- A socket destroyed before, during, or after initializer handling has one valid compression state.
- Initialization failure cannot leak memory or leave an invalid pointer for destruction.

## Unit 2: finalize exactly once

Work:

1. Finalize an initialized stream with `deflateEnd` and release it in `WorldSocket` destruction.
2. Keep cleanup local to `WorldSocket`; add no shared owner type unless a test proves direct ownership
   cannot be made safe.
3. Leave packet compression and socket-close sequencing unchanged.

Gate:

- Zero successful initializations perform zero finalizations.
- One successful initialization performs exactly one finalization and one release.
- Normal close, delayed close, and initialization-error paths cannot double-finalize.

## Unit 3: retain executable proof

Work:

1. Extend the existing `protocol.compression` checker anchor only enough to recognize initialized,
   failure-safe, finalized ownership.
2. Add the smallest focused lifecycle check that exercises production ownership if the current test
   target can do so without pulling in the database layer. Otherwise, keep the executable checker as
   the regression guard and record that limitation.
3. Compile the touched `WorldSocket` object and update every changed ledger block.

Gate:

- The focused check passes twice with identical output.
- The Plan 1 audit remains green and marks `protocol.compression` converted only with source proof and
  the chosen lifecycle check recorded.
- C++ formatting and `git diff --check` pass.

## Safety and execution boundary

Create `plan/03-world-compression-stream-lifetime` from updated `master` only after Plan 2 is merged.
Read the repository C++ and build guidance before implementation. Do not build or run tests until the
user authorizes Plan 3 execution. No database is needed: do not inspect, start, stop, or modify any
database, Docker database resource, client file, or Bottle.

## Completion predicate

Plan 3 is complete when the persistent world compression stream has deterministic null, initialized,
failure, and destruction states; every successful initialization is finalized and released exactly
once; the audit is green; and all compression bytes and surrounding socket behavior remain unchanged.
