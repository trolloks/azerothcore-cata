# Plan 32: reproducible Northshire collision and pathfinding (issue #134)

Status: **freeze, tool audit and the object-ADT reader fix complete; native acceptance BLOCKED**. The
acceptance needs the map tools built and the geometry extracted, and no build/extraction is
authorized yet. Nothing here claims a passing native run.

This record covers the checklist items that do not require a build: "freeze the scenario, required
data/artifact manifest and blockers", "audit the pinned reference and affected shared AC callers",
and one Cata prerequisite that the audit surfaced and that could be settled in code without running
the tools — the object-side `_obj0` ADT (finding 6). That fix is one step, not the finish line: the
archive layer beneath it (finding 7) and terrain chunk discovery (finding 4) stay open, and
everything downstream of extraction stays blocked.

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
4. **ADT addressing uses the root file, but chunk discovery still differs from the pin.** `System.cpp`
   reads `World\Maps\{map}\{map}_{x}_{y}.adt` and writes `{y:02}{x:02}` tiles. Cataclysm keeps that
   root ADT name and moves only textures/objects into `_tex0`/`_obj0` side files, and the pin's
   `map_extractor` reads the same root name (`System.cpp:1063`), so the file choice matches. The
   member list matches too: the pin's `adt_MCNK` and this branch's carry the same 34 data fields in
   the same order. **A matching member list does not mean the reader can find those chunks, though.**
   This branch reaches them through the legacy `MCIN` index — `ConvertADT` takes
   `adt.a_grid->getMCIN()` and then reads `cells->getMCNK(i, j)->areaid` (`System.cpp:420`,
   `System.cpp:442`) — while the pin iterates the ADT's chunks directly
   (`Trinity::Containers::MapEqualRange(adt.chunks, "MCNK")`, `System.cpp:488`). The local guard does
   not cover an ADT without `MCIN`: `adt_MHDR::getMCIN()` returns
   `reinterpret_cast<adt_MCIN*>(reinterpret_cast<uint8*>(&flags) + offsMCIN)` without checking
   `offsMCIN` (unlike `getMH2O()`, which does), so it yields a non-null pointer into the MHDR that the
   `if (!cells)` check at `System.cpp:421` cannot reject, and header bytes are then read as cell
   pointers. Whether the selected 4.3.4 tiles still carry `MCIN` is therefore a real prerequisite,
   not something the shared root filename and MCNK member list settle.
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
7. **The VMAP extractor cannot open the build-15595 archives at all.** `vmapexport.cpp`
   `fillArchiveNameVector` enumerates the WotLK set only — `locale-*.MPQ`, `expansion-locale-*.MPQ`,
   `lichking-locale-*.MPQ`, `common.MPQ`, `common-2.MPQ`, `expansion.MPQ`, `lichking.MPQ` and
   `patch*.MPQ` — and never opens Cata's `world.MPQ`, `art.MPQ`, `expansion1/2/3.MPQ`, `world2.MPQ` or
   the `wow-update` chains. Its `MPQFile` is backed by the bundled libmpq
   (`libmpq__archive_open`/`libmpq__file_number`) and looks each file up across whichever archives it
   happened to open, with no patch-chain concept; the pin's `vmapexport.cpp` opens the Cata base sets
   and applies `SFileOpenPatchArchive`, and its `mpqfile.cpp` reads with `SFILE_OPEN_PATCHED_FILE`.
   The credited commit `568fba707c` carried that StormLib/archive migration alongside the filename
   change. On a build-15595 install the ADT, WMO and M2 files live in archives this program never
   opens, so finding 6 alone cannot surface them. This branch's own `map_extractor` is already the
   Cata-shaped model for the fix: its `System.cpp` `CONF_mpq_list` starts at `world.MPQ`/`art.MPQ`/
   `expansion1.MPQ` and it applies `wow-update-<locale>-<build>.MPQ`/`wow-update-base-<build>.MPQ`
   through `SFileOpenPatchArchive` (`mpq_libmpq.cpp`). Not attempted here: this PR stays a bounded
   filename fix.

## Blockers (each needs explicit authorization or input)

- **Build authorization.** The four tools are not built. `var/extractors/{maps,vmaps,mmaps}` exist but
  are empty, so no geometry has ever been produced here, and the object-ADT fix in finding 6 is
  therefore **uncompiled and unproven against real tiles** — builds are not authorized for this
  branch.
- **VMAP archive enumeration and patch reading** (finding 7). The VMAP extractor still lists the
  WotLK archives and reads through libmpq, so it cannot open the build-15595 base sets or the
  `wow-update` chains at all. Finding 6 cannot be exercised until this is migrated; the in-tree
  `map_extractor` already carries the Cata-shaped pattern.
- **Terrain chunk discovery** (finding 4). Whether the selected 4.3.4 tiles still carry the legacy
  `MCIN` index the converter dereferences, or need the pin's direct chunk traversal, is unchecked.
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
