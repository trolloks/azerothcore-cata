# Plan 32: reproducible Northshire collision and pathfinding (issue #134)

Status: **object-ADT, archive-layer and terrain-chunk fixes done, all validated by building and running
against the real client; native map-0 acceptance STILL UNRUN**. Findings 4, 6 and 7 are now compiled
and exercised: the map extractor produces a sane anchor tile and the VMAP extraction completes. The
assembler/navmesh, server data placement, harness geometry path and the client acceptance remain open.

This record covers "freeze the scenario, required data/artifact manifest and blockers", "audit the
pinned reference and affected shared AC callers", and the Cata parser prerequisites that audit
surfaced: the object-side `_obj0` ADT (finding 6), the archive enumeration/patch reading (finding 7)
and terrain chunk discovery (finding 4, which turned out to be a hard crash). All three are fixed and
verified; nothing in the extraction path is left as an uninspected assumption.

## Frozen scenario

One map-0 (Eastern Kingdoms) Northshire route, anchored on the accepted Plan 22/29/31 fixture
position so it stays reproducible across plans:

| Input | Value |
| --- | --- |
| Map | `0` (Eastern Kingdoms), zone `12` (Northshire) |
| Anchor | `(-8949.95, -132.493, 83.5312)` — the harness `CHARACTER_POSITION` used by the accepted runs |
| Tile size | `533.33333` world units (`TILESIZE` in `src/tools/map_extractor/adt.h`) |
| Artifact | `maps/0004832.map` |

One tile is named by three coordinate systems, and they are **transposed relative to each other**, so
the manifest must not mix them:

- **Server grid `(gx, gy)` = `(48, 32)`.** `ComputeGridCoord` (`src/server/game/Grids/GridDefines.h`)
  returns `gx = max(0, (int)(32 - x/533.33333))` and `gy = max(0, (int)(32 - y/533.33333))`, and it is
  the helper `Map::GetGridTerrainData(x, y)` uses (`Map.cpp`); `GridTerrainLoader::LoadMap` then loads
  `{id:03}{gx:02}{gy:02}.map`. Do not compute this with `ComputeGridCoordSimple`, which returns
  `(63 - gx, 63 - gy)` — `(15, 31)` for this anchor.
- **Extractor/ADT `(x, y)` = `(32, 48)`.** The extractor iterates `{map}_{x}_{y}.adt` and writes
  `{id:03}{y:02}{x:02}.map` (`src/tools/map_extractor/System.cpp`), so the ADT here is
  `Azeroth_32_48.adt`.
- **MMAP tile `(tileX, tileY)` = `(32, 48)`.** `TerrainBuilder::loadMap` formats
  `{mapID}{tileY}{tileX}.map`, and `--tile X,Y` sets `tileX=X, tileY=Y` (`PathGenerator.cpp`).

All three resolve to the same file, `maps/0004832.map`: the extractor's `{y}{x}` output order and the
server's `{gx}{gy}` load order are the same bytes, and `mmaps_generator --tile 32,48` reads that same
file. (An earlier draft of this record wrote `0003248.map` and `--tile 48,32`, i.e. only 32/48
swapped between the classes; that selects the transposed tile and leaves the anchor's server grid
without terrain or navmesh.) The route and its neighbouring tiles stay inside the anchored 3x3
server-grid block (`gx` 47..49, `gy` 31..33); the exact walked polyline and the pinned obstacle are
frozen once geometry exists, because both are read out of the extracted collision data rather than
guessed.

## Required inputs (to be pinned before extraction)

- build-15595 client archives (locale + all `wow-update` patches) and the listfile used to enumerate
  them; the same provenance already recorded for the DBC extraction.
- Server DBC inputs the tools read (`Map.dbc`, `LiquidType.dbc`, `MapDifficulty`/area data as used).
- The pinned tool commit (this repository at the plan/32 branch head) — the pin's tools are a
  comparison reference, not the extractor being run.

## Artifact manifest plan

The tools are built by AzerothCore's generic tool mechanism, not per-tool `CMakeLists.txt` (the pin
carries per-tool files; AC removed them, so a naive diff of the two trees is not meaningful).
`src/tools/CMakeLists.txt` whitelists exactly the four map tools under `TOOLS_BUILD=maps-only`:
`map_extractor`, `mmaps_generator`, `vmap4_assembler`, `vmap4_extractor`.

