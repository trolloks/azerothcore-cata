# Cataclysm cinematics and campaign transitions (#342)

Snapshot: `acore_world_clean` rebuilt from `acore_world_gold` plus the zone importer,
2026-11-01. Pinned reference: TrinityCore 4.3.4 `c699217775d90794158422387b07a917e161b582`.

## Movie start path

`Player::SendMovieStart` writes `SMSG_TRIGGER_MOVIE` (`0x4625`) with one `uint32` movie id.
That wire shape matches the pinned core. `m_movie` is now the pending-movie state: a start
sets it, and `CMSG_COMPLETE_MOVIE` (`0x4136`, empty body) clears it. The state is not
persisted across login and has no script hook in either core.

| Producer | Source | Destination |
|---|---|---|
| `SCRIPT_COMMAND_PLAYMOVIE` (`34`) | `src/server/game/Scripting/MapScripts.cpp` | movie id in `datalong` |
| `SMART_ACTION_PLAYMOVIE` (`68`) | `src/server/game/AI/SmartScripts/SmartScript.cpp` | movie id in action param |
| ICC Lich King spell `73159` | `src/server/scripts/Northrend/IcecrownCitadel/boss_the_lich_king.cpp` | movie effect; see gap below |
| `.debug play movie` | `src/server/scripts/Commands/cs_debug.cpp` | requested movie id |

SmartAI rows present in the clean world:

| Entry / source | Event | Movie | Comment |
|---|---|---:|---|
| `27872` creature | quest `12499` finished | `14` | Highlord Bolvar Fordragon |
| `31333` creature | gossip select | `14` | Alexstrasza the Life-Binder |
| `20244300` script `9` | script line | `16` | Dedication of Honor |

`event_scripts`, `waypoint_scripts`, and `spell_scripts` contain no command `34` rows.
`movie_dbc` in the clean world is empty; movie names come from the client DBC, not this DB.

## Campaign transition inventory

| Campaign | Landed quests | Transition data found | Destination | Gap |
|---|---:|---|---|---|
| Gilneas | `97` sort `4714`, `21` sort `4755` | Chapters 1-2 scripts use no movie/teleport call. No SmartAI movie row or script command. Quests `14465` and `14467` carry reward spells `82892` and `69257`, but both spells are absent from `spell_dbc` and no `spell_target_position` row exists. | In-zone Gilneas only; no scripted cross-map handoff. | Chapter 3 and the post-Gilneas departure are not in the pinned TC source. |
| Kezan | `36` sort `4737` (43 with sub-sorts) | No SmartAI movie row, script command, area-trigger teleport, or `spell_target_position` row. Pinned `src/server/scripts/Maelstrom/kezan.cpp` has an empty `Kezan` namespace. | Kezan map `648`; no authored departure to Lost Isles. | Departure trigger and destination are absent. |
| Lost Isles | `80` sort `4720` | No scripts, movie rows, or teleport rows; pinned TC has no Lost Isles counterpart. | No authored arrival teleport found. | Entire transition and scripted arrival remain unported. |

Gilneas/Kezan quests and spawns are present in the clean world; the missing part is the
scripted transition/cinematic handoff, not quest-template coverage (#205 owns the chapter
content). The Gilneas chapter scripts are direct ports from pinned TC; the Kezan and Lost
Isles additions, if any are later written, must be marked as authored rather than TC ports.

## Authored gaps

- **Spell effect 45:** 4.3.4 defines `SPELL_EFFECT_PLAY_MOVIE` at effect `45`, while the
  current AC spell-effect table still treats `45` as `SPELL_EFFECT_ADD_HONOR`. Spell `73159`
  therefore does not start a movie through the spell path yet. This is a conversion-wide
  spell-effect gap, not a movie-packet gap.
- **No campaign trigger data:** no quest, SmartAI row, script command, area trigger, or
  `spell_target_position` in the clean world moves a Gilneas/Kezan/Lost Isles player between
  the campaign maps. Any such trigger added for #205 must carry an explicit authored note.
- **No movie names in DB:** `movie_dbc` is empty, so IDs `14` and `16` are evidence from
  SmartAI rows, not resolved cinematic names.

## Verification

Deterministic checks added with the handler:

- `OpcodeTableTest.DispatchesCompleteMovie` locks `CMSG_COMPLETE_MOVIE` status, processing
  place, and the `SMSG_TRIGGER_MOVIE` value.
- `MovieHandlerTest.StartsAndClearsPendingMovie` exercises start state, completion, and a
  repeated completion. No in-world cinematic check was run.
