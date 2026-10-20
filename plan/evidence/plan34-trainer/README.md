# Plan 34: native trainer learning and spellbook persistence

Issue #136. Trainer purchase through the unchanged build-15595 client. Full logs, screenshots and the packet
capture stay in the run directory (`/mnt/f79365ff-6a68-45da-925e-b9ddc6d5da6c/cata-runs/plan34-trainer/`);
this directory keeps the sanitized verdict (`acceptance.json`).

## Pins

- Client build **15595**; reference TrinityCore `c699217775d90794158422387b07a917e161b582`.
- Branch `plan/34-native-trainer-learning-and-spellbook-persistence`; generation 3 prepared with binaries
  reporting `b513b631d`, which carry the reviewed C++ (including the bounded required-ability copy); the
  harness is `8d04e1351`, whose verifier also requires the transaction order.
- Fixture: the shared **Human Warrior**, level 1, seeded with 50000 copper beside **Llane Beshere**
  (creature 911, spawn guid 79964, trainer table id **2**, Class/Warrior, `npcflag 51`).
- Offer: **Battle Shout, spell 6673** — `trainer_spell` MoneyCost 10 copper, ReqLevel 1, no skill or ability
  prerequisites; the run's list showed it at **9 copper** after the reputation discount.

## Commands

```bash
... prepare --mode trainer-learn ...            # same roots as plan 33, manifest plan34-trainer
... run --auto-login --stability-seconds 8 --timeout 180
... verify --confirm-expected-screen
```

## Expected vs actual

| check | expected | actual |
| --- | --- | --- |
| trainer frame | `SMSG_TRAINER_LIST` with trainer id **2** | id 2, five rows |
| state mapping | Battle Shout **available**, the level-locked rows **unavailable** | 6673 state **1**, the other four state **2** (4.x remap) |
| identity | the list, buy and success name the same runtime NPC (creature entry 911) | same guid for all three, entry 911 |
| order | list → buy → success/learned → cast → spell-go, positions retained in the evidence | list 424, buy 470, learned 473, success 474, cast 526, go 529 |
| purchase | `CMSG_TRAINER_BUY_SPELL` naming trainer 2 and spell 6673 | exactly one, trainer 2 / 6673 |
| result | `SMSG_TRAINER_BUY_SUCCEEDED` for the same NPC and `SMSG_LEARNED_SPELL` naming 6673 once, no failure | one each, zero failures |
| usability | `/cast Battle Shout` → the player's own `CMSG_CAST_SPELL` counter matched by a later `SMSG_SPELL_GO` (same spell and caster), no `SMSG_CAST_FAILED` | counter 1 / spell 6673 / caster = the fixture player, GO after the request |
| persistence | saved row: spell known, money − the pinned 9-copper price, bar slot | `character_spell` 1, `character_action` 1, money **49991** |

Screenshots in the run directory: `trainer-before-target.png`, `trainer-after-target.png`,
`trainer-gossip.png`, `trainer-open.png`, `trainer-selected.png`, `trainer-after-buy.png`,
`trainer-spellbook.png`, `trainer-after-cast.png`, `trainer-after-logout.png`. The spellbook capture shows
the General tab and the "You have learned a new ability: [Battle Shout]." line, not Battle Shout's entry
on the Warrior tab; the learned state itself is proved by the packet capture and the saved row.

## Cata differences the live runs pinned

- **State byte remap**: 4.x reads `Known = 0, Available = 1, Unavailable = 2`; AC sent the WotLK order
  (`Available = 0, …`), so the client treated the learnable spell as known and hid it.
- **Purchase flow**: a trainer row click only *selects*; the frame's **Train** button sends the purchase
  (`BuyTrainerService(selectedService)` in the 4.3.4 UI).
- The learned spell is **auto-placed** in the first action slot by the client (`character_action` 1), and
  `/cast <name>` works without dragging it.

## Not covered here

- In-run relog: persistence is verified from the saved row after logout, not from a second login in the
  same run.
- Negative batches: mismatched/stale trainer id, spell not offered, insufficient money, unmet class/level/
  skill, and a repeated purchase of an already-known spell — the client hides known spells, so the native
  reach of the repeat case still has to be established. The malformed-packet boundary is covered by
  `NPCPacketsTest.TrainerBuySpellRejectsMissingTrainerId`; the trainer-id mismatch check is server-side and
  currently source-verified only.
- Other trainers/professions and the broader spellbook/cooldown parity stay out of scope.

## Ownership and cleanup

The harness owned the MySQL container/volume, client copy, wine prefix and both servers; `run` stopped the
client and servers in its `finally` block and the generation is `accepted`. No credentials, dumps or raw
captures are committed.
