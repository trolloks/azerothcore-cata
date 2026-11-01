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

#include "Map.h"
#include "StringFormat.h"
#include "TerrainSwapCache.h"
#include "VMapFactory.h"
#include "VMapMgr2.h"
#include <gtest/gtest.h>
#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <string>
#include <vector>

namespace
{
    class TemporaryMapDirectory
    {
    public:
        TemporaryMapDirectory()
        {
            static uint32 counter = 0;
            _path = std::filesystem::temp_directory_path() /
                ("acore-terrain-swap-" + std::to_string(++counter));
            std::filesystem::create_directories(_path / "maps");
        }

        ~TemporaryMapDirectory()
        {
            std::error_code error;
            std::filesystem::remove_all(_path, error);
        }

        [[nodiscard]] std::string GetDataPath() const { return _path.string() + "/"; }
        [[nodiscard]] std::filesystem::path GetPath() const { return _path; }

    private:
        std::filesystem::path _path;
    };

    std::filesystem::path MapFilePath(TemporaryMapDirectory const& dir, uint32 mapId, GridCoord const& gridCoord)
    {
        return dir.GetPath() / "maps" / Acore::StringFormat("{:03}{:02}{:02}.map", mapId, gridCoord.x_coord, gridCoord.y_coord);
    }

    std::filesystem::path MMapFilePath(TemporaryMapDirectory const& dir, uint32 mapId, GridCoord const& gridCoord)
    {
        return dir.GetPath() / "mmaps" / Acore::StringFormat("{:03}{:02}{:02}.mmtile", mapId, gridCoord.x_coord, gridCoord.y_coord);
    }

    void WriteMMapFile(std::filesystem::path const& path)
    {
        std::filesystem::create_directories(path.parent_path());
        std::ofstream file(path, std::ios::binary);
        file.put('\0');
    }

    void WriteFlatMapFile(std::filesystem::path const& path, uint16 areaId, float height)
    {
        map_fileheader header{};
        header.mapMagic = MapMagic.asUInt;
        header.versionMagic = MapVersionMagic;
        header.buildMagic = 0;
        header.areaMapOffset = sizeof(map_fileheader);
        header.areaMapSize = sizeof(map_areaHeader);
        header.heightMapOffset = header.areaMapOffset + header.areaMapSize;
        header.heightMapSize = sizeof(map_heightHeader);

        map_areaHeader area{};
        area.fourcc = MapAreaMagic.asUInt;
        area.flags = MAP_AREA_NO_AREA;
        area.gridArea = areaId;

        map_heightHeader heightHeader{};
        heightHeader.fourcc = MapHeightMagic.asUInt;
        heightHeader.flags = MAP_HEIGHT_NO_HEIGHT;
        heightHeader.gridHeight = height;
        heightHeader.gridMaxHeight = height;

        std::ofstream file(path, std::ios::binary);
        file.write(reinterpret_cast<char const*>(&header), sizeof(header));
        file.write(reinterpret_cast<char const*>(&area), sizeof(area));
        file.write(reinterpret_cast<char const*>(&heightHeader), sizeof(heightHeader));
    }
}

