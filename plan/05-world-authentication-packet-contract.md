# Plan 5: world authentication packet contract

Status: complete.

## Outcome

Move the build 15595 world authentication wire formats into AzerothCore's existing typed packet
layer and prove their exact bytes. Cover `SMSG_AUTH_CHALLENGE`, `CMSG_AUTH_SESSION`, and
`SMSG_AUTH_RESPONSE` without changing account lookup, digest verification, socket encryption,
queue policy, or connection routing.

This plan does not launch the client, add dual world connections, change database schemas, alter
opcode values, or claim that the complete login flow works.

## Why this is next

Plan 4 made direction ownership reliable. These three authentication opcodes already match the
pinned Cataclysm reference and `cata-js`, so their payloads are the smallest useful protocol slice
that can now move behind typed client and server packet APIs.

The current code keeps the `CMSG_AUTH_SESSION` parser and both server serializers inline in
`WorldSocket.cpp` and `AuthHandler.cpp`. It also retains a commented WotLK parser beside the active
Cataclysm parser, and its one-byte error response does not share the success response's bit prefix.
That leaves one security boundary with several independent wire-format implementations.

## Pinned behavior

- Use TrinityCore commit `c699217775d90794158422387b07a917e161b582` as the canonical field order,
  digest-byte order, widths, bit order, and conditional response layout.
- Use `cata-js` commit `ab964a0e8dfe50a44fa92716ed05438f4a14dfd3` as corroborating evidence for
  the auth-session parser and successful client trace.
- Keep AzerothCore's socket lifecycle, prepared statements, account security checks, hooks, Warden,
  queue ownership, and `AuthCrypt` activation exactly where they are.
- Keep the Plan 4 opcode manifest unchanged.

## Unit 1: typed authentication codecs

Work:

1. Add `AuthenticationPackets.h` and `.cpp` under the existing `WorldPackets` layer.
2. Model the challenge seed, auth-session fields, digest, account name, addon bytes, success data,
   and optional queue data with their real widths.
3. Implement the three build 15595 payload codecs once. Reject truncated fields and addon or account
   lengths larger than the remaining payload at the packet boundary.
4. Do not copy dual-connection, redirect, RSA, or continued-session code from TrinityCore.

Gate:

- Fixed fields and bit-packed fields match canonical byte vectors exactly.
- An auth-session fixture restores all 20 digest bytes in canonical order and consumes the complete
  payload.
- Error, success, queued-success, and queued-error responses each have an explicit vector.

## Unit 2: integrate without moving authentication policy

Work:

1. Make `WorldSocket` construct the typed challenge and parse the typed auth session before the
   existing asynchronous account query.
2. Pass the typed auth-session object through the existing callback and digest check.
3. Make `WorldSession::SendAuthResponse` and `WorldSocket::SendAuthResponseError` use the same typed
   response serializer.
4. Delete the inline auth-session struct, commented WotLK parser, duplicate raw serializers, and
   temporary field-by-field auth logging made obsolete by the typed packet.

Gate:

- Account queries, bans, IP locks, realm checks, scripts, Warden, queue admission, and crypto timing
  are unchanged.
- Malformed auth packets still close at the socket boundary and never reach the database query.
- Every response path uses one serializer, including failures before a `WorldSession` exists.

## Unit 3: retain deterministic proof

Work:

1. Add one focused packet test file with fixed dummy account data, seeds, digest, addon bytes, and
   expected payload bytes.
2. Extend the conversion checker with a narrow auth-codec anchor and mutation self-checks.
3. Compile `game`, `worldserver`, and `unit_tests`; run the focused tests twice and the full audit
   twice.
4. Record the remaining authentication-flow gaps before selecting Plan 6.

Gate:

- The test needs no database, socket, client, Wine/Bottle state, or network port.
- The opcode value manifest remains byte-identical.
- Both full audit outputs are identical and contain no errors.

## Safety and execution boundary

Create `plan/05-world-authentication-packet-contract` from updated `master` only after Plan 4 is
merged. No database process is needed. Do not read or write existing database data, start a Docker
database, launch the personal client installation, or touch its Bottle.

## Completion predicate

Plan 5 is complete when the three world-auth payloads have one typed implementation, malformed input
fails before account lookup, all existing account and crypto policy stays in place, exact local wire
vectors pass twice, affected targets compile and link, the Plan 4 opcode manifest is unchanged, and
the committed conversion audit is green twice.

## Execution results

Plan 5 started from updated `master` commit `d5216f198` on
`plan/05-world-authentication-packet-contract`.

- `SMSG_AUTH_CHALLENGE`, `CMSG_AUTH_SESSION`, and `SMSG_AUTH_RESPONSE` now have one typed
  implementation under `WorldPackets::Auth`.
- The auth-session parser keeps the canonical digest permutation, uses all 12 account-length bits,
  checks declared lengths before allocation, and rejects trailing bytes before account lookup.
- Pre-session errors, normal success, queue admission, queue release, and queue refresh all use the
  same response serializer. Account checks, prepared statements, digest policy, `AuthCrypt`, Warden,
  hooks, and queue ownership remain in place.
- Five focused packet tests passed twice, covering exact challenge, auth-session, error, success,
  queued-success, queued-error, queue-refresh, malformed, and 300-byte account vectors.
- Every affected production translation unit compiled with the existing MySQL 8.0.46 toolchain.
  A temporary rebuilt game archive linked both `worldserver` and the updated `unit_tests` target.
  The database-free focused executable ran successfully; the manually relinked broad test binary
  could not start against the host MariaDB client and reported that environment mismatch before
  test discovery.
- The checker self-check, C++ codestyle, `git diff --check`, and the unchanged Plan 4 opcode manifest
  passed. The committed conversion audit passed twice against current upstream with zero errors and
  457 retained inventory warnings.

No database process, Docker volume, port, client file, or Bottle was read or modified. The remaining
authserver build admission and session-key handoff are isolated in Plan 6.
