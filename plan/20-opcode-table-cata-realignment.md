# Plan 20: full opcode-table realignment to Cata 4.3.4

Canonical issue: [#41](https://github.com/trolloks/azerothcore-cata/issues/41)

Status: blocks #32 (Plan 14). The last measured blocker preventing the real build 15595
client from completing world entry.

## The problem, measured

Diffing this fork's `src/server/game/Server/Protocol/Opcodes.h` against the pinned
Cataclysm TrinityCore reference (`c699217775d90794158422387b07a917e161b582`) by opcode
name:

- 884 opcode names exist in both tables
- **714 of them still carry WotLK numeric values**
- **367 of those are `SMSG_` server-to-client responses**

This is directly observable on the wire. In the latest real-client run the client's
requests all arrive on correct Cata values, and the server answers on WotLK values the
client cannot recognise:

| Response we send | Ours | TrinityCore (Cata) |
| --- | --- | --- |
| `SMSG_QUERY_TIME_RESPONSE` | `0x01CF` | (per reference) |
| `SMSG_PLAYED_TIME` | `0x01CD` | (per reference) |
| `SMSG_RAID_INSTANCE_INFO` | `0x02CC` | (per reference) |
| `SMSG_GAMEOBJECT_QUERY_RESPONSE` | `0x0915` | (per reference) |

The client visibly retries (`CMSG_QUERY_TIME` is sent twice in a single run), which is the
signature of a request whose answer never arrives in a recognisable form. `SMSG_NAME_QUERY_RESPONSE`
was one instance of exactly this class and fixing it alone (WotLK `0x051` → Cata `0x6E04`)
measurably advanced the client, so the remainder are expected to behave the same way.

## Approach

Adopt the pinned TrinityCore value for every opcode name present in both tables. This is
mechanical, not a judgement call: TrinityCore's Cata table is the authority for build 15595
and is already this repo's designated priority-2 reference.

Two things to handle deliberately:

1. **Collisions.** Adopting TrinityCore's values raises same-direction opcode collisions
   from 5 to 15. Every new collision is between dead WotLK leftovers that TrinityCore does
   not define at all (`CMSG_CHEAT_SETMONEY`, `CMSG_LEVEL_CHEAT`, `CMSG_BOT_DETECTED2`,
   commentator/GM opcodes) and a legitimately-renumbered opcode. `OpcodeTable::ValidateAndSet*`
   logs an error and keeps the first registration rather than crashing, so these are
   non-fatal, but they should be resolved rather than tolerated -- prefer removing the dead
   WotLK-only opcodes over leaving duplicate registrations.
2. **The `/*0xNNN*/` comments** in `Opcodes.cpp` become stale. They are cosmetic (the
   registration macros key off the enum name) but should be regenerated for readability.

Out of scope: opcodes this fork defines that TrinityCore does not, and opcodes TrinityCore
defines that this fork lacks. Both are separate follow-ups; this plan only corrects values
for names that already exist in both.

## Acceptance

- Every shared opcode name matches the pinned TrinityCore value.
- No new same-direction collisions remain; `worldserver` starts with no
  "Tried to override ... handler" errors in the log.
- `worldserver`/`authserver` build clean.
- A real build 15595 client run reaches the in-world-control marker
  (`CMSG_TIME_SYNC_RESP` observed, satisfying Plan 13's criterion in #27) and dismisses the
  loading screen, across two consecutive clean runs.