TEST(TerrainSwapTest, SelectsChildTerrainOnlyWhenItsGridExists)
{
    auto never = [](uint32, GridCoord const&) { return false; };
    auto always = [](uint32, GridCoord const&) { return true; };

    std::vector<uint32> const noVisibleMaps;
    EXPECT_EQ(Acore::SelectTerrainMapId(noVisibleMaps, 0, 1000.0f, 2000.0f, always), 0u);

    // A single visible map is used without a tile lookup.
    std::vector<uint32> const oneVisibleMap{ 648 };
    EXPECT_EQ(Acore::SelectTerrainMapId(oneVisibleMap, 0, 1000.0f, 2000.0f, never), 648u);
    EXPECT_EQ(Acore::SelectTerrainMapId(oneVisibleMap, 0, 1000.0f, 2000.0f, always), 648u);

    // Multiple visible maps: the first one with a grid file at the position wins.
    std::vector<uint32> const twoVisibleMaps{ 648, 654 };
    GridCoord const tile = Acore::ComputeGridCoord(1000.0f, 2000.0f);
    EXPECT_EQ(Acore::SelectTerrainMapId(twoVisibleMaps, 0, 1000.0f, 2000.0f,
        [&tile](uint32 mapId, GridCoord const& gridCoord)
        {
            return mapId == 654 && gridCoord.x_coord == tile.x_coord && gridCoord.y_coord == tile.y_coord;
        }), 654u);

    // No child tile: fall back to the base map.
    EXPECT_EQ(Acore::SelectTerrainMapId(twoVisibleMaps, 0, 1000.0f, 2000.0f, never), 0u);

    // The base map itself is never selected as a child.
    std::vector<uint32> const withBase{ 0, 648 };
    EXPECT_EQ(Acore::SelectTerrainMapId(withBase, 0, 1000.0f, 2000.0f, always), 648u);
}

TEST(TerrainSwapTest, SelectsNavMeshMapOnlyWhenChildTileExists)
{
    auto never = [](uint32, GridCoord const&) { return false; };
    auto always = [](uint32, GridCoord const&) { return true; };

    auto hasGrid = [](uint32, GridCoord const&) { return true; };
    auto has654Grid = [](uint32 mapId, GridCoord const&) { return mapId == 654; };
    auto noGrid = [](uint32, GridCoord const&) { return false; };

    std::vector<uint32> const noVisibleMaps;
    EXPECT_EQ(Acore::SelectTerrainNavMeshMapId(noVisibleMaps, 0, 1000.0f, 2000.0f, hasGrid, always), 0u);

    // A single visible child map needs its own tile; otherwise navigation uses the base mesh.
    std::vector<uint32> const oneVisibleMap{ 648 };
    EXPECT_EQ(Acore::SelectTerrainNavMeshMapId(oneVisibleMap, 0, 1000.0f, 2000.0f, hasGrid, always), 648u);
    EXPECT_EQ(Acore::SelectTerrainNavMeshMapId(oneVisibleMap, 0, 1000.0f, 2000.0f, hasGrid, never), 0u);
    EXPECT_EQ(Acore::SelectTerrainNavMeshMapId(oneVisibleMap, 0, 1000.0f, 2000.0f, noGrid, always), 0u);

    // Multiple visible maps: the terrain-selected child must also have an mmap tile.
    std::vector<uint32> const twoVisibleMaps{ 648, 654 };
    GridCoord const tile = Acore::ComputeGridCoord(1000.0f, 2000.0f);
    EXPECT_EQ(Acore::SelectTerrainNavMeshMapId(twoVisibleMaps, 0, 1000.0f, 2000.0f, has654Grid,
        [&tile](uint32 mapId, GridCoord const& gridCoord)
        {
            return mapId == 654 && gridCoord.x_coord == tile.x_coord && gridCoord.y_coord == tile.y_coord;
        }), 654u);

    // No child mmap tile: fall back to the base mesh.
    EXPECT_EQ(Acore::SelectTerrainNavMeshMapId(twoVisibleMaps, 0, 1000.0f, 2000.0f, has654Grid, never), 0u);

    // The base map is never selected as a child.
    std::vector<uint32> const withBase{ 0, 648 };
    EXPECT_EQ(Acore::SelectTerrainNavMeshMapId(withBase, 0, 1000.0f, 2000.0f, hasGrid, always), 648u);

    // The first grid-selected child wins; a later child's tile cannot override it.
    EXPECT_EQ(Acore::SelectTerrainNavMeshMapId(twoVisibleMaps, 0, 1000.0f, 2000.0f, hasGrid,
        [](uint32 mapId, GridCoord const&)
        {
            return mapId == 654;
        }), 0u);
    EXPECT_EQ(Acore::SelectTerrainNavMeshMapId(twoVisibleMaps, 0, 1000.0f, 2000.0f, noGrid, always), 0u);
}

