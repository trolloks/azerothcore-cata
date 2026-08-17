# Plan 6: build 15595 authentication handoff

Status: complete. Verified on 2026-08-17.

## Outcome

Make AzerothCore's existing authserver accept Cataclysm 4.3.4 build 15595, advertise a matching
realm, and produce a session key that the Plan 5 world-authentication path accepts. Prove the whole
authserver-to-worldserver handoff with a synthetic account in a disposable MySQL environment.

This plan does not launch the game client, alter an existing realm row, convert character-list
packets, add Cataclysm connection redirects, or claim that the character screen works.

## Why this is next

Plan 5 gives world authentication one checked packet contract, but the normal login path begins at
authserver. The current immutable base data contains build 12340 and the default realm row also
advertises 12340. A build 15595 client therefore cannot complete the supported authserver and realm
handoff even if the world socket codecs are correct.

The code already keeps accepted builds in `build_info`, realm builds in `realmlist`, and session keys
in the existing login database. Reusing that path is smaller and stays closer to upstream than
inventing a Cataclysm-only account service.

## Pinned behavior

- Use TrinityCore commit `c699217775d90794158422387b07a917e161b582` for build 15595 identity,
  auth protocol fields, SRP6 proof order, and realm-list behavior.
- Use the existing AzerothCore `AuthSession`, `RealmList`, account schema, prepared statements, and
  session-key handoff unless a fixture proves a Cataclysm wire difference.
- Add build 15595 as version 4.3.4 with no invented checksum or auth seeds.
- Add SQL only under `data/sql/updates/pending_db_auth/`. Do not edit base or archived SQL.
- Never update an existing `realmlist` row automatically. The disposable test database creates its
  own build 15595 realm explicitly; operators remain responsible for intentionally changing their
  own realm configuration.

## Unit 1: admit build 15595 without rewriting authserver

Work:

1. Add the minimal pending auth-database update for build 15595 and a 15595 default for newly
   created realm rows, without modifying existing rows.
2. Compare the current logon challenge, proof, and realm-list serializers with the pinned reference.
   Change only fields whose bytes differ for build 15595.
3. Keep account bans, IP locks, reconnect handling, security checks, logging, and SRP6 ownership in
   their current AzerothCore layers.

Gate:

- `RealmList::GetBuildInfo(15595)` resolves to 4.3.4 from an upgraded disposable database.
- A build 15595 logon challenge and proof fixture reaches authenticated realm-list state.
- The advertised test realm carries build 15595 and the configured isolated world port.
- Existing realm rows are byte-for-byte unchanged by the pending update.

## Unit 2: prove the authserver-to-worldserver key handoff

Work:

1. Create a fixed dummy account through existing account APIs or deterministic test seed data.
2. Complete the logical authserver exchange, persist its generated session key, then synthesize the
   matching Plan 5 `CMSG_AUTH_SESSION` digest from fixed challenges.
3. Feed that packet through the real world authentication callback with the disposable login
   database and capture the logical `SMSG_AUTH_RESPONSE` before header encryption.
4. Assert that an altered digest, wrong realm id, unsupported build, and unknown account fail on
   their existing policy paths.

Gate:

- The positive path returns `AUTH_OK` and creates one `WorldSession` for the dummy account.
- The digest uses the exact session key written by authserver; no test-only authentication bypass is
  added.
- Negative cases never create a session and retain their current response codes and close behavior.
- The proof contains only synthetic credentials, fixed challenges, and disposable database state.

## Unit 3: retain an isolated, repeatable proof

Work:

1. Reuse `mysql:8.4` in a uniquely named run-owned container and volume bound to `127.0.0.1` on a
   confirmed unused host port. Do not use the repository's default Compose names or volume.
2. Record the exact container, volume, port, process ids, schema revisions, and dummy account in a
   run manifest outside the Git worktree.
3. Provide idempotent prepare, run, inspect, and reset commands. Reset may remove only resources
   listed in that manifest.
4. Add narrow conversion anchors for build admission and the authentication handoff; run the focused
   proof and full conversion audit twice.

Gate:

- Pre-run and post-run checks show no existing container, volume, database, port, or client file was
  changed.
- A second run from an empty run-owned volume produces the same logical packet transcript.
- Cleanup removes only the recorded run-owned resources.
- The test needs no Wine/Bottle state and never launches the personal client installation.

## Safety and execution boundary

Create `plan/06-build-15595-authentication-handoff` from updated `master` after Plan 5 is merged.
Before starting Docker, resolve and record unique resource names and confirm the chosen loopback port
is unused. Never connect to any database endpoint not created by the Plan 6 harness.

Do not launch the source `Wow-64.exe` in the personal Cataclysm client tree.
Real-client acceptance belongs to the next vertical plan after the server handoff is deterministic.

## Completion predicate

Plan 6 is complete when build 15595 is admitted through a pending auth update, a disposable realm
advertises 15595, the existing authserver session key authenticates one synthetic world session,
negative policy paths remain intact, repeat runs produce the same sanitized logical transcript, and
no existing database, Docker resource, port, client tree, or Bottle is modified.

## Execution evidence

- `authserver` and `unit_tests` compiled and linked against MySQL client 8.0.46.
- The runner self-check, conversion-audit self-check, Python compilation, C++ style, SQL style, and
  whitespace checks passed.
- Two fresh `mysql:8.4` volumes completed `prepare`, `run`, `inspect`, and `reset`. Their normalized
  logical transcripts matched, and reset restored the exact pre-run Docker inventory.
- The real authserver rejected build 15596 with `000009`, admitted build 15595, emitted the
  119-byte challenge and 32-byte proof response, verified M2, advertised the owned realm, and
  persisted the same 40-byte session key consumed by the world query.
- The world fixture produced
  `unknown=0015:plain:closed,wrong-realm=0027:encrypted:closed,bad-digest=000D:encrypted:closed,`
  `success=400000000003000000000300000000000C:encrypted:open` through the production callback and
  natural session-manager `AUTH_OK` path.
- Source-client and personal-Bottle metadata stayed unchanged. The client was not launched.
