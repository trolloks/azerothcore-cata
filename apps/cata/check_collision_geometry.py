"""Validate produced Cata collision geometry against the server's own readers and pin the scenario.

This is the pre-flight for the Plan 32 native acceptance, not a substitute for it. It reads the
artifacts the server will read, using the server's own formats, naming and conversions, and it
proves that they are structurally loadable and internally coherent: the tile a file is named for
is the tile its contents describe, and the objects sit on the terrain. It also prints the frozen
scenario facts (the anchor's ground height, the pinned obstacle) that the run must reproduce.

It never claims that a server loaded the artifacts, that a query matched, or that a path exists.
Those are the acceptance's job; this only makes a silently degraded run detectable in advance.

Layouts are taken from this tree, not from a summary:
  * maps/{id:03}{gx:02}{gy:02}.map        GridTerrainLoader / GridTerrainData::LoadHeightData
  * vmaps/{id:03}_{gy:02}_{gx:02}.vmtile  StaticMapTree::LoadMapTile / ModelSpawn::readFromFile
  * mmaps/{id:03}{gx:02}{gy:02}.mmtile    MMapMgr::LoadTile / MmapTileHeader
  * mmaps/{id:03}.mmap                    MMapMgr::LoadNavMesh (a single dtNavMeshParams)
Grid coordinates follow the server's ComputeGridCoord, not ComputeGridCoordSimple: gx = 32 - x/533.33
and gy = 32 - y/533.33, and vmap tile files are named with {gy}_{gx} while map and mmap tiles use
{gx}{gy}. The two orders disagree, which is exactly what the tile/contents agreement check detects.
"""
import argparse
import json
import struct
import sys
from pathlib import Path

CLIENT_BUILD = 15595
# MapMagic.asUInt in GridTerrainData.h: the loader compares the uint32, not the characters.
MAP_MAGIC = 0x5350414D
MAP_VERSION = 10
HEIGHT_MAGIC = b"MHGT"
MAP_HEIGHT_NO_HEIGHT = 0x0001
MAP_HEIGHT_AS_INT16 = 0x0002
MAP_HEIGHT_AS_INT8 = 0x0004
VMAP_MAGIC = b"VMAP_4.8"
MOD_HAS_BOUND = 1 << 2
MMAP_MAGIC = 0x4D4D4150
DT_NAVMESH_VERSION = 7
MMAP_VERSION = 20
SIZE_OF_GRIDS = 533.33333
MAP_RESOLUTION = 128
MAX_NUMBER_OF_GRIDS = 64
# VMapMgr2::convertPositionToInternalRep: the vmap files are in the internal representation, and
# every query converts into it, so reading the artifacts back out needs the inverse.
MID = 0.5 * MAX_NUMBER_OF_GRIDS * SIZE_OF_GRIDS
MMAP_TILE_HEADER_SIZE = 56
DT_NAVMESH_PARAMS_SIZE = 28
VMAP_TILE_AGREEMENT_MIN = 0.80
ON_GROUND_MEDIAN_MAX = 5.0


def grid_coord(x, y):
    """The server's ComputeGridCoord, clamped to the 64x64 map (GridDefines.h)."""
    return (max(0, min(MAX_NUMBER_OF_GRIDS - 1, int(32 - x / SIZE_OF_GRIDS))),
            max(0, min(MAX_NUMBER_OF_GRIDS - 1, int(32 - y / SIZE_OF_GRIDS))))


def internal_to_world(x, y, z):
    """Inverse of VMapMgr2::convertPositionToInternalRep (mid - world, so world = mid - internal)."""
    return (MID - x, MID - y, z)


