# Plan 32: reproducible Northshire collision and pathfinding (issue #134)

Status: **parser fixes, the extraction pipeline, the harness geometry path, the scenario's data pins, the
derived clear/blocked LOS pair and the derived chase route are done, and a geometry-enabled native run has
now loaded the artifacts and passed the `ground-movement` boundary** (see "Geometry-enabled native run"
below). The frozen tile has all three artifact sets,
verified against the server's own naming and header constants; `prepare --geometry` can stage them and
enable LOS/height/navmesh; and the pinned obstacle, the ground heights, the LOS pair and the chase route
(read under the walker's own filter, with the blocked line no walkable surface covers) are read out of
those artifacts rather than guessed (`apps/cata/check_collision_geometry.py`). **The native map-0
acceptance is partly proven**: the geometry is loaded by a server and a world-entry/walk boundary passes,
while the clear/blocked LOS pair, the pinned ground heights as a server query, the pinned-obstacle chase
and the walk/stop/logout/relog leg are still unrun. The tracking rows stay as they are — `assets:maps`,
`assets:mmaps`, `assets:vmaps`, `maps-collision` and `data-pipeline` blocked, `creature-ai` partial with
its Plan 32 pathfinding claim blocked.

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
server-grid block (`gx` 47..49, `gy` 31..33). The pinned obstacle is read out of the extracted collision
data rather than guessed, and is now pinned — see "Scenario facts read out of the artifacts" below; the
one remaining item is the clear/blocked LOS pair, which has to come from the model's triangles rather
than from its bounding box, for the reason recorded there.

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

## Pipeline run (validated 2026-10-16)

The manifest above is no longer a plan: the whole pipeline was run against the real build-15595
client, on tools built from this branch, with the outputs on a scratch volume outside Git
(`.../cata-runs/plan32-pipeline/`). Artifacts are all under one data root so `DataDir` can point at
it directly.

```sh
CLIENT=/mnt/.../Fun/.issue118-ssd-client
P=.../cata-runs/plan32-pipeline
DATA="$P/data"        # artifacts land here: maps/, vmaps/, mmaps/
RAW="$P/raw"; mkdir -p "$DATA/maps" "$DATA/vmaps" "$DATA/mmaps" "$RAW"
# mmaps-config.yaml copied from src/tools/mmaps_generator with dataDir: "$DATA"
sed "s|dataDir: \"./\"|dataDir: \"$DATA\"|" src/tools/mmaps_generator/mmaps-config.yaml > "$P/mmaps-config.yaml"

map_extractor   -e 1 -f 0 -i "$CLIENT" -o "$DATA"                 # -> $DATA/maps
(cd "$RAW" && vmap4_extractor -d "$CLIENT/Data")                  # -> $RAW/Buildings (+ dir_bin)
vmap4_assembler "$RAW/Buildings" "$DATA/vmaps"                    # -> $DATA/vmaps
mmaps_generator --config "$P/mmaps-config.yaml" 0 --tile 32,48    # -> $DATA/mmaps
```

Results: `maps/` 7,192 tiles (696M); `vmaps/` 17,130 files (884M) — 140 `.vmtree`, 3,618 `.vmtile`,
9,910 `.vmo`, 3,461 `.m2`, one `.dtree`; `mmaps/` 2 files (1.2M). All four tools exited 0 with no
errors, and the mmaps run reported `Discovering maps... found 143` / `Discovering tiles... found
7395` before building only tile `[32,48]` (5 s).

The frozen tile (server grid `gx=48, gy=32`) now has all three artifact files the server asks for,
and each was checked against the server's own constants rather than assumed:

| Server request | Source of the name | Artifact | Size | sha256 (first 16) |
| --- | --- | --- | --- | --- |
| `GridTerrainLoader::LoadMap` | `{id:03}{gx:02}{gy:02}.map` | `maps/0004832.map` | 174,776 | `bde52927bc69d027` |
| `getTileFileName` | `{id:03}_{gy:02}_{gx:02}.vmtile` | `vmaps/000_32_48.vmtile` | 65,376 | `c8a280573d20b9f9` |
| `VMapMgr2::getMapFileName` | `{id:03}.vmtree` | `vmaps/000.vmtree` | 4,049,233 | `49b3ba72c75aca7d` |
| `MMapMgr::LoadNavMesh` | `{id:03}.mmap` | `mmaps/000.mmap` | 28 | `001e3d118da45c85` |
| `MMapMgr::LoadMMapTile` | `{id:03}{x:02}{y:02}.mmtile` | `mmaps/0004832.mmtile` | 1,227,584 | `9456dd26e8d8665b` |

Two naming questions were settled by that table rather than by reading code:

- **VMAP tiles are not transposed.** The assembler writes `<mapID>_<x>_<y>` from `unpackTileID`,
  while `StaticMapTree::getTileFileName` writes `<mapID>_<tileY>_<tileX>` (its `tileX_tileY` variant
  is commented out). They agree because the extractor's ADT `x` is the server's `tileY`: the anchor
  needs `000_32_48.vmtile`, and that is what the assembler produced.
- **MMAP tiles use the server-grid order.** `--tile 32,48` sets `tileX=32, tileY=48`, which loads
  `0004832.map` and writes `0004832.mmtile` — the file `MMapMgr` opens for grid (48, 32).

Headers were checked too: both VMAP files start with `VMAP_4.8` (what `readChunk` expects), and the
mmtile header is `mmapMagic = 0x4d4d4150`, `dtVersion = 7` (`DT_NAVMESH_VERSION`), `mmapVersion =
20` (`MMAP_VERSION`, `MapDefines.h`) — a first read of the hexdump mis-assigned those fields and was
corrected by checking them against the struct, which is worth remembering when reading these files.

## Scenario facts read out of the artifacts (validated 2026-10-16)

The freeze deferred "the exact walked polyline and the pinned obstacle ... because both are read out of
the extracted collision data rather than guessed". Geometry exists now, so they were read out, with
`apps/cata/check_collision_geometry.py`, which parses the artifacts exactly as the server does and
asserts that they are structurally loadable and internally coherent.

Settling the conventions needed measurement, not a summary:

- **The vmap files store positions in the server's internal representation, not in world coordinates.**
  `VMapMgr2::convertPositionToInternalRep`, which every `MapCollisionData` vmap query goes through, maps
  `world -> (mid - x, mid - y, z)` with `mid = 0.5 * 64 * 533.33333 = 17066.667`. Read without inverting
  that, every object sits outside the 64x64 map grid and lands nowhere near the terrain. With the inverse
  applied, the median **|z - terrain|** residual over the 692 spawns of `000_32_48.vmtile` is **0.11 m**
  (signed median 0.00 m), using heights from `0004832.map` through the server's own
  `getHeightFromFloat` interpolation. That residual is what pins the convention. The check uses the
  magnitude, not the signed median: with signed values a spread of large positive and negative
  placement errors around zero would cancel and read as objects on the ground.
- **The two names for one tile disagree in order.** Map and mmap tiles are `{id}{gx:02}{gy:02}`; vmap
  tiles are `{id:03}_{gy:02}_{gx:02}`. The anchor's files are therefore `0004832.map`, `0004832.mmtile`
  and `000_32_48.vmtile`. A transposed vmap tile is a plausible mistake and is now detectable: 692 of the
  736 spawns (94%) fall in the grid the file is named for.

Pinned facts, in world coordinates, with heights from the map tile rather than from the server:

| Fact | Value |
| --- | --- |
| Anchor (harness fixture) | `(-8949.95, -132.493, 83.5312)`, server grid `(48, 32)` |
| Terrain height at the anchor | `82.630` — the fixture stands `+0.901` above it |
| Pinned obstacle | `Nsabbey.wmo`, `ID 72420`, in `000_32_48.vmtile` |
| Obstacle position | `(-8897.00, -178.30, 80.07)` — 70.0 m from the anchor, terrain there `80.29` |
| Obstacle extents | `x[-8950.03, -8831.42] y[-248.44, -129.90] z[80.10, 169.18]` |
| Other candidates within 200 m | `Abbeygate02.wmo` (175 m, residual -0.00), `Abbeygate01.wmo` (139 m, -0.50) |

**The pair is derived from the triangles, not the bound.** The stored bound is the model's computed
extent, and for a building WMO that extent includes its grounds: `Nsabbey.wmo`'s box contains the fixture
position itself, so a segment crossing it proves nothing about a query result. The pair was therefore
derived from the model's own triangles (`--los-pair`):

| Query | Endpoints (world) | Length | Result |
| --- | --- | --- | --- |
| Blocked | `(-8830.95, -198.96, 84.83)` to `(-8860.72, -187.23, 82.37)` | 32.09 m | hit at 18.01 m, group 6 tri 590 |
| Clear | `(-8775.13, -220.95, 87.29)` to `(-8804.90, -209.22, 84.91)` | 32.09 m | no hits |

Both endpoints sit at a unit's eye height — the terrain plus `Unit::GetCollisionHeight`'s default
(`2.03128`), which is what `WorldObject::IsWithinLOS` adds before `Map::isInLineOfSight` runs. The search
is the server's own: the segment is walked over **every** spawn, culled only by the segment/bound
intersection the tile tree itself uses — not by any distance heuristic, because a large model's bound can
cross a segment while its centre is nowhere near it — then tested against each model's triangles through
`ModelInstance`'s transform
(`iInvRot * (origin - iPos) * iInvScale` and `iInvRot * direction`), and the same
`G3D::Matrix3::fromEulerAnglesZYX` call that the extractor, the assembler and the server all use. A
blocked line is only accepted when its nearest hit is the pinned obstacle, and a clear line when it hits
nothing at all; the blocked line's hit is additionally required to intersect the obstacle's stored bound,
so the triangles and the artifact agree rather than merely agreeing with themselves, and the chosen clear
line is re-tested against every spawn before it is reported.

One honest limitation: the blocked line crosses the obstacle's stored bound and the clear line does not.
The sharper contrast — a clear line that stays *inside* those grounds, isolating the wall from the
surroundings — is not available at this wall, because a line shifted outward far enough to clear the
trees and fences has already left the extent. It is printed with the derivation so the acceptance is not
misled about what the clear case proves.

### The chase route around the obstacle (`--chase`)

The acceptance's other expectation is the chase, and it is derived from the same pair. `--chase` takes
the blocked line's two ends, finds the walkable polygon at each — among the polygons the walker's own
filter accepts, the containing one whose **surface under that point** is nearest the ground, so a roof
over the same column of air cannot win and a ramp's single polygon height cannot stand in for the
surface height where the creature stands — and walks the tile's own polygon graph. That graph is
`dtPoly.neis`, the neighbour index plus one (0 for none, `0x8000|side` for another tile) that
`dtNavMesh::connectIntLinks` reads, so it is data the tile already stores rather than a re-derivation of
it, and the creature's filter (`PathGenerator::CreateFilter`: `NAV_GROUND` here) is applied to the
polygons *and* to the links, because `dtNavMeshQuery::passFilter` drops every polygon whose flags do not
match — including the ones with flags 0. This tile has `6300` of `6312` polygons on `NAV_GROUND` and
`12` water-only ones, and no route may use the latter.

| Fact | Value |
| --- | --- |
| Start (blocked, first end) | `(-8830.95, -198.96)`, terrain `82.801`, polygon `3324`, surface `-0.27` m |
| Goal (blocked, second end) | `(-8860.72, -187.23)`, terrain `80.339`, polygon `2793`, surface `+2.79` m |
| Blocked straight line | `32.00 m`; the walk stops after `3` polygons at `11.66 m`, where `3066` has no neighbour |
| Route | `56` polygons, `261.45 m` of centre-to-centre steps (reported, not asserted) |
| Start's component | `3767 of 6300` walkable polygons |
| Rejected | polygon `2753` (start cannot reach it) and a point past the tile edge — no route for either |

**The detour is tested on the line, not inferred from the route's length.** A longer centre-to-centre
cost does not show that the corridor leaves the straight line: two large polygons can span a blocked
line while their centres are far apart, and a navmesh that covers the line would let the creature walk
straight through the obstacle. The line is therefore walked the way `dtNavMeshQuery::raycast` walks one:
the fixed segment is cast at the current polygon with `dtIntersectSegmentPoly2D`'s own edge test, the
edge it leaves by is followed through the tile's `dtPoly.neis` (the same map `connectIntLinks` builds
links from), and an off-mesh connection or a polygon the walker's filter rejects ends the walk. The
line's height against a global chord decides nothing — a continuous ramp is walkable the whole way up —
so the two surfaces are compared at each actual portal instead, and a step above `walkable_climb` stops
the walk because a walker cannot take it even though a ray can. Reaching the goal polygon means the
navmesh covers the line the LOS pair blocks, which is a tile that disagrees with its collision. Here the
walk stops after `3` ground polygons at `11.66 m` of the `32.00 m` line, where polygon `3066` has no
neighbour: the wall's carved gap, ahead of the obstacle's blocking hit at `18.01 m`.