TEST(TerrainSwapTest, CachesMMapTilePresence)
{
    TemporaryMapDirectory const dir;
    GridCoord const tile = Acore::ComputeGridCoord(1000.0f, 2000.0f);
    GridCoord const missingTile = Acore::ComputeGridCoord(-5000.0f, -5000.0f);

    WriteMMapFile(MMapFilePath(dir, 654, tile));

    TerrainSwapCache cache(dir.GetDataPath(), 0);
    EXPECT_TRUE(cache.HasMMapTile(654, tile));
    EXPECT_FALSE(cache.HasMMapTile(654, missingTile));
    EXPECT_FALSE(cache.HasMMapTile(648, tile));

    // Remove the file after the first check: the cached result must remain stable.
    std::filesystem::remove(MMapFilePath(dir, 654, tile));
    EXPECT_TRUE(cache.HasMMapTile(654, tile));
}

TEST(TerrainSwapTest, LoadsChildGridTerrainAndCachesMissingTiles)
{
    TemporaryMapDirectory const dir;
    GridCoord const tile = Acore::ComputeGridCoord(1000.0f, 2000.0f);

    WriteFlatMapFile(MapFilePath(dir, 648, tile), 1234, 100.0f);
    WriteFlatMapFile(MapFilePath(dir, 654, tile), 5678, 200.0f);

    TerrainSwapCache cache(dir.GetDataPath(), 0);

    EXPECT_TRUE(cache.HasGridFile(648, tile));
    EXPECT_TRUE(cache.HasGridFile(654, tile));

    GridTerrainData* first = cache.GetGridTerrainData(648, tile);
    GridTerrainData* second = cache.GetGridTerrainData(654, tile);
    ASSERT_NE(first, nullptr);
    ASSERT_NE(second, nullptr);
    EXPECT_FLOAT_EQ(first->getHeight(1000.0f, 2000.0f), 100.0f);
    EXPECT_FLOAT_EQ(second->getHeight(1000.0f, 2000.0f), 200.0f);
    EXPECT_EQ(first->getArea(1000.0f, 2000.0f), 1234);
    EXPECT_EQ(second->getArea(1000.0f, 2000.0f), 5678);

    // The same tile returns the cached pointer, a missing tile stays null.
    EXPECT_EQ(cache.GetGridTerrainData(648, tile), first);
    GridCoord const missingTile = Acore::ComputeGridCoord(-5000.0f, -5000.0f);
    EXPECT_FALSE(cache.HasGridFile(648, missingTile));
    EXPECT_EQ(cache.GetGridTerrainData(648, missingTile), nullptr);
}

TEST(TerrainSwapTest, MissingVmapsLeaveTheTerrainWithoutStaticCollision)
{
    TemporaryMapDirectory const dir;
    TerrainSwapCache cache(dir.GetDataPath(), 0);

    EXPECT_EQ(cache.GetStaticCollision(648, Acore::ComputeGridCoord(1000.0f, 2000.0f)), nullptr);
}

TEST(TerrainSwapTest, LoadsChildVmapsWhenDataIsAvailable)
{
    char const* dataRoot = std::getenv("ACORE_CATA_TEST_MAP_DATA");
    if (!dataRoot)
        GTEST_SKIP() << "set ACORE_CATA_TEST_MAP_DATA to the extracted data root (dbc/maps/vmaps)";

    std::string dataPath = dataRoot;
    if (!dataPath.empty() && dataPath.back() != '/')
        dataPath += '/';

    if (!std::filesystem::exists(dataPath + "vmaps/648.vmtree"))
        GTEST_SKIP() << "no vmaps for the swap maps in this data root";

    VMAP::VMapFactory::createOrGetVMapMgr()->setEnableLineOfSightCalc(true);
    TerrainSwapCache cache(dataPath, 0);
    EXPECT_NE(cache.GetStaticCollision(648, GridCoord(24, 30)), nullptr);
}

