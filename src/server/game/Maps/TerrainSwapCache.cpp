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

#include "TerrainSwapCache.h"
#include "GridTerrainData.h"
#include "Log.h"
#include "MapCollisionData.h"
#include "StringFormat.h"
#include "VMapFactory.h"
#include "VMapMgr2.h"
#include <algorithm>
#include <cmath>
#include <filesystem>

namespace
{
    uint32 GetGridKey(GridCoord const& gridCoord)
    {
        return uint32(gridCoord.x_coord) * MAX_NUMBER_OF_GRIDS + uint32(gridCoord.y_coord);
    }
}

struct TerrainSwapCache::Entry
{
    explicit Entry(uint32 mapId) : MapId(mapId) { }

    uint32 MapId;
    std::unordered_map<uint32, std::shared_ptr<GridTerrainData>> Grids;
    std::unordered_map<uint32, bool> GridFileExists;
    std::unordered_map<uint32, bool> MMapTileExists;
    std::unordered_map<uint32, bool> VMapTileExists;
    std::shared_ptr<VMAP::StaticMapTree> StaticTree;
    std::shared_ptr<VMAP::StaticMapTree> FallbackTree;
    std::unique_ptr<StaticVMapCollisionData> StaticCollision;
    std::unique_ptr<StaticVMapCollisionData> FallbackCollision;
    std::unordered_map<uint32, bool> LoadedTiles;
    std::unordered_map<uint32, bool> FallbackLoadedTiles;
    bool StaticTreeInitialized = false;
    bool FallbackTreeInitialized = false;
};

TerrainSwapCache::TerrainSwapCache(std::string dataPath, uint32 baseMapId)
    : _dataPath(std::move(dataPath)), _baseMapId(baseMapId)
{
}

TerrainSwapCache::~TerrainSwapCache() = default;

std::string TerrainSwapCache::GetGridFileName(uint32 mapId, GridCoord const& gridCoord) const
{
    return Acore::StringFormat("{}maps/{:03}{:02}{:02}.map", _dataPath, mapId, gridCoord.x_coord, gridCoord.y_coord);
}

std::string TerrainSwapCache::GetMMapTileFileName(uint32 mapId, GridCoord const& gridCoord) const
{
    return Acore::StringFormat("{}mmaps/{:03}{:02}{:02}.mmtile", _dataPath, mapId, gridCoord.x_coord, gridCoord.y_coord);
}

TerrainSwapCache::Entry& TerrainSwapCache::GetEntry(uint32 mapId)
{
    std::unique_ptr<Entry>& entry = _entries[mapId];
    if (!entry)
        entry = std::make_unique<Entry>(mapId);

    return *entry;
}

bool TerrainSwapCache::HasGridFile(uint32 mapId, GridCoord const& gridCoord)
{
    std::lock_guard<std::mutex> lock(_mutex);
    return HasGridFileUnlocked(mapId, gridCoord);
}

bool TerrainSwapCache::HasMMapTile(uint32 mapId, GridCoord const& gridCoord)
{
    std::lock_guard<std::mutex> lock(_mutex);

    Entry& entry = GetEntry(mapId);
    uint32 const key = GetGridKey(gridCoord);
    auto itr = entry.MMapTileExists.find(key);
    if (itr != entry.MMapTileExists.end())
        return itr->second;

    bool const exists = std::filesystem::exists(GetMMapTileFileName(mapId, gridCoord));
    entry.MMapTileExists.emplace(key, exists);
    return exists;
}

bool TerrainSwapCache::HasGridFileUnlocked(uint32 mapId, GridCoord const& gridCoord)
{
    Entry& entry = GetEntry(mapId);
    uint32 const key = GetGridKey(gridCoord);
    auto itr = entry.GridFileExists.find(key);
    if (itr != entry.GridFileExists.end())
        return itr->second;

    bool const exists = std::filesystem::exists(GetGridFileName(mapId, gridCoord));
    entry.GridFileExists.emplace(key, exists);
    return exists;
}

bool TerrainSwapCache::HasVMapTileUnlocked(uint32 mapId, GridCoord const& gridCoord)
{
    Entry& entry = GetEntry(mapId);
    uint32 const key = GetGridKey(gridCoord);
    auto itr = entry.VMapTileExists.find(key);
    if (itr != entry.VMapTileExists.end())
        return itr->second;

    std::string const fileName = _dataPath + "vmaps/" +
        VMAP::StaticMapTree::getTileFileName(mapId, gridCoord.x_coord, gridCoord.y_coord);
    bool const exists = std::filesystem::exists(fileName);
    entry.VMapTileExists.emplace(key, exists);
    return exists;
}

