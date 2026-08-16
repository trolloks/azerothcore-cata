# Plan 2: deterministic bit-buffer contract

Status: ready for review. Execution has not started.

## Outcome

Make AzerothCore's existing `ByteBuffer` bit cursor and packed-byte helpers deterministic and prove
their Cataclysm wire bytes with small local fixtures. Keep the current class and ownership model so
upstream changes remain easy to receive.

This plan does not convert opcodes, socket framing, authentication, compression, GUID layout, packet
handlers, databases, or client behavior.

## Why this is next

Plan 1 found that the default constructor leaves `_curbitval` undefined, move assignment drops bit
state, and `clear()` leaves prior bit state alive. The experimental `WriteBits` path also performs an
unsafe native-width shift before its 64-bit mask. Every current Cataclysm auth, character-list, and
packed-GUID experiment depends on these primitives, so proving them is smaller and safer than fixing
each packet separately.

Compression-stream ownership and direction-safe opcode tables are independent later plans. Combining
them here would make failures harder to localize.

## Pinned behavior

- Preserve current AzerothCore `ByteBuffer` ownership, exceptions, and call sites.
- Use TrinityCore `c699217775d90794158422387b07a917e161b582` only for Cataclysm bit order and
  byte-sequence semantics.
- Use `cata-js` `ab964a0e8dfe50a44fa92716ed05438f4a14dfd3` only as corroborating packet evidence.
- Do not copy either implementation wholesale.

## Unit 1: define cursor state

Work:

1. Define one valid empty bit state for every constructor.
2. Preserve that state through copy and move construction and assignment.
3. Reset read position, write position, bit position, and current bit byte together in `clear()`.
4. Leave moved-from buffers valid and empty.

Gate:

- Default, reserved, message-backed, copied, moved, assigned, and cleared buffers produce the same
  empty-state behavior.
- No uninitialized or stale bit byte can be observed.

## Unit 2: bound bit operations

Work:

1. Keep Cataclysm's most-significant-bit-first wire order.
2. Remove undefined shifts and define accepted bit counts at the API boundary.
3. Make byte-alignment behavior explicit for reads, writes, and `FlushBits()`.
4. Keep packed GUID byte-sequence helpers byte-compatible with the pinned Cataclysm reference.
5. Fix adjacent string extraction only if a fixture proves the existing helper truncates valid packet
   bytes; do not broaden this into a general buffer rewrite.

Gate:

- Invalid widths fail at the boundary instead of invoking undefined behavior.
- Zero, partial-byte, exact-byte, cross-byte, 32-bit, and 64-bit cases have exact expected bytes.
- Read/write round trips supplement exact vectors; they are not the only proof.

## Unit 3: leave a small regression harness

Work:

1. Add focused tests using the repository's existing test structure; add no framework or dependency.
2. Include exact byte vectors for one auth-style bit field, one character-list count, and one packed
   GUID mask/byte sequence.
3. Extend the Plan 1 checker anchor only enough to detect a regression to undefined cursor state.
4. Update the ledger rows for the changed blocks and anchor proof.

Gate:

- The smallest targeted test command passes twice with identical results.
- The Plan 1 checker remains green and marks `protocol.byte-buffer-bits` converted only when the
  source and fixture proof both pass.
- Formatting and diff checks pass.

## Safety and execution boundary

Read the repository C++ and build guidance before implementation. Do not configure, build, or run the
targeted test command until the user explicitly authorizes that execution. No database is needed; do
not inspect, start, stop, or modify any database or Docker database resource. Do not launch or modify
the client or Bottle.

## Completion predicate

Plan 2 is complete when all `ByteBuffer` construction, assignment, reset, bit-width, alignment, and
packed-byte paths have deterministic exact-byte proof; the Plan 1 audit remains green; and no socket,
opcode, handler, database, or client behavior changed.
