# Plan 30: death, corpse recovery and spirit-healer resurrection

PASS for the bounded [#101](https://github.com/trolloks/azerothcore-cata/issues/101) scenario: one human
warrior, map 0, Northshire graveyard 105, owned-corpse reclaim and native spirit-healer resurrection
in the unchanged x64 build-15595 client. Corpse recovery ran at source `41a4d6d401e0`; the traced healer
cycle ran at `fb2cf10e6`. A fresh final-source replay at `f57e62bfce73` also verifies the corrected
spell-result codes and ghost-login movement. All three have independent source/binary identities
and cleanup records. Artifact-only commits do not change that C++ or SQL source.

`accepted-source-identity.json` pins the earlier compiled patch; `healer-runtime-inputs.json` pins
the traced healer run; `spell-result-runtime-inputs.json` pins the final-source replay.

## Result

| Checkpoint | Health | Ghost | Owned reclaimable corpses | Legs/sword durability |
| --- | ---: | --- | ---: | ---: |
| Accepted-run start | 1/60 | No | 0 | 21/25 |
| Death, normal logout, ghost relog | 1/60 | Yes | 1 | 19/25 |
| First reclaim | 30/60 | No | 0 | 19/25 |
| Second death, manual release, ghost logout/relog | 1/60 | Yes | 1 | 17/25 |
| Second reclaim, living logout/relog | 30/60 | No | 0 | 17/25 |

The final saved snapshots before and after the living relog are identical, including position,
health, flags, items and absence of a corpse. Final normal logout reached `online=0` before cleanup.
Native `PLAYER_DEAD` and `PLAYER_UNGHOST` events occur twice each. The client's 32 incoming
wolf swing events match the server's entire damage sequence exactly, including misses and a critical.
Both death sequences consume precisely the observed starting health. Each death invokes
`DurabilityLossAll` once; release, reclaim and logout add no death charge. The corpse row is owned
by character 1 and remains identical across each ghost relog.

Manual release sends `MSG_MOVE_TELEPORT` and receives `MSG_MOVE_TELEPORT_ACK`; the native window
immediately shows the selected graveyard. Corpse runs use ordinary movement and jumps, followed
by the native Accept button. Both successful reclaims remove the ghost UI and keep the client running.

The accepted run starts from the saved character left by the earlier diagnostic crashes, at 1 HP
and durability 21. No health or damage override was applied. Its second death starts from the normal
30-HP corpse-reclaim result. Earlier failed attempts are diagnostics, not acceptance passes.

## Spirit healer

The traced healer cycle used the same native wolf and the existing Spirit Healer 6491,
spawn 87047, at `(-8924, -189, 80.6554)`. `healer-data.json` pins that NPC and native spell/effect
records for the healer, resurrection sickness and Battle Stance. The existing DB binding
`17251 -> spell_gen_spirit_healer_res` supplies the native confirmation dialog.

| Checkpoint | Health | Ghost | Owned corpses | Legs/sword durability |
| --- | ---: | --- | ---: | ---: |
| Living start, persisted from the first healer recovery | 30/60 | No | 0 | 17/25 |
| Wolf death, manual release, ghost logout/relog | 1/60 | Yes | 1 | 15/25 |
| Native healer Accept, living logout/relog | 30/60 | No | 0 | 9/25 |

The client sends gossip hello/selection, receives `SMSG_SPIRIT_HEALER_CONFIRM`, and sends
`CMSG_SPIRIT_HEALER_ACTIVATE` after Accept. This cycle sends no corpse-reclaim request. Thirty
incoming wolf swings match the native damage sequence exactly and consume the starting 30 HP.
There is one death, release and resurrection, one death durability call and one healer durability
call. The saved corpse is identical across the ghost relog; the final living saved snapshots are
identical across the living relog. Native `PLAYER_UNGHOST` and the final world-entry state confirm
30/60 HP without ghost state. No resurrection-sickness debuff is present at level 1, as expected.
Higher-level sickness is not claimed. `healer-state-snapshots.json`, `healer-server-state.jsonl`,
`healer-client-events-and-state.json`, `healer-packet-events.log` and the `healer-final-*.jpg`
screenshots record the final cycle.

The added acceptance work exposed two issues before this pass. A wolf's 1-damage hit was truncated
to zero by Battle Stance's 5% damage-taken reduction. Passive stage traces prove `1 -> 1 -> 0 -> 0`
before the fix and `1 -> 1 -> 1 -> 1` afterward. The pinned TC reference rounds this result upward;
`fc3617e83` ports [Ovahlord's rounding fix](https://github.com/trolloks/TrinityCore/commit/6cb2bd953dbf1fc63ef2e15d198ee7c32779fd0b).
The before trace and failing regression logs are retained. No damage or health override was used.
The existing build also had all scripts disabled, leaving the bound healer confirmation script
absent. Enabling only `SCRIPTS_SPELLS=static` supplies it. The final full cycle uses that setting
throughout. `fb2cf10e6` additionally rejects healer resurrection of an already living player;
its regression fails before the guard and passes afterward.

## Final source replay

The earlier healer release screenshot exposed a false “Can't do that while confused” message.
WotLK `SPELL_FAILED_DONT_REPORT=27` is Cataclysm `SPELL_FAILED_CONFUSED=27`; the native hidden
failure value is 28. All 205 result values now match pinned TC, including 180 changed existing
values. Generated enum metadata is refreshed. The pending world SQL migration converts the same
numeric `conditions.ErrorType` values once through the database updater. The disposable before/after
check verifies all 99 nonzero condition rows. `spell-result-audit.json` and
`condition-migration-check.json` record the source and exact check.

Root and ghost water-walk restoration on login now call the existing native movement senders.
The final replay starts with a fresh normal character, without health or durability overrides:

| Checkpoint | Health | Ghost | Owned corpses | Legs/sword durability |
| --- | ---: | --- | ---: | ---: |
| Fresh character | 60/60 | No | 0 | 25/25 |
| Wolf death, manual release, ghost logout/relog | 1/60 | Yes | 1 | 23/25 |
| Native healer Accept | 30/60 | No | 0 | 17/25 |
| Living logout/relog and final logout | 30/60 | No | 0 | 17/25 |

All 63 native incoming swings match the server damage sequence exactly and total 60 damage.
The observer records one death, one unghost transition, three world entries/exits and zero
`UI_ERROR_MESSAGE` events. Packet headers show one release, one healer confirmation/activation,
two native water-walk requests and three water-walk/land-walk acknowledgements. No corpse-reclaim
request is used in this healer replay. The final saved living rows are identical across relog,
excluding the capture timestamp. `spell-final-*.jpg` shows the final source; the older
`healer-final-release.jpg` is retained as before-fix evidence of the incorrect message.

The final replay uses normal worldserver execution and a read-only addon. It needs no debugger
or packet rewriting. The launcher reached teardown after the final normal saved logout, then its
one-second worldserver subprocess wait timed out. Its generic result is **failed during teardown**,
not an accepted Plan 22 lifecycle result. The Plan 30 scene verdict comes from the independent
native witness, packet headers, exact damage sequence and saved rows. A subsequent strict reset
removed the owned container/volume and left zero owned processes and Wine processes, all recorded
ports unused and the protected client/Bottle unchanged. The timeout and cleanup are recorded in
`spell-result-runtime-inputs.json` and `spell-result-cleanup.json`.

Exact final orchestration used the existing harness with private helpers:

```sh
cmake --build var/build-plan7 --target revision.h
cmake --build var/build-plan7 --target worldserver authserver unit_tests -j 4
python3 /tmp/plan30-control.py prepare
python3 /tmp/plan30-control.py session
python3 /tmp/plan30-control.py capture-frame spell-final-release
python3 /tmp/plan30-collect-final.py
```

The session drove normal target/startattack/stopattack, Release Spirit, ghost logout/Enter World,
left turn 0.8226 seconds and forward 0.75 seconds to the existing healer, right-click and native
Accept, then living logout/Enter World/final logout. Release and Accept were `(896,193)` and
`(830,205)` in the observed 1792x1008 client area. Framebuffer captures verify owned-window focus
before and after capture; two earlier GNOME captures lost focus and were discarded.

## Scene and inputs

`combat-fixture.json` pins the Plan 29 Diseased Young Wolf template 299, level-one class stats,
display/model/faction records, starter items, attack spell 6603, ghost spell 8326 and its required
DBC records, map/area records, graveyard links and graveyard coordinates. The controlled spawn
900100 is at `(-8918, -136, 80.5378)` on map 0. Its ordinary health is 42 and attack power is 9.
The owned scene retains Plan 29's deterministic loot setup; it is not used by player corpse recovery.
Only the disposable database receives the SQL recorded in the fixture. No repository SQL changed.

The selected graveyard is 105 at `(-8935.330078125, -188.64599609375, 80.41649627685547)`.
The native class-power DBC's SHA-256 and all 25 records are in `class-power-data.json`.
`class-power-fixture-audit.json` confirms that the test fixture preserves their exact order.
The complete server DBC/maps tree hashes, unchanged client hash and selected world settings are
recorded in `runtime-inputs.json`. The owned runner retains `vmap.enableLOS=0` and
`vmap.enableHeight=0`; this scenario makes no VMAP enforcement claim.
Incidental combat durability wear is zero to isolate death charges; death loss remains 10% and
spirit-resurrection loss remains 25%. Items 39 and 49778 have maximum durability 25, so each death
subtracts 2. The ordinary level-one sickness exemption remains unchanged.

## Crash diagnosis and fix

Two earlier reclaims completed on the server but crashed the client at `0x14003347A`, reading
address 1. `crash-diagnostics.json` preserves sanitized exception details and hashes of both
reports/dumps. The complete reports and dumps remain in the private owned-run diagnostic directory.

The missing `SMSG_POWER_UPDATE` entry count was corrected first; that alone did not resolve the crash.
The second trace showed resurrection setting warrior energy to 50 through a raw WotLK power index.
Native class data gives warriors rage in slot 0, alternate power in slot 1, and no energy slot.
Disassembly traces the invalid slot through the client's power-name callback to the faulting string
read. Player power and regeneration accesses now use native class slots and ignore unavailable powers.
During both successful cycles, all unused warrior power/max-power slots stay zero. The 33 observed
power packets contain one counted rage entry each. Existing non-player slots remain unchanged,
preserving the current pet happiness consumers.

The unchanged server binary runs as a child of a batch debugger. A separate, unlinked server-side
type object supplies class layouts. The observer reads death/release/reclaim/resurrection/durability
entry and return states, incoming swing data, and power calls/packets. It writes no gameplay fields.
An owned read-only addon records native combat events, player health, ghost/dead flags and durability.
Its final saved data is filtered to this accepted attempt in `client-events-and-state.json`.
The generic runner's `observed Plan 22` result covers its lifecycle only; it is not the Plan 30 verdict.

## Verification

Exact build/test commands, run from the repository root:

```sh
cmake -S . -B var/build-plan7 -DSCRIPTS_SPELLS=static
cmake --build var/build-plan7 --target revision.h worldserver authserver unit_tests -j4
cmake --build var/build-plan7 --target worldserver -j4
var/build-plan7/src/test/unit_tests --gtest_filter='DeathHandlerTest.*:DeathMovementPacketsTest.*:DeathMovementHandlerTest.*:ResurrectRequestTest.*:IntegrationTestFixture.HasteChangesIntervalNotRage'
var/build-plan7/src/test/unit_tests
python3 apps/codestyle/codestyle-cpp.py
python3 apps/codestyle/codestyle-sql.py
python3 plan/evidence/plan30-death/verify_evidence.py
```

The native stack uses the existing harness documented in [client-automation.md](../../client-automation.md).
Exact local orchestration commands included:

```sh
PYTHONPATH=apps/cata python3 -u /tmp/plan30-prepare.py
PYTHONPATH=apps/cata python3 /tmp/plan30-seed.py
PYTHONPATH=apps/cata python3 /tmp/plan30-healer-data.py
PYTHONPATH=apps/cata python3 /tmp/plan30-healer-final-observer.py
setsid env PYTHONPATH=apps/cata python3 -u /tmp/plan30-healer-client.py > /tmp/plan30-healer-client5.log 2>&1 < /dev/null &
```

These temporary orchestration/probe files remain outside git. Preparation creates an owned DB from
the recorded cache, copies the pinned DBC/maps, and gives the client its own Wine prefix and writable
files. The fixture SQL is recorded in `combat-fixture.json`; the healer already exists in that DB.
Retries reuse the generation and saved character. Binary replacements are checked against the
recorded source and hashes before restarting. No packet relay or GM action is used.

For manual reproduction, create the normal human warrior, approach spawn 900100, use
`/target Diseased Young Wolf`, `/startattack`, then `/stopattack`, and let the ordinary wolf kill it.
Click Release Spirit. For corpse recovery, run to the owned body and accept reclaim. For the healer
case, log out normally with `/logout`, relog the same ghost, approach spawn 87047, right-click the
healer, and click its native Accept button. Log out and relog alive, then log out again before
checking the saved rows. In the final 1792x1008 window, Release Spirit was at `(896,192)` and healer
Accept at `(830,205)`; screenshots were inspected before each click. The traced healer cycle starts
from the first normal healer recovery, rather than a synthetic health/durability adjustment.

Builds exit 0. All 18 focused tests pass. Full suite: 11,493 ran, 6,013 passed, 5,480 existing skips,
one disabled. Both linters pass. Compressed build/full-test logs retain their original hashes in
`acceptance.json`; the focused log is plain text. The offline verifier checks both native death and
reclaim cycles and the healer cycles, final result-code replay, condition migration, combat amounts, power fields/packets, ghost/living persistence, corpse ownership,
durability and cleanup. It starts no live stack.

The deterministic tests cover premature reclaim, foreign-corpse access, duplicate release,
wrong resurrection offerer, duplicate response and duplicate healer resurrection on a living player.
The damage regression checks zero damage, a 1-damage hit under a 5% reduction, and ordinary damage. Packet tests cover the
resurrection offer, root/water-walk requests and ACKs, self/observer teleport and teleport ACK.
The new power tests cover unavailable warrior energy, class slots, maximum-health preservation,
existing non-player energy/happiness, and the existing rage reward invariant.

Canonical source is [TC c699217](https://github.com/trolloks/TrinityCore/tree/c699217775d90794158422387b07a917e161b582).
Attributed production commits retain Ovahlord, Shauren, Warpten and Rat's source history.
The pinned [cata-js checkout](https://github.com/trolloks/dragonsoul-js/tree/ab964a0e8dfe50a44fa92716ed05438f4a14dfd3)
did not yield an independent death/reclaim implementation. Graphify located declarations and
relationships; concrete implementations and the pinned source were inspected directly.

## Cleanup and scope

`cleanup.json`, `healer-cleanup.json` and `spell-result-cleanup.json` record removal of the exact run-owned container and volume, zero owned processes,
unused recorded ports and unchanged protected client/bottle inputs. All owned generations are reset, including the interrupted preparation; only one owned stack ran at a time. Existing databases were
not used. Raw crash reports, probes and logs remain outside the repository in the owned diagnostic
directory; credentials, full packet dumps, client assets and debugger objects are not published.

Other classes were not tested in the native client. New Cataclysm resource gameplay, creature/pet
power conversion, battleground/mass resurrection, cross-map corpses and other graveyards remain
outside this boundary. The relevant parity rows remain partial.

Live character DB fields can lag while online. Immediate recovery health comes from the native
client and server trace; persistence is checked after completed normal logouts.