```sh
# once a build is authorized: reuse the existing var/build-plan7 tree so its stored settings and
# ccache launcher (already recorded in its CMakeCache.txt) are preserved - do not create a new build
# tree (.agents/docs/build.md). TOOLS_BUILD is currently "none" there, so re-configure that same tree
# to enable only the four map tools, leaving every other cached setting untouched.
cmake -S . -B var/build-plan7 -DTOOLS_BUILD=maps-only
cmake --build var/build-plan7 --target map_extractor vmap4_extractor vmap4_assembler mmaps_generator -j 4

# map tiles -> <out>/maps/{id:03}{y:02}{x:02}.map   (-e 1 = maps only, -f 0 = float heights)
# -i takes the client install root: System.cpp appends "/Data/" itself when opening archives.
./map_extractor     -e 1 -f 0 -i "<client_dir>" -o "<out>"
# VMAP geometry; output path is its own working dir (default "."), input via -d.
# -d appends only a slash and searches directly beneath it, so it needs the Data directory,
# not the install root (otherwise it looks for <client_dir>/enUS/... and reports no MPQ).
./vmap4_extractor   -d "<client_dir>/Data"
# assemble per-tile VMAP trees into per-map archives
./vmap4_assembler   <vmaps_dir> <assembled_dir>
# navmesh for the frozen tile (tileX,tileY -- see the coordinate table above); the readme calls
# this binary movement_extractor
./mmaps_generator   0 --tile 32,48
```

(The per-tool usage strings above are taken from the sources: `map_extractor/System.cpp`
`Usage()`, `vmap4_extractor/vmapexport.cpp` `processArgv`, and `mmaps_generator/Info/readme.txt`.
The mmaps readme still names the binary `movement_extractor` while the CMake project is
`mmaps_generator` — doc drift worth noting when scripting the manifest.)

Output goes outside Git. Manifest to publish per artifact set: tool commit, build/version, input
archive hashes, per-tile inventory, output file hashes, and the config used. The `assets:maps`,
`assets:vmaps` and `assets:mmaps` parity rows carry the same fields.

## Tool audit findings (build-free)

1. **Per-tool CMakeLists are gone in AC.** `map_extractor`, `vmap4_extractor`, `vmap4_assembler`
   and `mmaps_generator` have no local `CMakeLists.txt`; they are generated by
   `src/tools/CMakeLists.txt`. The pin has all four. Any manifest must name `TOOLS_BUILD=maps-only`,
   not the pin's per-directory targets.
2. **`map_extractor` links StormLib** (`target_link_libraries(map_extractor ... storm)`), and AC's
   `mpq_libmpq.cpp` reads archives through `SFileOpenArchive`, so Cata MPQ archives are supported —
   the historic MPQ blocker does not apply to the map tool.
3. **A latent build risk is flagged in-tree.** `src/tools/CMakeLists.txt` carries a commented-out
   `target_link_libraries(${TOOL_PROJECT_NAME} PRIVATE acore-dependency-interface)` annotated
   "Need fix errors in maps tools", and the map tools must link `common/mpq/zlib/Recast/g3dlib/fkYAML`
   explicitly. Treat "the four tools build cleanly" as an unproven prerequisite.
