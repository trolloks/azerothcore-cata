"""Validate produced Cata collision geometry against the server's own readers and pin the scenario.

This is the pre-flight for the Plan 32 native acceptance, not a substitute for it. It reads the
artifacts the server will read, using the server's own formats, naming and conversions, and it
checks that they can actually be loaded: every file the loaders open is required, every declared
section is inside the file, and every payload carries the header the reader validates.

It never claims that a server loaded the artifacts, that a query matched, or that a path exists.
Those are the acceptance's job; this only makes a silently degraded run detectable in advance.

Layouts and constants are taken from this tree, not from a summary:
  * maps/{id:03}{gx:02}{gy:02}.map        GridTerrainData::Load / LoadAreaData / LoadLiquidData /
                                          LoadHolesData / getHeightFromFloat / getHeightFromFlat
  * vmaps/{id:03}_{gy:02}_{gx:02}.vmtile  StaticMapTree::LoadMapTile / ModelSpawn::readFromFile
  * vmaps/{id:03}.vmtree                  StaticMapTree::InitMap
  * vmaps/<spawn.name>.vmo                WorldModelStore::AcquireModelInstance (always ".vmo")
  * mmaps/{id:03}.mmap                    MMapMgr::LoadNavMesh (a single dtNavMeshParams)
  * mmaps/{id:03}{gx:02}{gy:02}.mmtile    MMapMgr::LoadTile / MmapTileHeader (56 bytes, asserted in
                                          MapDefines.h) / dtNavMesh::addTile's payload validation
Grid coordinates follow the server's ComputeGridCoord, not ComputeGridCoordSimple: gx = 32 - x/533.33
and gy = 32 - y/533.33, and vmap tile files are named with {gy}_{gx} while map and mmap tiles use
{gx}{gy}. The two orders disagree, which is exactly what the tile/contents agreement check detects.
"""
import argparse
import json
import struct
import sys
import tempfile
from pathlib import Path

CLIENT_BUILD = 15595
# MapMagic.asUInt in GridTerrainData.h: the loader compares the uint32, not the characters.
MAP_MAGIC = 0x5350414D
MAP_VERSION = 10
AREA_MAGIC = b"AREA"
HEIGHT_MAGIC = b"MHGT"
LIQUID_MAGIC = b"MLIQ"
MAP_AREA_NO_AREA = 0x0001
MAP_LIQUID_NO_TYPE = 0x0001
MAP_LIQUID_NO_HEIGHT = 0x0002
MAP_HEIGHT_NO_HEIGHT = 0x0001
MAP_HEIGHT_AS_INT16 = 0x0002
MAP_HEIGHT_AS_INT8 = 0x0004
MAP_HEIGHT_HAS_FLIGHT_BOUNDS = 0x0008
FLIGHT_BOUNDS_BYTES = 9 * 2 * 2
AREA_MAP_BYTES = 16 * 16 * 2
LIQUID_ENTRY_BYTES = 16 * 16 * 2
LIQUID_FLAGS_BYTES = 16 * 16
HOLES_BYTES = 16 * 16 * 2
VMAP_MAGIC = b"VMAP_4.8"
MOD_M2 = 1
MOD_HAS_BOUND = 1 << 2
MMAP_MAGIC = 0x4D4D4150
DT_NAVMESH_MAGIC = 0x444E4156
DT_NAVMESH_VERSION = 7
MMAP_VERSION = 20
# static_assert(sizeof(MmapTileHeader) == 56) in MapDefines.h, and MMapMgr::LoadTile reads the whole
# struct before fileHeader.size bytes of dtNavMesh tile data.
MMAP_TILE_HEADER_SIZE = 56
DT_NAVMESH_PARAMS_SIZE = 28
# dtMeshHeader is fifteen ints, three walkable floats, bmin, bmax and bvQuantFactor.
DT_MESH_HEADER_SIZE = 100
# dtNavMesh::addTile sizes its buffers from the header counts, so the payload has to hold them.
# Element sizes are this tree's Detour, not the upstream defaults:
#   dtLink 16 because DT_POLYREF64 is defined (deps/recastnavigation/recastnavigation.diff enables it
#   deliberately) and makes dtPolyRef 8 bytes: dtPolyRef + next + edge/side/bmin/bmax
#   dtPoly 32 (firstLink 4, verts 6x2, neis 6x2, flags 2, vertCount 1, areaAndtype 1)
#   dtPolyDetail 12 (two 4-byte bases, two bytes, padded), dtBVNode 16 (six int16 plus an int)
#   dtOffMeshConnection 36 (six floats, a float, two shorts, two bytes, an int)
DT_POLY_SIZE = 32
DT_LINK_SIZE = 16
DT_POLY_DETAIL_SIZE = 12
DT_BV_NODE_SIZE = 16
DT_OFF_MESH_CONNECTION_SIZE = 36
SIZE_OF_GRIDS = 533.33333
MAP_RESOLUTION = 128
MAX_NUMBER_OF_GRIDS = 64
# VMapMgr2::convertPositionToInternalRep: the vmap files are in the internal representation, and
# every query converts into it, so reading the artifacts back out needs the inverse.
MID = 0.5 * MAX_NUMBER_OF_GRIDS * SIZE_OF_GRIDS
VMAP_TILE_AGREEMENT_MIN = 0.80
ON_GROUND_MEDIAN_MAX = 5.0


def grid_coord(x, y):
    """The server's ComputeGridCoord, clamped to the 64x64 map (GridDefines.h)."""
    return (max(0, min(MAX_NUMBER_OF_GRIDS - 1, int(32 - x / SIZE_OF_GRIDS))),
            max(0, min(MAX_NUMBER_OF_GRIDS - 1, int(32 - y / SIZE_OF_GRIDS))))


def internal_to_world(x, y, z):
    """Inverse of VMapMgr2::convertPositionToInternalRep (mid - world, so world = mid - internal)."""
    return (MID - x, MID - y, z)