class MapTile:
    """A loaded maps/*.map tile: the headers the loader validates and the terrain heights."""

    def __init__(self, data, path):
        self.path = path
        self.size = len(data)
        header = struct.unpack_from("<11I", data, 0)
        (self.map_magic, self.map_version, self.build, _, area_size, height_offset, _, _, _, _, _) = header
        if self.map_magic != MAP_MAGIC:
            raise ValueError(f"{path}: not a map tile (magic {self.map_magic:#010x})")
        self.has_area = area_size > 8
        self.grid_height = 0.0
        self._kind = None
        self._v9 = None
        self._v8 = None
        self._multiplier = 1.0
        if height_offset + 16 > len(data):
            raise ValueError(f"{path}: height section offset {height_offset} is beyond the file")
        fourcc, flags, grid_height, grid_max = struct.unpack_from("<4sIff", data, height_offset)
        if fourcc != HEIGHT_MAGIC:
            raise ValueError(f"{path}: height section is {fourcc!r}, not {HEIGHT_MAGIC!r}")
        self.height_flags = flags
        self.grid_height = grid_height
        self.grid_max_height = grid_max
        if flags & MAP_HEIGHT_NO_HEIGHT:
            return
        offset = height_offset + 16
        if flags & MAP_HEIGHT_AS_INT16:
            count9, count8, fmt, step = 129 * 129, 128 * 128, "H", 2
            self._multiplier = (grid_max - grid_height) / 65535.0
            self._kind = "int16"
        elif flags & MAP_HEIGHT_AS_INT8:
            count9, count8, fmt, step = 129 * 129, 128 * 128, "B", 1
            self._multiplier = (grid_max - grid_height) / 255.0
            self._kind = "int8"
        else:
            count9, count8, fmt, step = 129 * 129, 128 * 128, "f", 4
            self._kind = "float"
        needed = offset + (count9 + count8) * step
        if needed > len(data):
            raise ValueError(f"{path}: height data needs {needed} bytes, file has {len(data)}")
        self._v9 = struct.unpack_from(f"<{count9}{fmt}", data, offset)
        self._v8 = struct.unpack_from(f"<{count8}{fmt}", data, offset + count9 * step)
        # Float heights are absolute; the quantised forms are relative to gridHeight.
        self._base = 0.0 if self._kind == "float" else grid_height

    def height(self, x, y):
        """GridTerrainData::getHeightFromFloat, the triangle interpolation the server uses."""
        if self._v9 is None:
            return None
        gx = MAP_RESOLUTION * (32 - x / SIZE_OF_GRIDS)
        gy = MAP_RESOLUTION * (32 - y / SIZE_OF_GRIDS)
        ix, iy = int(gx), int(gy)
        fx, fy = gx - ix, gy - iy
        ix &= MAP_RESOLUTION - 1
        iy &= MAP_RESOLUTION - 1
        mul, base = self._multiplier, self._base
        h1 = self._v9[ix * 129 + iy] * mul + base
        h2 = self._v9[(ix + 1) * 129 + iy] * mul + base
        h3 = self._v9[ix * 129 + iy + 1] * mul + base
        h4 = self._v9[(ix + 1) * 129 + iy + 1] * mul + base
        h5 = 2 * (self._v8[ix * 128 + iy] * mul + base)
        if fx + fy < 1:
            a, b, c = (h2 - h1, h5 - h1 - h2, h1) if fx > fy else (h5 - h1 - h3, h3 - h1, h1)
        else:
            a, b, c = (h2 + h4 - h5, h4 - h2, h5 - h4) if fx > fy else (h4 - h3, h3 + h4 - h5, h5 - h4)
        return a * fx + b * fy + c


def read_vmap_tile(data):
    """Parse a .vmtile the way StaticMapTree::LoadMapTile and ModelSpawn::readFromFile do."""
    if data[0:8] != VMAP_MAGIC:
        raise ValueError(f"vmap tile magic is {data[0:8]!r}, expected {VMAP_MAGIC!r}")
    count = struct.unpack_from("<I", data, 8)[0]
    offset = 12
    spawns = []
    for index in range(count):
        flags, adt_id, spawn_id = struct.unpack_from("<IHI", data, offset)
        offset += 10
        pos = struct.unpack_from("<3f", data, offset)
        offset += 12
        rotation = struct.unpack_from("<3f", data, offset)
        offset += 12
        scale = struct.unpack_from("<f", data, offset)[0]
        offset += 4
        bound = None
        if flags & MOD_HAS_BOUND:
            low = struct.unpack_from("<3f", data, offset)
            offset += 12
            high = struct.unpack_from("<3f", data, offset)
            offset += 12
            # The mirroring flips both horizontal axes, so renormalise the box.
            bound = ((min(low[0], high[0]), min(low[1], high[1]), min(low[2], high[2])),
                     (max(low[0], high[0]), max(low[1], high[1]), max(low[2], high[2])))
        name_length = struct.unpack_from("<I", data, offset)[0]
        offset += 4
        if name_length > 500 or offset + name_length + 4 > len(data):
            raise ValueError(
                f"vmap tile spawn {index}: implausible name length {name_length} - the tile is not in "
                f"this tree's ModelSpawn layout (a set from another toolchain, such as the pinned "
                f"reference tree, cannot be read by this server)")
        name = data[offset:offset + name_length].decode("latin-1").rstrip("\x00")
        offset += name_length
        offset += 4  # the referenced tree element
        world = internal_to_world(*pos)
        if bound:
            world_bounds = (internal_to_world(*bound[0]), internal_to_world(*bound[1]))
            bound = ((min(world_bounds[0][0], world_bounds[1][0]), min(world_bounds[0][1], world_bounds[1][1]),
                      bound[0][2]),
                     (max(world_bounds[0][0], world_bounds[1][0]), max(world_bounds[0][1], world_bounds[1][1]),
                      bound[1][2]))
        spawns.append({"index": index, "flags": flags, "adt_id": adt_id, "id": spawn_id,
                       "internal": pos, "world": world, "rotation": rotation, "scale": scale,
                       "bound": bound, "name": name})
    return spawns, offset


