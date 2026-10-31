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

#include "MMapMgr.h"
#include "MapDefines.h"
#include <DetourNavMeshQuery.h>
#include <gtest/gtest.h>
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <memory>
#include <string>

using namespace MMAP;

namespace
{
    std::string EnsureTrailingSlash(std::string path)
    {
        if (!path.empty() && path.back() != '/')
            path += '/';
        return path;
    }

    dtPolyRef FindNearestPoly(dtNavMeshQuery const* query, dtQueryFilter const& filter,
        float const point[3], float closest[3], float yExtent = 20.0f)
    {
        float extents[3] = { 10.0f, yExtent, 10.0f };
        dtPolyRef ref = 0;
        if (dtStatusSucceed(query->findNearestPoly(point, extents, &filter, &ref, closest)) && ref)
            return ref;

        extents[0] = 50.0f;
        extents[1] = yExtent * 5.0f;
        extents[2] = 50.0f;
        if (dtStatusSucceed(query->findNearestPoly(point, extents, &filter, &ref, closest)) && ref)
            return ref;

        return 0;
    }

    bool ReadNavMeshParams(std::string const& path, dtNavMeshParams& params)
    {
        FILE* file = fopen(path.c_str(), "rb");
        if (!file)
            return false;

        bool const ok = fread(&params, sizeof(dtNavMeshParams), 1, file) == 1;
        fclose(file);
        return ok;
    }

    bool LoadRebasedTile(dtNavMesh* navMesh, std::string const& path,
        dtNavMeshParams const& sourceParams, dtNavMeshParams const& destinationParams)
    {
        FILE* file = fopen(path.c_str(), "rb");
        if (!file)
            return false;

        MmapTileHeader fileHeader;
        if (fread(&fileHeader, sizeof(MmapTileHeader), 1, file) != 1 ||
            fileHeader.mmapMagic != MMAP_MAGIC || fileHeader.mmapVersion != MMAP_VERSION)
        {
            fclose(file);
            return false;
        }

        unsigned char* data = static_cast<unsigned char*>(dtAlloc(fileHeader.size, DT_ALLOC_PERM));
        if (!data)
        {
            fclose(file);
            return false;
        }

        if (fread(data, fileHeader.size, 1, file) != 1)
        {
            dtFree(data);
            fclose(file);
            return false;
        }
        fclose(file);

        dtMeshHeader* header = reinterpret_cast<dtMeshHeader*>(data);
        if (header->magic != DT_NAVMESH_MAGIC || header->version != DT_NAVMESH_VERSION)
        {
            dtFree(data);
            return false;
        }

        float const worldTileX = sourceParams.orig[0] + float(header->x) * sourceParams.tileWidth;
        float const worldTileZ = sourceParams.orig[2] + float(header->y) * sourceParams.tileHeight;
        header->x = int(std::lround((worldTileX - destinationParams.orig[0]) / destinationParams.tileWidth));
        header->y = int(std::lround((worldTileZ - destinationParams.orig[2]) / destinationParams.tileHeight));

        if (!dtStatusSucceed(navMesh->addTile(data, fileHeader.size, DT_TILE_FREE_DATA, 0, nullptr)))
        {
            dtFree(data);
            return false;
        }

        return true;
    }
}

TEST(MMapTest, LoadsGeneratedTerrainSwapNavMeshAndFindsAPath)
{
    char const* dataRoot = std::getenv("ACORE_CATA_TEST_MAP_DATA");
    if (!dataRoot)
        GTEST_SKIP() << "set ACORE_CATA_TEST_MAP_DATA to the extracted data root (dbc/maps/vmaps/mmaps)";

    std::string const dataPath = EnsureTrailingSlash(dataRoot);

    struct Case
    {
        uint32 MapId;
        char const* MapFile;
        char const* TileFile;
        float X;
        float Y;
        float Z;
    };

    // Known non-empty tiles; coordinates are world (x, y, z), converted to Detour's (y, z, x).
    for (Case const& testCase : { Case{ 648, "648.mmap", "6482731.mmtile", 2400.0f, 266.67f, 50.0f },
                                  Case{ 654, "654.mmap", "6543327.mmtile", -800.0f, 2400.0f, 50.0f } })
    {
        SCOPED_TRACE(testCase.MapId);

        std::string const mapPath = dataPath + "mmaps/" + testCase.MapFile;
        std::string const tilePath = dataPath + "mmaps/" + testCase.TileFile;
        if (!std::filesystem::exists(mapPath) || !std::filesystem::exists(tilePath))
            GTEST_SKIP() << "generated terrain-swap mmaps are not present under " << dataPath;

        std::shared_ptr<dtNavMesh> navMesh = MMapMgr::LoadNavMeshFromFile(mapPath);
        ASSERT_NE(navMesh, nullptr);
        ASSERT_TRUE(MMapMgr::LoadTileFromFile(navMesh.get(), testCase.MapId, tilePath));

        ManagedNavMeshQuery query = MMapMgr::CreateNavMeshQuery(navMesh.get());
        ASSERT_NE(query, nullptr);

        dtQueryFilter filter;
        float const startPoint[3] = { testCase.Y, testCase.Z, testCase.X };
        float const endPoint[3] = { testCase.Y + 100.0f, testCase.Z, testCase.X };
        float startClosest[3] = { 0.0f, 0.0f, 0.0f };
        float endClosest[3] = { 0.0f, 0.0f, 0.0f };

        dtPolyRef const startRef = FindNearestPoly(query.get(), filter, startPoint, startClosest);
        dtPolyRef const endRef = FindNearestPoly(query.get(), filter, endPoint, endClosest);
        ASSERT_NE(startRef, 0u);
        ASSERT_NE(endRef, 0u);

        dtPolyRef path[256] = {};
        int pathCount = 0;
        dtStatus const status = query->findPath(startRef, endRef, startClosest, endClosest,
            &filter, path, &pathCount, 256);

        EXPECT_TRUE(dtStatusSucceed(status));
        EXPECT_FALSE(dtStatusDetail(status, DT_PARTIAL_RESULT));
        ASSERT_GT(pathCount, 0);
        EXPECT_EQ(path[pathCount - 1], endRef);
    }
}

