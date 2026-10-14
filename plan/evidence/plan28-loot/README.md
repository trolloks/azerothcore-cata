# Plan 28: controlled single-player loot

PASS on real 4.3.4 build 15595, tested AC commit `b65ae7070449`.
The native Battered Chest (2843, display 259, Lock 57) uses owned spawn/loot ID 900099,
one Small Pumpkin (4656), and exactly 37 copper. Restock is 3600 seconds; repeat checks
stay within this source lifetime. The fixture JSON pins native DBC rows, source hashes,
original SQL templates, declared disposable SQL mutations, and the final source records.
No repository SQL or personal client/bottle was modified.

Normal client input opened the chest, released and reopened it before taking anything,
took the pumpkin and money separately, then reopened the exhausted source. A normal
logout saved one pumpkin (item GUID 11, bag 0, slot 24) and 37 copper. After normal relog,
the backpack retained both awards and opening the same chest showed an empty loot frame.
A second normal logout produced an identical complete saved inventory and money total.
See the screenshots, `loot-packets.log`, two after-award TSVs, and `acceptance.json`.
The before-award inventory contained no pumpkin and the character had zero copper.

The existing ownership/lifecycle runner created one disposable generation, normal character,
DB container/volume and Wine prefix. Its CHARACTER_CREATION_MODE was reused for setup;
this is separate Plan 28 acceptance, not a Plan 22 verifier pass. Early attempts were
inconclusive: blocked ownership/focus, a chest inside the stairs, and clearing the native
lock (which sends generic gameobject-use rather than the opening spell). The corrected
fixture preserves Lock 57 and sits on open ground. Those attempts are archived locally.
Final lifecycle state was observed before reset; reset left zero active owned processes,
and protected input hashes still match. Credentials and full manifests are not published.

## Commands and local checks

User authorized the build and disposable client check. No configure was run.

```sh
cmake --build var/build-plan7 --target worldserver authserver unit_tests -j 4
var/build-plan7/src/test/unit_tests --gtest_filter='LootViewTest.*:ItemInventoryTest.*:ItemPacketsTest.*'
PYTHONPATH=apps/cata python3 /tmp/plan28-prepare.py
python3 /tmp/plan28-seed.py
PYTHONPATH=apps/cata python3 -u /tmp/plan28-client.py
python3 /tmp/plan28-input.py capture native-open
python3 /tmp/plan28-input.py click 520 725
python3 /tmp/plan28-input.py click 520 682
python3 /tmp/plan28-input.py chat '/logout'
python3 /tmp/plan28-record.py
python3 apps/cata/run_real_client_authentication.py reset --manifest /mnt/f79365ff-6a68-45da-925e-b9ddc6d5da6c/Fun/.plan28-loot99/manifest.json
```

The local `/tmp` probes adapt the existing runner rather than add live-stack tests.
The recorded fixture is the authoritative source setup, including its placement and lock
corrections. Input verifies the owned active window, holds mouse clicks for 100 ms, and
starts chat commands with the slash binding. Relog uses the runner's normal character
selection. SQL snapshots wait for the normal logout to persist character state.

All 15 focused tests pass. Nine loot cases cover empty/no-permission currency counts,
item wire layout, full inventory, foreign winner/source ownership, invalid/repeated take,
foreign/repeated release, and notification opcodes. Four wire/opcode checks fail on the
unchanged baseline; `baseline-wire.log` records that reproduction. C++ and SQL lint and
`git diff --check` pass. The build log and test output are included.

## Scope and reference

TrinityCore pin: `c699217775d90794158422387b07a917e161b582`.
The zero currency-count byte and notification values follow Ovahlord's upstream work
(`0ae695908c743587dc93371e50f6a800b94a4175` and `c80ce80644d`); the production commit
credits that author. Existing AC handlers, ownership/capacity checks, storage, saves and
hooks remain in use. The test fixture enables the achievement-hook registry needed by
its real item-award path.

This proves item/money delivery from one controlled single-player source. It does not
prove currency awards, group rolls/master loot, pickpocketing, disenchanting, combat
eligibility, all loot sources, or awards across server restarts. Inventory and currency
parity remain partial/unassessed outside this boundary.