void TerrainSwapCache::EnsureStaticTree(Entry& entry)
{
    if (entry.StaticTreeInitialized)
        return;

    entry.StaticTreeInitialized = true;
    if (!VMAP::VMapFactory::createOrGetVMapMgr()->isMapLoadingEnabled())
        return;

    std::shared_ptr<VMAP::StaticMapTree> tree =
        std::make_shared<VMAP::StaticMapTree>(entry.MapId, _dataPath + "vmaps");
    if (tree->InitMap(VMAP::VMapMgr2::getMapFileName(entry.MapId)))
        entry.StaticTree = std::move(tree);
}

void TerrainSwapCache::EnsureFallbackTree(Entry& entry)
{
    if (entry.FallbackTreeInitialized)
        return;

    entry.FallbackTreeInitialized = true;
    if (entry.MapId == _baseMapId || !VMAP::VMapFactory::createOrGetVMapMgr()->isMapLoadingEnabled())
        return;

    std::shared_ptr<VMAP::StaticMapTree> tree =
        std::make_shared<VMAP::StaticMapTree>(_baseMapId, _dataPath + "vmaps");
    if (tree->InitMap(VMAP::VMapMgr2::getMapFileName(_baseMapId)))
        entry.FallbackTree = std::move(tree);
}

GridTerrainData* TerrainSwapCache::GetGridTerrainData(uint32 mapId, GridCoord const& gridCoord)
{
    std::lock_guard<std::mutex> lock(_mutex);

    Entry& entry = GetEntry(mapId);
    uint32 const key = GetGridKey(gridCoord);
    auto itr = entry.Grids.find(key);
    if (itr != entry.Grids.end())
        return itr->second.get();

    std::shared_ptr<GridTerrainData> terrainData;
    std::string const fileName = GetGridFileName(mapId, gridCoord);
    if (std::filesystem::exists(fileName))
    {
        std::shared_ptr<GridTerrainData> loaded = std::make_shared<GridTerrainData>();
        if (loaded->Load(fileName) == TerrainMapDataReadResult::Success)
            terrainData = std::move(loaded);
        else
            LOG_ERROR("maps", "Error loading terrain swap map file: {}", fileName);
    }

    entry.Grids.emplace(key, terrainData);
    return terrainData.get();
}

StaticVMapCollisionData* TerrainSwapCache::GetStaticCollision(uint32 mapId, GridCoord const& gridCoord)
{
    std::lock_guard<std::mutex> lock(_mutex);

    Entry& entry = GetEntry(mapId);
    EnsureStaticTree(entry);

    uint32 const key = GetGridKey(gridCoord);
    // shortcut: one base-map fallback level covers the 4.3.4 terrain swaps; walk parent chains if nested swaps appear.
    bool const useFallback = mapId != _baseMapId &&
        (!entry.StaticTree || !HasVMapTileUnlocked(mapId, gridCoord));
    if (useFallback)
    {
        EnsureFallbackTree(entry);
        if (!entry.FallbackTree)
            return nullptr;

        if (entry.FallbackLoadedTiles.find(key) == entry.FallbackLoadedTiles.end())
        {
            entry.FallbackTree->LoadMapTile(gridCoord.x_coord, gridCoord.y_coord);
            entry.FallbackLoadedTiles.emplace(key, true);
        }

        if (!entry.FallbackCollision)
            entry.FallbackCollision = std::make_unique<StaticVMapCollisionData>(mapId, entry.FallbackTree);

        return entry.FallbackCollision.get();
    }

    if (!entry.StaticTree)
        return nullptr;

    if (entry.LoadedTiles.find(key) == entry.LoadedTiles.end())
    {
        entry.StaticTree->LoadMapTile(gridCoord.x_coord, gridCoord.y_coord);
        entry.LoadedTiles.emplace(key, true);
    }

    if (!entry.StaticCollision)
        entry.StaticCollision = std::make_unique<StaticVMapCollisionData>(mapId, entry.StaticTree);

    return entry.StaticCollision.get();
}
