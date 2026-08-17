# Plan 8: build 15595 character screen

Status: draft. Start only after Plan 7 is merged with real-client world `AUTH_OK` evidence.

## Outcome

Make the real Cataclysm 4.3.4 build 15595 client accept `CMSG_CHAR_ENUM` and the corresponding
`SMSG_CHAR_ENUM` for a synthetic account with an empty character list, then remain visibly stable at
the character screen.

Plan 8 does not create, delete, customize, rename, or select a character and does not enter the world.
Those behaviors require later plans.

## Entry boundary

Plan 7 must first prove the ordered client milestone `COP_GET_CHARACTERS Initiating` after world
`AUTH_OK`. If the real Plan 7 run stops earlier, revise this draft around the observed boundary rather
than treating character enumeration as reached.

## Unit 1: pin the enumeration contract

Work:

- Compare the current request handler and empty response with the pinned TrinityCore and cata.js
  references.
- Keep the request and response direction types explicit. Reuse existing packet and session layers.
- Add exact fixtures for the build 15595 empty-list payload and malformed input behavior.

Gate:

- The focused fixture proves every bit, count, and field emitted for an empty account.
- The checker catches a mutation to the opcode direction or payload shape.

## Unit 2: prove the real character screen

Work:

- Reuse the Plan 7 disposable database, server, cached read-only client base, fresh linked client
  view, fresh Wine prefix, manifest, and protected-input checks.
- Use only the synthetic empty account and stop when the client displays the empty character screen.
- Correlate the client semantic log with decoded server receive/send logs; use a PID-bound screenshot
  only as corroboration.

Gate:

- The client sends `CMSG_CHAR_ENUM`, accepts `SMSG_CHAR_ENUM`, and remains connected at the empty
  character screen.
- No character mutation or world-entry opcode is sent by the harness.
- Source client, personal Bottle, external data, existing databases, and unrelated processes remain
  unchanged.

## Unit 3: retain and repeat

Work:

- Commit only a sanitized allowlist of symbolic milestones and payload-shape results.
- Require two distinct fresh generations with equal normalized results, then reset each generation.
- Mutation-check both the synthetic fixture and the real-client acceptance predicate.

Gate:

- Replay rejects a changed empty-list payload or missing request/response milestone.
- Both runs reset without a leftover process, listener, container, volume, or protected-input change.

## Completion predicate

Plan 8 is complete only when two fresh isolated build 15595 client runs display the empty character
screen, deterministic fixtures protect the accepted enumeration contract, and bounded reset proves
all personal and pre-existing state unchanged.