```
$ python3 apps/cata/check_collision_geometry.py --data-root .../cata-runs/plan32-pipeline/data --chase
  [PASS] navmesh.tile_arrays: 6312 polygons and 11046 vertices walked out of the tile's own arrays
  [PASS] navmesh.creature_filter: 6300 of 6312 polygons pass the walker's flags 0x01
  [PASS] navmesh.ends_on_navmesh: start … polygon 3324, whose surface is -0.27 m above the terrain
                      there; goal … polygon 2793, whose surface is +2.79 m above the terrain there
  [PASS] navmesh.route: a 56-polygon route from polygon 3324 to 2793 through the walker's own links
  [PASS] navmesh.blocked_line_not_walkable: the walker's straight line over the blocked 32.09 m is
                      obstructed after 3 polygon(s): polygon 3066 edge 1 has no neighbour
  [PASS] navmesh.unreachable_rejected: no route is reported to a polygon the start cannot reach, and a
                      point outside the tile resolves to no polygon instead of to the nearest one …
  Chase path PASS.
```

Two honest limits are printed with the derivation, because each one bounds what the route means:

- **Single tile.** A step that would leave through a `DT_EXT_LINK` has no neighbour to follow here, so
  a route that exists only via an adjacent tile reads as unreachable. The tile is checked with the one
  `.mmtile` the pipeline produced.
