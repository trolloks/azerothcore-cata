"""Validate produced Cata collision geometry against the server's own readers and pin the scenario.

This is the pre-flight for the Plan 32 native acceptance, not a substitute for it. It reads the
artifacts the server will read, using the server's own formats, naming and conversions, and it
checks that they can actually be loaded: every file the loaders open is required, every declared
section is inside the file, and every payload carries the header the reader validates.

It never claims that a server loaded the artifacts or that a query matched. Its path check walks the
tile's own polygon graph, which is connectivity in the artifact, not a creature walking the route;
those remain the acceptance's job, and this only makes a silently degraded run detectable in advance.

Layouts and constants are taken from this tree, not from a summary:
  * maps/{id:03}{gx:02}{gy:02}.map        GridTerrainData::Load / LoadAreaData / LoadLiquidData /
                                          LoadHolesData / getHeightFromFloat / getHeightFromFlat
  * vmaps/{id:03}_{gy:02}_{gx:02}.vmtile  StaticMapTree::LoadMapTile / ModelSpawn::readFromFile
  * vmaps/{id:03}.vmtree                  StaticMapTree::InitMap
  * vmaps/<spawn.name>.vmo                WorldModelStore::AcquireModelInstance (always ".vmo")
  * mmaps/{id:03}.mmap                    MMapMgr::LoadNavMesh (a single dtNavMeshParams)
  * mmaps/{id:03}{gx:02}{gy:02}.mmtile    MMapMgr::LoadTile / MmapTileHeader (56 bytes, asserted in
                                          MapDefines.h) / dtNavMesh::addTile's payload validation /
                                          the dtMeshTile vertex and polygon arrays, whose dtPoly.neis
                                          is the graph a path query walks
Grid coordinates follow the server's ComputeGridCoord, not ComputeGridCoordSimple: gx = 32 - x/533.33
and gy = 32 - y/533.33, and vmap tile files are named with {gy}_{gx} while map and mmap tiles use
{gx}{gy}. The two orders disagree, which is exactly what the tile/contents agreement check detects.
"""
import argparse
import heapq
import json
import math
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
# dtPoly is read through these two, not through the file's own stride: vertCount above six or a
# vertex/neighbour index outside its array is a tile this Detour cannot walk (DT_VERTS_PER_POLYGON is
# a build-time constant), so the arrays are rejected instead of truncated.
DT_VERTS_PER_POLYGON = 6
DT_POLYTYPE_OFFMESH_CONNECTION = 1
# dtPoly.neis holds the neighbour polygon index plus one, and 0x8000|side marks a link into another
# tile (dtNavMesh::connectIntLinks reads exactly that), so one tile's own polygons carry its graph.
DT_EXT_LINK = 0x8000
# PathGenerator::CreateFilter's include mask for a creature that can walk (MapDefines.h's NAV_GROUND).
# A creature that can enter water adds NAV_WATER|NAV_MAGMA, so a route that exists only through those
# flags is not one a walker takes; dtNavMeshQuery::passFilter drops every polygon whose flags do not
# intersect this mask, including the ones with flags 0.
NAV_GROUND = 0x01
SIZE_OF_GRIDS = 533.33333
MAP_RESOLUTION = 128
MAX_NUMBER_OF_GRIDS = 64
# VMapMgr2::convertPositionToInternalRep: the vmap files are in the internal representation, and
# every query converts into it, so reading the artifacts back out needs the inverse.
MID = 0.5 * MAX_NUMBER_OF_GRIDS * SIZE_OF_GRIDS
VMAP_TILE_AGREEMENT_MIN = 0.80
ON_GROUND_MEDIAN_MAX = 5.0
# Unit::GetCollisionHeight's default for a normally scaled unit (DEFAULT_COLLISION_HEIGHT), which
# WorldObject::IsWithinLOS adds to both endpoints before Map::isInLineOfSight runs.
DEFAULT_COLLISION_HEIGHT = 2.03128
WALL_MAX_VERTICAL = 0.35
WALL_MIN_EDGE = 1.0
WALL_GROUND_MARGIN = 3.0


def grid_coord(x, y):
    """The server's ComputeGridCoord, clamped to the 64x64 map (GridDefines.h)."""
    return (max(0, min(MAX_NUMBER_OF_GRIDS - 1, int(32 - x / SIZE_OF_GRIDS))),
            max(0, min(MAX_NUMBER_OF_GRIDS - 1, int(32 - y / SIZE_OF_GRIDS))))


def internal_to_world(x, y, z):
    """Inverse of VMapMgr2::convertPositionToInternalRep (mid - world, so world = mid - internal)."""
    return (MID - x, MID - y, z)


def world_to_internal(x, y, z):
    """VMapMgr2::convertPositionToInternalRep, the direction every vmap query goes."""
    return (MID - x, MID - y, z)


def vec_sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def vec_cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def vec_dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def vec_length(a):
    return vec_dot(a, a) ** 0.5


def vec_normalized(a):
    length = vec_length(a)
    return (a[0] / length, a[1] / length, a[2] / length) if length else (0.0, 0.0, 0.0)


def mat3_mul(a, b):
    return tuple(tuple(sum(a[row][k] * b[k][col] for k in range(3)) for col in range(3))
                 for row in range(3))


def mat3_transpose(a):
    return tuple(tuple(a[col][row] for col in range(3)) for row in range(3))


def mat3_vec(m, v):
    """G3D::Matrix3::operator*(Vector3): row-major, row dot column."""
    return tuple(m[row][0] * v[0] + m[row][1] * v[1] + m[row][2] * v[2] for row in range(3))


def rotation_matrix(rotation):
    """G3D::Matrix3::fromEulerAnglesZYX(pi*y/180, pi*x/180, pi*z/180), i.e. Rz * (Ry * Rx)."""
    angle_z, angle_y, angle_x = (math.radians(rotation[1]), math.radians(rotation[0]),
                                 math.radians(rotation[2]))
    cos_z, sin_z = math.cos(angle_z), math.sin(angle_z)
    cos_y, sin_y = math.cos(angle_y), math.sin(angle_y)
    cos_x, sin_x = math.cos(angle_x), math.sin(angle_x)
    matrix_z = ((cos_z, -sin_z, 0.0), (sin_z, cos_z, 0.0), (0.0, 0.0, 1.0))
    matrix_y = ((cos_y, 0.0, sin_y), (0.0, 1.0, 0.0), (-sin_y, 0.0, cos_y))
    matrix_x = ((1.0, 0.0, 0.0), (0.0, cos_x, -sin_x), (0.0, sin_x, cos_x))
    return mat3_mul(matrix_z, mat3_mul(matrix_y, matrix_x))


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


def read_bih(data, offset, path):
    """BoundingIntervalHierarchy::readFromFile: bounds, entry count, object count.

    Returns the object count and the offset after the block; every declared count is bounds-checked
    because the server's fread fails on a short read.
    """
    if offset + 28 > len(data):
        raise ValueError(f"{path}: BIH block at {offset} is beyond the file")
    tree_size = struct.unpack_from("<I", data, offset + 24)[0]
    offset += 28
    if offset + 4 * tree_size > len(data):
        raise ValueError(f"{path}: BIH declares {tree_size} tree entries beyond the file")
    offset += 4 * tree_size
    count = struct.unpack_from("<I", data, offset)[0]
    offset += 4
    if offset + 4 * count > len(data):
        raise ValueError(f"{path}: BIH declares {count} objects beyond the file")
    return count, offset + 4 * count