TEST(TerrainSwapTest, ComparesRealSwapAndBaseTilesWhenDataIsAvailable)
{
    char const* dataRoot = std::getenv("ACORE_CATA_TEST_MAP_DATA");
    if (!dataRoot)
        GTEST_SKIP() << "set ACORE_CATA_TEST_MAP_DATA to the extracted data root (dbc/maps/vmaps)";

    std::string dataPath = dataRoot;
    if (!dataPath.empty() && dataPath.back() != '/')
        dataPath += '/';

    TerrainSwapCache cache(dataPath, 0);

    struct TileCase
    {
        uint32 BaseMapId;
        uint32 SwapMapId;
        int32 Gx;
        int32 Gy;
    };

    // Base map tiles (0) and the Gilneas (654) / Lost Isles (648) swap tiles that cover them.
    for (TileCase const& tileCase : { TileCase{ 0, 648, 31, 26 }, TileCase{ 0, 654, 36, 26 } })
    {
        GridCoord const tile(tileCase.Gx, tileCase.Gy);
        GridTerrainData* baseTerrain = cache.GetGridTerrainData(tileCase.BaseMapId, tile);
        GridTerrainData* swapTerrain = cache.GetGridTerrainData(tileCase.SwapMapId, tile);
        ASSERT_NE(baseTerrain, nullptr) << "base map " << tileCase.BaseMapId;
        ASSERT_NE(swapTerrain, nullptr) << "swap map " << tileCase.SwapMapId;

        // Sample the tile on both sides of the swap and require the swap terrain to differ.
        float const tileMinX = (32.0f - float(tileCase.Gx) - 1.0f) * SIZE_OF_GRIDS;
        float const tileMinY = (32.0f - float(tileCase.Gy) - 1.0f) * SIZE_OF_GRIDS;
        float maxDelta = 0.0f;
        for (int32 ix = 0; ix < 5; ++ix)
        {
            for (int32 iy = 0; iy < 5; ++iy)
            {
                float const x = tileMinX + (float(ix) + 0.5f) * SIZE_OF_GRIDS / 5.0f;
                float const y = tileMinY + (float(iy) + 0.5f) * SIZE_OF_GRIDS / 5.0f;
                float const baseHeight = baseTerrain->getHeight(x, y);
                float const swapHeight = swapTerrain->getHeight(x, y);
                if (baseHeight > INVALID_HEIGHT && swapHeight > INVALID_HEIGHT)
                    maxDelta = std::max(maxDelta, std::fabs(baseHeight - swapHeight));
            }
        }

        EXPECT_GT(maxDelta, 1.0f) << "swap map " << tileCase.SwapMapId << " tile " << tileCase.Gx << "," << tileCase.Gy;
        ::testing::Test::RecordProperty("maxHeightDelta_" + std::to_string(tileCase.SwapMapId) + "_" +
            std::to_string(tileCase.Gx) + "_" + std::to_string(tileCase.Gy), std::to_string(maxDelta));

        auto hasGridFile = [&cache](uint32 mapId, GridCoord const& gridCoord)
        {
            return cache.HasGridFile(mapId, gridCoord);
        };
        float const sampleX = tileMinX + SIZE_OF_GRIDS / 2.0f;
        float const sampleY = tileMinY + SIZE_OF_GRIDS / 2.0f;
        EXPECT_EQ(Acore::SelectTerrainMapId({}, tileCase.BaseMapId, sampleX, sampleY, hasGridFile), tileCase.BaseMapId);
        EXPECT_EQ(Acore::SelectTerrainMapId({ tileCase.SwapMapId }, tileCase.BaseMapId, sampleX, sampleY, hasGridFile),
            tileCase.SwapMapId);

        auto hasMMapTile = [&cache](uint32 mapId, GridCoord const& gridCoord)
        {
            return cache.HasMMapTile(mapId, gridCoord);
        };
        bool const childTileExists = cache.HasMMapTile(tileCase.SwapMapId, tile);
        EXPECT_EQ(Acore::SelectTerrainNavMeshMapId({ tileCase.SwapMapId }, tileCase.BaseMapId, sampleX, sampleY,
            hasGridFile, hasMMapTile), childTileExists ? tileCase.SwapMapId : tileCase.BaseMapId);
    }
}
