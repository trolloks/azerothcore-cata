# Quest 2158 acceptance

Native Cataclysm **Rest and Relaxation** completes through normal build-15595 client actions.
The source manifest is [plan26-cata-quest2158.json](../../../apps/cata/fixtures/plan26-cata-quest2158.json).
The runtime and cleanup record is [acceptance.json](acceptance.json).

The normally created Human Warrior discovers Falkhaan, autoaccepts, abandons and reaccepts,
then selects the quest from Farley's gossip. Full bags reject the reward and retain the quest.
After three fixture slots are released offline, normal turn-in awards one Goldshire Iron Bracer,
five Small Pumpkins, five Refreshing Spring Waters, 45 XP and 27 Stormwind reputation.
Money remains zero. The 25-point native reputation reward receives the Human 10% racial bonus.
Normal logout saves the result; login reloads it. Further client reward attempts add nothing.
Final database assertions prove one rewarded row, no active quest row and exact quantities.

Invalid reward slot checks pass in `QuestPacketsTest`; the real client blocks its own invalid
choice before transmission. No crafted network request was injected. Quest 2158 has no
kill/collect objective, scripted dependency, chain, or phase transition. Those remain separate
parity work. NPC placement and full bags are declared owned test preconditions, not world parity.
No GM completion command or quest-row insertion was used.

`worldserver`, `authserver` and `unit_tests` built in the existing ccache/PCH tree.
All seven `QuestPacketsTest.*` checks, the owned-focus checks and both codestyle linters pass.
The conversion auditor self-check fails its stale opcode-model assertion; the same seven
model issues reproduce against `origin/master`, so this is not a new pass claim.

The run reused the verified SQL/client caches. Prepare took 282.9 seconds. Observation deadlines
and the unconverted inventory-destruction disconnect required resumed sessions; their logs remain
under the local evidence root in the JSON. Plan 27/#98 records destruction opcode `0x4A27`.
Raw packet logging was unavailable in the scripts-disabled build; no sniff proof is claimed.
Both owned generations were reset. Ownership checks passed and protected inputs stayed unchanged.

The committed screenshots show native details, full-bag rejection, successful awards and the
post-relog NPC without the completed quest reward. Original logs and assertion snapshots remain
at the recorded local evidence root. The JSON pins their runtime commit separately from later
record-only commits.