TEST(MMapTest, LoadsRebasedBaseAndChildNavMeshesAndFindsPathsWithinEach)
{
    char const* dataRoot = std::getenv("ACORE_CATA_TEST_MAP_DATA");
    if (!dataRoot)
        GTEST_SKIP() << "set ACORE_CATA_TEST_MAP_DATA to the extracted data root (dbc/maps/vmaps/mmaps)";

    std::string const dataPath = EnsureTrailingSlash(dataRoot);
    std::string const baseMapPath = dataPath + "mmaps/001.mmap";
    std::string const baseTilePath = dataPath + "mmaps/0013126.mmtile";
    std::string const childMapPath = dataPath + "mmaps/654.mmap";
    std::string const childTilePath = dataPath + "mmaps/6543127.mmtile";

    if (!std::filesystem::exists(baseMapPath) || !std::filesystem::exists(baseTilePath) ||
        !std::filesystem::exists(childMapPath) || !std::filesystem::exists(childTilePath))
        GTEST_SKIP() << "base/child boundary mmaps are not present under " << dataPath;

    dtNavMeshParams baseParams;
    dtNavMeshParams childParams;
    ASSERT_TRUE(ReadNavMeshParams(baseMapPath, baseParams));
    ASSERT_TRUE(ReadNavMeshParams(childMapPath, childParams));
    ASSERT_NEAR(baseParams.tileWidth, childParams.tileWidth, 0.01f);
    ASSERT_NEAR(baseParams.tileHeight, childParams.tileHeight, 0.01f);

    dtNavMeshParams compositeParams = {};
    compositeParams.orig[0] = std::min(baseParams.orig[0], childParams.orig[0]);
    compositeParams.orig[1] = 0.0f;
    compositeParams.orig[2] = std::min(baseParams.orig[2], childParams.orig[2]);
    compositeParams.tileWidth = baseParams.tileWidth;
    compositeParams.tileHeight = baseParams.tileHeight;
    compositeParams.maxTiles = baseParams.maxTiles + childParams.maxTiles;
    compositeParams.maxPolys = 256;

    dtNavMesh* rawNavMesh = dtAllocNavMesh();
    ASSERT_NE(rawNavMesh, nullptr);
    ASSERT_TRUE(dtStatusSucceed(rawNavMesh->init(&compositeParams)));
    std::shared_ptr<dtNavMesh> navMesh(rawNavMesh, NavMeshDeleter());

    ASSERT_TRUE(LoadRebasedTile(navMesh.get(), baseTilePath, baseParams, compositeParams));
    ASSERT_TRUE(LoadRebasedTile(navMesh.get(), childTilePath, childParams, compositeParams));

    ManagedNavMeshQuery query = MMapMgr::CreateNavMeshQuery(navMesh.get());
    ASSERT_NE(query, nullptr);

    // The sampled 654 fallback tiles are water/cliff edges (terrain heights differ by
    // roughly 100 m), so there is no land path across the seam. Verify that both source
    // origins rebase into one mesh and that each side still routes internally; a
    // cross-seam path needs a deliberate seam policy, not origin normalization alone.
    dtQueryFilter filter;
    auto checkPath = [&](char const* traceName, float startY, float endY)
    {
        SCOPED_TRACE(traceName);
        float const startPoint[3] = { startY, 50.0f, 266.0f };
        float const endPoint[3] = { endY, 50.0f, 266.0f };
        float startClosest[3] = { 0.0f, 0.0f, 0.0f };
        float endClosest[3] = { 0.0f, 0.0f, 0.0f };

        dtPolyRef const startRef = FindNearestPoly(query.get(), filter, startPoint, startClosest, 500.0f);
        dtPolyRef const endRef = FindNearestPoly(query.get(), filter, endPoint, endClosest, 500.0f);
        EXPECT_NE(startRef, 0u);
        EXPECT_NE(endRef, 0u);
        if (!startRef || !endRef)
            return false;

        dtMeshTile const* startTile = nullptr;
        dtMeshTile const* endTile = nullptr;
        dtPoly const* poly = nullptr;
        EXPECT_TRUE(dtStatusSucceed(navMesh->getTileAndPolyByRef(startRef, &startTile, &poly)));
        EXPECT_TRUE(dtStatusSucceed(navMesh->getTileAndPolyByRef(endRef, &endTile, &poly)));
        EXPECT_EQ(startTile, endTile);

        dtPolyRef path[256] = {};
        int pathCount = 0;
        dtStatus const status = query->findPath(startRef, endRef, startClosest, endClosest,
            &filter, path, &pathCount, 256);
        EXPECT_TRUE(dtStatusSucceed(status));
        EXPECT_FALSE(dtStatusDetail(status, DT_PARTIAL_RESULT));
        EXPECT_GT(pathCount, 0);
        if (pathCount > 0)
            EXPECT_EQ(path[pathCount - 1], endRef);
        return pathCount > 0;
    };

    EXPECT_TRUE(checkPath("child", 2400.0f, 2420.0f));
    EXPECT_TRUE(checkPath("base", 3000.0f, 3020.0f));
}