- **The polygon's own surface, not the detail mesh.** The height under a point comes from the polygon's
  vertex fan, which is what a convex polygon's surface is; Detour can refine that with the detail mesh
  (`dtPolyDetail`), which this check does not parse.

`--self-check` covers the parse, the filter, the surface height and the line walk synthetically: a
shared edge routes; a detached polygon, a roof polygon and a polygon whose flags the walker's filter
rejects are not destinations; a sloped ramp's surface under the point beats the overlapping floor while
the ramp's centre height would not; two touching rectangles that span the blocked line and hold a 2.5x
longer route are still reported **walkable** because the walk crosses their shared portal, a 4 m carved
gap is reported obstructed, a **continuous ramp** whose middle edge is 10 m above its ends is walkable
(each portal step is 0 m, which a chord comparison would wrongly call a gap), a 5 m step at a portal is
obstructed, a neighbour the filter rejects ends the walk, and a crossing through **every** edge of a
polygon — including its closing edge, whose number must be wrapped or it collides with the absent-crossing
sentinel — is followed; and a vertex index, a neighbour index or a
vertex count outside this Detour's own arrays is rejected instead of followed. The native run is still
the judge.

The checker reports thirteen checks, one for each way the artifacts can fail to be loadable: the map
magic/version/build (15595); every declared map section the terrain loader opens (area, height, liquid,
holes) inside the file with its own magic, so a section offset beyond EOF fails instead of being
ignored; the height section in its flat, int16, int8 and float forms, with `MAP_HEIGHT_NO_HEIGHT`
treated as the constant field `getHeightFromFlat` returns rather than as missing terrain; the vmap
tile's exact record consumption, so a truncated tile fails instead of being half-read; the required
`{id}.vmtree` parsed as the BIH `StaticMapTree::InitMap` reads — bounds, entry count, object count,
then `GOBJ` — with every tile reference checked against that object count, because `LoadMapTile` skips
a reference at or above `iNTreeValues` and that object then has no collision;
each spawn's model resolving as `<name>.vmo`, which is the only name `WorldModelStore` opens; the
tile/contents agreement; the on-terrain residual; one `dtNavMeshParams` with usable
tileWidth/tileHeight/maxTiles; the mmtile header (`MMAP_MAGIC`, `dtVersion 7`, `mmapVersion 20`) with
`header + size == file length`; the reported obstacles' `.vmo` files parsing, carrying collision triangles, and having
every triangle
index inside its group's vertex array (a model can satisfy the byte count while the indices are
unusable, and the server indexes the vertex array directly);
and the `dtMeshHeader` inside the declared payload — magic, version,
its tile index recomputed from the params the way `MapBuilder` writes it, and `dtNavMesh::addTile`'s
own aligned buffer arithmetic with **this tree's** element sizes, so the counts must describe arrays
that fit in the declared payload. Those sizes are not the upstream defaults: the vendored Detour
enables `DT_POLYREF64` (it is in `deps/recastnavigation/recastnavigation.diff`), which makes
`dtPolyRef` eight bytes and `dtLink` sixteen, and `dtMeshHeader` is 100 bytes, ending in
`bvQuantFactor`. For the anchor tile the counts need **exactly** the 1,227,528 declared bytes, which
is what confirms the arithmetic rather than merely fitting inside it. An empty, truncated or arbitrary
payload therefore cannot pass. The optional flight-bounds arrays the
height loader reads (`MAP_HEIGHT_HAS_FLIGHT_BOUNDS`, two arrays of nine int16) are checked too,
including on flat terrain. Against the produced set it passes:

