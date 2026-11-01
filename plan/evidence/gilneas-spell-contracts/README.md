# Gilneas spell contracts (#205)

Reference: TrinityCore `c699217775d90794158422387b07a917e161b582`, TDB 434.22011 and native build-15595 DBCs.

The clean-world builder now imports spell destinations and implicit target conditions, translates TC area-aura
flags into AC autocast, and preserves authored phase-area grants on replay. TDB-owned spell-target blocks replace
legacy blocks by spell/source id, including changed effect masks; AC-only spell blocks stay intact. The pending migration restores eleven
Gilneas destinations, nineteen target-condition rows and the native area-aura windows. It replaces the scratch
Two Forms/keg bindings. Keg credit remains on the existing abomination → Liam → explosion path.

The spell engine admits the implemented phase-update effects and the two client-handled Worgen aura types.
Other newly numbered effects and auras stay excluded. Effect 45 starts a validated movie. Two Forms and Darkflight
use native Altered Form 97709. AC's player combat hook supplies the behavior of TC's server-only combat proc 97681.
The global draft kill-credit bypass is absent; guard-assisted credit retains the per-creature flag from #380.

## Upstream authorship

- Two Forms / Darkflight: Shauren <shauren.trinity@gmail.com>, `a3d07b5e6ac45c23ec4116028c4bccae9307083b`.
- Automatic combat form: Ovahlord <dreadkiller@gmx.de>, `3506dae6539a349a41b705e519345792be0b3d7f`.
- Phase-update effects: Aokromes <aokromes@gmail.com>, `e8d994f568`.
- Movie effect: Shauren <shauren.trinity@gmail.com>, `aa30f9c5f5`.
- TDB release/data history: Aokromes <aokromes@gmail.com>, `6b8b16f74a`.

The earlier merged chapter ports #373/#374 credit upstream contributors as co-authors but have the downstream
squash author. Shared merged history is preserved. These follow-up port commits use upstream authors as required.

## Verification

- `python3 -B -m unittest discover -s apps/cata -p test_clean_world_spells.py`: passes.
- `python3 -B -m unittest discover -s apps/cata -p test_starter_content_relations.py`: 33 pass.
- `python3 -B apps/cata/audit_native_spells.py --self-test`: passes.
- `python3 -B apps/cata/audit_gilneas_spells.py --dbc-dir <native-dbc-dir>`: nine required spells admitted.
- C++ and SQL codestyle and `git diff --check`: pass.
- Pending SQL was replayed twice in an isolated SQLite schema: identical results, unrelated bindings preserved.
  This checks data selection and idempotency; it does not claim a MySQL/server run.
- The C++ movie-effect and campaign-target tests are added but have not been built or run.

No configure/build, live database changes or in-world testing. A human still needs to check Two Forms, forced
combat/Darkflight forms, the Last Stand transition/movie, later Duskhaven phases and abomination credit.
