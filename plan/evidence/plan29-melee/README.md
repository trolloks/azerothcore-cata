# Plan 29: one melee encounter and creature death

PASS on real 4.3.4 build 15595. Production code: `da6beb28d`. Test code: `5f1416ed5`.
The runtime binary was built at the production commit while test files were in progress; its
hash is pinned. Later commits change tests and evidence only.
The existing owned-run harness created a normal human warrior and one disposable DB/client
generation. The controlled spawn 900100 uses native Diseased Young Wolf template 299,
level one, 42 health, faction 32, display 31049 and its normal combat stats/AI. The fixture
pins the template, class-level stats, model/faction DBC records, starting weapon, autoattack
spell 6603 and item records. Only placement and deterministic loot were changed in its DB:
one Small Pumpkin 4656 and 37 copper. No repository SQL was changed.

The client targeted and auto-attacked normally, landed hits, sent `/stopattack`, resumed,
and killed the wolf. Client combat events and the server observer agree on damage:
10, 9, 10, 9, 22. Server health before those hits was 42, 32, 23, 13, 4. Nonlethal client
`SWING_DAMAGE` events report -1 overkill; the final critical reports 18 overkill. Effective
damage is 60 minus 18, exactly the pinned 42 health. The native log records `PARTY_KILL`
and `UNIT_DIED` for the same creature GUID. See `WoWCombatLog.txt` and the event JSON.

At return from the real `Creature::setDeathState`, the server observer records health 0,
DeathState::Corpse = 2, null victim, zero threat entries and zero PvE combat references.
The player also has a null victim and zero combat references, and both combat flags are
clear. Client samples record health 0/42, dead, no combat and no threat. The corpse opens
through the normal loot path. Manual item/money takes and a repeated open award exactly
one pumpkin and 37 copper. Normal logout saves them; normal relog retains them. Complete
saved inventory and money snapshots before and after relog are identical.

`acceptance.json` contains the asserted comparison and binary hashes. The selected packet
log includes normal attack-start/stop, threat clear and loot requests/responses. Five new
tests exercise the actual swing serializer, unknown/spoofed targets, a registered
non-attackable target, repeated idle stop and real CreatureAI evade cleanup. Evade cleanup
clears threat, both combat references/flags and rejects repeated entry. Existing threat,
combat and inventory/loot suites also pass: 211 tests total. Reverting only the wire field
to its old zero value fails the serializer test; see `baseline-test.log`.

## Observation and cleanup

The unchanged server executable ran as the child of a local batch debugger. A separate
object compiled from the same C++ flags supplies class layouts, without linking or running
its code. The observer samples `CalcDamageInfo` at `Unit::SendAttackStateUpdate` and installs
a finish breakpoint on `Creature::setDeathState(DeathState, bool)`. It reads health, flags,
death state, victim, `_myThreatListEntries` size and `_pveRefs` size. `server-state.jsonl`
contains those samples. No health, threat, damage or combat fields were written by the probe.

An owned client observer records native `COMBAT_LOG_EVENT_UNFILTERED` arguments and target
API samples, then saves them on normal logout. The event JSON preserves Lua's absent fields;
a missing combat/threat field means the client API returned nil. The source Interface
contents were copied into the owned generation before the observer was added. The source
client and personal bottle remained unchanged. The JPEGs are explicitly binary to preserve
their bytes under the repository's default text attributes.

Early attempts were inconclusive for the full acceptance: the first killed wolf's default
60-second corpse expired during inspection, and diagnostic restarts retained its one-hour
respawn timer. No earlier loot award occurred. Only spawn 900100's persisted timer was reset
before the final encounter. The final owned configuration retained corpses for 300 seconds;
the production default is unchanged. Attempts to attach a debugger were rejected; launching
the owned server as its child worked. These attempts are archived locally, not counted as
additional passing encounters. One DB generation was reused.

The first normal logout persisted the awards and native observer data. After normal relog,
the saved inventory still matched and the backpack retained the pumpkin. Harness cleanup
followed the final quit request before a second normal logout completed. The final snapshot
is therefore `after-relog-saved.tsv`, not a second logout proof. The generation was then reset:
zero active recorded processes, zero Wine processes and protected input hashes unchanged.
Credentials, the full manifest, debugger object and raw global logs are not published.

## Commands

The user explicitly authorized builds, tests and owned real-client acceptance, including
repeat builds as needed. Existing configuration was reused; no configure was run.

```sh
cmake --build var/build-plan7 --target worldserver authserver -j 4
cmake --build var/build-plan7 --target unit_tests -j 4
var/build-plan7/src/test/unit_tests --gtest_filter='MeleeRequestTest.*:CombatManagerIntegrationTest.*:ThreatManagerIntegrationTest.*:SnakeTrapEvadeTest.*:LootViewTest.*:ItemInventoryTest.*:ItemPacketsTest.*'
python3 apps/codestyle/codestyle-cpp.py
python3 apps/codestyle/codestyle-sql.py
python3 /tmp/plan29-baseline.py
python3 /tmp/plan29-prepare.py
python3 /tmp/plan29-seed.py
python3 /tmp/plan29-observer.py
PYTHONPATH=apps/cata python3 -u /tmp/plan29-client.py
python3 /tmp/plan29-input.py chat '/combatlog'
python3 /tmp/plan29-input.py click 1170 750 3
python3 /tmp/plan29-input.py chat '/stopattack'
python3 /tmp/plan29-input.py chat '/startattack'
python3 /tmp/plan29-input.py click 1170 780 3
python3 /tmp/plan29-input.py click 1173 798
python3 /tmp/plan29-input.py click 1173 758
python3 /tmp/plan29-input.py chat '/logout'
python3 /tmp/plan29-read-witness.py
python3 /tmp/plan29-record.py
python3 apps/cata/run_real_client_authentication.py reset --manifest /mnt/f79365ff-6a68-45da-925e-b9ddc6d5da6c/Fun/.plan29-melee100/manifest.json
git diff --check
```

The local probes adapt the existing lifecycle/input harness and are not repository e2e tests.
Use the committed fixture as the exact source setup, including original rows and declared
mutations. All input reacquires the owned active window; mouse clicks are held for 100 ms.
The generic CHARACTER_CREATION_MODE supplies lifecycle setup only. This is independent
Plan 29 acceptance, not a Plan 22 verifier result.

## Reference and remaining gaps

TrinityCore pin `c699217775d90794158422387b07a917e161b582`, `Unit::SendAttackStateUpdate` and
`CombatLogPackets::AttackerStateUpdate`. Upstream mechanism commit
`4daf6d5f23c9e505419b34b1054b86559c6167d5` is by Ovahlord, credited on the production commit.
Only the no-overkill wire sentinel changes. AC damage arrays, god-mode handling, guards,
AI, threat ownership, hooks, death, loot and saves remain in use. Request/start/stop/threat
layouts were audited against the pin. Graphify located declarations but its implementation
coverage was incomplete, so implementation paths were checked directly.

This proves one bounded solo melee encounter. Ranged weapons, dual wield, pets, PvP, full
combat-formula parity, healing, other creature AIs, player death and server-restart reward
behavior remain outside this plan. The four affected parity rows remain partial.

The unused `HITINFO_DEBUG` payload remains outside this acceptance. It emits 48 bytes in AC
and 52 in the pinned reference. No current source call site sets that flag; only its enum
and serializer branch reference it. Recheck that layout before enabling debug swing packets.
