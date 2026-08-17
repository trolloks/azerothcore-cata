# Plan 7: isolated build 15595 client authentication

Status: draft. Start only after Plan 6 is merged.

## Outcome

Drive the real Cataclysm 4.3.4 build 15595 client through the proven Plan 6 authserver and world
authentication path in fully disposable state. Capture the first client-visible post-auth result and
the first logical server packet that the client rejects.

This plan proves real-client acceptance only through authentication and arrival at the character
screen boundary. It does not convert character enumeration, create or delete characters, enter the
world, extract client data, or modify the personal client installation or Bottle.

## Isolation boundary

1. Create a uniquely named, run-owned Bottle and a run-owned client copy or proven copy-on-write
   view. Never launch the source client tree or the personal `Battle.NET` Bottle.
2. Reuse the Plan 6 run-owned MySQL container, volume, manifest ownership checks, synthetic account,
   build 15595 realm, and unused loopback-port allocation.
3. Record source-client and personal-Bottle metadata before and after the run. Any change is a hard
   failure.
4. Record every owned process id and verify its executable, start time, and configuration before
   signalling it. Reset removes only manifest-owned resources.
5. Keep screenshots, logs, raw captures, credentials, and process manifests outside Git. Commit only
   a sanitized logical transcript made from the synthetic account.

## Unit 1: prepare a disposable client runtime

Work:

- Prove the chosen copy-on-write mechanism does not write the source client; otherwise make a full
  run-owned copy.
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

## Unit 3: retain a replayable acceptance trace

Work:

- Synthesize a deterministic logical transcript from the dummy run at the existing WorldPacket
  capture boundaries.
- Replay the auth portion without Wine and require the same opcodes, payload relationships, crypto
  activation point, and close behavior.
- Repeat one fresh isolated client run, compare sanitized milestones, then perform bounded reset.

Gate:

- Replay catches mutations to the Plan 5 packet contract and Plan 6 key handoff.
- Two fresh client runs reach the same authentication milestone.
- Reset restores Docker, ports, source client, and personal Bottle to their pre-run manifests.

## Completion predicate

Plan 7 is complete when the actual build 15595 client accepts both authentication servers through
world `AUTH_OK`, the next client-visible boundary is recorded honestly, a sanitized replay fixture
protects the accepted path, and all personal or pre-existing state is proven unchanged.
