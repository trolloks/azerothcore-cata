# Plan 32: reproducible Northshire collision and pathfinding (issue #134)

Status: **freeze and tool audit complete; native acceptance BLOCKED**. The acceptance needs the map
tools built and the geometry extracted, and no build/extraction is authorized yet. Nothing here
claims a passing native run.

This record deliberately covers only the two checklist items that do not require a build:
"freeze the scenario, required data/artifact manifest and blockers" and "audit the pinned reference
and affected shared AC callers". Everything downstream of extraction is listed as a blocker.

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

- **Server grid `(gx, gy)` = `(48, 32)`.** `gx = 32 - x/533.33333`, `gy = 32 - y/533.33333`
  (`ComputeGridCoordSimple`, `src/server/game/Grids/GridDefines.h`); `GridTerrainLoader::LoadMap`
  loads `{id:03}{gx:02}{gy:02}.map`.
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
# once a build is authorized
cmake -S . -B var/build-plan32 -DTOOLS_BUILD=maps-only -DCMAKE_BUILD_TYPE=Release
cmake --build var/build-plan32 --target map_extractor vmap4_extractor vmap4_assembler mmaps_generator -j 4

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
4. **ADT addressing is the WotLK-shaped path.** `System.cpp` reads `World\Maps\{map}\{map}_{x}_{y}.adt`
   and writes `{y:02}{x:02}` tiles. Cataclysm keeps that root ADT name but moved textures/objects to
   `_tex0`/`_obj0` side files; whether AC's reader consumes the 4.3.4 root ADT chunk layout
   unchanged (MCNK height/sculpt data) is the single most important semantic question for *terrain*,
   and it can only be settled against real 4.3.4 tiles.
5. **The route cannot be validated with geometry disabled.** The harness writes
   `MoveMaps.Enable=0`, `vmap.enableLOS=0`, `vmap.enableHeight=0`
   (`apps/cata/run_real_client_authentication.py`), while `worldserver.conf.dist` defaults them on.
   The acceptance therefore needs a harness configuration path that leaves LOS/height/navmesh on;
   otherwise a green run would prove only the fallback queries the issue warns about.
6. **The VMAP reader cannot see Cata's object-side ADT.** At the pin
   (`c699217775d90794158422387b07a917e161b582`), `vmap4_extractor/wdtfile.cpp` `WDTFile::GetMap`
   opens `World\Maps\%s\%s_%d_%d_obj0.adt`; this branch's same method opens only `%s_%d_%d.adt`, and
   `ADTFile::init` then looks for the model names and `MDDF`/`MODF` placements in that root file.
   Cataclysm moved those placements into the `_obj0` side file, so building and running this reader
   unchanged does not consume the object-side ADT the reference reads. This is a separate, bounded
   prerequisite from finding 4, and it is the one that gates the planned obstacle LOS and chase
   acceptance: without object collision there is no obstacle to block or path around.

## Blockers (each needs explicit authorization or input)

- **Build authorization.** The four tools are not built. `var/extractors/{maps,vmaps,mmaps}` exist but
  are empty, so no geometry has ever been produced here.
- **VMAP object-ADT reader gap** (finding 6). The extractor must read the Cata `_obj0.adt`
  object-side file before VMAP geometry can carry the obstacle the LOS and chase acceptance need;
  the pin already does this and this branch does not.
- **Extraction run** against the pinned build-15595 archives, including the listfile.
- **Client archives provenance** for the map tiles (the same gate the DBC extraction already has).
- **Harness geometry path**: the accepted modes disable LOS/height/navmesh; enabling them (and
  keeping the anti-cheat/evade/path-query checks intact) is part of the acceptance work.

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