class MmapFiles:
    """The two mmaps artifacts, checked the way MMapMgr loads them."""

    def __init__(self, par_path, tile_path):
        self.par_path = par_path
        self.tile_path = tile_path
        self.params_size = len(par_path.read_bytes())
        data = tile_path.read_bytes()
        magic, dt_version, mmap_version, size = struct.unpack_from("<4I", data, 0)
        self.magic = magic
        self.dt_version = dt_version
        self.mmap_version = mmap_version
        self.declared_size = size
        self.actual_size = len(data)
        self.header_size = MMAP_TILE_HEADER_SIZE


def median(values):
    ordered = sorted(values)
    return ordered[len(ordered) // 2] if ordered else float("nan")


def check(data_root, map_id=0, anchor=(-8949.95, -132.493, 83.5312), neighbours=1, radius=200.0):
    data_root = Path(data_root)
    anchor_x, anchor_y, anchor_z = anchor
    gx, gy = grid_coord(anchor_x, anchor_y)
    checks = []

    def record(name, ok, detail):
        checks.append({"check": name, "pass": bool(ok), "detail": detail})
        return ok

    map_path = data_root / "maps" / f"{map_id:03d}{gx:02d}{gy:02d}.map"
    vmap_path = data_root / "vmaps" / f"{map_id:03d}_{gy:02d}_{gx:02d}.vmtile"
    par_path = data_root / "mmaps" / f"{map_id:03d}.mmap"
    mtile_path = data_root / "mmaps" / f"{map_id:03d}{gx:02d}{gy:02d}.mmtile"
    for path in (map_path, vmap_path, par_path, mtile_path):
        if not path.is_file():
            record(f"present:{path.name}", False, f"{path} is missing")
            return {"data_root": str(data_root), "map": map_id, "anchor": list(anchor),
                    "grid": [gx, gy], "checks": checks, "facts": {}, "pass": False}

    tile = MapTile(map_path.read_bytes(), str(map_path))
    record("map.headers", tile.map_magic == MAP_MAGIC and tile.map_version == MAP_VERSION
           and tile.build == CLIENT_BUILD,
           f"magic {tile.map_magic:#010x} version {tile.map_version} build {tile.build} "
           f"(expected {MAP_MAGIC:#010x} {MAP_VERSION} {CLIENT_BUILD})")
    record("map.height_section", tile._kind is not None,
           f"MHGT flags {tile.height_flags:#06x} heights {tile._kind} "
           f"{tile.grid_height:.2f}..{tile.grid_max_height:.2f}")

    spawns, consumed = read_vmap_tile(vmap_path.read_bytes())
    record("vmap.structure", consumed == vmap_path.stat().st_size,
           f"{len(spawns)} spawns consumed {consumed} of {vmap_path.stat().st_size} bytes")

    def terrain(x, y):
        tile_gx, tile_gy = grid_coord(x, y)
        if (tile_gx, tile_gy) == (gx, gy):
            return tile.height(x, y)
        path = data_root / "maps" / f"{map_id:03d}{tile_gx:02d}{tile_gy:02d}.map"
        if not path.is_file():
            return None
        return MapTile(path.read_bytes(), str(path)).height(x, y)

    in_tile, on_ground, residuals = 0, 0, []
    for spawn in spawns:
        x, y, z = spawn["world"]
        if grid_coord(x, y) != (gx, gy):
            continue
        in_tile += 1
        height = terrain(x, y)
        if height is None:
            continue
        residual = z - height
        residuals.append(residual)
        if abs(residual) <= ON_GROUND_MEDIAN_MAX:
            on_ground += 1
    agreement = in_tile / len(spawns) if spawns else 0.0
    record("vmap.tile_matches_contents", agreement >= VMAP_TILE_AGREEMENT_MIN,
           f"{in_tile}/{len(spawns)} spawns ({agreement:.1%}) fall in the grid {gx},{gy} the file is "
           f"named for (the vmap file name is <id>_<gy>_<gx>, so a transposed or mismatched tile "
           f"shows up here)")
    ground_median = median(residuals)
    record("vmap.objects_on_terrain", abs(ground_median) <= ON_GROUND_MEDIAN_MAX and on_ground > 0,
           f"median |z - terrain| residual {ground_median:+.2f} over {len(residuals)} objects in the "
           f"tile, {on_ground} within {ON_GROUND_MEDIAN_MAX:.0f} m; a wrong frame or a missing map "
           f"tile moves this by thousands of metres")

    mmap = MmapFiles(par_path, mtile_path)
    record("mmap.params", mmap.params_size == DT_NAVMESH_PARAMS_SIZE,
           f"{par_path.name} is {mmap.params_size} bytes (a single dtNavMeshParams is "
           f"{DT_NAVMESH_PARAMS_SIZE})")
    record("mmap.tile_headers",
           mmap.magic == MMAP_MAGIC and mmap.dt_version == DT_NAVMESH_VERSION
           and mmap.mmap_version == MMAP_VERSION
           and mmap.header_size + mmap.declared_size == mmap.actual_size,
           f"magic {mmap.magic:#010x} dtVersion {mmap.dt_version} mmapVersion {mmap.mmap_version} "
           f"header+size {mmap.header_size}+{mmap.declared_size} vs file {mmap.actual_size}")

    anchor_height = terrain(anchor_x, anchor_y)
    obstacles = []
    if neighbours:
        candidates = []
        for dx in range(-neighbours, neighbours + 1):
            for dy in range(-neighbours, neighbours + 1):
                path = data_root / "vmaps" / f"{map_id:03d}_{gy + dy:02d}_{gx + dx:02d}.vmtile"
                if not path.is_file():
                    continue
                extra, _ = read_vmap_tile(path.read_bytes())
                candidates.extend(extra)
        buildings = {}
        for spawn in candidates:
            if not spawn["bound"] or spawn["flags"] & 1:  # MOD_M2: doodads, not buildings
                continue
            spawn["distance"] = ((spawn["world"][0] - anchor_x) ** 2
                                 + (spawn["world"][1] - anchor_y) ** 2) ** 0.5
            if spawn["distance"] > radius:
                continue
            low, high = spawn["bound"]
            spawn["footprint"] = (high[0] - low[0]) * (high[1] - low[1])
            # The same WMO is referenced by every tile its bound reaches, so dedupe on identity.
            buildings.setdefault((spawn["id"], spawn["name"]), spawn)
        buildings = list(buildings.values())
        # The largest buildings bound most of the walkable area; the nearest one is the first thing a
        # route out of the anchor meets. Both are reported so the scenario can pin one deliberately.
        selected = sorted(buildings, key=lambda s: s["footprint"], reverse=True)[:3]
        selected += sorted(buildings, key=lambda s: s["distance"])[:1]
        seen = set()
        selected = [s for s in selected if not (s["id"], s["name"]) in seen and not seen.add((s["id"], s["name"]))]
        for spawn in selected:
            height = terrain(spawn["world"][0], spawn["world"][1])
            obstacles.append({"name": spawn["name"], "id": spawn["id"], "tile": [gy, gx],
                              "world_position": list(spawn["world"]),
                              "distance_from_anchor": spawn["distance"],
                              "footprint": spawn["footprint"],
                              "world_bounds": [list(spawn["bound"][0]), list(spawn["bound"][1])],
                              "terrain_at_position": height,
                              "residual": None if height is None else spawn["world"][2] - height})
    facts = {
        "anchor": {"position": [anchor_x, anchor_y, anchor_z], "terrain_height": anchor_height,
                   "stand_height": None if anchor_height is None else anchor_z - anchor_height},
        "vmap_tile": vmap_path.name, "vmap_spawns": len(spawns), "spawns_in_tile": in_tile,
        "on_ground_fraction": None if not residuals else on_ground / len(residuals),
        "median_residual": ground_median, "obstacles": obstacles,
        "bounds_do_not_decide_los": ("The obstacle bounds are the computed model extents, and for a "
                                     "building WMO that includes its grounds; a segment crossing a bound "
                                     "therefore does not prove a blocked query. Derive the clear and "
                                     "blocked pair from the model's own triangles, not from these boxes."),
    }
    return {"data_root": str(data_root), "map": map_id, "anchor": list(anchor), "grid": [gx, gy],
            "checks": checks, "facts": facts, "pass": all(c["pass"] for c in checks)}


def render(report):
    lines = [f"collision geometry: data-root {report['data_root']} map {report['map']} "
             f"grid {report['grid'][0]},{report['grid'][1]}"]
    for check in report["checks"]:
        lines.append(f"  [{'PASS' if check['pass'] else 'FAIL'}] {check['check']}: {check['detail']}")
    facts = report["facts"]
    if facts:
        anchor = facts["anchor"]
        lines.append(f"  anchor terrain height {anchor['terrain_height']:.3f} "
                     f"(position z {anchor['position'][2]:.3f}, stand height {anchor['stand_height']:+.3f})")
        lines.append(f"  {facts['vmap_spawns']} vmap spawns, {facts['spawns_in_tile']} in the anchor tile, "
                     f"median residual {facts['median_residual']:+.2f} m")
        for obstacle in facts["obstacles"]:
            low, high = obstacle["world_bounds"]
            lines.append(f"  obstacle {obstacle['name']} id {obstacle['id']} "
                         f"{obstacle['distance_from_anchor']:6.1f} m from the anchor, footprint "
                         f"{obstacle['footprint']:.0f} m2, position "
                         f"({obstacle['world_position'][0]:.2f},{obstacle['world_position'][1]:.2f},"
                         f"{obstacle['world_position'][2]:.2f}) residual {obstacle['residual']:+.2f} m")
            lines.append(f"    world bounds x[{low[0]:.2f},{high[0]:.2f}] y[{low[1]:.2f},{high[1]:.2f}] "
                         f"z[{low[2]:.2f},{high[2]:.2f}]")
    lines.append(f"Collision geometry {'PASS' if report['pass'] else 'FAIL'}. This does not prove a "
                 f"server loaded it, that any query matched, or that a path exists.")
    return "\n".join(lines)


def self_check():
    """Synthetic cases for the parsing and the height maths; no artifacts required."""
    # A planar height field h = x + y over the whole tile grid must interpolate back exactly.
    v9 = [float(x + y) for x in range(129) for y in range(129)]
    v8 = [float((x + 0.5) + (y + 0.5)) for x in range(128) for y in range(128)]
    body = struct.pack("<4sIff", HEIGHT_MAGIC, 0, 0.0, 300.0)
    body += struct.pack(f"<{129 * 129}f", *v9) + struct.pack(f"<{128 * 128}f", *v8)
    header = struct.pack("<11I", 0x5350414D, MAP_VERSION, CLIENT_BUILD, 0, 0, 64, len(body), 0, 0, 0, 0)
    buffer = header + b"\0" * (64 - len(header)) + body
    tile = MapTile(buffer, "<synthetic>")
    assert tile.build == CLIENT_BUILD and tile._kind == "float"
    for x, y in ((-8949.95, -132.493), (0.0, 0.0), (-16000.0, 15000.0)):
        gx = MAP_RESOLUTION * (32 - x / SIZE_OF_GRIDS)
        gy = MAP_RESOLUTION * (32 - y / SIZE_OF_GRIDS)
        ix = int(gx) & (MAP_RESOLUTION - 1)
        iy = int(gy) & (MAP_RESOLUTION - 1)
        # The field is h = row + column, so the interpolation has to return it exactly; picking the
        # wrong triangle or the wrong corner pair breaks planarity and this assertion catches it.
        expected = (ix + iy) + (gx - int(gx)) + (gy - int(gy))
        assert abs(tile.height(x, y) - expected) < 1e-6, (x, y, tile.height(x, y), expected)
    # An int16 section must scale by (max - min) / 65535 and add gridHeight.
    body16 = struct.pack("<4sIff", HEIGHT_MAGIC, MAP_HEIGHT_AS_INT16, 100.0, 200.0)
    body16 += struct.pack(f"<{129 * 129}H", *([32768] * (129 * 129)))
    body16 += struct.pack(f"<{128 * 128}H", *([32768] * (128 * 128)))
    header16 = struct.pack("<11I", 0x5350414D, MAP_VERSION, CLIENT_BUILD, 0, 0, 64, len(body16), 0, 0, 0, 0)
    tile16 = MapTile(header16 + b"\0" * (64 - len(header16)) + body16, "<synthetic16>")
    assert tile16._kind == "int16"
    assert abs(tile16.height(0.0, 0.0) - 150.0) < 0.01, tile16.height(0.0, 0.0)
    # The grid the server computes must match its own examples, and the vmap file order is {gy}_{gx}.
    assert grid_coord(-8949.95, -132.493) == (48, 32)
    assert grid_coord(0.0, 0.0) == (32, 32)
    assert grid_coord(-20000.0, -20000.0) == (63, 63)
    assert abs(MID - 17066.66667) < 1e-3
    # A synthetic vmap tile round-trips, including the mirror and the bound renormalisation.
    name = b"Nsabbey.wmo"
    record_bytes = struct.pack("<IHI", MOD_HAS_BOUND, 0, 72420)
    record_bytes += struct.pack("<3f", 25963.67, 17244.96, 80.07)
    record_bytes += struct.pack("<3f", 0.0, 0.0, 0.0) + struct.pack("<f", 1.0)
    record_bytes += struct.pack("<3f", 25898.0, 17197.0, 80.1) + struct.pack("<3f", 26017.0, 17315.0, 169.2)
    record_bytes += struct.pack("<I", len(name)) + name + struct.pack("<I", 7)
    tile_bytes = VMAP_MAGIC + struct.pack("<I", 1) + record_bytes
    spawns, consumed = read_vmap_tile(tile_bytes)
    assert consumed == len(tile_bytes) and len(spawns) == 1
    spawn = spawns[0]
    assert spawn["name"] == "Nsabbey.wmo" and spawn["id"] == 72420
    assert abs(spawn["world"][0] - -8897.0) < 0.01 and abs(spawn["world"][1] - -178.30) < 0.01
    low, high = spawn["bound"]
    assert low[0] < high[0] and low[1] < high[1] and abs(low[0] - -8950.33) < 0.01
    # Structural rejection: a truncated spawn list must be reported rather than half-read.
    try:
        read_vmap_tile(tile_bytes[:-8])
    except ValueError:
        pass
    else:
        raise AssertionError("a truncated vmap tile was accepted")
    try:
        read_vmap_tile(b"NOPE_4.8" + tile_bytes[8:])
    except ValueError:
        pass
    else:
        raise AssertionError("a wrong vmap magic was accepted")
    assert median([3.0, 1.0, 2.0]) == 2.0
    print("Collision geometry checker self-check passed")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-root", type=Path, help="directory holding maps/, vmaps/ and mmaps/")
    parser.add_argument("--map", type=int, default=0, help="map id (default 0, Eastern Kingdoms)")
    parser.add_argument("--anchor", default="-8949.95,-132.493,83.5312",
                        help="anchor x,y,z in world coordinates (default: the frozen Plan 32 fixture)")
    parser.add_argument("--neighbours", type=int, default=1,
                        help="tiles around the anchor to scan for candidate obstacles (default 1)")
    parser.add_argument("--radius", type=float, default=200.0,
                        help="only report obstacles within this many metres of the anchor (default 200)")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    parser.add_argument("--self-check", action="store_true", help="run the synthetic checks only")
    args = parser.parse_args()
    if args.self_check:
        if args.data_root or args.json:
            parser.error("--self-check cannot be combined with --data-root or --json")
        self_check()
        return 0
    if not args.data_root:
        parser.error("--data-root is required")
    try:
        anchor = tuple(float(value) for value in args.anchor.split(","))
    except ValueError:
        parser.error("--anchor must be x,y,z")
    if len(anchor) != 3:
        parser.error("--anchor must be x,y,z")
    report = check(args.data_root, args.map, anchor, args.neighbours, args.radius)
    print(json.dumps(report, indent=2, sort_keys=True) if args.json else render(report))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, struct.error, KeyError) as error:
        print(f"collision geometry check failed: {error}", file=sys.stderr)
        sys.exit(1)
