# Plan 18: Item/Container/GameObject/DynamicObject/Corpse update-fields Cata conversion

Canonical issue: [#37](https://github.com/trolloks/azerothcore-cata/issues/37)

Status: blocked by #36 (Plan 17); not blocking. Continues the "Object model" plan family started by
Plan 17, converting the remaining `UpdateFields.h` boundaries -- `ITEM_END`, `CONTAINER_END`,
`GAMEOBJECT_END`, `DYNAMICOBJECT_END`, `CORPSE_END` and every field offset inside them -- from their
current WotLK layout to the real build 15595 layout, using the same reference sources as Plan 17
(pinned TrinityCore commit `c699217775d90794158422387b07a917e161b582`, corroborated by `cata-js`).

Deliberately split out of Plan 17 because none of these object types gate the player's own
login/create block or the 90% loading-screen hang Plan 14 is blocked on -- items, containers,
gameobjects, dynamic objects, and corpses only matter once the player can see and interact with them
in the world, which requires Plan 17's fix to land first. Keeping this separate lets Plan 17 stay
small and reviewable instead of a single conversion touching the entire update-fields table at once.

## Acceptance

- Every boundary and field offset in scope matches the pinned TrinityCore reference exactly.
- `worldserver` builds clean with the renumbered fields.
- A real build 15595 client correctly displays at least one of each affected object type it can
  currently be made to encounter (an item in inventory, a lootable corpse, a gameobject, a dynamic
  object such as a ground-targeted spell effect) without visible corruption or client-side rejection.
