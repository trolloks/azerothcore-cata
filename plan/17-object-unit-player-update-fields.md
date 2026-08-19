# Plan 17: Object/Unit/Player update-fields Cata conversion

Canonical issue: [#36](https://github.com/trolloks/azerothcore-cata/issues/36)

Status: complete, merged in #39 (commit 736a374d1). Opens the "Object model" plan family. Converts
`src/server/game/Entities/Object/Updates/UpdateFields.h`'s `EObjectFields`/`EUnitFields`/
`EPlayerFields`-equivalent boundaries (`OBJECT_END`, `UNIT_END`, `PLAYER_END`) and every field offset
inside them from their current WotLK layout to the real build 15595 layout. This is the minimal
Object-model slice needed to unblock Plan 14: the real client's own compiled expectation of where
each field (health, displayId, glyphs, mastery, PLAYER_BYTES_*, ...) lives in the values-update array
almost certainly does not match what our fork currently sends, which is the leading remaining
candidate for the client hanging at the 90% loading screen after world entry (see Plan 14's updated
status for the full evidence trail, including the two related fixes already landed -- the opcode
table and `OBJECT_UPDATE_TYPE` -- that were necessary but not sufficient on their own).

## Reference sources, in priority order

1. Pinned TrinityCore commit `c699217775d90794158422387b07a917e161b582`, checked out locally at
   `/mnt/f79365ff-6a68-45da-925e-b9ddc6d5da6c/Fun/TrinityCore/TrinityCore`
   (`src/server/game/Entities/Object/Updates/UpdateFields.h`). This is the canonical field layout --
   real TrinityCore Cata source, not a reimplementation.
2. `cata-js` commit `ab964a0e8dfe50a44fa92716ed05438f4a14dfd3`
   (`src/server/game/entities/object/updates/update_fields.ts`,
   `src/server/game/entities/object/updates/update_field_flags.ts`) as a second, independently-derived
   source that already agrees with (1) on every boundary checked so far (`OBJECT_END = 8`,
   `UNIT_END = 146`, `PLAYER_END = 1384`) -- useful for catching transcription mistakes in either
   source.
3. `docs/bugs-fixed.md` and `docs/smsg-update-object.md` in `cata-js` for the specific fields already
   proven necessary at login time and the exact wire layout of the values block
   (`u8 blockCount`, `u32[blockCount] updateMask`, then only the non-zero field values in ascending
   index order).

## Scope

In scope:

- `OBJECT_FIELD_*` (`OBJECT_END`)
- `UNIT_FIELD_*` (`UNIT_END`)
- `PLAYER_*` fields through `PLAYER_END` -- note TrinityCore's reference also defines
  `PLAYER_END_NOT_SELF` (a smaller boundary used when building the values block for players *other*
  than the target); confirm whether this fork's `BuildValuesUpdate` needs an equivalent split or
  whether the existing self/other handling already covers it structurally and only the boundary
  values are wrong.
- Every existing usage of the changed field names across the codebase (`GetUInt32Value`,
  `SetUInt32Value`, `SetFlag`, `HasFlag`, etc. call sites) -- a rename/renumber this size will not
  compile cleanly without touching every call site; that is expected and is not scope creep.
- `OBJECT_UPDATE_FLAGS` (`UPDATEFLAG_*`) if the pinned reference shows any bit has moved -- not yet
  audited, check before assuming it is unaffected.

Out of scope (Plan 18):

- `ITEM_FIELD_*` (`ITEM_END`), `CONTAINER_FIELD_*` (`CONTAINER_END`), `GAMEOBJECT_FIELD_*`
  (`GAMEOBJECT_END`), `DYNAMICOBJECT_FIELD_*` (`DYNAMICOBJECT_END`), `CORPSE_FIELD_*`
  (`CORPSE_END`) -- not required for the player's own login/create block, deferred so this plan stays
  reviewable.

## Acceptance

- Every boundary and field offset in scope matches the pinned TrinityCore reference exactly (spot
  checked against `cata-js` as a second source).
- `worldserver` builds clean with the renumbered fields.
- A real build 15595 client run reaches and holds the in-world-control marker
  (`CMSG_TIME_SYNC_RESP` observed, matching Plan 13's own acceptance criterion in #27) across at
  least two consecutive clean runs -- single clean runs have been misleading before in this
  investigation (see Plan 14's history).