def median(values):
    ordered = sorted(values)
    return ordered[len(ordered) // 2] if ordered else float("nan")


def median_abs(values):
    return median([abs(value) for value in values])


class MapTile:
    """A maps/*.map tile: the sections GridTerrainData::Load reads and the terrain heights."""

    def __init__(self, data, path):
        self.path = path
        self.data = data
        self.size = len(data)
        header = struct.unpack_from("<11I", data, 0)
        (self.map_magic, self.map_version, self.build, area_offset, self.area_size,
         self.height_offset, self.height_size, self.liquid_offset, self.liquid_size,
         self.holes_offset, self.holes_size) = header
        if self.map_magic != MAP_MAGIC:
            raise ValueError(f"{path}: not a map tile (magic {self.map_magic:#010x})")
        self.area_offset = area_offset
        self._kind = None
        self._v9 = None
        self._v8 = None
        self._multiplier = 1.0
        self._base = 0.0
        self.grid_height = 0.0
        self.grid_max_height = 0.0
        self.height_flags = 0

    def load_height_section(self):
        """The height section, with the flat/quantised/float forms the loader distinguishes."""
        fourcc, flags, grid_height, grid_max = struct.unpack_from("<4sIff", self.data, self.height_offset)
        if fourcc != HEIGHT_MAGIC:
            raise ValueError(f"{self.path}: height section is {fourcc!r}, not {HEIGHT_MAGIC!r}")
        self.height_flags = flags
        self.grid_height = grid_height
        self.grid_max_height = grid_max
        offset = self.height_offset + 16
        if flags & MAP_HEIGHT_NO_HEIGHT:
            # getHeightFromFlat returns gridHeight everywhere: valid flat terrain, not absent data.
            self._kind = "flat"
        elif flags & MAP_HEIGHT_AS_INT16:
            count9, count8, fmt, step, self._kind = 129 * 129, 128 * 128, "H", 2, "int16"
            self._multiplier = (grid_max - grid_height) / 65535.0
        elif flags & MAP_HEIGHT_AS_INT8:
            count9, count8, fmt, step, self._kind = 129 * 129, 128 * 128, "B", 1, "int8"
            self._multiplier = (grid_max - grid_height) / 255.0
        else:
            count9, count8, fmt, step, self._kind = 129 * 129, 128 * 128, "f", 4, "float"
        if self._kind != "flat":
            if offset + (count9 + count8) * step > self.size:
                raise ValueError(f"{self.path}: height data needs {offset + (count9 + count8) * step} "
                                 f"bytes, file has {self.size}")
            self._v9 = struct.unpack_from(f"<{count9}{fmt}", self.data, offset)
            self._v8 = struct.unpack_from(f"<{count8}{fmt}", self.data, offset + count9 * step)
            # Float heights are absolute; the quantised forms are relative to gridHeight.
            self._base = 0.0 if self._kind == "float" else grid_height
            offset += (count9 + count8) * step
        # LoadHeightData reads two arrays of nine int16 whenever the flag is set, flat included.
        if flags & MAP_HEIGHT_HAS_FLIGHT_BOUNDS:
            if offset + FLIGHT_BOUNDS_BYTES > self.size:
                raise ValueError(f"{self.path}: flight bounds need {offset + FLIGHT_BOUNDS_BYTES} bytes, "
                                 f"file has {self.size}")
            offset += FLIGHT_BOUNDS_BYTES
        if offset > self.size:
            raise ValueError(f"{self.path}: height section ends at {offset}, file has {self.size}")

    def height(self, x, y):
        """getHeightFromFloat (and getHeightFromFlat) as the server interpolates them."""
        if self._kind is None:
            return None
        if self._kind == "flat":
            return self.grid_height
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


def section_problems(tile, data):
    """Every section GridTerrainData::Load opens must exist, have its magic and fit in the file."""
    problems = []
    if tile.area_offset:
        if tile.area_offset + 8 > len(data):
            problems.append(f"area section at {tile.area_offset} is beyond the file")
        else:
            fourcc, flags, _ = struct.unpack_from("<4sHH", data, tile.area_offset)
            if fourcc != AREA_MAGIC:
                problems.append(f"area fourcc {fourcc!r} is not {AREA_MAGIC!r}")
            elif not flags & MAP_AREA_NO_AREA and tile.area_offset + 8 + AREA_MAP_BYTES > len(data):
                problems.append(f"area map needs {tile.area_offset + 8 + AREA_MAP_BYTES} bytes")
    if tile.liquid_offset:
        if tile.liquid_offset + 16 > len(data):
            problems.append(f"liquid section at {tile.liquid_offset} is beyond the file")
        else:
            fourcc, flags, _flags, _type, _ox, _oy, width, height, _level = \
                struct.unpack_from("<4sBBHBBBBf", data, tile.liquid_offset)
            if fourcc != LIQUID_MAGIC:
                problems.append(f"liquid fourcc {fourcc!r} is not {LIQUID_MAGIC!r}")
            else:
                needed = tile.liquid_offset + 16
                if not flags & MAP_LIQUID_NO_TYPE:
                    needed += LIQUID_ENTRY_BYTES + LIQUID_FLAGS_BYTES
                if not flags & MAP_LIQUID_NO_HEIGHT:
                    needed += width * height * 4
                if needed > len(data):
                    problems.append(f"liquid data needs {needed} bytes")
    if tile.holes_size and tile.holes_offset + HOLES_BYTES > len(data):
        problems.append(f"holes at {tile.holes_offset} need {HOLES_BYTES} bytes")
    return problems


def read_vmap_tree(data):
    """StaticMapTree::InitMap: magic, the tiled flag, the BIH, then the GOBJ chunk.

    BoundingIntervalHierarchy::readFromFile reads bounds, a tree-entry count and an object count
    before GOBJ, and fails on a short read; the object count becomes iNTreeValues, which is what the
    tile's references are validated against.
    """
    if data[0:8] != VMAP_MAGIC:
        return f"tree magic is {data[0:8]!r}, expected {VMAP_MAGIC!r}", None
    if len(data) < 13:
        return f"tree is {len(data)} bytes, too short for the tiled flag and NODE chunk", None
    tiled = data[8]
    if data[9:13] != b"NODE":
        return f"tree chunk is {data[9:13]!r}, expected b'NODE'", None
    try:
        offset = 13 + 24  # AABox lo/hi, six floats
        if offset > len(data):
            return f"tree ends before the BIH bounds, which need 24 bytes after NODE", None
        tree_size = struct.unpack_from("<I", data, offset)[0]
        offset += 4
        if offset + 4 * tree_size > len(data):
            return (f"BIH declares {tree_size} tree entries ({4 * tree_size} bytes) beyond the "
                    f"{len(data)}-byte file; readFromFile fails on that short read", None)
        offset += 4 * tree_size
        count = struct.unpack_from("<I", data, offset)[0]
        offset += 4
        if offset + 4 * count > len(data):
            return (f"BIH declares {count} objects ({4 * count} bytes) beyond the file; the primitive "
                    f"count would be a short read", None)
        offset += 4 * count
        if data[offset:offset + 4] != b"GOBJ":
            return f"chunk after the BIH is {data[offset:offset + 4]!r}, expected b'GOBJ'", None
    except struct.error as error:
        return f"tree could not be parsed: {error}", None
    return None, {"tiled": tiled, "tree_entries": tree_size, "objects": count,
                  "consumed": offset + 4, "size": len(data)}


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
        referenced = struct.unpack_from("<I", data, offset)[0]
        offset += 4
        world = internal_to_world(*pos)
        if bound:
            world_bounds = (internal_to_world(*bound[0]), internal_to_world(*bound[1]))
            bound = ((min(world_bounds[0][0], world_bounds[1][0]), min(world_bounds[0][1], world_bounds[1][1]),
                      bound[0][2]),
                     (max(world_bounds[0][0], world_bounds[1][0]), max(world_bounds[0][1], world_bounds[1][1]),
                      bound[1][2]))
        spawns.append({"index": index, "flags": flags, "adt_id": adt_id, "id": spawn_id,
                       "internal": pos, "world": world, "rotation": rotation, "scale": scale,
                       "bound": bound, "name": name, "referenced": referenced})
    return spawns, offset


class MmapFiles:
    """The two mmaps artifacts, checked the way MMapMgr and dtNavMesh consume them."""

    def __init__(self, par_path, tile_path):
        self.params_path = par_path
        self.params_size = len(par_path.read_bytes())
        self.data = tile_path.read_bytes()
        self.tile_size = len(self.data)
        magic, dt_version, mmap_version, size = struct.unpack_from("<4I", self.data, 0)
        self.magic = magic
        self.dt_version = dt_version
        self.mmap_version = mmap_version
        self.declared_size = size
        self.header_size = MMAP_TILE_HEADER_SIZE

    def params(self):
        """dtNavMeshParams as MMapMgr::LoadNavMesh reads it before dtNavMesh::init."""
        return struct.unpack_from("<3f2f2i", self.params_path.read_bytes(), 0)

    def payload(self):
        """The dtMeshHeader that dtNavMesh::addTile validates at the start of the tile data."""
        if self.header_size + DT_MESH_HEADER_SIZE > self.tile_size:
            return None
        fields = struct.unpack_from("<15i10f", self.data, self.header_size)
        return {"magic": fields[0], "version": fields[1], "x": fields[2], "y": fields[3],
                "layer": fields[4], "poly_count": fields[6], "vert_count": fields[7],
                "max_link_count": fields[8], "detail_mesh_count": fields[9],
                "detail_vert_count": fields[10], "detail_tri_count": fields[11],
                "bv_node_count": fields[12], "off_mesh_con_count": fields[13],
                "bmin": fields[18:21], "bmax": fields[21:24], "bv_quant_factor": fields[24]}

    def required_payload(self, payload):
        """dtNavMesh::addTile's own buffer arithmetic, in the same order and alignment."""
        def aligned(value):
            return (value + 3) & ~3

        return (DT_MESH_HEADER_SIZE
                + aligned(12 * payload["vert_count"])
                + aligned(DT_POLY_SIZE * payload["poly_count"])
                + aligned(DT_LINK_SIZE * payload["max_link_count"])
                + aligned(DT_POLY_DETAIL_SIZE * payload["detail_mesh_count"])
                + aligned(12 * payload["detail_vert_count"])
                + aligned(4 * payload["detail_tri_count"])
                + aligned(DT_BV_NODE_SIZE * payload["bv_node_count"])
                + aligned(DT_OFF_MESH_CONNECTION_SIZE * payload["off_mesh_con_count"]))


def check(data_root, map_id=0, anchor=(-8949.95, -132.493, 83.5312), neighbours=1, radius=200.0):
    data_root = Path(data_root)
    anchor_x, anchor_y, anchor_z = anchor
    gx, gy = grid_coord(anchor_x, anchor_y)
    checks = []
    gaps = []

    def record(name, ok, detail):
        checks.append({"check": name, "pass": bool(ok), "detail": detail})
        return ok

    map_path = data_root / "maps" / f"{map_id:03d}{gx:02d}{gy:02d}.map"
    vmap_path = data_root / "vmaps" / f"{map_id:03d}_{gy:02d}_{gx:02d}.vmtile"
    tree_path = data_root / "vmaps" / f"{map_id:03d}.vmtree"
    par_path = data_root / "mmaps" / f"{map_id:03d}.mmap"
    mtile_path = data_root / "mmaps" / f"{map_id:03d}{gx:02d}{gy:02d}.mmtile"
    for path in (map_path, vmap_path, tree_path, par_path, mtile_path):
        if not path.is_file():
            record(f"present:{path.name}", False, f"{path} is missing")
            return {"data_root": str(data_root), "map": map_id, "anchor": list(anchor),
                    "grid": [gx, gy], "checks": checks, "gaps": gaps, "facts": {}, "pass": False}

    data = map_path.read_bytes()
    tile = MapTile(data, str(map_path))
    record("map.headers", tile.map_magic == MAP_MAGIC and tile.map_version == MAP_VERSION
           and tile.build == CLIENT_BUILD,
           f"magic {tile.map_magic:#010x} version {tile.map_version} build {tile.build} "
           f"(expected {MAP_MAGIC:#010x} {MAP_VERSION} {CLIENT_BUILD})")
    problems = section_problems(tile, data)
    record("map.sections", not problems,
           "every declared section the terrain loader opens is present and inside the file: "
           f"area@{tile.area_offset}({tile.area_size}) height@{tile.height_offset}({tile.height_size}) "
           f"liquid@{tile.liquid_offset}({tile.liquid_size}) holes@{tile.holes_offset}({tile.holes_size})"
           + (f" - {'; '.join(problems)}" if problems else ""))
    height_ok = True
    height_detail = ""
    try:
        tile.load_height_section()
        height_detail = (f"MHGT flags {tile.height_flags:#06x} heights {tile._kind} "
                         f"{tile.grid_height:.2f}..{tile.grid_max_height:.2f}")
    except (ValueError, struct.error) as error:
        height_ok = False
        height_detail = str(error)
    record("map.height_section", height_ok, height_detail)

    spawns, consumed = read_vmap_tile(vmap_path.read_bytes())
    record("vmap.structure", consumed == vmap_path.stat().st_size,
           f"{len(spawns)} spawns consumed {consumed} of {vmap_path.stat().st_size} bytes")
    tree_problem, tree_info = read_vmap_tree(tree_path.read_bytes())
    record("vmap.tree", tree_problem is None,
           tree_problem or f"{tree_path.name} carries {VMAP_MAGIC!r}, the tiled flag {tree_info['tiled']}, "
                           f"a BIH of {tree_info['tree_entries']} entries and "
                           f"{tree_info['objects']} objects, then GOBJ "
                           f"({tree_info['consumed']}/{tree_info['size']} bytes)")
    if tree_info:
        outside = [spawn["index"] for spawn in spawns if spawn["referenced"] >= tree_info["objects"]]
        record("vmap.tree_references", not outside,
               f"every tile reference is within the tree's {tree_info['objects']} objects "
               f"(LoadMapTile skips a reference of {tree_info['objects']} or more, leaving that object "
               f"without collision)"
               + (f" - {len(outside)} outside, first at spawn {outside[0]}" if outside else ""))

    def terrain(x, y):
        tile_gx, tile_gy = grid_coord(x, y)
        if (tile_gx, tile_gy) == (gx, gy):
            return tile.height(x, y)
        path = data_root / "maps" / f"{map_id:03d}{tile_gx:02d}{tile_gy:02d}.map"
        if not path.is_file():
            return None
        neighbour = MapTile(path.read_bytes(), str(path))
        try:
            neighbour.load_height_section()
        except (ValueError, struct.error):
            return None
        return neighbour.height(x, y)

    in_tile, residuals = 0, []
    for spawn in spawns:
        x, y, z = spawn["world"]
        if grid_coord(x, y) != (gx, gy):
            continue
        in_tile += 1
        height = terrain(x, y)
        if height is None:
            continue
        residuals.append(z - height)
    agreement = in_tile / len(spawns) if spawns else 0.0
    record("vmap.tile_matches_contents", agreement >= VMAP_TILE_AGREEMENT_MIN,
           f"{in_tile}/{len(spawns)} spawns ({agreement:.1%}) fall in the grid {gx},{gy} the file is "
           f"named for (the vmap file name is <id>_<gy>_<gx>, so a transposed or mismatched tile "
           f"shows up here)")
    # The magnitude is what the check claims: signed residuals cancel, so a spread of placement
    # errors around zero would otherwise look like objects on the ground.
    absolute = median_abs(residuals)
    signed = median(residuals)
    record("vmap.objects_on_terrain", absolute <= ON_GROUND_MEDIAN_MAX and residuals,
           f"median |z - terrain| {absolute:.2f} m over {len(residuals)} objects in the tile "
           f"(signed median {signed:+.2f}); a wrong frame or a missing map tile moves this by "
           f"thousands of metres")

    mmap = MmapFiles(par_path, mtile_path)
    params = mmap.params()
    finite = all(value == value and abs(value) != float("inf") for value in params)
    record("mmap.params", mmap.params_size == DT_NAVMESH_PARAMS_SIZE and finite
           and params[3] > 0 and params[4] > 0 and params[5] > 0 and params[6] != 0,
           f"{par_path.name} is {mmap.params_size} bytes (one dtNavMeshParams is "
           f"{DT_NAVMESH_PARAMS_SIZE}); tileWidth {params[3]:.2f} tileHeight {params[4]:.2f} "
           f"maxTiles {params[5]} maxPolys {params[6]} (dtNavMesh::init reads maxPolys as unsigned, so "
           f"a 1<<31 value is the generator's own encoding)")
    record("mmap.tile_headers",
           mmap.magic == MMAP_MAGIC and mmap.dt_version == DT_NAVMESH_VERSION
           and mmap.mmap_version == MMAP_VERSION
           and mmap.header_size + mmap.declared_size == mmap.tile_size,
           f"magic {mmap.magic:#010x} dtVersion {mmap.dt_version} mmapVersion {mmap.mmap_version} "
           f"header+size {mmap.header_size}+{mmap.declared_size} vs file {mmap.tile_size}")
    payload = mmap.payload()
    payload_problems = []
    if payload is None:
        payload_problems.append(f"payload is shorter than a {DT_MESH_HEADER_SIZE}-byte dtMeshHeader")
    else:
        if payload["magic"] != DT_NAVMESH_MAGIC:
            payload_problems.append(f"mesh magic {payload['magic']:#010x} is not {DT_NAVMESH_MAGIC:#010x}")
        if payload["version"] != DT_NAVMESH_VERSION:
            payload_problems.append(f"mesh version {payload['version']} is not {DT_NAVMESH_VERSION}")
        for key in ("poly_count", "vert_count", "max_link_count", "detail_mesh_count",
                    "detail_vert_count", "detail_tri_count", "bv_node_count", "off_mesh_con_count"):
            if payload[key] < 0:
                payload_problems.append(f"{key} {payload[key]} is negative")
        if payload["poly_count"] <= 0 or payload["vert_count"] <= 0:
            payload_problems.append("payload declares no polygons or no vertices")
        if payload["max_link_count"] <= 0:
            payload_problems.append("payload declares no links, and addTile writes links[count-1]")
        required = mmap.required_payload(payload)
        if required > mmap.declared_size:
            payload_problems.append(f"the declared {mmap.declared_size}-byte payload is smaller than "
                                    f"the {required} bytes its counts describe")
        if payload["magic"] == DT_NAVMESH_MAGIC and payload["version"] == DT_NAVMESH_VERSION \
                and payload["poly_count"] > 0:
            # MapBuilder.cpp computes the tile's place in the params grid from its own bounds, so
            # the header has to agree with both or the tile is not the one the params describe.
            centre_x = (payload["bmin"][0] + payload["bmax"][0]) / 2
            centre_z = (payload["bmin"][2] + payload["bmax"][2]) / 2
            got_x = int((centre_x - params[0]) / params[3]) if params[3] else None
            got_y = int((centre_z - params[2]) / params[4]) if params[4] else None
            if got_x != payload["x"] or got_y != payload["y"]:
                payload_problems.append(f"header tile {payload['x']},{payload['y']} disagrees with the "
                                        f"params and bounds ({got_x},{got_y})")
    record("mmap.payload", not payload_problems,
           "; ".join(payload_problems) if payload_problems
           else f"dtMeshHeader inside the declared payload: magic {DT_NAVMESH_MAGIC:#010x} version "
                f"{DT_NAVMESH_VERSION}, {payload['poly_count']} polygons, {payload['vert_count']} "
                f"vertices, tile {payload['x']},{payload['y']} consistent with the params; the counts "
                f"need {required} of the {mmap.declared_size} declared bytes "
                f"(bvQuantFactor {payload['bv_quant_factor']:.3f})")

    anchor_height = terrain(anchor_x, anchor_y)
    obstacles, candidates = [], []
    if neighbours:
        for dx in range(-neighbours, neighbours + 1):
            for dy in range(-neighbours, neighbours + 1):
                path = data_root / "vmaps" / f"{map_id:03d}_{gy + dy:02d}_{gx + dx:02d}.vmtile"
                if not path.is_file():
                    continue
                extra, _ = read_vmap_tile(path.read_bytes())
                candidates.extend(extra)
        buildings = {}
        for spawn in candidates:
            if not spawn["bound"] or spawn["flags"] & MOD_M2:  # doodads, not buildings
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

    # WorldModelStore::AcquireModelInstance always opens <name>.vmo; a spawn without one loads as a
    # missing model at run time. The scenario's own obstacles have to resolve; anything else is
    # reported so the run is not surprised, rather than silently accepted.
    def resolves(name):
        return (data_root / "vmaps" / f"{name}.vmo").is_file()

    scenario_missing = [obstacle["name"] for obstacle in obstacles if not resolves(obstacle["name"])]
    record("vmap.models_scenario", not scenario_missing,
           "every reported obstacle's model resolves as <name>.vmo"
           + (f" - missing: {', '.join(scenario_missing)}" if scenario_missing else ""))
    unique_names = sorted({spawn["name"] for spawn in spawns})
    unresolved = [name for name in unique_names if not resolves(name)]
    if unresolved:
        gaps.append({"gap": "vmap.models", "count": len(unresolved), "of": len(unique_names),
                     "models": unresolved[:10],
                     "detail": "referenced by the anchor tile but with no <name>.vmo, so the server "
                               "logs a model load failure and that object has no collision"})
    facts = {
        "anchor": {"position": [anchor_x, anchor_y, anchor_z], "terrain_height": anchor_height,
                   "stand_height": None if anchor_height is None else anchor_z - anchor_height},
        "vmap_tile": vmap_path.name, "vmap_spawns": len(spawns), "spawns_in_tile": in_tile,
        "median_absolute_residual": absolute, "median_signed_residual": signed,
        "navmesh": {"mesh_bounds": {"x": [payload["bmin"][0], payload["bmax"][0]],
                                    "z": [payload["bmin"][2], payload["bmax"][2]]}
                    if payload else None,
                    "params_orig": [params[0], params[2]], "tile": [payload["x"], payload["y"]]
                    if payload else None},
        "obstacles": obstacles,
        "bounds_do_not_decide_los": ("The obstacle bounds are the computed model extents, and for a "
                                     "building WMO that includes its grounds; a segment crossing a bound "
                                     "therefore does not prove a blocked query. Derive the clear and "
                                     "blocked pair from the model's own triangles, not from these boxes."),
        "not_verified": ("Model file contents, the Detour-side meaning of the tile's x/y outside the "
                         "generator's own formula, and whether any query or path succeeds."),
    }
    return {"data_root": str(data_root), "map": map_id, "anchor": list(anchor), "grid": [gx, gy],
            "checks": checks, "gaps": gaps, "facts": facts,
            "pass": all(entry["pass"] for entry in checks)}


def render(report):
    def number(value, spec=".3f"):
        return "unavailable" if value is None else format(value, spec)

    lines = [f"collision geometry: data-root {report['data_root']} map {report['map']} "
             f"grid {report['grid'][0]},{report['grid'][1]}"]
    for check in report["checks"]:
        lines.append(f"  [{'PASS' if check['pass'] else 'FAIL'}] {check['check']}: {check['detail']}")
    for gap in report["gaps"]:
        lines.append(f"  [GAP ] {gap['gap']}: {gap['count']} of {gap['of']} referenced models do not "
                     f"resolve ({', '.join(gap['models'])})")
    facts = report["facts"]
    if facts:
        anchor = facts["anchor"]
        lines.append(f"  anchor terrain height {number(anchor['terrain_height'])} "
                     f"(position z {anchor['position'][2]:.3f}, stand height "
                     f"{number(anchor['stand_height'], '+.3f')})")
        lines.append(f"  {facts['vmap_spawns']} vmap spawns, {facts['spawns_in_tile']} in the anchor tile, "
                     f"median |residual| {number(facts['median_absolute_residual'], '.2f')} m")
        for obstacle in facts["obstacles"]:
            low, high = obstacle["world_bounds"]
            lines.append(f"  obstacle {obstacle['name']} id {obstacle['id']} "
                         f"{obstacle['distance_from_anchor']:6.1f} m from the anchor, footprint "
                         f"{obstacle['footprint']:.0f} m2, position "
                         f"({obstacle['world_position'][0]:.2f},{obstacle['world_position'][1]:.2f},"
                         f"{obstacle['world_position'][2]:.2f}) residual "
                         f"{number(obstacle['residual'], '+.2f')} m")
            lines.append(f"    world bounds x[{low[0]:.2f},{high[0]:.2f}] y[{low[1]:.2f},{high[1]:.2f}] "
                         f"z[{low[2]:.2f},{high[2]:.2f}]")
    lines.append(f"Collision geometry {'PASS' if report['pass'] else 'FAIL'}. This does not prove a "
                 f"server loaded it, that any query matched, or that a path exists.")
    return "\n".join(lines)


def build_map(heights="flat", flat_height=40.0, liquid_offset=0, holes_size=0, area_offset=0,
              area_flags=0):
    """A synthetic map tile: used by the self-check to exercise the loader's sections."""
    HEADER_SIZE = 44  # map_fileheader: eleven uint32 fields
    area = b""
    if area_offset:
        area = b"\0" * (area_offset - HEADER_SIZE) + struct.pack("<4sHH", AREA_MAGIC, area_flags, 9)
        if not area_flags & MAP_AREA_NO_AREA:
            area += b"\0" * AREA_MAP_BYTES
    height_offset = HEADER_SIZE + len(area)
    if heights == "flat":
        section = struct.pack("<4sIff", HEIGHT_MAGIC, MAP_HEIGHT_NO_HEIGHT, flat_height, flat_height)
    else:
        v9 = [float(x + y) for x in range(129) for y in range(129)]
        v8 = [float((x + 0.5) + (y + 0.5)) for x in range(128) for y in range(128)]
        section = struct.pack("<4sIff", HEIGHT_MAGIC, 0, 0.0, 300.0)
        section += struct.pack(f"<{129 * 129}f", *v9) + struct.pack(f"<{128 * 128}f", *v8)
    holes_offset = HEADER_SIZE + len(area) + len(section) if holes_size else 0
    header = struct.pack("<11I", MAP_MAGIC, MAP_VERSION, CLIENT_BUILD, area_offset, AREA_MAP_BYTES,
                         height_offset, len(section), liquid_offset, 0, holes_offset, holes_size)
    tail = b"\0" * HOLES_BYTES if holes_size else b""
    return header + area + section + tail


def build_vmtile(spawns):
    """A synthetic .vmtile; each spawn is (name, id, world position, world low, world high)."""
    records = b""
    for name, spawn_id, world, low, high in spawns:
        internal = (MID - world[0], MID - world[1], world[2])
        i_low = (MID - low[0], MID - low[1], low[2])
        i_high = (MID - high[0], MID - high[1], high[2])
        flags = MOD_HAS_BOUND if low else MOD_M2
        records += struct.pack("<IHI", flags, 0, spawn_id) + struct.pack("<3f", *internal)
        records += struct.pack("<3f", 0.0, 0.0, 0.0) + struct.pack("<f", 1.0)
        if low:
            records += struct.pack("<3f", *i_low) + struct.pack("<3f", *i_high)
        encoded = name.encode()
        records += struct.pack("<I", len(encoded)) + encoded + struct.pack("<I", 0)
    return VMAP_MAGIC + struct.pack("<I", len(spawns)) + records


def build_vmtree(objects=1, tree_entries=3):
    """A tree StaticMapTree::InitMap can load: magic, tiled flag, NODE, BIH, GOBJ.

    The BIH is bounds (six floats), a tree-entry count and its entries, then an object count and its
    entries; the object count becomes iNTreeValues.
    """
    entries = [3 << 30] + [0] * (tree_entries - 1)
    body = struct.pack("<6f", 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    body += struct.pack("<I", tree_entries) + struct.pack(f"<{tree_entries}I", *entries)
    body += struct.pack("<I", objects) + struct.pack(f"<{objects}I", *range(objects))
    return VMAP_MAGIC + b"\x01" + b"NODE" + body + b"GOBJ"


def build_mmap(orig=(-7466.6665, 1.1754943508222875e-38, -16000.0), max_polys=-2147483648):
    return struct.pack("<3f2f2i", *orig, SIZE_OF_GRIDS, SIZE_OF_GRIDS, 839, max_polys)


def build_mmtile(tile_x, tile_y, bmin, bmax, payload_bytes=4096, magic=DT_NAVMESH_MAGIC,
                 version=DT_NAVMESH_VERSION, poly_count=10, vert_count=20):
    mesh = struct.pack("<15i10f", magic, version, tile_x, tile_y, 0, 0, poly_count, vert_count,
                       30, 0, 0, 0, 0, 0, 0, 1.6, 0.53, 1.6,
                       bmin[0], bmin[1], bmin[2], bmax[0], bmax[1], bmax[2], 1.0)
    payload = mesh + b"\0" * max(0, payload_bytes - len(mesh))
    header = struct.pack("<4I", MMAP_MAGIC, DT_NAVMESH_VERSION, MMAP_VERSION, len(payload))
    header += b"\x01\x00\x00\x00"
    header += b"\0" * (MMAP_TILE_HEADER_SIZE - len(header))
    return header + payload


def write_synthetic_root(root, spawns=None, heights="flat", liquid_offset=0, with_tree=True,
                         model_names=("Nsabbey.wmo",), anchor=(32.0, 32.0), flat_height=40.0,
                         payload_bytes=4096):
    """A minimal but loadable data root for the anchor grid of the given anchor position."""
    root = Path(root)
    for name in ("maps", "vmaps", "mmaps"):
        (root / name).mkdir(parents=True, exist_ok=True)
    gx, gy = grid_coord(*anchor)
    (root / "maps" / f"000{gx:02d}{gy:02d}.map").write_bytes(
        build_map(heights=heights, flat_height=flat_height, liquid_offset=liquid_offset))
    if spawns is None:
        spawns = [("Nsabbey.wmo", 72420, (anchor[0] - 10.0, anchor[1], flat_height),
                   (anchor[0] - 20.0, anchor[1] - 20.0, flat_height - 10.0),
                   (anchor[0] + 20.0, anchor[1] + 20.0, flat_height + 10.0))]
    (root / "vmaps" / f"000_{gy:02d}_{gx:02d}.vmtile").write_bytes(build_vmtile(spawns))
    if with_tree:
        (root / "vmaps" / "000.vmtree").write_bytes(build_vmtree())
    for name in model_names:
        (root / "vmaps" / f"{name}.vmo").write_bytes(VMAP_MAGIC + b"WMOD" + b"\0" * 64)
    params = build_mmap()
    (root / "mmaps" / "000.mmap").write_bytes(params)
    orig = struct.unpack_from("<3f", params, 0)
    bmin = (anchor[0] - 200.0, 40.0, anchor[1] - 200.0)
    bmax = (anchor[0] + 200.0, 200.0, anchor[1] + 200.0)
    # Match the generator's own placement formula so the payload check has something consistent.
    tile_x = int(((bmin[0] + bmax[0]) / 2 - orig[0]) / SIZE_OF_GRIDS)
    tile_y = int(((bmin[2] + bmax[2]) / 2 - orig[2]) / SIZE_OF_GRIDS)
    (root / "mmaps" / f"000{gx:02d}{gy:02d}.mmtile").write_bytes(
        build_mmtile(tile_x, tile_y, bmin, bmax, payload_bytes=payload_bytes))
    return gx, gy


def self_check():
    """Synthetic cases for the maths, the parser and the false-PASS cases a pre-flight must reject."""
    # A planar height field must interpolate back exactly; a wrong triangle or corner pair breaks it.
    tile = MapTile(build_map(heights="planar"), "<synthetic>")
    tile.load_height_section()
    for x, y in ((100.0, 100.0), (0.0, 0.0), (-16000.0, 15000.0)):
        gx = MAP_RESOLUTION * (32 - x / SIZE_OF_GRIDS)
        gy = MAP_RESOLUTION * (32 - y / SIZE_OF_GRIDS)
        ix = int(gx) & (MAP_RESOLUTION - 1)
        iy = int(gy) & (MAP_RESOLUTION - 1)
        expected = (ix + iy) + (gx - int(gx)) + (gy - int(gy))
        assert abs(tile.height(x, y) - expected) < 1e-6, (x, y, tile.height(x, y), expected)
    # MAP_HEIGHT_NO_HEIGHT is valid flat terrain and must not read as absent.
    flat_bytes = build_map(heights="flat", flat_height=42.0)
    flat = MapTile(flat_bytes, "<flat>")
    flat.load_height_section()
    assert flat._kind == "flat" and flat.height(1.0, 2.0) == 42.0
    assert not section_problems(flat, flat_bytes)
    # A declared section beyond EOF must be rejected, not ignored.
    bogus = build_map(heights="planar")
    header = list(struct.unpack_from("<11I", bogus, 0))
    header[7] = 99999999  # liquidMapOffset beyond the file
    header[8] = 16
    broken = struct.pack("<11I", *header) + bogus[44:]
    assert section_problems(MapTile(broken, "<broken>"), broken), "an out-of-file section was accepted"
    # Grid maths, the internal representation and the vmap parser.
    assert grid_coord(-8949.95, -132.493) == (48, 32)
    assert grid_coord(0.0, 0.0) == (32, 32)
    assert grid_coord(-20000.0, -20000.0) == (63, 63)
    assert abs(MID - 17066.66667) < 1e-3
    spawns, consumed = read_vmap_tile(build_vmtile(
        [("Nsabbey.wmo", 72420, (-8897.0, -178.3, 80.07), (-8950.33, -248.44, 80.1),
          (-8831.42, -129.90, 169.18))]))
    assert consumed == len(build_vmtile(
        [("Nsabbey.wmo", 72420, (-8897.0, -178.3, 80.07), (-8950.33, -248.44, 80.1),
          (-8831.42, -129.90, 169.18))]))
    assert spawns[0]["name"] == "Nsabbey.wmo" and spawns[0]["id"] == 72420
    assert abs(spawns[0]["world"][0] - -8897.0) < 0.01 and abs(spawns[0]["world"][1] - -178.30) < 0.01
    low, high = spawns[0]["bound"]
    assert low[0] < high[0] and low[1] < high[1] and abs(low[0] - -8950.33) < 0.01
    for broken in (build_vmtile([])[:-8], b"NOPE_4.8" + build_vmtile([])[8:]):
        try:
            read_vmap_tile(broken)
        except ValueError:
            pass
        else:
            raise AssertionError("a truncated or wrongly magicked vmap tile was accepted")
    problem, info = read_vmap_tree(build_vmtree(objects=7, tree_entries=5))
    assert problem is None and info["objects"] == 7 and info["consumed"] == info["size"]
    assert read_vmap_tree(b"NOPE_4.8" + build_vmtree()[8:])[0] is not None
    assert read_vmap_tree(VMAP_MAGIC + b"\x01" + b"NODE")[0] is not None
    assert read_vmap_tree(build_vmtree()[:-4])[0] is not None
    # The median the coherence check uses must be of magnitudes, not of signed values.
    payload_probe = MmapFiles.__new__(MmapFiles)
    assert payload_probe.required_payload(
        {"vert_count": 20, "poly_count": 10, "max_link_count": 30, "detail_mesh_count": 0,
         "detail_vert_count": 0, "detail_tri_count": 0, "bv_node_count": 0,
         "off_mesh_con_count": 0}) == 1140
    assert DT_MESH_HEADER_SIZE == 100 and DT_LINK_SIZE == 16
    assert median([3.0, 1.0, 2.0]) == 2.0
    assert median([-100.0, 0.0, 100.0]) == 0.0 and median_abs([-100.0, 0.0, 100.0]) == 100.0

    # A loadable synthetic root must pass, and each way of breaking it must fail rather than pass.
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary) / "good"
        gx, gy = write_synthetic_root(root)
        report = check(root, 0, (32.0, 32.0, 40.0))
        assert report["pass"], [c for c in report["checks"] if not c["pass"]]
        assert report["facts"]["obstacles"], "the synthetic obstacle was not reported"
        assert render(report)
        missing_any = check(root, 0, (9000.0, 9000.0, 40.0))
        assert not missing_any["pass"], "missing artifacts for another grid were accepted"
        no_tree = Path(temporary) / "no-tree"
        write_synthetic_root(no_tree)
        (no_tree / "vmaps" / "000.vmtree").unlink()
        assert not check(no_tree, 0, (32.0, 32.0, 40.0))["pass"], "a missing vmtree was accepted"
        no_model = Path(temporary) / "no-model"
        write_synthetic_root(no_model, model_names=())
        assert not check(no_model, 0, (32.0, 32.0, 40.0))["pass"], "a missing obstacle model was accepted"
        empty_payload = Path(temporary) / "empty-payload"
        gx, gy = write_synthetic_root(empty_payload)
        (empty_payload / "mmaps" / f"000{gx:02d}{gy:02d}.mmtile").write_bytes(b"\0" * MMAP_TILE_HEADER_SIZE)
        assert not check(empty_payload, 0, (32.0, 32.0, 40.0))["pass"], "an empty Detour payload was accepted"
        wrong_mesh = Path(temporary) / "wrong-mesh"
        gx, gy = write_synthetic_root(wrong_mesh)
        (wrong_mesh / "mmaps" / f"000{gx:02d}{gy:02d}.mmtile").write_bytes(
            build_mmtile(0, 0, (0.0, 0.0, 0.0), (1.0, 1.0, 1.0), magic=0x12345678))
        assert not check(wrong_mesh, 0, (32.0, 32.0, 40.0))["pass"], "a wrong mesh magic was accepted"
        undercounted = Path(temporary) / "undercounted-payload"
        # 1016 is what the previous arithmetic produced for 10 polygons, 20 vertices and 30 links with
        # a 96-byte header and 12-byte links; the real layout needs 1140, so this must not pass.
        write_synthetic_root(undercounted, payload_bytes=1016)
        assert not check(undercounted, 0, (32.0, 32.0, 40.0))["pass"], \
            "a payload sized by the undercounting arithmetic was accepted"
        short_payload = Path(temporary) / "short-payload"
        write_synthetic_root(short_payload, payload_bytes=DT_MESH_HEADER_SIZE)
        assert not check(short_payload, 0, (32.0, 32.0, 40.0))["pass"], \
            "a payload too small for the arrays its counts describe was accepted"
        truncated_tree = Path(temporary) / "truncated-tree"
        gx, gy = write_synthetic_root(truncated_tree)
        (truncated_tree / "vmaps" / "000.vmtree").write_bytes(VMAP_MAGIC + b"\x01" + b"NODE")
        assert not check(truncated_tree, 0, (32.0, 32.0, 40.0))["pass"], \
            "a tree without its BIH was accepted"
        foreign_ref = Path(temporary) / "foreign-ref"
        gx, gy = write_synthetic_root(foreign_ref)
        tile_path = foreign_ref / "vmaps" / f"000_{gy:02d}_{gx:02d}.vmtile"
        payload = tile_path.read_bytes()
        tile_path.write_bytes(payload[:-4] + struct.pack("<I", 5))
        assert not check(foreign_ref, 0, (32.0, 32.0, 40.0))["pass"], \
            "a tile reference outside the tree's objects was accepted"
        no_flight = Path(temporary) / "no-flight-bounds"
        gx, gy = write_synthetic_root(no_flight, heights="flat")
        map_path = no_flight / "maps" / f"000{gx:02d}{gy:02d}.map"
        raw = bytearray(map_path.read_bytes())
        height_offset = struct.unpack_from("<11I", raw, 0)[5]
        struct.pack_into("<I", raw, height_offset + 4,
                         MAP_HEIGHT_NO_HEIGHT | MAP_HEIGHT_HAS_FLIGHT_BOUNDS)
        map_path.write_bytes(bytes(raw))
        assert not check(no_flight, 0, (32.0, 32.0, 40.0))["pass"], \
            "a height section claiming flight bounds without them was accepted"
        bogus_section = Path(temporary) / "bogus-section"
        write_synthetic_root(bogus_section, liquid_offset=99999999)
        assert not check(bogus_section, 0, (32.0, 32.0, 40.0))["pass"], "an out-of-file section was accepted"
        split = Path(temporary) / "split"
        write_synthetic_root(split, spawns=[
            ("Nsabbey.wmo", 72420, (32.0, 32.0, -60.0), (22.0, 22.0, -70.0), (42.0, 42.0, -50.0)),
            ("Pastor.wmo", 1, (32.0, 32.0, 40.0), (22.0, 22.0, 30.0), (42.0, 42.0, 50.0)),
            ("Shelf.wmo", 2, (32.0, 32.0, 140.0), (22.0, 22.0, 130.0), (42.0, 42.0, 150.0))],
            model_names=("Nsabbey.wmo", "Pastor.wmo", "Shelf.wmo"))
        assert not check(split, 0, (32.0, 32.0, 40.0))["pass"], \
            "residual signs cancelling into a passing median was accepted"
        # An unavailable neighbouring terrain height must render, not raise.
        unavailable = {"data_root": "x", "map": 0, "anchor": [0.0, 0.0, 0.0], "grid": [32, 32],
                       "checks": [{"check": "c", "pass": True, "detail": "d"}], "gaps": [],
                       "facts": {"anchor": {"position": [0.0, 0.0, 0.0], "terrain_height": None,
                                            "stand_height": None},
                                 "vmap_spawns": 1, "spawns_in_tile": 0,
                                 "median_absolute_residual": None, "obstacles": [
                                     {"name": "n", "id": 1, "distance_from_anchor": 1.0, "footprint": 1.0,
                                      "world_position": [0.0, 0.0, 0.0], "residual": None,
                                      "world_bounds": [[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]]}]},
                       "pass": True}
        assert "unavailable" in render(unavailable)
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
    except (OSError, ValueError, TypeError, struct.error, KeyError) as error:
        print(f"collision geometry check failed: {error}", file=sys.stderr)
        sys.exit(1)
