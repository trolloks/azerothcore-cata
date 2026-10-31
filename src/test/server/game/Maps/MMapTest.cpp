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
#include <DetourNavMeshQuery.h>
#include <gtest/gtest.h>
#include <cmath>
#include <cstdlib>
#include <filesystem>
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
        float const point[3], float closest[3])
    {
        float extents[3] = { 10.0f, 20.0f, 10.0f };
        dtPolyRef ref = 0;
        if (dtStatusSucceed(query->findNearestPoly(point, extents, &filter, &ref, closest)) && ref)
            return ref;

        extents[0] = 50.0f;
        extents[1] = 100.0f;
        extents[2] = 50.0f;
        if (dtStatusSucceed(query->findNearestPoly(point, extents, &filter, &ref, closest)) && ref)
            return ref;

        return 0;
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
