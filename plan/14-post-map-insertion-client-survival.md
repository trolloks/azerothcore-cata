# Plan 14: build 15595 post-map-insertion client survival

Canonical issue: [#32](https://github.com/trolloks/azerothcore-cata/issues/32)

Status: blocking #27, root cause reclassified. The byte-identical `0x6ffffff53ea2`/2176-byte "stack
overflow" `WINEDEBUG=+seh` warning that this plan spent most of its investigation chasing turned out
to be a false signal: `stop_wine()`'s own `wineserver -k` teardown forces every wine thread through
the same SEH dispatch path this trace captures, so the warning shows up on any run where the client
process was still alive at teardown -- regardless of game state -- while runs where the client had
already exited cleanly show ordinary trace output with no warning at all. It is not gameplay-caused
and should not be treated as a failure signal (see the updated comment in `wine_environment()`,
`apps/cata/run_real_client_authentication.py`).

The real blocker is the client going unresponsive after world entry (no further C->S traffic,
including no `CMSG_TIME_SYNC_RESP`, ever observed) -- i.e. the client's own "stuck at 90% loading
screen" state, the same failure family `cata-js` documented and fixed
(`docs/bugs-fixed.md` bug #1 in that project). Two real, evidence-backed fixes have landed on this
branch so far:

- 39 `CMSG_`/`SMSG_` opcodes in `Opcodes.h` were still on stale WotLK numeric values instead of the
  real build 15595 values (diffed against `cata-js`'s proven opcode table). Confirmed live:
  `CMSG_SET_ACTIVE_MOVER` was unrecognized before the fix and dispatched correctly after; the client
  session got measurably further (real streamed NPC movement traffic) but still did not clear 90%.
- `OBJECT_UPDATE_TYPE` (`UpdateData.h`) still carried WotLK's `UPDATETYPE_MOVEMENT = 1` slot, which
  Cata's real protocol dropped, shifting `UPDATETYPE_CREATE_OBJECT2` to `3` instead of the correct
  `2` -- exactly `cata-js`'s bug #1, their documented root cause of the identical 90% hang. Fixed and
  the dead `Object::BuildMovementUpdateBlock` (zero callers) removed. Confirmed still insufficient
  alone: the client still hangs at 90% after this fix.

Both fixes are real and correct but did not clear the hang, which points at something larger: our
`UpdateFields.h` field-count boundaries (`OBJECT_END`, `UNIT_END`, `PLAYER_END`, ...) are still
WotLK's layout, not Cata's -- confirmed against both `cata-js`'s field table and the pinned
TrinityCore reference commit (`c699217775d90794158422387b07a917e161b582`,
`/mnt/f79365ff-6a68-45da-925e-b9ddc6d5da6c/Fun/TrinityCore/TrinityCore`), which agree with each other:
`OBJECT_END` should be `8` (ours: `6`), and `PLAYER_END` should be `1384` (ours: `1326` -- missing
~58 fields Cata actually added). The values-block in every `SMSG_UPDATE_OBJECT` is sized and indexed
against these boundaries, so a real Cata client almost certainly misparses the player's own
values-update block against our fork's WotLK-shaped layout. This is Object model work, not
world-entry work, and is large enough to be its own plan family slice: see #36 (Plan 17) and #37
(Plan 18, non-blocking remainder).

Update (2026-08-19): #36 and #40 are both complete and merged in #39, which took the client from
~86% to ~99% of the loading bar and got it sending genuine in-world opcodes. World entry still does
not complete. Plan 14 stays open, now blocked on #41 (full opcode-table realignment) -- 714 of 884
shared opcode names still carry WotLK values, 367 of them SMSG responses, so the client asks and the
server answers on opcodes it cannot recognise.