```
$ python3 apps/cata/check_collision_geometry.py --data-root .../cata-runs/plan32-pipeline/data
  [PASS] map.sections: area@44(520) height@564(132152) liquid@132716(41548) holes@174264(512)
  [PASS] vmap.tree: 000.vmtree carries b'VMAP_4.8', the tiled flag 1, a BIH of 814683 entries and
                      197613 objects, then GOBJ (4049233/4049233 bytes)
  [PASS] vmap.tree_references: every tile reference is within the tree's 197613 objects
  [PASS] vmap.objects_on_terrain: median |z - terrain| 0.11 m over 692 objects (signed median -0.00)
  [PASS] mmap.payload: dtMeshHeader inside the declared payload: magic 0x444e4156 version 7,
                      6312 polygons, 11046 vertices, tile 13,13 consistent with the params; the
                      counts need 1227528 of the 1227528 declared bytes (bvQuantFactor 3.750)
  [GAP ] vmap.models: 1 of 117 referenced models do not resolve (Innchandelier.m2)
  anchor terrain height 82.630 (position z 83.531, stand height +0.901)
  Collision geometry PASS.
```

One gap is reported rather than swallowed: `Innchandelier.m2`, referenced by this tile, has no
`<name>.vmo`, which is the only name `WorldModelStore::AcquireModelInstance` opens, so the server will
log a model load failure and that object has no collision. The scenario's own obstacles all resolve, so
it does not affect the frozen route — but it is exactly the class of missing prerequisite the run must
not discover by accident. The checker fails outright when a scaffold the scenario *does* depend on is
absent or unusable (no `{id}.vmtree`, a tree that cannot be parsed, a tile reference outside its
object count, no obstacle model, a payload its own counts do not fit).

