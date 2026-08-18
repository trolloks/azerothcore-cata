# Plan 15: build 15595 basic movement packet contract

Canonical issue: [#33](https://github.com/trolloks/azerothcore-cata/issues/33)

Status: blocked by #27 (Plan 13) and #32 (Plan 14). Opens the "Movement" plan family: proves the
client's first self-initiated movement-related packet round-trips correctly once idle in the world,
without AzerothCore's movement validation rejecting or kicking the session. `MovementInfo`'s wire
layout and the correct opcode must be derived from the pinned Cataclysm reference before
implementation; nothing about the current WotLK-shaped reader/writer is assumed compatible.
