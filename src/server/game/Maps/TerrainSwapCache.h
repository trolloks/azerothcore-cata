/*
 * This file is part of the AzerothCore Project. See AUTHORS file for Copyright information
 *
 * This program is free software; you can redistribute it and/or modify it
 * under the terms of the GNU General Public License as published by the
 * Free Software Foundation; either version 2 of the License, or (at your
 * option) any later version.
 *
 * This program is distributed in the hope that it will be useful, but WITHOUT
 * ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
 * FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for
 * more details.
 *
 * You should have received a copy of the GNU General Public License along
 * with this program. If not, see <http://www.gnu.org/licenses/>.
 */

#ifndef ACORE_TERRAIN_SWAP_CACHE_H
#define ACORE_TERRAIN_SWAP_CACHE_H

#include "Define.h"
#include "GridDefines.h"
#include <memory>
#include <mutex>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

class GridTerrainData;
class StaticVMapCollisionData;

namespace VMAP
{
    class StaticMapTree;
}

// Lazily loads the .map and vmap data of terrain swap maps (child maps) that are queried
// from the base map. The base map keeps using Map's own grid/collision data.
// shortcut: child terrain stays loaded for the base map's lifetime; add reference-counted
// unloading if swapped-zone tile churn ever matters.
class TerrainSwapCache
{
public:
    TerrainSwapCache(std::string dataPath, uint32 baseMapId);
    ~TerrainSwapCache();

    TerrainSwapCache(TerrainSwapCache const&) = delete;
    TerrainSwapCache& operator=(TerrainSwapCache const&) = delete;

    [[nodiscard]] bool HasGridFile(uint32 mapId, GridCoord const& gridCoord);
    [[nodiscard]] bool HasMMapTile(uint32 mapId, GridCoord const& gridCoord);
    GridTerrainData* GetGridTerrainData(uint32 mapId, GridCoord const& gridCoord);
    StaticVMapCollisionData* GetStaticCollision(uint32 mapId, GridCoord const& gridCoord);

private:
    struct Entry;

    Entry& GetEntry(uint32 mapId);
    void EnsureStaticTree(Entry& entry);
    void EnsureFallbackTree(Entry& entry);
    [[nodiscard]] bool HasGridFileUnlocked(uint32 mapId, GridCoord const& gridCoord);
    [[nodiscard]] bool HasVMapTileUnlocked(uint32 mapId, GridCoord const& gridCoord);
    [[nodiscard]] std::string GetGridFileName(uint32 mapId, GridCoord const& gridCoord) const;
    [[nodiscard]] std::string GetMMapTileFileName(uint32 mapId, GridCoord const& gridCoord) const;

    std::string _dataPath;
    uint32 _baseMapId;
    std::mutex _mutex;
    std::unordered_map<uint32, std::unique_ptr<Entry>> _entries;
};

namespace Acore
{
    /// 4.3.4 picks the terrain swap map whose child grid file exists for the queried tile;
    /// a single visible map is used as-is and an absent child tile falls back to the base map.
    template <typename HasGridFile>
    uint32 SelectTerrainMapId(std::vector<uint32> const& visibleMapIds, uint32 baseMapId, float x, float y,
        HasGridFile&& hasGridFile)
    {
        if (visibleMapIds.empty())
            return baseMapId;

        if (visibleMapIds.size() == 1)
            return visibleMapIds.front();

        GridCoord const gridCoord = Acore::ComputeGridCoord(x, y);
        for (uint32 mapId : visibleMapIds)
            if (mapId != baseMapId && hasGridFile(mapId, gridCoord))
                return mapId;

        return baseMapId;
    }

    /// 4.3.4 navigation uses the child mesh only when both its terrain grid and mmap tile
    /// exist; missing child tiles fall back to the base mesh.
    template <typename HasGridFile, typename HasMMapTile>
    uint32 SelectTerrainNavMeshMapId(std::vector<uint32> const& visibleMapIds, uint32 baseMapId, float x, float y,
        HasGridFile&& hasGridFile, HasMMapTile&& hasMMapTile)
    {
        uint32 const mapId = SelectTerrainMapId(visibleMapIds, baseMapId, x, y, hasGridFile);
        if (mapId == baseMapId)
            return baseMapId;

        GridCoord const gridCoord = Acore::ComputeGridCoord(x, y);
        return hasGridFile(mapId, gridCoord) && hasMMapTile(mapId, gridCoord) ? mapId : baseMapId;
    }
}

#endif
