# Plan 27 inventory acceptance

Issue [#98](https://github.com/trolloks/azerothcore-cata/issues/98) was exercised with the real
build-15595 client on commit `8a51207ef0a81cedde1755b79aae7ce53a2ca0e7`, run `224bd7db8ee6`, generation 2.
The [native item fixture](../../../apps/cata/fixtures/plan27-native-inventory.json) pins the source records.

The normally created Human Warrior equipped a six-slot Small Brown Pouch, split and merged pumpkins,
moved them between backpack and pouch, swapped occupied slots, unequipped and equipped the starting vest,
destroyed a two-item stack, and consumed one pumpkin through `/use Small Pumpkin`. Only the pouch and
ten pumpkins were inserted as declared inventory fixtures. Their GUIDs were unused values below the
running item allocator. The disposable world database used the native pouch's six-slot template.

The [packet extract](inventory-packets.txt) records the normal item requests, including destruction at
`0x4A27` and pumpkin use with spell 433. The [acceptance record](acceptance.json) contains the operation
sequence and exact inventory rows. [Before logout/relog](before-relog.tsv) and [after](after-relog.tsv)
match for item GUID, bag, slot, entry, count, durability, and charges. Seven unique items remain; pumpkins
total seven after destroying two and consuming one. The vest and pouch remain equipped.

The acceptance record pins the restored SQL cache hash. [Food spell 433](food-spell433.json) pins its
native `Spell.dbc` and `SpellEffect.dbc` rows and source hashes. Rows are little-endian 32-bit words in the
repository's native `SpellEntryfmt` and `SpellEffectEntryfmt` column order. Local preparation, client,
and operation scripts are listed in the JSON; their procedural scenario is described above.

The [focused test result](focused-tests.txt) covers the six packet and inventory tests, including invalid
slot, insufficient space, required-level rejection, and a foreign bound item. The same six tests passed
again during the continuation review. Both required codestyle checks passed.

Protected client inputs matched the saved baseline when checked outside the filesystem sandbox. The
sandbox's metadata view gives a different personal-bottle hash; that result is not a native isolation
check. Raw logs, packet capture, the failed first generation, and all intermediate screenshots remain
in the local run directory `.plan27-inventory98` on the Fun volume. Manifests contain disposable database
credentials and are deliberately excluded from this record.

Generation 2 was reset through the ownership-checking harness after recording the evidence. Its client
and server processes and owned database container/volume were removed; the isolation check passed again.

Selected screenshots show the [split](verified-split.png), [unequipped vest](verified-unequipped.png),
and [inventory after relog](verified-relogged.png). The tutorial overlay partially obscures the pouch in
the split image; the packet sequence and final persisted inventory provide the remaining evidence.

Graphify's existing graph covers the packet class and inventory storage/validation paths. Its
`HandleDestroyItemOpcode` node is a declaration, so the implementation in `ItemHandler.cpp` and
`Player::DestroyItemCount` were reviewed directly. Ownership, unequip restrictions, non-destroyable
flags, quest notifications, hooks, and persistence remain on the existing AzerothCore paths.

This acceptance is bounded to basic operations and one consumable. It does not establish bank, reforging,
transmogrification, random properties, sockets, set bonuses, all equipped-item spells, or all cooldowns.
