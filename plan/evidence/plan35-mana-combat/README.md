# Plan 35: first mana-class combat and resource recovery

Issue #137. A natively created level-1 Human Mage selects a pinned Saytr and casts Fireball from action
button 0 through the unchanged build-15595 client. Full logs, screenshots and the packet capture stay in
the run directory (`/mnt/f79365ff-6a68-45da-925e-b9ddc6d5da6c/cata-runs/plan35-mage/`); this directory
keeps the sanitized verdict (`acceptance.json`).

## Pins

- Client build **15595**; reference TrinityCore `c699217775d90794158422387b07a917e161b582`.
- Branch `plan/35-first-mana-class-combat-and-resource-recovery`; generation 5 prepared with binaries
  reporting `8c880bace`, harness `cfc161bd5`.
- Character: level-1 **Human Mage** (race 1 / class 8) built by the native creation flow; Fireball **133**
  auto-placed on action button 0.
- Target: **Saytr, entry 361**, faction 14, no static spawns; seeded at spawn guid 9990003,
  `(-8949.95, -128.5, 83.5312)`, four yards from the mage start. An earlier seed on the far side of the
  mage was refused with `SPELL_FAILED_UNIT_NOT_INFRONT` (136); the arc test also admits targets whose
  radius reaches the facing boundary, so the position is evidence of acceptance, not of the exact axis.
- Fixture guids stay under `0xFFFFFF`: the worldserver seeds its dynamic spawn id from
  `MAX(creature.guid) + 1` and stops with "Creature spawn id overflow" once that reaches the cap.

## Commands

```bash
... prepare --mode mage-combat ...              # same roots as plan 33, manifest plan35-mage
... run --auto-login --stability-seconds 8 --timeout 180
... verify --confirm-expected-screen
```

## Expected vs actual

| check | expected | actual |
| --- | --- | --- |
| creation | the native flow creates the mage | one `SMSG_CHAR_CREATE`, creation marker count **1** |
| identity | row read by name/account: class 8, race 1, Fireball on button 0 | guid 1, class 8, race 1, `character_action` count **1** |
| selection | `/targetexact Saytr` resolves the seeded creature | `CMSG_SET_SELECTION` after the marker; cast target high 24 bits = **361** |
| request | `CMSG_CAST_SPELL` naming spell 133 and the Saytr | index **804**, spell 133, target = the Saytr guid |
| correlation | the request names the victim and the damage log carries that same guid | request and damage target `0xf13000016900042b` (entry 361) |
| resource | mana falls at cast completion: last value before the request vs the first after it | `SMSG_POWER_UPDATE` index **814**: **165 → 156** |
| cast | `SMSG_SPELL_GO` for the mage and spell 133 | index **815**, caster = the mage, spell 133 |
| damage | `SMSG_SPELLNONMELEEDAMAGELOG` from the mage to entry 361, fire school, damage >= 1 | index **820**, attacker = the mage, spell 133, **18** damage, school **4** |
| order | request → power → go → damage | 804 → 814 → 815 → 820 |
| persistence | the action bar row is saved for the recreated character | `character_action` button 0 / action 133 / type 0; a retry retires the previous row (rename + `deleteDate`) so the replacement cannot inherit its guid or its actions |

Screenshots in the run directory: `creation-screen.png`, `creation-class.png`, `mage-target.png`,
`mage-after-cast.png`.

Not covered yet (tracked on #137): interrupted cast, insufficient-mana refusal, out-of-combat regeneration,
invalid/dead/out-of-range targets, and logout/relog persistence of power and spell state.
