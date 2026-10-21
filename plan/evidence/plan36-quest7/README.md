# Plan 36 — first normal kill-objective quest loop (#138)

Quest **7 “Kobold Camp Cleanup”** was completed on the real 4.3.4 build-15595 client: the 783
prerequisite was accepted and turned in, eight authentic Kobold Vermin kills were credited, the
objective completed at 8/8, and Marshal McBride handed in 7 for **25 money**, **170 XP**,
**275 Stormwind reputation** and **no item**. The server saved the rewarded row and removed the
active quest.

## Pins

- Issue #138; branch `plan/36-first-normal-kill-objective-quest-loop`; run `e231b7f842b7`,
  generation 6.
- Client build **15595**; reference TrinityCore `c699217775d90794158422387b07a917e161b582`.
- Quest **7** (level 2, XP difficulty 5, money 25, no item, required entry 6 × 8); prerequisite
  **783** (40 XP, 82 reputation, no item); giver/ender **197 Marshal McBride**; starter **823
  Deputy Willem**; target **6 Kobold Vermin**.
- Fixture: `apps/cata/fixtures/plan36-cata-quest7.json` (quest template/addon, offer reward,
  request items, NPC relations and the 33 authentic map-0 spawns).
- Character: seeded Human Warrior **Cataplan**, level 2 at turn-in, real starter kit, Attack 6603
  on action button 0, tutorials marked seen, `cinematic=1`. Verification of the final saved rows
  was by the run's owned character database.

## Expected vs actual

| check | expected | actual |
| --- | --- | --- |
| 783 accept | auto-accept at Deputy Willem | `SMSG_QUESTGIVER_QUEST_DETAILS` 783; quest accepted |
| 783 reward | 40 XP, 0 money, 82 rep | `SMSG_QUESTGIVER_QUEST_COMPLETE` 783 XP **40**, money 0; reputation 82 after 783 |
| 7 kill credit | 8 × entry 6, counters 1..8 | eight `SMSG_QUESTUPDATE_ADD_KILL` events, current **1,2,3,4,5,6,7,8**, required 8 |
| 7 objective complete | completion at 8/8 | quest log screenshot **6/8** then eight credited kills; reward frame opened |
| 7 reward | 25 money, 170 XP, 275 rep, no item | `SMSG_QUESTGIVER_QUEST_COMPLETE` 7 money **25**, XP **170**; final DB money **25**, Stormwind **357** (82+275); five starter items, no reward item |
| 7 persistence | rewarded row, no active row | DB `character_queststatus_rewarded` 7 and 783; no active `character_queststatus` row |
| teleport | `.go` reaches the camp | ACK fix; character saved at `(-8749.19,-115.32,85.92)` beside spawn 80024; kobolds visible and fightable |
| full bags | N/A for quest 7 (no item) | documented N/A; Plan 26 quest 2158 retains the live item-reward full-bag failure |

## Manual completion

The automated harness drove the native 783 accept/turn-in, the 7 abandon/reaccept and the kill
loop; it reached the 8/8 reward and the operator completed the remaining inputs and the hand-in
after the harness's focus check lost the client window. The packet capture and the run-owned
character database are the evidence for the completed sequence. This is the Plan 26 precedent
(native client interaction; the harness records, it does not replace the player path).

## Packet evidence

From generation 6's `packets.pkt` (client/server capture of the real client):

- `SMSG_QUESTGIVER_QUEST_DETAILS` 783 and 7 (accept/reaccept).
- `CMSG_QUESTLOG_REMOVE_QUEST` slot 0 (7 abandoned) and the second 7 details (reaccept).
- Eight `SMSG_QUESTUPDATE_ADD_KILL` for quest 7, entry 6, current 1..8, required 8.
- `SMSG_QUESTGIVER_OFFER_REWARD`, `CMSG_QUESTGIVER_CHOOSE_REWARD` and
  `SMSG_QUESTGIVER_QUEST_COMPLETE` for 783 and 7.
- 783 completion: XP 40, money 0. 7 completion: XP 170, money 25.
- `SMSG_LOG_XPGAIN` type 1: 40 and 170.

## Final database (generation 6)

```
characters: 16909060 Cataplan level 2 xp 569 money 25
queststatus: (none)
rewarded:   7, 783
reputation: faction 72 standing 357
items:      58231, 39, 40, 49778, 6948 (the starter kit; no reward item)
```

## Teleport conversion fix

The client's valid Cata `MSG_MOVE_TELEPORT_ACK` was decoded with the WotLK packed-guid-first
layout, so the server rejected the ACK and kept the player at the abbey; the camp grid (and its
Kobold Vermin) never reached the client. `HandleMoveTeleportAck` now uses the existing Cata
`ReadTeleportAck` layout, and `CMSG_MOVE_TIME_SKIPPED` uses the Cata time-then-bit-packed-GUID
layout (with the relay writing time then the packed GUID). Unit tests:
`MovementPacketsTest.ReadsCataTeleportAcknowledgment` and
`MovementPacketsTest.ReadsAndWritesCataMoveTimeSkipped`, both over the captured 4.3.4 bytes.

## Limits

- The 3/8 intermediate relog checkpoint was not captured: the harness reached the final rewarded
  state after the operator took over. The final rewarded row and the 6/8 quest-log screenshot are
  retained.
- The ninth-kill cap and post-reward kill were not rerun after the manual hand-in. The eight
  add-kill counters stop at 8 by construction; the separate post-8 checks remain unrun.
- Full-bag failure is N/A for quest 7 (no item or choice reward). The inverse full-bag check was
  not run; Plan 26 quest 2158 is the live item-reward full-bag evidence.
- Party credit/sharing, other objective types, other races/classes, and quest 15/21 are excluded.
- The harness `quest_kill_accepted` predicate describes the fully automated 3/8 → 9th → repeat
  flow and is not the verdict for this manual run; the packet/DB record above is.

## Artifacts

`quest-kill-6of8.png` (quest log at 6/8, rewards 25/170 and Kobold Vermin visible),
`quest-kill-783-accepted.png`, `quest-kill-783-complete.png`, `quest-kill-7-accepted.png`,
`quest-kill-7-reaccepted.png`. The disposable client, database container/volume, cache and raw
run root were removed after acceptance; the committed screenshots and `acceptance.json` are the
retained record.