4. **ADT addressing: the Cata root ADT has no MCIN — measured, and fixed in this branch.**
   `System.cpp` reads `World\Maps\{map}\{map}_{x}_{y}.adt` and writes `{y:02}{x:02}` tiles; Cataclysm
   keeps the root name and moves textures/objects into `_tex0`/`_obj0`, and the pin reads the same
   root name, so the file choice is right. The member list matched too: `adt_MCNK` carries the same
   34 fields in the same order as the pin's.
   **Chunk discovery did not match, and it was fatal.** A real build-15595 ADT (extracted and dumped:
   `World\Maps\Azeroth\Azeroth_32_48.adt` plus the three `wow-update-base` chains) lays out
   `MVER → MHDR → MH2O → MCNK …` with `offsMCIN = 0`, `offsTex = 0` and `offsModels = 0`: Cataclysm
   root ADTs have **no MCIN index at all**. The old reader reached cells through `MCIN`
   (`adt.a_grid->getMCIN()` then `cells->getMCNK(i, j)`), and `adt_MHDR::getMCIN()` adds `offsMCIN`
   without checking it, so the `if (!cells)` guard could not reject an MCIN-less file and MHDR bytes
   were read as cell pointers. Run against the real client, `map_extractor` **segfaulted inside
   `ConvertADT` on the first map and produced zero tiles**.
   `ADT_file` now indexes the cells by walking the chunk list, recording each `MCNK` under its own
   `iy`/`ix` — the row/column order the conversion loops and the reference's
   `area_ids[mcnk->iy][mcnk->ix]` use (`ADT_file::getMCNK`), and `ConvertADT` rejects a file whose
   cells are missing, duplicated or malformed instead of dereferencing a header slot.
   **Verified against the real client:** the same command exits 0, processes all 206 maps and writes
   7,192 tiles. The anchor tile decodes to `MAPS` / version 10 / build 15595 with `MHGT` heights
   61.29–276.96 (the fixture's z=83.53 lies inside) and a `MLIQ` section, and the server's own
   `MapMagic`/`MapVersionMagic` (`GridTerrainData.h`) match, so `GridTerrainLoader` accepts it. The
   cell layout was then checked against the raw file rather than by plausibility: reading each MCNK's
   own `ix`/`iy`/`areaid` out of the ADT, the produced tile's `area_ids[iy][ix]` matches that grid
   **256/256**, while the transposed reading (cell `ix` used as the row, as an earlier revision of
   this branch wrongly did) matches only 188/256. As a by-product, the fixture position's own cell
   spans world X (-8966.66, -8933.33] and world Y (-133.33, -100.00] and carries client area **9
   (Elwynn Forest)**, not 12 — the `12` cells are the tile's `iy=0` row, so the harness's "Northshire"
   label is not the terrain cell area at the spawn. Raw evidence:
   `.../cata-runs/plan32-terrain-mcnk/`.
5. **The route cannot be validated with geometry disabled.** The harness writes
   `MoveMaps.Enable=0`, `vmap.enableLOS=0`, `vmap.enableHeight=0`
   (`apps/cata/run_real_client_authentication.py`), while `worldserver.conf.dist` defaults them on.
   The acceptance therefore needs a harness configuration path that leaves LOS/height/navmesh on;
   otherwise a green run would prove only the fallback queries the issue warns about.
6. **The VMAP reader could not see Cata's object-side ADT — fixed in this branch.** At the pin
   (`c699217775d90794158422387b07a917e161b582`), `vmap4_extractor/wdtfile.cpp` `WDTFile::GetMap`
   opens `World\Maps\%s\%s_%d_%d_obj0.adt`; this branch opened only `%s_%d_%d.adt`, and
   `ADTFile::init` then looked for the model names and `MDDF`/`MODF` placements in that root file.
   Cataclysm moved those placements into the `_obj0` side file, so the reader found none of them and
   the extractor produced no object collision. `GetMap` now opens `_obj0.adt`, mirroring the pin
   (TrinityCore `568fba707c`; the code commit is authored by that commit's original author). Without
   this the extractor produces no object collision, so it is one prerequisite for the obstacle LOS
   and chase acceptance — but it is **not** the whole distance to it: the archive layer underneath is
   a separate, larger gap (finding 7), so a correct filename cannot surface the object data on a
   build-15595 install by itself. An earlier revision of this record called `_obj0` the whole gap on
   the strength of a token sweep; that sweep only matched format tokens and never looked at archive
   names, so the claim is withdrawn here.
7. **The VMAP extractor could not open the build-15595 archives — fixed and validated in this
   branch.** `vmapexport.cpp` enumerated the WotLK set only — `locale-*.MPQ`, `expansion-locale-*.MPQ`,
   `lichking-locale-*.MPQ`, `common.MPQ`, `common-2.MPQ`, `expansion.MPQ`, `lichking.MPQ` and
   `patch*.MPQ` — and its `MPQFile` was backed by the bundled libmpq, which cannot read Blizzard's
   incremental binary-diff patches. On a build-15595 install none of `world.MPQ`, `art.MPQ`,
   `expansion1/2/3.MPQ`, `world2.MPQ` or the `wow-update` chains were opened, so the ADT, WMO and M2
   files were unreachable and finding 6 could not take effect.
   The tool now reuses the StormLib layer the in-tree `map_extractor` already had (`023d2bd21` added
   the Cata base-archive list, `09707af2a` vendored StormLib and switched that tool to it): it opens
   `world.MPQ` as the base archive, attaches `art.MPQ`, `world2.MPQ`, `expansion1/2/3.MPQ` and the
   `wow-update-base-*` chain to it, and opens the locale archive with its `patch-<locale>` and
   `wow-update-<locale>-*` chains. `src/tools/CMakeLists.txt` now links `storm` for this tool too.
   This is a deliberate divergence from upstream, which still uses libmpq for both tools; the Cata
   patch format requires it. Discovery is restricted to the target client's base generation
   (`NEW_BASE_SET_BUILD` 15211 .. `CLIENT_BUILD` 15595), matching the pin's skip rule, so a
   `wow-update-base-16000.MPQ` or a retained pre-rebase patch cannot silently change the
   reconstructed data. A missing required archive, a base that will not open or a patch that will
   not attach now fails the run (`OpenMPQArchives` returns false and main exits non-zero before
   parsing) instead of leaving the locale archive open and reporting "No errors" with missing
   geometry; the MPQ reader also rejects a failed or short read rather than exposing unread bytes.

   **Functional check against the real client** (`.../.issue118-ssd-client/Data`, build-15595): the
   pre-migration binary opened only `locale-enUS.MPQ`, failed every WotLK archive and produced no
   geometry. The migrated binary logged opening `world.MPQ` and applying `art/world2/expansion1/2/3`
   plus `wow-update-base-15211/15354/15595` and the locale chains, processed 143 maps and extracted
   **10,496 assets (2,245 WMO, 8,249 M2)** ending "Work complete. No errors." That set includes
   ADT-level doodads (`Abbeyshelf01.m2`, `Elwynnstonefencepost.m2`, `Elwynnrock1.m2`), which only
   appear when the object-side `_obj0` read of finding 6 works, so that fix is functionally exercised
   as well. Re-checked after the review hardening with identical results, and the two new failure
   paths were exercised directly: a scratch Data dir without `world.MPQ` exits non-zero with
   "nothing was extracted" and no success line, and a `wow-update-base-16000.MPQ` is reported as
   skipped and not attached. Raw logs live outside Git at
   `.../cata-runs/plan32-archive-migration/`.

## Blockers (each needs explicit authorization or input)

- **Build authorization — satisfied for the tools.** The four map tools build from this branch, so
  findings 4, 6 and 7 are compiled rather than inspected, and both extraction runs above used that
  binary. The server/game targets were not built and `var/extractors/{maps,vmaps,mmaps}` were not
  populated.
- **Assembler, navmesh and data placement.** The map and VMAP stages now produce their artifact sets,
  but `vmap4_assembler` and `mmaps_generator` have not been run for the frozen tile and nothing has
  been installed into the server's data directory.
- **Client archives provenance** for the map tiles (the same gate the DBC extraction already has);
  the runs above used the on-disk client copy without recording its provenance.
- **Harness geometry path**: the accepted modes disable LOS/height/navmesh; enabling them (and
  keeping the anti-cheat/evade/path-query checks intact) is part of the acceptance work.
- **Native acceptance**: the map-0 route, the LOS/height/navmesh queries and the pinned obstacle
  chase are still unrun.

## Acceptance plan (unchanged, for when the above is authorized)

Run the owned native client with the selected tiles loaded and server LOS, height and navmesh use
enabled, and prove: clear vs blocked LOS against a pinned obstacle, correct ground heights at pinned
points, one normal creature chase that paths around that obstacle, and a normal walk/stop/logout/
relog at a legal position. Deterministic cases: wrong-build/version headers, truncated or missing
selected tiles/models, clear/blocked query expectations, unreachable path handling. Acceptance must
fail (not silently pass) when geometry is unavailable or disabled.

## Not covered by this record

Whole-continent extraction, flying/swimming, transports, cross-map transfer, terrain swaps and
all-zone navigation. This freezes one map-0 route only.
