# Plan 35 negative batch: every refusal is free

Issue #137. One live session on the natively created level-1 Human Mage: invalid target, interrupted
cast, GCD press, out-of-range target, dead target, insufficient mana. Every accepted Fireball pays
exactly 9 mana; every refusal leaves the world and the pool untouched.

## Pins

- Client build **15595**; reference TrinityCore `c699217775d90794158422387b07a917e161b582`.
- Branch `plan/35c-mage-negative`; generation 4; binaries `8db89cf0df8e+`; harness commit in this PR.
- Character: level-1 **Human Mage** (race 1 / class 8) from the native creation flow. Fireball **133**
  on button 0 (key 1); Arcane Intellect **1459** on button 1 (key 2).
- Fireball: 40 yd, 1.5 s cast, **9 mana**. Arcane Intellect: custom starting spell, instant; its
  26-mana cost starts the shared 1.5 s cooldown. The stock Frost Armor 168 is absent from the
  server's Cata `Spell.dbc`, so the mode seeds the spell and action and enables
  `PlayerStart.CustomSpells`.
- 4.3.4 queues a press made at the cooldown's end; the predicate accepts that queued completion only
  after the window and only at its full 9-mana cost.
- `Rate.Mana = 0.0001` for this mode: without it a regeneration tick between a Fireball's cost and
  its completion can mask the exact 9 (observed in generation 3).
- Fixtures: Advanced Training Dummy **24792** at `(-8949.95, -128.5, 83.5312)`, 1,000,000 health;
  Adder **3300** at the same front point, 1 health; Initiate's Training Dummy **32541** at
  `(-8952.08, -83.9177, 88.3006)`, about 48.6 yd. The Adder's west-side spawn was refused with
  `SPELL_FAILED_UNIT_NOT_INFRONT` (136), so it shares the dummy's proven front point.

## Commands

```bash
... prepare --mode mage-negative ...   # generation 4
... run --auto-login --stability-seconds 8 --timeout 300
... verify --confirm-expected-screen
```

## Expected vs actual

| check | expected | actual |
| --- | --- | --- |
| invalid target | no request, go or cost | self-target selection **744**; no Fireball request before the dummy selection **831** |
| interrupted | cancel + 42, no go/cost | request **867**, cancel **875**, failure **876 result 42**; no completion; recovery go **913** |
| GCD | no immediate go/cost | Arcane Intellect go **930**; window **930..941**; Fireball request **941** is queued; completion **953** costs 9 (power **952**: 130 -> 121); recovery go **972** |
| out of range | no request, go or cost | far dummy selection **1001**; no Fireball request before the Adder selection **1089** |
| dead target | no request, go or cost | Adder kill go **1121**, damage **1126** (overkill 20); target cleared at **1140**; no cast request |
| insufficient mana | no go/cost at mana < 9 | request **1589**, failure **1590 result 87** at mana **4**; last drain go **1569** started at **13** |
| every Fireball | exact 9 | 165 -> 156 -> 130 -> 121 -> 112 -> 103 -> 94 -> 85 -> 76 -> 67 -> 58 -> 49 -> 40 -> 31 -> 22 -> 13 -> 4 |

The only non-Fireball drop is Arcane Intellect's 26 (165 -> 130 after the interrupt recovery); every
Fireball completion in the capture drops the pool by 9. The last successful drain cast starts at 13,
inside the 9..17 band, and the final press starts at 4.

## Mana trace

Initial pool 165. Interrupt recovery 156. Arcane Intellect 130. The queued GCD cast 121. GCD recovery
112. Adder kill 103. Eleven drain casts: 94, 85, 76, 67, 58, 49, 40, 31, 22, 13, 4. The insufficient
attempt starts at 4.

## Screenshots

`creation-screen.png`, `creation-class.png`, `mage-negative-invalid.png`,
`mage-negative-interrupted.png`, `mage-negative-out-of-range.png`, `mage-negative-dead.png`,
`mage-negative-insufficient.png` in the run directory.

## Notes

- The GCD press is not a server failure in 4.3.4: the client queued it and the server completed it at
  the cooldown's end. The mode proves the press produces no immediate go or cost, and attributes the
  queued completion's full 9-mana cost; a true GCD refusal is not observable with this client.
- After the kill the client cleared the Adder's target (selection 0 at 1140), so the dead-target
  press sent no request. That is still a refusal with no effect and no cost; the mode does not
  distinguish an empty target from `SPELL_FAILED_TARGETS_DEAD` (111).
- `mage_negative.spell_count` is 0 because the starting spells are re-learned at login and
  `character_spell` is empty by design; the persisted witness is `character_action` (button 0 /
  Fireball 133; `action_count` 1).