class WorldModel:
    """A vmaps/<name>.vmo, parsed as WorldModel::readFile and GroupModel::readFromFile do."""

    def __init__(self, data, path):
        self.path = path
        self.size = len(data)
        if data[0:8] != VMAP_MAGIC:
            raise ValueError(f"{path}: magic {data[0:8]!r} is not {VMAP_MAGIC!r}")
        if data[8:12] != b"WMOD":
            raise ValueError(f"{path}: expected a WMOD chunk at 8, found {data[8:12]!r}")
        offset = 20  # magic, WMOD, chunkSize, RootWMOID
        if data[offset:offset + 4] != b"GMOD":
            raise ValueError(f"{path}: expected a GMOD chunk, found {data[offset:offset + 4]!r}")
        groups = struct.unpack_from("<I", data, offset + 4)[0]
        offset += 8
        self.groups = []
        for index in range(groups):
            if offset + 32 > len(data):
                raise ValueError(f"{path}: group {index} starts beyond the file")
            bound = struct.unpack_from("<6f", data, offset)
            flags, wmo_id = struct.unpack_from("<2I", data, offset + 24)
            offset += 32
            if data[offset:offset + 4] != b"VERT":
                raise ValueError(f"{path}: group {index} expected VERT, found {data[offset:offset + 4]!r}")
            count = struct.unpack_from("<I", data, offset + 8)[0]
            offset += 12
            group = {"index": index, "flags": flags, "wmo_id": wmo_id,
                     "bound": (bound[0:3], bound[3:6]), "vertices": (), "triangles": ()}
            self.groups.append(group)
            if count == 0:
                # GroupModel::readFromFile returns here: a group without collision geometry.
                continue
            if offset + 12 * count > len(data):
                raise ValueError(f"{path}: group {index} declares {count} vertices beyond the file")
            group["vertices"] = struct.unpack_from(f"<{3 * count}f", data, offset)
            offset += 12 * count
            if data[offset:offset + 4] != b"TRIM":
                raise ValueError(f"{path}: group {index} expected TRIM, found {data[offset:offset + 4]!r}")
            triangles = struct.unpack_from("<I", data, offset + 8)[0]
            offset += 12
            if offset + 12 * triangles > len(data):
                raise ValueError(f"{path}: group {index} declares {triangles} triangles beyond the file")
            group["triangles"] = struct.unpack_from(f"<{3 * triangles}I", data, offset)
            worst = max(group["triangles"]) if group["triangles"] else 0
            if worst >= count:
                # The byte count can be satisfied while the indices are unusable; the server indexes
                # the vertex array directly, so this is invalid collision data, not an obstacle.
                raise ValueError(f"{path}: group {index} triangle index {worst} is outside its "
                                 f"{count} vertices")
            offset += 12 * triangles
            if data[offset:offset + 4] != b"MBIH":
                raise ValueError(f"{path}: group {index} expected MBIH, found {data[offset:offset + 4]!r}")
            group["mesh_objects"], offset = read_bih(data, offset + 4, path)
            if data[offset:offset + 4] != b"LIQU":
                raise ValueError(f"{path}: group {index} expected LIQU, found {data[offset:offset + 4]!r}")
            liquid_size = struct.unpack_from("<I", data, offset + 4)[0]
            offset += 8
            if liquid_size:
                offset = skip_wmo_liquid(data, offset, path)
        if data[offset:offset + 4] != b"GBIH":
            raise ValueError(f"{path}: expected a GBIH chunk after the groups, "
                             f"found {data[offset:offset + 4]!r}")
        self.group_objects, offset = read_bih(data, offset + 4, path)
        self.consumed = offset

    def triangle_count(self):
        return sum(len(group["triangles"]) // 3 for group in self.groups)

    def triangle(self, group_index, triangle_index):
        group = self.groups[group_index]
        base = triangle_index * 3
        vertices = group["vertices"]
        return tuple(tuple(vertices[3 * group["triangles"][base + corner] + axis]
                           for axis in range(3)) for corner in range(3))


def skip_wmo_liquid(data, offset, path):
    """WmoLiquid::readFromFile: tiles, a corner, a type, then heights and flags."""
    if offset + 24 > len(data):
        raise ValueError(f"{path}: liquid block at {offset} is beyond the file")
    tiles_x, tiles_y = struct.unpack_from("<2I", data, offset)
    offset += 24  # tilesX, tilesY, corner (3 floats), type
    if tiles_x and tiles_y:
        offset += 4 * (tiles_x + 1) * (tiles_y + 1) + tiles_x * tiles_y
    else:
        offset += 4
    if offset > len(data):
        raise ValueError(f"{path}: liquid block runs beyond the file")
    return offset


class TerrainReader:
    """The server's terrain heights over one map, tile by tile (getHeightFromFloat)."""

    def __init__(self, data_root, map_id=0):
        self.data_root = Path(data_root)
        self.map_id = map_id
        self._tiles = {}

    def height(self, x, y):
        tile_x, tile_y = grid_coord(x, y)
        key = (tile_x, tile_y)
        if key not in self._tiles:
            path = self.data_root / "maps" / f"{self.map_id:03d}{tile_x:02d}{tile_y:02d}.map"
            tile = None
            if path.is_file():
                try:
                    tile = MapTile(path.read_bytes(), str(path))
                    tile.load_height_section()
                except (ValueError, struct.error):
                    tile = None
            self._tiles[key] = tile
        tile = self._tiles[key]
        return None if tile is None else tile.height(x, y)


class ModelCache:
    """vmaps/<name>.vmo files, parsed once, with failures remembered as absent."""

    def __init__(self, data_root):
        self.data_root = Path(data_root)
        self._models = {}

    def load(self, name):
        if name not in self._models:
            path = self.data_root / "vmaps" / f"{name}.vmo"
            model = None
            if path.is_file():
                try:
                    model = WorldModel(path.read_bytes(), str(path))
                except (ValueError, struct.error):
                    model = None
            self._models[name] = model
        return self._models[name]


def bound_crossed(bound, origin, direction, max_distance):
    """G3D::Ray::intersectionTime(AABox), as a slab test over the ray's reach."""
    lower, upper = 0.0, max_distance
    for axis in range(3):
        low, high = bound[0][axis], bound[1][axis]
        delta, start = direction[axis], origin[axis]
        if abs(delta) < 1e-12:
            if start < low or start > high:
                return False
            continue
        first, second = (low - start) / delta, (high - start) / delta
        if first > second:
            first, second = second, first
        lower, upper = max(lower, first), min(upper, second)
        if lower > upper:
            return False
    return True


def segment_hits(spawns, models, first, second):
    """StaticMapTree::isInLineOfSight's work for one segment: bound culling, then triangles."""
    origin = world_to_internal(*first)
    target = world_to_internal(*second)
    delta = vec_sub(target, origin)
    length = vec_length(delta)
    if length < 1e-6:
        return [], 0.0
    direction = tuple(component / length for component in delta)
    hits = []
    for spawn in spawns:
        if not spawn["internal_bound"]:
            continue
        if not bound_crossed(spawn["internal_bound"], origin, direction, length):
            continue
        model = models.load(spawn["name"])
        if model is None:
            continue
        hit = model_hit(Instance(spawn), model, origin, direction, length)
        if hit:
            hits.append({"name": spawn["name"], "id": spawn["id"], "distance": hit[0],
                         "group": hit[1], "triangle": hit[2]})
    hits.sort(key=lambda hit: hit["distance"])
    return hits, length


def point_is_free(spawns, models, point, probe=0.75):
    """A point clear of collision geometry sideways: not embedded in a wall or a prop.

    Vertical probes are deliberately omitted: a building WMO carries its own floors and plaza, so a
    point standing on them is above geometry without being inside it.
    """
    x, y, z = point
    probes = ((x + probe, y, z), (x - probe, y, z), (x, y + probe, z), (x, y - probe, z))
    return all(not segment_hits(spawns, models, point, other)[0] for other in probes)


class Instance:
    """ModelInstance and ModelInstance::intersectRay, in the internal representation."""

    def __init__(self, spawn):
        self.spawn = spawn
        self.rotation = rotation_matrix(spawn["rotation"])
        self.inverse = mat3_transpose(self.rotation)  # a rotation's inverse is its transpose
        self.inverse_scale = 1.0 / spawn["scale"] if spawn["scale"] else 0.0

    def to_model(self, origin, direction):
        """iInvRot * (origin - iPos) * iInvScale and iInvRot * direction, as the server does."""
        offset = vec_sub(origin, self.spawn["internal"])
        rotated = mat3_vec(self.inverse, offset)
        return (tuple(component * self.inverse_scale for component in rotated),
                mat3_vec(self.inverse, direction))

    def to_internal(self, point):
        """The forward transform the assembler uses: iRotation * (point * iScale) + iPos."""
        scaled = tuple(component * self.spawn["scale"] for component in point)
        rotated = mat3_vec(self.rotation, scaled)
        return tuple(rotated[axis] + self.spawn["internal"][axis] for axis in range(3))


def ray_triangle(origin, direction, a, b, c, max_distance):
    """Moller-Trumbore; the hit distance along direction, or None."""
    edge1 = vec_sub(b, a)
    edge2 = vec_sub(c, a)
    pvec = vec_cross(direction, edge2)
    determinant = vec_dot(edge1, pvec)
    if abs(determinant) < 1e-9:
        return None
    inverse = 1.0 / determinant
    tvec = vec_sub(origin, a)
    u = vec_dot(tvec, pvec) * inverse
    if u < -1e-6 or u > 1 + 1e-6:
        return None
    qvec = vec_cross(tvec, edge1)
    v = vec_dot(direction, qvec) * inverse
    if v < -1e-6 or u + v > 1 + 1e-6:
        return None
    distance = vec_dot(edge2, qvec) * inverse
    return distance if 1e-4 <= distance <= max_distance else None


def model_hit(instance, model, origin, direction, max_distance):
    """The nearest triangle of a model the ray hits, with its group and triangle index."""
    model_origin, model_direction = instance.to_model(origin, direction)
    model_max = max_distance * instance.inverse_scale
    best = None
    for group in model.groups:
        if not group["triangles"]:
            continue
        for index in range(len(group["triangles"]) // 3):
            distance = ray_triangle(model_origin, model_direction,
                                    *model.triangle(group["index"], index), model_max)
            if distance is not None and (best is None or distance < best[0]):
                best = (distance, group["index"], index)
    if best is None:
        return None
    # The server scales the hit distance back and reports it in internal units.
    return (best[0] * instance.spawn["scale"], best[1], best[2])


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
    offset = 13
    try:
        count, offset = read_bih(data, offset, "<vmtree>")
    except (ValueError, struct.error) as error:
        return f"tree BIH could not be parsed: {error}", None
    if data[offset:offset + 4] != b"GOBJ":
        return f"chunk after the BIH is {data[offset:offset + 4]!r}, expected b'GOBJ'", None
    return None, {"tiled": tiled, "tree_entries": None, "objects": count,
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
        internal_bound = bound
        world = internal_to_world(*pos)
        if bound:
            world_bounds = (internal_to_world(*bound[0]), internal_to_world(*bound[1]))
            bound = ((min(world_bounds[0][0], world_bounds[1][0]), min(world_bounds[0][1], world_bounds[1][1]),
                      bound[0][2]),
                     (max(world_bounds[0][0], world_bounds[1][0]), max(world_bounds[0][1], world_bounds[1][1]),
                      bound[1][2]))
        spawns.append({"index": index, "flags": flags, "adt_id": adt_id, "id": spawn_id,
                       "internal": pos, "world": world, "rotation": rotation, "scale": scale,
                       "bound": bound, "internal_bound": internal_bound, "name": name,
                       "referenced": referenced})
    return spawns, offset


class MmapFiles:
    """The two mmaps artifacts, checked the way MMapMgr and dtNavMesh consume them."""

    def __init__(self, par_path, tile_path):
        self.params_path = par_path
        self.params_size = len(par_path.read_bytes())
        self._read(tile_path.read_bytes())

    @classmethod
    def from_bytes(cls, data):
        """The same view over an in-memory tile: the self-check has no data root to read from."""
        instance = cls.__new__(cls)
        instance.params_path, instance.params_size = None, 0
        instance._read(data)
        return instance

    def _read(self, data):
        self.data = data
        self.tile_size = len(data)
        (self.magic, self.dt_version, self.mmap_version,
         self.declared_size) = struct.unpack_from("<4I", data, 0)
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
                "walkable_height": fields[15], "walkable_radius": fields[16],
                "walkable_climb": fields[17],
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

    def arrays(self):
        """The tile's dtMeshTile vertex and polygon arrays, and the problems that make them unusable.

        addTile copies these arrays straight out of the payload, so a count or index outside its own
        array is corrupt data rather than a missing check: the path query is what walks them.
        """
        def aligned(value):
            return (value + 3) & ~3

        payload = self.payload()
        if payload is None:
            return [], [], [f"the payload is shorter than a {DT_MESH_HEADER_SIZE}-byte dtMeshHeader"]
        if self.required_payload(payload) > self.declared_size:
            return [], [], [f"the declared {self.declared_size}-byte payload is smaller than the "
                            f"{self.required_payload(payload)} bytes its counts describe"]
        offset = self.header_size + DT_MESH_HEADER_SIZE
        verts = struct.unpack_from(f"<{3 * payload['vert_count']}f", self.data, offset)
        offset += aligned(12 * payload["vert_count"])
        problems, polygons = [], []
        for index in range(payload["poly_count"]):
            base = offset + DT_POLY_SIZE * index
            vert_count, area_and_type = struct.unpack_from("<2B", self.data, base + 30)
            polygon = {"index": index,
                       "verts": struct.unpack_from(f"<{DT_VERTS_PER_POLYGON}H", self.data, base + 4),
                       "neis": struct.unpack_from(f"<{DT_VERTS_PER_POLYGON}H", self.data, base + 16),
                       "flags": struct.unpack_from("<H", self.data, base + 28)[0],
                       "vert_count": vert_count,
                       "type": area_and_type >> 6,
                       "area": area_and_type & 0x3f}
            if vert_count > DT_VERTS_PER_POLYGON:
                problems.append(f"polygon {index} declares {vert_count} vertices, but this Detour "
                                f"reads DT_VERTS_PER_POLYGON {DT_VERTS_PER_POLYGON} per polygon")
                continue
            if vert_count < 3:
                problems.append(f"polygon {index} declares {vert_count} vertices, not a polygon")
                continue
            for edge in range(vert_count):
                if polygon["verts"][edge] >= payload["vert_count"]:
                    problems.append(f"polygon {index} edge {edge} indexes vertex "
                                    f"{polygon['verts'][edge]} of {payload['vert_count']}")
            polygons.append(polygon)
        return verts, polygons, problems


def navmesh_graph(mmap):
    """(verts, polygons, adjacency, problems): one tile's polygon graph, as the tile stores it."""
    verts, polygons, problems = mmap.arrays()
    if problems:
        return verts, polygons, [], problems
    adjacency = []
    for polygon in polygons:
        neighbours = set()
        for edge in range(polygon["vert_count"]):
            neighbour = polygon["neis"][edge]
            if neighbour == 0 or neighbour & DT_EXT_LINK:
                continue
            if neighbour - 1 >= len(polygons):
                problems.append(f"polygon {polygon['index']} edge {edge} links to polygon "
                                f"{neighbour - 1} of {len(polygons)}")
                continue
            neighbours.add(neighbour - 1)
        adjacency.append(neighbours)
    return verts, polygons, adjacency, problems


def nav_coord(world):
    """PathGenerator::BuildPolyPath's frame: dtPoint = (world y, world z, world x)."""
    return (world[1], world[2], world[0])


def polygon_centre(polygon, verts):
    """dtNavMeshQuery::getPolyCenter: the mean of the polygon's own vertices."""
    centre = [0.0, 0.0, 0.0]
    for edge in range(polygon["vert_count"]):
        base = polygon["verts"][edge] * 3
        for axis in range(3):
            centre[axis] += verts[base + axis]
    return tuple(component / polygon["vert_count"] for component in centre)


def polygon_vertex(polygon, verts, edge):
    """One vertex of a polygon, through the tile's own index array."""
    base = polygon["verts"][edge] * 3
    return (verts[base], verts[base + 1], verts[base + 2])


def polygon_height(polygon, verts, point):
    """The polygon's surface height under a point, or None when the point is outside it.

    A polygon is convex, so its vertex fan covers it and the point falls in exactly one triangle, whose
    barycentric interpolation gives the height there (dtNavMeshQuery::closestHeightPointTriangle's
    answer for the polygon's own surface). A ramp or a staircase does not have one height per polygon,
    so a polygon's centre height says nothing about the surface under a given point.
    """
    corners = [polygon_vertex(polygon, verts, edge) for edge in range(polygon["vert_count"])]
    for index in range(1, len(corners) - 1):
        height = triangle_height((corners[0], corners[index], corners[index + 1]), point)
        if height is not None:
            return height
    return None


def triangle_height(triangle, point):
    """The height on a triangle at a point's horizontal position, or None when it is outside it."""
    first, second, third = triangle
    x, z = point[0], point[2]
    area = (second[2] - third[2]) * (first[0] - third[0]) + (third[0] - second[0]) * (first[2] - third[2])
    if abs(area) < 1e-12:
        return None
    first_weight = ((second[2] - third[2]) * (x - third[0]) + (third[0] - second[0]) * (z - third[2])) / area
    second_weight = ((third[2] - first[2]) * (x - third[0]) + (first[0] - third[0]) * (z - third[2])) / area
    third_weight = 1.0 - first_weight - second_weight
    if min(first_weight, second_weight, third_weight) < -1e-9:
        return None
    return (first_weight * first[1] + second_weight * second[1] + third_weight * third[1])


def navmesh_polygon(verts, polygons, point, ground, include_flags=NAV_GROUND):
    """The walkable polygon at a point: the containing one whose surface is nearest the ground.

    A roof, a balcony or a bridge also contains the point horizontally, so the surface height under the
    point picks the polygon the creature would stand on; the tie-break is the horizontal centre
    distance. Polygons the creature's filter rejects are not candidates at all.
    """
    best, best_score = None, None
    for index, polygon in enumerate(polygons):
        if polygon["type"] == DT_POLYTYPE_OFFMESH_CONNECTION or not polygon["flags"] & include_flags:
            continue
        height = polygon_height(polygon, verts, point)
        if height is None:
            continue
        centre = polygon_centre(polygon, verts)
        score = (abs(height - ground), math.hypot(centre[0] - point[0], centre[2] - point[2]))
        if best_score is None or score < best_score:
            best, best_score = index, score
    return best


def creature_graph(polygons, adjacency, include_flags=NAV_GROUND):
    """(walkable indices, graph): the creature's filter applied to the polygons and to the links.

    detNavMeshQuery's passFilter rejects a polygon the include mask does not match, so a route can
    neither start, end, nor pass through one: a water-only polygon must not supply a false land route
    or join two components a walker cannot cross between.
    """
    walkable = [index for index, polygon in enumerate(polygons) if polygon["flags"] & include_flags]
    allowed = set(walkable)
    return walkable, [{neighbour for neighbour in neighbours if neighbour in allowed}
                      for neighbours in adjacency]


def segment_polygon(polygon, verts, first, second):
    """dtIntersectSegmentPoly2D: (tmin, tmax, entering edge, leaving edge) in the horizontal plane.

    The same vertex walk and the same perpendicular in (x, z) that Detour uses, so a segment that
    leaves the polygon does so through the edge raycast follows. The edge of loop index i runs from
    corner i-1 to corner i, and Detour stores that closing index as nverts-1, so the edge number is
    wrapped here too; -1 is reserved for a crossing that does not exist, which is how
    dtNavMeshQuery::raycast detects that the ray ends in this polygon.
    """
    tmin, tmax, entering, leaving = 0.0, 1.0, -1, -1
    direction = (second[0] - first[0], second[1] - first[1], second[2] - first[2])
    corners = [polygon_vertex(polygon, verts, edge) for edge in range(polygon["vert_count"])]
    for index in range(len(corners)):
        previous = corners[index - 1]
        corner = corners[index]
        edge = (corner[0] - previous[0], corner[1] - previous[1], corner[2] - previous[2])
        difference = (first[0] - previous[0], first[1] - previous[1], first[2] - previous[2])
        perpendicular = edge[2] * difference[0] - edge[0] * difference[2]
        denominator = direction[2] * edge[0] - direction[0] * edge[2]
        if abs(denominator) < 1e-12:
            if perpendicular < 0:
                return None
            continue
        fraction = perpendicular / denominator
        if denominator < 0:  # the segment enters across this edge
            if fraction > tmin:
                tmin, entering = fraction, (index - 1) % len(corners)
                if tmin > tmax:
                    return None
        elif fraction < tmax:  # the segment leaves across this edge
            tmax, leaving = fraction, (index - 1) % len(corners)
            if tmax < tmin:
                return None
    return tmin, tmax, entering, leaving


def navmesh_line_walk(verts, polygons, include_flags, start_polygon, goal_polygon, first, second, climb):
    """(crossed, reached, stopped): whether a walker can follow a straight line polygon by polygon.

    This is the single-tile case of dtNavMeshQuery::raycast: cast the fixed segment at the current
    polygon, follow the edge it leaves by, take the neighbour the tile's own dtPoly.neis gives (the same
    mapping connectIntLinks builds links from), and skip an off-mesh connection or a polygon the
    walker's filter rejects. On top of raycast, the surfaces on each side of the crossed edge have to
    meet within the agent's climb, because a walker cannot take a step above it even though a ray can.

    Reaching the goal polygon means the navmesh covers the straight line, which is what a navmesh that
    disagrees with the collision would do; a missing neighbour, a filtered-out neighbour or too big a
    step stops the walk, which is an obstructed line. The line's height against a global chord decides
    nothing here: a continuous ramp is walkable the whole way up.
    """
    crossed, polygon = [start_polygon], start_polygon
    for _ in range(len(polygons)):
        if polygon == goal_polygon:
            return crossed, True, None
        intersection = segment_polygon(polygons[polygon], verts, first, second)
        if intersection is None:
            return crossed, False, f"the segment does not cross polygon {polygon}"
        _, furthest, _, leaving = intersection
        if leaving == -1:
            # The segment ends inside this polygon, so the goal's own polygon is another level over the
            # same point: the two surfaces at the goal have to meet within the climb.
            step = portal_step(polygons[polygon], polygons[goal_polygon], verts, second)
            if step is not None and step <= climb:
                if polygon != goal_polygon:
                    crossed.append(goal_polygon)
                return crossed, True, None
            return crossed, False, (f"the segment ends on polygon {polygon}, which is "
                                    f"{'not measurable' if step is None else f'{step:.2f} m'} from the "
                                    f"goal's polygon {goal_polygon}")
        neighbour = polygons[polygon]["neis"][leaving]
        edge = leaving
        if neighbour == 0:
            return crossed, False, f"polygon {polygon} edge {edge} has no neighbour"
        if neighbour & DT_EXT_LINK:
            return crossed, False, f"polygon {polygon} edge {edge} leaves the tile"
        neighbour -= 1
        if polygons[neighbour]["type"] == DT_POLYTYPE_OFFMESH_CONNECTION:
            return crossed, False, f"polygon {polygon} edge {edge} is an off-mesh connection"
        if not polygons[neighbour]["flags"] & include_flags:
            return crossed, False, (f"polygon {neighbour} across edge {edge} fails the walker's "
                                    f"flags {include_flags:#04x}")
        # Height continuity at the transition, evaluated on each side of the shared edge.
        crossing = (first[0] + furthest * (second[0] - first[0]),
                    first[1] + furthest * (second[1] - first[1]),
                    first[2] + furthest * (second[2] - first[2]))
        step = portal_step(polygons[polygon], polygons[neighbour], verts, crossing)
        if step is None or step > climb:
            return crossed, False, (f"the step into polygon {neighbour} across edge {edge} is "
                                    f"{'not measurable' if step is None else f'{step:.2f} m'} "
                                    f"(climb {climb:.2f} m)")
        polygon = neighbour
        crossed.append(neighbour)
    return crossed, False, "the walk crossed every polygon in the tile without reaching the goal"


def portal_step(current, neighbour, verts, crossing):
    """The height difference between two polygons at a shared edge, or None if unmeasurable."""
    before = polygon_height(current, verts, crossing)
    after = polygon_height(neighbour, verts, crossing)
    if before is None or after is None:
        return None
    return abs(before - after)


def navmesh_route(verts, polygons, adjacency, start, goal):
    """(path, cost) over the tile's own polygon graph, costed at the polygon centres.

    dtNavMeshQuery::findPath walks these same links with area costs; connectivity is what this check
    asks about. Centre-to-centre steps overstate a smoothed path, so the cost is an upper bound and
    the result is the polygon sequence, not a walkable polyline.
    """
    if start is None or goal is None:
        return None, None
    centres = [polygon_centre(polygon, verts) for polygon in polygons]
    distance = {start: 0.0}
    previous = {}
    queue = [(0.0, start)]
    while queue:
        cost, index = heapq.heappop(queue)
        if cost > distance.get(index, math.inf):
            continue
        if index == goal:
            break
        for neighbour in adjacency[index]:
            step = math.hypot(centres[index][0] - centres[neighbour][0],
                              centres[index][2] - centres[neighbour][2])
            if cost + step < distance.get(neighbour, math.inf):
                distance[neighbour] = cost + step
                previous[neighbour] = index
                heapq.heappush(queue, (cost + step, neighbour))
    if goal not in distance:
        return None, None
    path = [goal]
    while path[-1] != start:
        path.append(previous[path[-1]])
    path.reverse()
    return path, distance[goal]


def reachable_polygons(adjacency, start):
    """The polygon indices a path from start can reach, walking the tile's own links."""
    if start is None:
        return set()
    seen, stack = {start}, [start]
    while stack:
        for neighbour in adjacency[stack.pop()]:
            if neighbour not in seen:
                seen.add(neighbour)
                stack.append(neighbour)
    return seen


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

    terrain_reader = TerrainReader(data_root, map_id)
    terrain_reader._tiles[(gx, gy)] = tile

    def terrain(x, y):
        return terrain_reader.height(x, y)

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
    obstacle_models = ModelCache(data_root)
    shapeless = []
    for obstacle in obstacles:
        model = obstacle_models.load(obstacle["name"])
        if model is None or not model.triangle_count():
            shapeless.append(obstacle["name"])
    record("vmap.model_geometry", not shapeless,
           "every reported obstacle's .vmo parses and carries collision triangles"
           + (f" - without geometry: {', '.join(shapeless)}" if shapeless else ""))
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


def tile_spawns(data_root, map_id, gx, gy, neighbours):
    """Every ModelSpawn in a tile and its neighbours: the set a query's broad phase can reach."""
    spawns = []
    for dx in range(-neighbours, neighbours + 1):
        for dy in range(-neighbours, neighbours + 1):
            path = Path(data_root) / "vmaps" / f"{map_id:03d}_{gy + dy:02d}_{gx + dx:02d}.vmtile"
            if path.is_file():
                spawns.extend(read_vmap_tile(path.read_bytes())[0])
    return spawns


def derive_los_pair(data_root, map_id=0, anchor=(-8949.95, -132.493, 83.5312),
                    obstacle_name="Nsabbey.wmo", neighbours=1, eye=DEFAULT_COLLISION_HEIGHT):
    """Derive the scenario's blocked and clear LOS pair from the obstacle's own triangles.

    Both endpoints sit at a unit's eye height above the terrain, which is what WorldObject::IsWithinLOS
    feeds to Map::isInLineOfSight, and the pair is only accepted when the blocking triangles all belong
    to the pinned obstacle and a parallel pair shifted outward is clear of every object.
    """
    data_root = Path(data_root)
    terrain = TerrainReader(data_root, map_id)
    models = ModelCache(data_root)
    gx, gy = grid_coord(anchor[0], anchor[1])
    spawns = tile_spawns(data_root, map_id, gx, gy, neighbours)
    instances = [spawn for spawn in spawns if spawn["name"] == obstacle_name and spawn["bound"]]
    if not instances:
        raise ValueError(f"no {obstacle_name} spawn near the anchor")
    store = max(instances, key=lambda spawn: (spawn["bound"][1][0] - spawn["bound"][0][0])
                * (spawn["bound"][1][1] - spawn["bound"][0][1]))
    model = models.load(store["name"])
    if model is None or not model.triangle_count():
        raise ValueError(f"{store['name']}.vmo carries no collision triangles")
    instance = Instance(store)
    # The model's own vertices transformed by the instance, to report against the stored bound.
    extent_low, extent_high = [1e9] * 3, [-1e9] * 3
    for group in model.groups:
        vertices = group["vertices"]
        for index in range(len(vertices) // 3):
            point = instance.to_internal((vertices[3 * index], vertices[3 * index + 1],
                                          vertices[3 * index + 2]))
            for axis in range(3):
                extent_low[axis] = min(extent_low[axis], point[axis])
                extent_high[axis] = max(extent_high[axis], point[axis])
    lo, hi = extent_low, extent_high

    centre = [0.0, 0.0, 0.0]
    for group in model.groups:
        low, high = group["bound"]
        for axis in range(3):
            centre[axis] += (low[axis] + high[axis]) / 2
    centre = instance.to_internal(tuple(component / len(model.groups) for component in centre))

    walls = []
    for group in model.groups:
        for index in range(len(group["triangles"]) // 3):
            corners = [instance.to_internal(vertex) for vertex in model.triangle(group["index"], index)]
            normal = vec_normalized(vec_cross(vec_sub(corners[1], corners[0]), vec_sub(corners[2], corners[0])))
            if abs(normal[2]) > WALL_MAX_VERTICAL:
                continue
            low, high = sorted(corners, key=lambda point: point[2])[:2]
            width = vec_length(vec_sub(high, low))
            if width < WALL_MIN_EDGE:
                continue
            midpoint = tuple((low[axis] + high[axis]) / 2 for axis in range(3))
            world = internal_to_world(*midpoint)
            ground = terrain.height(world[0], world[1])
            if ground is None or abs(world[2] - ground) > WALL_GROUND_MARGIN:
                continue
            horizontal = vec_normalized((normal[0], normal[1], 0.0))
            # Point the normal away from the model's centre: outward is the walkable side.
            toward_centre = (centre[0] - midpoint[0], centre[1] - midpoint[1], 0.0)
            if vec_dot(horizontal, toward_centre) > 0:
                horizontal = tuple(-component for component in horizontal)
            tangent = (-horizontal[1], horizontal[0], 0.0)
            walls.append({"group": group["index"], "triangle": index, "midpoint": midpoint,
                          "normal": horizontal, "tangent": tangent, "width": width,
                          "corners_world": [internal_to_world(*corner) for corner in corners]})
    walls.sort(key=lambda wall: wall["width"], reverse=True)

    def push(wall, along_normal, along_tangent=0.0):
        internal = tuple(wall["midpoint"][axis] + along_normal * wall["normal"][axis]
                         + along_tangent * wall["tangent"][axis] for axis in range(3))
        world = internal_to_world(*internal)
        ground = terrain.height(world[0], world[1])
        if ground is None:
            return None
        return (world[0], world[1], ground + eye)

    best = None
    for wall in walls[:60]:
        # The longest blocked segment through this wall is the most robust query, so try the widest
        # offsets first and keep the longest the wall supports. Every candidate is tested against the
        # full spawn set: segment_hits culls by the segment/bound intersection, the same broad phase
        # the tile's tree uses, so a large model whose bound centre is far away but whose edge crosses
        # the segment is still tested.
        midpoint = internal_to_world(*wall["midpoint"])
        blocked = None
        for offset in (20.0, 16.0, 13.0, 10.0, 8.0, 6.0, 5.0, 4.0, 3.0, 2.5):
            first, second = push(wall, offset), push(wall, -offset)
            if first is None or second is None:
                continue
            if not point_is_free(spawns, models, first) or not point_is_free(spawns, models, second):
                continue
            hits, length = segment_hits(spawns, models, first, second)
            if hits and hits[0]["name"] == obstacle_name:
                blocked = {"first": first, "second": second, "length": length, "hits": hits,
                           "offset": offset, "wall": wall}
                break
        if not blocked:
            continue
        if best is None or blocked["length"] > best["length"]:
            best = blocked
        if best["length"] >= 30.0:
            break
    blocked = best
    if blocked:
        wall = blocked["wall"]
        clear = None
        for shift in (3.0, 5.0, 7.0, 10.0, 14.0, 20.0, 28.0, 36.0, 45.0, 60.0):
            first = push(wall, shift + blocked["offset"])
            second = push(wall, shift - blocked["offset"])
            if first is None or second is None:
                continue
            if not point_is_free(spawns, models, first) or not point_is_free(spawns, models, second):
                continue
            hits, length = segment_hits(spawns, models, first, second)
            if not hits:
                clear = {"first": first, "second": second, "length": length, "hits": [], "offset": shift}
                break
    if blocked and clear:
        first_hit = blocked["hits"][0]
        # The hit and the artifact's own bound have to agree, or one of them is being misread.
        origin = world_to_internal(*blocked["first"])
        delta = vec_sub(world_to_internal(*blocked["second"]), origin)
        length = vec_length(delta)
        direction = tuple(component / length for component in delta)
        if not bound_crossed(store["internal_bound"], origin, direction, length):
            raise ValueError("the blocking hit does not intersect the obstacle's stored bound")
        if segment_hits(spawns, models, clear["first"], clear["second"])[0]:
            raise ValueError("the chosen clear line is hit when every spawn is considered")
        clear_origin = world_to_internal(*clear["first"])
        clear_delta = vec_sub(world_to_internal(*clear["second"]), clear_origin)
        clear_length = vec_length(clear_delta)
        clear_direction = tuple(component / clear_length for component in clear_delta)
        clear_crosses_bound = bound_crossed(store["internal_bound"], clear_origin, clear_direction,
                                            clear_length)
        return {"data_root": str(data_root), "map": map_id, "obstacle": store["name"],
                "obstacle_id": store["id"], "obstacle_position": list(store["world"]),
                "obstacle_triangles": model.triangle_count(), "eye_height": eye,
                "blocked": {"first": list(blocked["first"]), "second": list(blocked["second"]),
                            "length": blocked["length"], "offset_from_wall": blocked["offset"],
                            "hits": blocked["hits"],
                            "blocking_triangle_world": [list(corner)
                                                        for corner in blocked["wall"]["corners_world"]],
                            "wall_width": blocked["wall"]["width"]},
                "clear": {"first": list(clear["first"]), "second": list(clear["second"]),
                          "length": clear["length"], "offset_from_wall": clear["offset"], "hits": [],
                          "crosses_obstacle_bound": clear_crosses_bound},
                "hit_model": first_hit["name"],
                "model_extent_internal": [list(lo), list(hi)],
                "stored_bound_internal": [list(store["internal_bound"][0]),
                                          list(store["internal_bound"][1])],
                "terrain_at_blocked_midpoint": terrain.height(
                    (blocked["first"][0] + blocked["second"][0]) / 2,
                    (blocked["first"][1] + blocked["second"][1]) / 2)}
    raise ValueError("no wall of the pinned obstacle produced a blocked pair with a clear partner")


def chase_path(data_root, map_id=0, anchor=(-8949.95, -132.493, 83.5312), obstacle_name="Nsabbey.wmo",
               neighbours=1, eye=DEFAULT_COLLISION_HEIGHT, include_flags=NAV_GROUND):
    """Derive the pinned chase route around the obstacle from the tile's own polygon graph.

    The acceptance's "one normal creature chase that paths around that obstacle" needs an expectation
    to reproduce, and the LOS pair supplies its two ends: its blocked line crosses the pinned obstacle
    at eye height, so a creature walking from one end to the other has to leave the straight line. The
    route is the tile's own graph (dtPoly.neis) under the walker's own filter, and each end's polygon
    is the containing one whose surface under that point is nearest the ground. What the obstacle means
    for the chase is tested directly rather than inferred from the route's length: no walkable surface
    may cover the blocked line at the height a walker would take it, or a navmesh that disagrees with
    the collision would let the creature walk straight through the obstacle.

    Single tile only: a step that would leave through a DT_EXT_LINK has no neighbour here, so a route
    that exists only via an adjacent tile is reported unreachable. This is a derived expectation for a
    run to reproduce, not proof a server loaded the tile or a creature walked it.
    """
    data_root = Path(data_root)
    gx, gy = grid_coord(anchor[0], anchor[1])
    checks, gaps = [], []

    def record(name, ok, detail):
        checks.append({"check": name, "pass": bool(ok), "detail": detail})
        return ok

    pair = derive_los_pair(data_root, map_id, anchor, obstacle_name, neighbours, eye)
    mmap = MmapFiles(data_root / "mmaps" / f"{map_id:03d}.mmap",
                     data_root / "mmaps" / f"{map_id:03d}{gx:02d}{gy:02d}.mmtile")
    verts, polygons, adjacency, problems = navmesh_graph(mmap)
    record("navmesh.tile_arrays", not problems,
           f"{len(polygons)} polygons and {len(verts) // 3} vertices walked out of the tile's own "
           f"arrays" + ("; " + "; ".join(problems) if problems else ""))
    facts = {"data_root": str(data_root), "map": map_id, "grid": [gx, gy],
             "obstacle": pair["obstacle"], "obstacle_id": pair["obstacle_id"],
             "blocked_line_length": pair["blocked"]["length"],
             "not_verified": ("Whether a server loaded this tile, whether dtNavMeshQuery reproduces "
                              "these polygons, the route as a smoothed walkable polyline, and the "
                              "detail mesh's own surface, which this check does not parse.")}
    report = {"data_root": str(data_root), "map": map_id, "anchor": list(anchor), "grid": [gx, gy],
              "checks": checks, "gaps": gaps, "facts": facts, "pass": False}
    if problems:
        return report

    walkable, adjacency = creature_graph(polygons, adjacency, include_flags)
    record("navmesh.creature_filter", bool(walkable),
           f"{len(walkable)} of {len(polygons)} polygons pass the walker's flags {include_flags:#04x} "
           f"(PathGenerator::CreateFilter's mask; dtNavMeshQuery::passFilter drops the rest, so no "
           f"route may use one)")

    terrain = TerrainReader(data_root, map_id)
    ends = {}
    for role, world in (("start", pair["blocked"]["first"]), ("goal", pair["blocked"]["second"])):
        ground = terrain.height(world[0], world[1])
        surface = ground if ground is not None else world[2]
        point = nav_coord((world[0], world[1], surface))
        polygon = navmesh_polygon(verts, polygons, point, surface, include_flags)
        ends[role] = {"world": list(world), "ground": ground, "point": point, "polygon": polygon,
                      "surface": None if polygon is None
                                 else polygon_height(polygons[polygon], verts, point)}

    def end_note(role):
        end = ends[role]
        if end["polygon"] is None:
            return (f"{role} ({end['world'][0]:.2f},{end['world'][1]:.2f}) is not inside any walkable "
                    f"polygon of this tile")
        if end["ground"] is None:
            return (f"{role} ({end['world'][0]:.2f},{end['world'][1]:.2f}) is in polygon "
                    f"{end['polygon']}, whose surface is {end['surface']:.2f} m with no terrain height "
                    f"to compare it to")
        return (f"{role} ({end['world'][0]:.2f},{end['world'][1]:.2f}) is in polygon "
                f"{end['polygon']}, whose surface is {end['surface'] - end['ground']:+.2f} m above the "
                f"terrain there")

    record("navmesh.ends_on_navmesh", all(ends[role]["polygon"] is not None for role in ends),
           "; ".join(end_note(role) for role in ("start", "goal")))
    if ends["start"]["polygon"] is None or ends["goal"]["polygon"] is None:
        return report
    straight = math.hypot(ends["start"]["point"][0] - ends["goal"]["point"][0],
                          ends["start"]["point"][2] - ends["goal"]["point"][2])
    route, cost = navmesh_route(verts, polygons, adjacency,
                                ends["start"]["polygon"], ends["goal"]["polygon"])
    record("navmesh.route", route is not None,
           (f"a {len(route)}-polygon route from polygon {ends['start']['polygon']} to "
            f"{ends['goal']['polygon']} through the walker's own links" if route
            else f"no route links the start polygon {ends['start']['polygon']} to the goal polygon "
                 f"{ends['goal']['polygon']} inside this tile"))

    # The route's length cannot show that the corridor leaves the straight line, so the line itself is
    # walked: the walker's own straight line between the two endpoints is followed polygon by polygon
    # through the tile's portals at the surfaces it stands on, and reaching the goal polygon means the
    # navmesh covers the line the LOS pair blocks.
    line = [(end["world"][1], end["surface"], end["world"][0]) for end in (ends["start"], ends["goal"])]
    payload = mmap.payload()
    crossed, reached, stopped = navmesh_line_walk(verts, polygons, include_flags,
                                                  ends["start"]["polygon"], ends["goal"]["polygon"],
                                                  line[0], line[1], payload["walkable_climb"])
    record("navmesh.blocked_line_not_walkable", not reached,
           (f"the walker's straight line over the blocked {pair['blocked']['length']:.2f} m is "
            f"obstructed after {len(crossed)} polygon(s): {stopped}" if not reached
            else f"the navmesh lets a walker follow the blocked {pair['blocked']['length']:.2f} m line "
                 f"through {len(crossed)} polygons, so this tile disagrees with the collision the LOS "
                 f"pair recorded"))

    reachable = reachable_polygons(adjacency, ends["start"]["polygon"])
    outside = [index for index in walkable if index not in reachable]
    detached = None
    if outside:
        detached = min(outside, key=lambda index: math.hypot(
            polygon_centre(polygons[index], verts)[0] - ends["goal"]["point"][0],
            polygon_centre(polygons[index], verts)[2] - ends["goal"]["point"][2]))
    detached_route, _ = navmesh_route(verts, polygons, adjacency, ends["start"]["polygon"], detached)
    off_tile, off_tile_polygon = None, None
    for offset in (300.0, 600.0, 1200.0):
        candidate = (ends["goal"]["point"][0] + offset, ends["goal"]["point"][1],
                     ends["goal"]["point"][2] + offset)
        off_tile_polygon = navmesh_polygon(verts, polygons, candidate, candidate[1], include_flags)
        if off_tile_polygon is None:
            off_tile = candidate
            break
    record("navmesh.unreachable_rejected", detached_route is None and off_tile_polygon is None
           and (detached is not None or off_tile is not None),
           "no route is reported to a polygon the start cannot reach, and a point outside the tile "
           "resolves to no polygon instead of to the nearest one; "
           + (f"{len(walkable) - len(reachable)} of {len(walkable)} walkable polygons are outside the "
              f"start's component and the nearest of them to the goal is polygon {detached}"
              if detached is not None else "every walkable polygon in this tile is reachable from the "
                                           "start"))
    facts.update({
        "polygons": len(polygons), "vertices": len(verts) // 3, "walkable_polygons": len(walkable),
        "include_flags": include_flags,
        "agent": {"height": payload["walkable_height"], "radius": payload["walkable_radius"],
                  "climb": payload["walkable_climb"]},
        "reachable_polygons": len(reachable),
        "ends": {role: {"world": ends[role]["world"], "ground": ends[role]["ground"],
                        "polygon": ends[role]["polygon"],
                        "surface": ends[role]["surface"]} for role in ends},
        "straight_line": straight, "route_polygons": len(route) if route else None,
        "route_cost": cost, "line_polygons": crossed, "line_reached_goal": reached,
        "line_stopped": stopped,
        "detached_polygon": detached, "off_tile_point": list(off_tile) if off_tile else None})
    report["pass"] = all(entry["pass"] for entry in checks)
    return report


def render_chase(report):
    def number(value, spec=".2f"):
        return "unavailable" if value is None else format(value, spec)

    lines = [f"chase path: data-root {report['data_root']} map {report['map']} "
             f"grid {report['grid'][0]},{report['grid'][1]}"]
    for check in report["checks"]:
        lines.append(f"  [{'PASS' if check['pass'] else 'FAIL'}] {check['check']}: {check['detail']}")
    facts = report["facts"]
    if facts.get("ends"):
        for role in ("start", "goal"):
            end = facts["ends"][role]
            offset = (f"{end['surface'] - end['ground']:+.2f} m above the terrain"
                      if end["surface"] is not None and end["ground"] is not None else "unavailable")
            lines.append(f"  {role}: world ({end['world'][0]:.2f},{end['world'][1]:.2f},"
                         f"{end['world'][2]:.2f}) terrain {number(end['ground'], '.3f')} polygon "
                         f"{end['polygon']} surface {offset}")
        lines.append(f"  straight line {number(facts['straight_line'])} m walked through "
                     f"{len(facts['line_polygons'])} polygon(s), reaching the goal: "
                     f"{facts['line_reached_goal']}; route {facts['route_polygons']} polygons costed at "
                     f"{number(facts['route_cost'])} m over the polygon centres")
        lines.append(f"  {facts['reachable_polygons']} of {facts['walkable_polygons']} walkable "
                     f"polygons reachable from the start (flags {facts['include_flags']:#04x} of "
                     f"{facts['polygons']}); nearest unreachable polygon {facts['detached_polygon']}")
    lines.append("Chase path " + ("PASS" if report["pass"] else "FAIL")
                 + ". A route in this tile's graph is not a server-loaded navmesh, a creature chase, "
                   "or a smoothed walkable polyline.")
    return "\n".join(lines)


def render_los_pair(pair):
    lines = [f"LOS pair for {pair['obstacle']} id {pair['obstacle_id']} "
             f"({pair['obstacle_position'][0]:.2f},{pair['obstacle_position'][1]:.2f},"
             f"{pair['obstacle_position'][2]:.2f}); eye height {pair['eye_height']:.5f}"]
    blocked, clear = pair["blocked"], pair["clear"]
    lines.append(f"  BLOCKED  ({blocked['first'][0]:9.2f},{blocked['first'][1]:8.2f},"
                 f"{blocked['first'][2]:7.2f}) -> ({blocked['second'][0]:9.2f},{blocked['second'][1]:8.2f},"
                 f"{blocked['second'][2]:7.2f})  {blocked['length']:6.2f} m, "
                 f"{len(blocked['hits'])} hit(s)")
    for hit in blocked["hits"][:4]:
        lines.append(f"     hit {hit['distance']:6.2f} m by {hit['name']} group {hit['group']} "
                     f"triangle {hit['triangle']}")
    lines.append(f"  CLEAR    ({clear['first'][0]:9.2f},{clear['first'][1]:8.2f},{clear['first'][2]:7.2f})"
                 f" -> ({clear['second'][0]:9.2f},{clear['second'][1]:8.2f},{clear['second'][2]:7.2f})"
                 f"  {clear['length']:6.2f} m, no hits")
    lines.append(f"  the blocked line crosses the obstacle's stored bound, the clear line "
                 f"{'does' if clear['crosses_obstacle_bound'] else 'does not'} "
                 f"(that bound is the model extent, which for a building includes its grounds)")
    lines.append("  Derived from the obstacle's triangles, not its bounds: this is the expectation the "
                 "native acceptance has to reproduce.")
    return "\n".join(lines)


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
                 f"server loaded it, that any query matched, or that any creature walks a route; the "
                 f"chase route's expectation is --chase.")
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
                 version=DT_NAVMESH_VERSION, poly_count=10, vert_count=20, arrays=None):
    """A tile whose counts describe its payload; arrays=(vertices, polygons) writes real arrays.

    Without arrays the two arrays are padding of the declared size, which is enough for the header and
    payload-arithmetic cases; the path query needs real ones. A polygon is (vertex indices, neis,
    flags) with the file's own neighbour encoding (neighbour index plus one), and a positive fixture
    needs a walkable flag: a polygon with flags 0 fails the creature filter a query applies.
    """
    detail = b""
    if arrays:
        vertices, polygons = arrays
        vert_count, poly_count = len(vertices), len(polygons)
        detail = struct.pack(f"<{3 * vert_count}f", *[value for vertex in vertices for value in vertex])
        for polygon_verts, polygon_neis, polygon_flags in polygons:
            detail += struct.pack("<I", 0)
            detail += struct.pack(f"<{DT_VERTS_PER_POLYGON}H", *(tuple(polygon_verts) + (0,) * (
                DT_VERTS_PER_POLYGON - len(polygon_verts))))
            detail += struct.pack(f"<{DT_VERTS_PER_POLYGON}H", *(tuple(polygon_neis) + (0,) * (
                DT_VERTS_PER_POLYGON - len(polygon_neis))))
            detail += struct.pack("<HBB", polygon_flags, len(polygon_verts), 0)
    mesh = struct.pack("<15i10f", magic, version, tile_x, tile_y, 0, 0, poly_count, vert_count,
                       30, 0, 0, 0, 0, 0, 0, 1.6, 0.53, 1.6,
                       bmin[0], bmin[1], bmin[2], bmax[0], bmax[1], bmax[2], 1.0)
    payload = mesh + detail + b"\0" * max(0, payload_bytes - len(mesh) - len(detail))
    header = struct.pack("<4I", MMAP_MAGIC, DT_NAVMESH_VERSION, MMAP_VERSION, len(payload))
    header += b"\x01\x00\x00\x00"
    header += b"\0" * (MMAP_TILE_HEADER_SIZE - len(header))
    return header + payload


def build_vmo(groups):
    """A synthetic vmaps/<name>.vmo; each group is (bound low, bound high, vertices, triangles)."""
    body = struct.pack("<I", 8) + struct.pack("<I", 59)  # WMOD chunkSize, RootWMOID
    body += b"GMOD" + struct.pack("<I", len(groups))
    for low, high, vertices, triangles in groups:
        body += struct.pack("<6f", low[0], low[1], low[2], high[0], high[1], high[2])
        body += struct.pack("<2I", 0x209, 1)
        body += b"VERT" + struct.pack("<2I", 12 * len(vertices), len(vertices))
        for vertex in vertices:
            body += struct.pack("<3f", *vertex)
        body += b"TRIM" + struct.pack("<2I", 12 * len(triangles), len(triangles))
        for triangle in triangles:
            body += struct.pack("<3I", *triangle)
        tree = (3 << 30, 0, 0)
        body += b"MBIH" + struct.pack("<6f", 0.0, 0.0, 0.0, 1.0, 1.0, 1.0)
        body += struct.pack("<I", len(tree)) + struct.pack("<3I", *tree)
        body += struct.pack("<I", len(triangles)) + struct.pack(f"<{len(triangles)}I",
                                                                *range(len(triangles)))
        body += b"LIQU" + struct.pack("<I", 0)
    tree = (3 << 30, 0, 0)
    body += b"GBIH" + struct.pack("<6f", 0.0, 0.0, 0.0, 1.0, 1.0, 1.0)
    body += struct.pack("<I", len(tree)) + struct.pack("<3I", *tree)
    body += struct.pack("<I", len(groups)) + struct.pack(f"<{len(groups)}I", *range(len(groups)))
    return VMAP_MAGIC + b"WMOD" + body


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
    wall = build_vmo([((-6.0, 0.0, 0.0), (6.0, 0.0, 12.0),
                       [(-6.0, 0.0, 0.0), (6.0, 0.0, 0.0), (6.0, 0.0, 12.0), (-6.0, 0.0, 12.0)],
                       [(0, 1, 2), (0, 2, 3)])])
    for name in model_names:
        (root / "vmaps" / f"{name}.vmo").write_bytes(wall)
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
    # The rotation convention, checked against the one three call sites in this tree share.
    assert mat3_vec(rotation_matrix((0.0, 90.0, 0.0)), (1.0, 0.0, 0.0))[1] > 0.999
    assert mat3_vec(rotation_matrix((0.0, 0.0, 0.0)), (1.0, 2.0, 3.0)) == (1.0, 2.0, 3.0)
    identity = Instance({"internal": (0.0, 0.0, 0.0), "rotation": (0.0, 0.0, 0.0), "scale": 1.0})
    moved = Instance({"internal": (0.0, 0.0, 0.0), "rotation": (0.0, 90.0, 0.0), "scale": 1.0})
    assert abs(moved.to_internal((1.0, 0.0, 0.0))[1] - 1.0) < 1e-9
    assert abs(identity.to_internal((1.0, 0.0, 0.0))[0] - 1.0) < 1e-9
    # A wall model: a vertical quad in the x-z plane at y = 0, plus a ray across and beside it.
    wall = build_vmo([((-5.0, 0.0, 0.0), (5.0, 0.0, 10.0),
                       [(-5.0, 0.0, 0.0), (5.0, 0.0, 0.0), (5.0, 0.0, 10.0), (-5.0, 0.0, 10.0)],
                       [(0, 1, 2), (0, 2, 3)])])
    model = WorldModel(wall, "<vmo>")
    assert model.triangle_count() == 2 and model.consumed == len(wall)
    hit = model_hit(identity, model, (0.0, -3.0, 5.0), (0.0, 1.0, 0.0), 10.0)
    assert hit is not None and abs(hit[0] - 3.0) < 1e-6, hit
    assert model_hit(identity, model, (0.0, -3.0, 5.0), (-1.0, 0.0, 0.0), 10.0) is None
    assert model_hit(identity, model, (0.0, -3.0, 5.0), (0.0, 1.0, 0.0), 1.0) is None  # out of reach
    assert ray_triangle((0.0, -1.0, 0.0), (0.0, 1.0, 0.0), (-1.0, 0.0, -1.0), (1.0, 0.0, -1.0),
                        (0.0, 0.0, 1.0), 10.0) is not None
    assert ray_triangle((0.0, -1.0, 0.0), (0.0, 1.0, 0.0), (-1.0, 0.0, 3.0), (1.0, 0.0, 3.0),
                        (0.0, 0.0, 5.0), 10.0) is None
    # A triangle index outside the vertex array is invalid data, not a usable model.
    broken_vmo = build_vmo([((-1.0, -1.0, -1.0), (1.0, 1.0, 1.0),
                             [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)],
                             [(0, 1, 99)])])
    try:
        WorldModel(broken_vmo, "<broken>")
    except ValueError:
        pass
    else:
        raise AssertionError("a triangle index outside the vertex array was accepted")
    # A bound can cross a segment while its centre is far away, so the broad phase cannot be the
    # centre distance: segment_hits has to find this hit.
    far = {"index": 0, "flags": 0x4, "adt_id": 0, "id": 77, "name": "Far.wmo", "rotation": (0.0, 0.0, 0.0),
           "scale": 1.0, "internal": (0.0, 0.0, 0.0), "bound": None,
           "internal_bound": ((-500.0, -500.0, 0.0), (200.0, 200.0, 80.0))}
    far_models = ModelCache(Path("."))
    far_models._models["Far.wmo"] = WorldModel(wall, "<far>")
    far_hits, _ = segment_hits([far], far_models, internal_to_world(0.0, -4.0, 5.0),
                               internal_to_world(0.0, 8.0, 5.0))
    assert far_hits and far_hits[0]["name"] == "Far.wmo", far_hits
    far_centre = tuple((far["internal_bound"][0][axis] + far["internal_bound"][1][axis]) / 2
                       for axis in range(3))
    assert max(abs(far_centre[0] - 0.0), abs(far_centre[1] - 2.0)) > 90.0, far_centre
    assert bound_crossed(((-1.0, -1.0, -1.0), (1.0, 1.0, 1.0)), (0.0, -5.0, 0.0), (0.0, 1.0, 0.0), 10.0)
    assert not bound_crossed(((-1.0, -1.0, -1.0), (1.0, 1.0, 1.0)), (9.0, -5.0, 0.0), (0.0, 1.0, 0.0), 10.0)
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
        # The pair search has to find the synthetic wall and a clear line beside it.
        pair_root = Path(temporary) / "pair"
        write_synthetic_root(pair_root)
        pair = derive_los_pair(pair_root, 0, (32.0, 32.0, 40.0))
        assert pair["blocked"]["hits"] and pair["blocked"]["hits"][0]["name"] == "Nsabbey.wmo", pair
        assert not pair["clear"]["hits"] and pair["blocked"]["length"] >= 20.0, pair
        assert abs(pair["eye_height"] - DEFAULT_COLLISION_HEIGHT) < 1e-9
        # End to end for the same concern: a second WMO whose bound is huge and whose centre is far
        # away, but whose wall crosses the line the search would otherwise call clear.
        big_root = Path(temporary) / "big-bound"
        write_synthetic_root(big_root, model_names=("Nsabbey.wmo", "Big.wmo"), spawns=[
            ("Nsabbey.wmo", 72420, (32.0, 32.0, 40.0), (12.0, 12.0, 30.0), (52.0, 52.0, 50.0)),
            ("Big.wmo", 99, (32.0, 38.0, 40.0), (32.0 - 500.0, 38.0 - 500.0, 0.0),
             (32.0 + 200.0, 38.0 + 200.0, 80.0))])
        try:
            big_pair = derive_los_pair(big_root, 0, (32.0, 32.0, 40.0))
        except ValueError:
            big_pair = None
        if big_pair:
            big_spawns = tile_spawns(big_root, 0, 32, 32, 1)
            big_models = ModelCache(big_root)
            assert not segment_hits(big_spawns, big_models, big_pair["clear"]["first"],
                                    big_pair["clear"]["second"])[0], \
                "the derived clear line is blocked when every spawn is considered"
        # The path query walks the tile's own polygons: a shared edge must route, a detached polygon
        # must not, a roof must not win over the ground, and an index outside either array is rejected.
        def tile(vertices, polygons):
            return MmapFiles.from_bytes(build_mmtile(0, 0, (-200.0, 0.0, -200.0),
                                                     (200.0, 200.0, 200.0),
                                                     arrays=(vertices, polygons)))

        ground = [(-1.0, 40.0, 0.0), (0.0, 40.0, 0.0), (0.0, 40.0, 2.0), (-1.0, 40.0, 2.0),
                  (1.0, 40.0, 0.0), (1.0, 40.0, 2.0),
                  (5.0, 40.0, 0.0), (6.0, 40.0, 0.0), (6.0, 40.0, 2.0), (5.0, 40.0, 2.0)]
        roof = [(-1.0, 45.0, 0.0), (0.0, 45.0, 0.0), (0.0, 45.0, 2.0), (-1.0, 45.0, 2.0)]
        mesh = ground + roof
        # A shares its x=0 edge with B (polygon 1); C is detached; D is the roof over A.
        room = [((0, 1, 2, 3), (0, 2, 0, 0), NAV_GROUND), ((1, 4, 5, 2), (0, 0, 0, 1), NAV_GROUND),
                ((6, 7, 8, 9), (0, 0, 0, 0), NAV_GROUND), ((10, 11, 12, 13), (0, 0, 0, 0), NAV_GROUND)]
        verts_, polys_, adjacency_, problems_ = navmesh_graph(tile(mesh, room))
        assert not problems_ and len(polys_) == 4, problems_
        _, graph_ = creature_graph(polys_, adjacency_)
        start = navmesh_polygon(verts_, polys_, (-0.5, 40.0, 1.0), 40.0)
        neighbour = navmesh_polygon(verts_, polys_, (0.5, 40.0, 1.0), 40.0)
        detached = navmesh_polygon(verts_, polys_, (5.5, 40.0, 1.0), 40.0)
        assert (start, neighbour, detached) == (0, 1, 2), (start, neighbour, detached)
        route, cost = navmesh_route(verts_, polys_, graph_, start, neighbour)
        assert route == [0, 1] and 0.0 < cost < 3.0, (route, cost)
        assert navmesh_route(verts_, polys_, graph_, start, detached) == (None, None)
        assert reachable_polygons(graph_, start) == {0, 1}
        assert navmesh_route(verts_, polys_, graph_, start, 3) == (None, None), \
            "the roof polygon was treatable as a destination"
        # A polygon whose flags the creature's filter does not match is not walkable, and a link into
        # one is not a link: a water-only second polygon must not supply the route.
        water = [room[0], ((1, 4, 5, 2), (0, 0, 0, 1), 0x08), room[2], room[3]]
        verts_, polys_, adjacency_, _ = navmesh_graph(tile(mesh, water))
        walkable_, graph_ = creature_graph(polys_, adjacency_)
        assert walkable_ == [0, 2, 3] and graph_[0] == set(), graph_
        assert navmesh_polygon(verts_, polys_, (0.5, 40.0, 1.0), 40.0) is None, \
            "a water-only polygon was selected for a land walker"
        assert navmesh_route(verts_, polys_, graph_, 0, 1) == (None, None)
        assert navmesh_polygon(verts_, polys_, (0.5, 40.0, 1.0), 40.0, 0x09) == 1, \
            "a water-only polygon was not reachable with NAV_GROUND|NAV_WATER"
        # A gently sloped ramp from height 0 to 20 over 100 m with an overlapping floor at 3: at x=1
        # the ramp's surface is 0.2 and its centre is 10, so a centre height picks the floor wrongly.
        ramp = [(-1.0, 0.0, 0.0), (-1.0, 20.0, 100.0), (1.0, 20.0, 100.0), (1.0, 0.0, 0.0),
                (-1.0, 3.0, 0.0), (1.0, 3.0, 0.0), (1.0, 3.0, 100.0), (-1.0, 3.0, 100.0)]
        ramp_room = [((0, 1, 2, 3), (0, 0, 0, 0), NAV_GROUND), ((4, 5, 6, 7), (0, 0, 0, 0), NAV_GROUND)]
        ramp_verts, ramp_polys, _, _ = navmesh_graph(tile(ramp, ramp_room))
        assert abs(polygon_height(ramp_polys[0], ramp_verts, (0.0, 0.2, 1.0)) - 0.2) < 1e-6, \
            polygon_height(ramp_polys[0], ramp_verts, (0.0, 0.2, 1.0))
        assert navmesh_polygon(ramp_verts, ramp_polys, (0.0, 0.2, 1.0), 0.2) == 0, \
            "the overlapping floor was selected over the ramp surface at the point"
        assert navmesh_polygon(ramp_verts, ramp_polys, (0.0, 0.2, 1.0), 3.0) == 1
        # The blocked line is walked polygon by polygon, and only the actual portal transitions decide:
        # two touching rectangles span the line and their centres sit 100 m apart while the line is 40 m
        # long, so a length ratio would call it a detour even though a walker can follow the line all
        # the way — the check must call that walkable. A 4 m carved gap is the obstructed shape. A
        # continuous ramp whose middle edge is 10 m above the ends is walkable too: each portal step is
        # 0 m, and a chord comparison is what would wrongly read those samples as a gap.
        span_verts = [(-100.0, 40.0, -100.0), (-100.0, 40.0, 100.0), (0.0, 40.0, 100.0),
                      (0.0, 40.0, -100.0), (0.0, 40.0, -100.0), (0.0, 40.0, 100.0),
                      (100.0, 40.0, 100.0), (100.0, 40.0, -100.0)]
        carved_verts = [(-100.0, 40.0, -100.0), (-100.0, 40.0, 100.0), (-2.0, 40.0, 100.0),
                        (-2.0, 40.0, -100.0), (2.0, 40.0, -100.0), (2.0, 40.0, 100.0),
                        (100.0, 40.0, 100.0), (100.0, 40.0, -100.0)]
        ramp_verts = [(-100.0, 40.0, -100.0), (-100.0, 40.0, 100.0), (0.0, 50.0, 100.0),
                      (0.0, 50.0, -100.0), (0.0, 50.0, -100.0), (0.0, 50.0, 100.0),
                      (100.0, 40.0, 100.0), (100.0, 40.0, -100.0)]
        step_verts = [(-100.0, 40.0, -100.0), (-100.0, 40.0, 100.0), (0.0, 40.0, 100.0),
                      (0.0, 40.0, -100.0), (0.0, 45.0, -100.0), (0.0, 45.0, 100.0),
                      (100.0, 45.0, 100.0), (100.0, 45.0, -100.0)]
        # poly 0 (x −100..0) leaves through its edge 2 (x = 0) into poly 1; poly 1 leaves through its
        # edges 0 (x = 0) back into poly 0. None leaves in the carved shape, whose gap is 4 m wide.
        touching = [((0, 1, 2, 3), (0, 0, 2, 0), NAV_GROUND), ((4, 5, 6, 7), (1, 0, 0, 0), NAV_GROUND)]
        carved = [((0, 1, 2, 3), (0, 0, 0, 0), NAV_GROUND), ((4, 5, 6, 7), (0, 0, 0, 0), NAV_GROUND)]
        ramp = [((0, 1, 2, 3), (0, 0, 2, 0), NAV_GROUND), ((4, 5, 6, 7), (1, 0, 0, 0), NAV_GROUND)]
        step = [((0, 1, 2, 3), (0, 0, 2, 0), NAV_GROUND), ((4, 5, 6, 7), (1, 0, 0, 0), NAV_GROUND)]
        water_second = [((0, 1, 2, 3), (0, 0, 2, 0), NAV_GROUND), ((4, 5, 6, 7), (1, 0, 0, 0), 0x08)]
        first_line, second_line = (-20.0, 0.0, 0.0), (20.0, 0.0, 0.0)
        for label, vertices, polygons, linked, walkable in (
                ("touching", span_verts, touching, True, True),
                ("carved", carved_verts, carved, False, False),
                ("ramp", ramp_verts, ramp, True, True),
                ("step", step_verts, step, True, False),
                ("water", span_verts, water_second, False, False)):
            verts_, polys_, adjacency_, _ = navmesh_graph(tile(vertices, polygons))
            _, graph_ = creature_graph(polys_, adjacency_)
            route, cost = navmesh_route(verts_, polys_, graph_,
                                        navmesh_polygon(verts_, polys_, first_line, 0.0),
                                        navmesh_polygon(verts_, polys_, second_line, 0.0))
            crossed, reached, stopped = navmesh_line_walk(verts_, polys_, NAV_GROUND, 0, 1,
                                                          first_line, second_line, 1.6)
            assert (route is not None) == linked, (label, route)
            if route:
                # Centre-to-centre steps overstate a path, so the length cannot be the detour test.
                assert cost >= 2.0 * 40.0, (label, cost)
            assert reached == walkable, (label, reached, stopped)
            assert crossed[0] == 0, (label, crossed)
            if walkable:
                assert stopped is None and crossed[-1] == 1, (label, crossed)
            else:
                assert stopped is not None, (label, crossed)
        # A crossing through a polygon's closing edge (from its last corner back to corner 0) is a real
        # portal: the edge number must be wrapped, or it collides with the -1 sentinel and the walk
        # reports an obstruction over a connected corridor. Every edge is tested by mirroring a second
        # rectangle across it, with the square's edge 3 being the closing one.
        width = 100.0
        square = [(0.0, 40.0, 0.0), (0.0, 40.0, width), (width, 40.0, width), (width, 40.0, 0.0)]
        across = [(-width, 40.0, 0.0), (-width, 40.0, width), (0.0, 40.0, width), (0.0, 40.0, 0.0),
                  (0.0, 40.0, width), (0.0, 40.0, 2 * width), (width, 40.0, 2 * width),
                  (width, 40.0, width),
                  (width, 40.0, 0.0), (width, 40.0, width), (2 * width, 40.0, width),
                  (2 * width, 40.0, 0.0),
                  (0.0, 40.0, -width), (0.0, 40.0, 0.0), (width, 40.0, 0.0), (width, 40.0, -width)]
        ends = [(-width / 2, 40.0, width / 2), (width / 2, 40.0, 1.5 * width),
                (1.5 * width, 40.0, width / 2), (width / 2, 40.0, -width / 2)]
        start_of_line = (width / 2, 40.0, width / 2)
        for edge in range(4):
            verts_, polys_, adjacency_, _ = navmesh_graph(tile(
                square + across[4 * edge:4 * edge + 4],
                [((0, 1, 2, 3), tuple(2 if number == edge else 0 for number in range(4)), NAV_GROUND),
                 ((4, 5, 6, 7), (0, 0, 0, 0), NAV_GROUND)]))
            _, _, entering, leaving = segment_polygon(polys_[0], verts_, start_of_line, ends[edge])
            assert (entering, leaving) == (-1, edge), (edge, entering, leaving)
            crossed, reached, stopped = navmesh_line_walk(verts_, polys_, NAV_GROUND, 0, 1,
                                                          start_of_line, ends[edge], 1.6)
            assert reached and crossed == [0, 1] and stopped is None, (edge, crossed, reached, stopped)
        # A segment that never leaves a polygon keeps -1 for its leaving edge, and the walk reports the
        # segment ending there rather than a portal it cannot follow.
        verts_, polys_, _, _ = navmesh_graph(tile(square + across[:4],
                                                  [((0, 1, 2, 3), (0, 0, 0, 2), NAV_GROUND),
                                                   ((4, 5, 6, 7), (0, 0, 0, 0), NAV_GROUND)]))
        inside = segment_polygon(polys_[0], verts_, (width / 2, 40.0, width / 2),
                                 (width / 4, 40.0, width / 4))
        assert inside[2] == -1 and inside[3] == -1, inside
        assert navmesh_line_walk(verts_, polys_, NAV_GROUND, 0, 1, (width / 2, 40.0, width / 2),
                                 (width / 4, 40.0, width / 4), 1.6)[1] is False
        # A vertex or neighbour index outside its array is corrupt data, not a usable graph.
        assert navmesh_graph(tile([(0.0, 40.0, 0.0)], [((0, 1, 2, 3), (0, 0, 0, 0), NAV_GROUND)]))[3]
        assert any("links to polygon" in problem for problem in
                   navmesh_graph(tile(ground, [((0, 1, 2, 3), (0, 9, 0, 0), NAV_GROUND)]))[3])
        # A polygon declaring seven vertices is a build this Detour cannot read (DT_VERTS_PER_POLYGON).
        seven = bytearray(build_mmtile(0, 0, (-1.0, 0.0, -1.0), (1.0, 1.0, 1.0),
                                       arrays=(ground[:4], [((0, 1, 2, 3), (0, 0, 0, 0), NAV_GROUND)])))
        struct.pack_into("<B", seven, MMAP_TILE_HEADER_SIZE + DT_MESH_HEADER_SIZE + 12 * 4 + 30, 7)
        assert any("DT_VERTS_PER_POLYGON" in problem
                   for problem in navmesh_graph(MmapFiles.from_bytes(bytes(seven)))[3])
        # A chase report renders with the endpoints absent, as the check's own render does.
        assert "Chase path FAIL" in render_chase(
            {"data_root": "x", "map": 0, "grid": [32, 32], "checks": [], "gaps": [],
             "facts": {"ends": {}, "straight_line": None, "route_polygons": None, "route_cost": None,
                       "line_polygons": [0], "line_reached_goal": False, "line_stopped": "x",
                       "reachable_polygons": 0, "walkable_polygons": 0, "polygons": 0,
                       "include_flags": NAV_GROUND, "detached_polygon": None}, "pass": False})
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
    parser.add_argument("--los-pair", action="store_true",
                        help="derive the blocked/clear LOS pair from the pinned obstacle's triangles")
    parser.add_argument("--chase", action="store_true",
                        help="derive the chase route around the pinned obstacle from the tile's polygons")
    parser.add_argument("--obstacle", default="Nsabbey.wmo",
                        help="obstacle for --los-pair and --chase (default Nsabbey.wmo)")
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
    if args.los_pair:
        pair = derive_los_pair(args.data_root, args.map, anchor, args.obstacle, args.neighbours)
        print(json.dumps(pair, indent=2, sort_keys=True) if args.json else render_los_pair(pair))
        return 0
    if args.chase:
        report = chase_path(args.data_root, args.map, anchor, args.obstacle, args.neighbours)
        print(json.dumps(report, indent=2, sort_keys=True) if args.json else render_chase(report))
        return 0 if report["pass"] else 1
    report = check(args.data_root, args.map, anchor, args.neighbours, args.radius)
    print(json.dumps(report, indent=2, sort_keys=True) if args.json else render(report))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, TypeError, struct.error, KeyError) as error:
        print(f"collision geometry check failed: {error}", file=sys.stderr)
        sys.exit(1)
