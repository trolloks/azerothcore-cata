# Plan 24 phase acceptance: issue #97

## Pinned scenario and run

- AC commit: `c1137496bff096a2dcffda0e904dc3bff52408bd`.
- Client: real Cataclysm build 15595, Wine GE-Proton10-28.
- Scenario: `apps/cata/fixtures/plan24-phase-scenario.json`, single phase 125 in area 12.
- Isolated run: `.plan24-phase97-fast/generation-1`, final retry on 2026-10-13.
- Packet capture: `logs/World.pkt`, SHA-256
  `3c05cde770cd13bea00eebf7dcfa3d03174b9341d7f04bc0737bae471201525d`.
- Existing diagnostic worldserver binary records packets; no build was performed for this acceptance run.
- Scratch fixtures: Marshal McBride (197), Battered Chest (2843), and unphased Deputy Willem (823).
  The fixtures are seeded only into the generation's isolated world schema.

## Expected and observed

The character crosses the measured Northshire/Elwynn boundary using ordinary movement. It logs out and
back in normally at the inside and outside stops. No phase-changing GM command is used.

| Step | Expected membership | Capture evidence (packet index / tick in ms) |
| --- | --- | --- |
| Initial Northshire login | Empty | Phase packet 71 / 32895; unphased control created |
| Enter Elwynn | 125 | Phase packet 278 / 39209; both phased fixtures created 282 / 39356 |
| Relog inside | 125 restored | Logout 369 / 60379; phase 440 / 64500; fixtures 447 / 64640 |
| Interact inside | Gossip and loot allowed | Gossip 532–533 / 71786–71787; loot 557 / 78359 |
| Exit to Northshire | Empty | Phase 608 / 87571; both fixture GUIDs removed 611 / 87725 |
| Relog outside | Empty restored | Logout 677 / 108375; phase 746 / 112859; control 753 / 113006 |
| Interact outside | Fixtures unavailable | Screenshots show both absent; no fixture interaction packets |
| Re-enter Elwynn | 125 | Phase 882 / 134188; fixtures recreated 888 / 134338 |
| Interact after re-entry | Gossip and loot allowed | Gossip 917–918 / 140587–140593; loot 952 / 146967 |

Final-run GUIDs: NPC `f1300000c5000494`, chest `f110000b1b000200`, control `f130000337000495`.
The control remains visible across the phase boundary. Outside rejection is client-side unavailability;
this run does not inject stale GUID requests to test a server-side rejection independently.

Accepted movement stops are approximately `(-9070.533, -36.747)`, `(-9066.108, -36.615)`,
and `(-9070.291, -36.671)`. Screenshots are retained under `evidence/raw/phase-stage-*.png`
and `phase-chest-*.png`; prior retries remain under `previous-runs/`.

## Timing and verification

Reusing the prepared generation took 0.0 seconds; the complete run, including two normal logout timers,
took 165.6 seconds. The earlier movement-only run took 82.3 seconds. Fresh preparation was 396.7 seconds
before the transaction restore optimization; that optimization has offline checks but no fresh-run timing yet.

The runner's generic Plan 13 verifier reports this multi-login scenario as inconclusive. It confirms
endpoint ownership and unchanged isolation hashes, but its bootstrap predicate is not the phase acceptance
predicate. Phase results above come from the packet capture and owned-window screenshots.

Cleanup stops the generation's owned client and auth/world processes. Raw evidence and isolated database
fixtures are retained for review and reproducible retries. Personal client and Bottle isolation hashes are
unchanged. The isolated database container is retained for reuse.