It rejects what should be rejected, and one of those rejections matters in practice: an empty data root
fails on the missing tile, and **the pinned reference tree's own data directory fails**, because that set
is in TrinityCore's `ModelSpawn` layout (`uint8` flags) while this server reads `uint32`. The two are not
interchangeable, so the harness's documented `--data-root` example — which points at the reference tree —
must not be used for a geometry-enabled run; the extraction root must be. Nothing in the scenario may
substitute one for the other.

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
   `area_ids[mcnk->iy][mcnk->ix]` use (`ADT_file::getMCNK`). `ConvertADT` rejects a file whose cells
   are missing, duplicated or malformed instead of dereferencing a header slot, and the walk
   bounds-checks every declared chunk against the file and validates the sub-chunks the conversion
   reads — MCVT always, and the legacy MCLQ only when `sizeMCLQ > 8`, because Cataclysm leaves
   `offsMCLQ` at the end of the cell. Sub-chunk offsets are compared against the MCNK's full extent
   (they are relative to the chunk start, while the declared size counts only the payload), and a
   consumed MCLQ must provide the flag and liquid arrays the converter reads.
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

## Geometry-enabled native run (2026-10-16)

The server/game targets were built from merged `master` (`22219fd4b`, Release), one owned generation was
prepared with `--geometry` against the root above, and the mode below ran against the real build-15595
client.

