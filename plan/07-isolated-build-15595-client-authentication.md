# Plan 7: isolated build 15595 client authentication

Status: complete.

Implementation status: two fresh isolated client runs passed, generation 5 passed the Plan 6 semantic
replay, both generations reset cleanly, and the cached client base was purged.

## Outcome

Drive the real Cataclysm 4.3.4 build 15595 client through the proven Plan 6 authserver and world
authentication path in fully disposable state. Capture the first client-visible post-auth result and
record the next observed protocol boundary without inferring client rejection.

This plan proves real-client acceptance only through authentication and arrival at the character
screen boundary. It does not convert character enumeration, create or delete characters, enter the
world, extract client data, or modify the personal client installation or Bottle.

## Isolation boundary

1. Create one manifest-owned, read-only client base copy. Give each generation a fresh client view
   with private writable `WTF`, `Cache`, `Logs`, `Errors`, and `Screenshots` directories and
   symlinks to that base. Never launch the source client tree or the personal `Battle.NET` Bottle.
2. Reuse the Plan 6 run-owned MySQL container, volume, manifest ownership checks, synthetic account,
   build 15595 realm, and unused loopback-port allocation.
3. Record source-client and personal-Bottle metadata before and after the run. Any change is a hard
   failure.
4. Record every owned process id and verify its executable, start time, and configuration before
   signalling it. Reset removes only manifest-owned resources.
5. Keep screenshots, logs, raw captures, credentials, and process manifests outside Git. Commit only
   a sanitized logical transcript made from the synthetic account.

The executable interface is `apps/cata/run_real_client_authentication.py` with `self-check`,
`prepare`, `run`, `verify`, `replay`, `compare-last-two`, and `reset` commands. Its run root must be
outside the worktree. Every generation owns a linked client view, fresh Wine prefix, three
disposable databases, server processes, Docker resources, configs, and evidence directory through
one atomic mode-0600 manifest. The first generation caches the immutable base and released database
state as a mode-0600 logical dump. Later generations restore that dump into a new labeled MySQL
volume, then reapply the pending migration and generation-specific account and realm seed. The cache
key covers the immutable MySQL image ID and the exact immediate SQL files read from all six base and
released directories, so unrelated source changes do not repeat hundreds of imports. Reset may act
only after the recorded resource identity still matches; the final reset may explicitly purge both
the cached client base and database dump.

The current worldserver still requires WotLK-shaped DBC records during startup. Plan 7 therefore
accepts a separate, read-only `--server-dbc-root` containing exactly the bootstrap data understood
by the current loader, while maps remain Cataclysm format 10. This is an authentication-only test
shim, not evidence that Cataclysm DBC/DB2 loading is converted; that conversion remains separate
roadmap work.

Authentication uses the fixed loopback auth port 3724 because the client configuration does not
prove a custom login port. `prepare` therefore fails before mutation when that port is occupied. It
also fails for insufficient run-root space, an unavailable local MySQL 8.4 image, unusable DISPLAY,
an unexpected client hash, incompatible server data, or binaries that do not match the current
source revision. These outcomes are `INCONCLUSIVE`, not acceptance.

## Unit 1: prepare a disposable client runtime

Work:

- Make one full run-owned base copy, mark it read-only, and prove per-generation writable state does
  not change either that base or the source client.
- Build or restore the manifest-owned database cache while retaining a fresh container and volume
  for every generation. Never cache pending migrations or synthetic runtime rows.
- Generate a run-owned `realmlist.wtf` pointing only to the isolated authserver.
- Start authserver and the minimum worldserver configuration against disposable databases. Do not
  connect either server to an operator database.

Gate:

- The client executable hash matches build 15595 before launch.
- Source client and personal Bottle metadata are unchanged after a no-login launch and reset.
- Every client, Wine, server, Docker, and database resource is named in the outside-worktree
  manifest.

## Unit 2: run the real authentication smoke

Work:

- Launch the isolated client and enter only the synthetic Plan 6 credentials.
- Observe auth challenge, proof, realm selection, world initializer, `CMSG_AUTH_SESSION`, and the
  logical `SMSG_AUTH_RESPONSE` before encryption.
- Stop at the character-screen boundary or the first earlier disconnect/error. Do not patch the
  next failure inside this plan unless it is an authentication regression.

Gate:

- The real client accepts build 15595 authserver SRP, lists the isolated build 15595 realm, connects
  to its isolated world port, and accepts world `AUTH_OK`.
- The server transcript contains no credentials, session key, address, personal identifier, or
  nondeterministic crypto bytes.
- A failed milestone is recorded as `INCONCLUSIVE` or `FAIL`, never inferred from server logs alone.

Credentials are entered interactively into the copied client. They must not appear in argv,
environment variables, manifests, screenshots, or committed fixtures. Client `connection.log` is
the acceptance judge; decoded `network.opcode` logs establish packet direction and order. Screenshots
corroborate the PID-bound client state but cannot replace those semantic logs.

## Unit 3: retain a replayable acceptance trace

Work:

- Synthesize a deterministic allowlisted milestone transcript from the dummy run. Do not commit raw
  logs, packets, endpoints, paths, identifiers, timestamps, credentials, or crypto material.
- Replay the existing Plan 6 handoff fixture without Wine and require the same opcode relationships,
  crypto activation point, and close behavior. This combined evidence is not a raw network replay.
- Repeat one fresh isolated client run, compare sanitized milestones, then perform bounded reset.

Gate:

- Replay catches mutations to the Plan 5 packet contract and Plan 6 key handoff.
- Two fresh client runs reach the same authentication milestone.
- Reset restores Docker, ports, source client, and personal Bottle to their pre-run manifests.

## Result

Fresh generations 5 and 6 both produced the same sanitized result with the pinned build 15595
executable:

- authserver `LOGIN_OK`;
- connection to the manifest-owned world endpoint;
- world `AUTH_OK` accepted by the client;
- `COP_GET_CHARACTERS` initiated and completed successfully;
- decoded `CMSG_AUTH_SESSION`, `SMSG_AUTH_RESPONSE`, `CMSG_CHAR_ENUM`, and `SMSG_CHAR_ENUM` in order.

Generation 5 also replayed the Plan 6 SRP/session-key/world-auth fixture. Both generations used fresh
MySQL volumes, Wine prefixes, and writable client directories. Reset removed their containers,
volumes, processes, linked client views, and prefixes; the final reset purged the single read-only
17 GB client base. Source client, personal Bottle, and external data metadata remained unchanged.

The real client accepted the empty character list. That observation establishes Plan 8's entry
boundary but does not complete Plan 8: the character packet contract and stable visible screen still
need their own deterministic fixtures and acceptance proof.

## Completion predicate

Plan 7 is complete when the actual build 15595 client accepts both authentication servers through
world `AUTH_OK`, the next client-visible boundary is recorded honestly, a sanitized replay fixture
protects the accepted path, and all personal or pre-existing state is proven unchanged.

The committed fixture must record two distinct fresh generations reaching, in order, `LOGIN_OK`, the
owned world endpoint, `COP_AUTHENTICATE ... AUTH_OK ... TRUE`, `COP_GET_CHARACTERS Initiating`, and
decoded `CMSG_AUTH_SESSION`/`SMSG_AUTH_RESPONSE`. The checker deliberately rejects a converted anchor
when that fixture, client proof, Plan 6 replay, or reset proof is missing.