| Fact | Value |
| --- | --- |
| Mode | `ground-movement` |
| Staging | `MoveMaps.Enable=1`, `vmap.enableLOS=1`, `vmap.enableHeight=1`; the server logged `VMap support included. LineOfSight:true, getHeight:true, indoorCheck:true PetLOS:true` |
| Verdict | harness `accepted` / `ground_movement_pass` (with #150's world-entry-landing fix) |
| Movement | the six key pairs accepted, deltas action-appropriate, stable after the final stop |
| Height cross-check | settled live z `82.4957` at `(-8945.393, -132.482)` against the artifact-derived terrain height `82.495` there (stand height +0.001 m) |
| Navmesh identity | the staged mmap tile is the one the checker reads: 6312 polygons, 11046 vertices |
| Client render | a `gnome-screenshot -w` capture of the owned window shows the world rendered; `window.xwd` cannot show it because DXVK's Vulkan swapchain is not readable by X |

The harness root, generation, logs and raw captures are outside Git (`.../cata-runs/plan32-acceptance/`,
its own manifest root because the older Plan 7 root sits below the harness's 25 GiB floor; its database
cache is built, so later prepares there are fast). `prepare` copies the four data sets into the
generation, so the staged-and-loaded claim covers exactly those bytes. The MySQL development files this
build needs are `libmysqlclient-dev` 8.0.46 at `/usr/include/mysql` and
`/usr/lib/x86_64-linux-gnu/libmysqlclient.so` on this host — the `/tmp/mysql-dev` staging tree in the
older build note does not survive a reboot.

**What the stages above do not prove.** The server's height query is exercised through movement
acceptance — the server accepts the landing at the pinned point and its Z matches the artifact-derived
seed within 0.1 m — not through an explicit height API probe. No client archive provenance was
recorded for the staged artifacts. A later `--geometry` run must rebuild `worldserver`/`authserver`
first — `prepare` refuses binaries whose `--version` does not report the current HEAD.

## Geometry-mode acceptance (2026-10-18)

`--mode plan32-geometry` drives one pinned stage per generation, selected and frozen through
`PLAN32_STAGE`. Four generations reached `accepted` on the 2026-10-18 branch (`f01ef8c7e`, then
`25e319c7e` for the session signals):

| Stage | Generation | Verdict | Governing evidence |
| --- | --- | --- | --- |
| blocked | 6 (re-verified offline under the strengthened predicates) | `plan32_geometry_blocked_pass` | two `SMSG_CAST_FAILED` result 49 at the client's counters 1 and 2, no `SPELL_GO`; both cast rays break on `Nsabbey.wmo` at eye and low height, 18.01 m into the 32.09 m line |
| clear | 7 (re-verified offline) | `plan32_geometry_clear_pass` | `SMSG_SPELL_GO` at counters 1 and 2, no failure; both rays whole at both heights for both casts |
| chase | 13 | `plan32_geometry_chase_pass` | one `SPELL_GO`, then the followed fixture's own splines around the courtyard wall: 7 points, detour 2.02 (≥ 1.25), arrival 1.5 m, gap 4.13 m (≤ 10), 36 melee swings; pull ray whole while the fixture's straight line to the player's end point breaks on `Nsabbey.wmo` at both heights, and the staged mmap routes it 28.64 m against 8.49 m straight |
| relog | 15 | `plan32_geometry_relog_pass` | walk tap accepted to a stop at `(-8779.044, -219.407, 85.008)`, exactly one `SMSG_LOGOUT_COMPLETE`, then the second session's `MSG_MOVE_FALL_LAND` at that identical position; both login requests and callbacks name the seeded character, and the seeded login point is 7.9 m away |

Each generation is its own harness run (`prepare --geometry`, `run`, `verify`) with the staged
vmaps/mmaps/maps/dbc copied into it and hashed. What the verdicts rest on, beyond the stage rows:

- **The run's own artifacts.** `plan32_geometry_facts` records the staged vmap and mmap content
  hashes, the worldserver config's `MoveMaps.Enable`, `vmap.enableLOS` and `vmap.enableHeight`, and
  the server's `VMap support included. LineOfSight:true, getHeight:true, indoorCheck:true PetLOS:true`
  line; verify requires each to match that generation's staged inputs, and prepare refuses the mode
  without `--geometry`.
- **The selected unit's identity.** The macro's selection is bound to the fixture's own create block:
  entry 361 in the field and in the GUID bits, `HIGHGUID_UNIT`, and a position within 0.5 m of the
  seeded spawn, advanced by that unit's own spline packets at each cast.
- **The cast correlation.** Every request's client counter has to come back on its own outcome for the
  same spell at the agreed target, with no cancel between request and answer and the exact expected
  counter list; a synthetic tamper check covers the predicate.
- **The rays, not the flag.** Cast rays are re-derived from the staged vmaps between the two units'
  cast-time positions at eye height and at a low point on the fixture. The blocked stage must break on
  the pinned obstacle at both heights; the clear and chase stages must stay whole at both.
- **Movement ownership and pins.** The accepted ground-movement samples, owned window and endpoint,
  the stability hold and the character-row identity are the harness's usual checks; the run's settle Z
  is compared with the pinned seed Z within 0.1 m, and the seeded points sit on the derived lines
  within 0.25 m (6 mm perpendicular on the clear line in the relog run).
- **The chase and relog stage claims.** The chase detour is measured from the fixture's own spline
  points and stops at the move that arrives at the player, so an evading fixture's walk home cannot
  inflate it; the staged route and the broken straight line carry the "must path around" burden. The
  relog verdict splits the movement samples at the second world-entry landing and requires it to agree
  with the walk's stop within 0.5 m while both stay at least 1 m from the seeded login point, so the
  position came from the row the server saved at logout rather than a reseed.

Failures kept in the record: generations 8 and 9 used a corner that the navmesh calls walkable while
the vmaps call wall, so the player's own walk stopped against it; the accepted corner is validated
against both. Generation 11 typed its `/target` macro before any movement and lost the first
keypresses to the window-focus handover — its own screenshot shows "arget saytr" as a say — so the
chase automation now retries until the server logs the selection it produces. Generation 14 proved the
relog persistence and then failed on a wrong success condition: the server's time-sync liveness line
is not per session, so the second login is identified by its world-entry landing instead.

## Blockers (each needs explicit authorization or input)

- **Build authorization — satisfied for the tools and the server.** The four map tools build from this
  branch, so the parser fixes are compiled rather than inspected, and every extraction run above used
  that binary. `worldserver`/`authserver` also build from merged `master` and are what the
  2026-10-16 geometry run used.
- **Harness data staging and geometry path — exercised by a run.** `prepare --geometry` requires a
  non-empty `vmaps/` and `mmaps/` under `--data-root`, stages them with the same content-hash check used
  for `dbc/` and `maps/`, and writes `MoveMaps.Enable`, `vmap.enableLOS` and `vmap.enableHeight` as `1`;
  without the flag every accepted mode behaves exactly as before. The 2026-10-16 run proves the staging
  for map 0 grid 48,32 is loadable, not merely complete: the server came up with LOS, height and
  `MoveMaps` enabled and the client walked in the world. Other grids are still only complete, not proven
  loadable.
- **Native acceptance — satisfied 2026-10-18.** The clear and blocked LOS pair, the pinned heights as
  accepted movement, the pinned-obstacle chase and the walk/stop/logout/relog leg are recorded in
  "Geometry-mode acceptance" above. The client archive provenance below stays open.
- **Client archives provenance** for all three artifact sets; the runs above used the on-disk client
  copy without recording its provenance.

## Acceptance plan for the remaining stages (pinned before their implementation)

Status: executed 2026-10-18 — see "Geometry-mode acceptance" above; the plan is kept as the pinned
pre-implementation record.

Before any run, `apps/cata/check_collision_geometry.py` must pass against the exact root `--data-root`
stages (it fails on missing, truncated, misnamed or foreign-toolchain artifacts). Issue #134's acceptance
then requires, with the selected tiles loaded and server LOS, height and navmesh use enabled: clear vs
blocked LOS against the pinned obstacle, correct ground heights at pinned points, one normal creature
chase that paths around the obstacle, and a normal walk/stop/logout/relog at a legal position. The
2026-10-16 run covers the loading and walk boundaries; the rest needs the client driven to pinned states,
and each stage's evidence is pinned here before a mode that drives it is written.

- **LOS pair.** The endpoint coordinates are query inputs, not places the character has to stand. Pin the
  API, both endpoints, the phase, the eye-height offsets and a tolerance, and expect the derived results:
  the blocked `32.09 m` line hits `Nsabbey.wmo` group 6 triangle 590 at `18.01 m`, the clear line has no
  hits. The observation has to reach the server's own query rather than the client's prediction, and a
  normal spell cast whose LOS requirement the server enforces — `SMSG_CAST_FAILED` for the blocked line
  against `SMSG_SPELL_GO` for the clear one — is a mechanism the harness already drives.
- **Pinned ground heights.** `Map::GetHeight` selects between the grid height and the vmap surface, so a
  terrain value is not the expected answer where a model supplies the floor, and the goal point's
  `+2.79 m` navmesh-over-terrain difference has to be settled before its Z is pinned. The LOS pair's
  eye-height endpoints are not spawn heights. Pin the query, the coordinates and a tolerance per point.
- **Pinned obstacle chase.** A successful path query is not evidence: `PathGenerator::CalculatePath`
  returns success with `PATHFIND_NOT_USING_PATH` whenever the navmesh, the query or either endpoint tile
  is unavailable, so a shortcut around a missing mmap looks like a path. The observation must reject that
  type, forced shortcuts and teleports. It starts from a normal pull while LOS is clear (proximity
  acquisition itself checks LOS, so a creature seeded behind the wall can never pull), and then the
  creature's own path type plus timestamped server-side positions must continue to melee range within a
  declared tolerance, with no premature evade. The checker's exact 56-polygon route is *not* the
  expectation: it is single-tile, carries no detail mesh, and a chase stops at melee range.
- **Walk/stop/logout/relog** at a legal position under geometry, with the position persisted across the
  relog.
- **Setup constraints.** This build is Release with `SCRIPTS=none`, so the Commands scripts are disabled
  and a GM grant alone cannot supply `.tele` or `.npc add`; a GM player as chase target also sets
  `forceDest` in `TargetedMovementGenerator`, which is exactly the forced shortcut the chase must
  exclude. A seeded character row sets the login position only — it does not move a live player — so the
  remaining states have to be reached by ordinary movement, and the row must not be reseeded between the
  final logout and the relog.
- Deterministic rejection cases run on isolated inputs rather than by altering artifacts beneath a
  running server: wrong-build/version headers, truncated or missing selected tiles/models, the
  clear/blocked query expectations and unreachable path handling (`--chase` reports the rejected
  disconnected polygon and off-tile point). Acceptance must fail, not silently pass, when geometry is
  unavailable or disabled.

## Not covered by this record

Whole-continent extraction, flying/swimming, transports, cross-map transfer, terrain swaps and
all-zone navigation. This freezes one map-0 route only.
