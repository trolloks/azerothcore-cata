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

#include "UpdateData.h"
#include "WorldPacket.h"
#include <gtest/gtest.h>
#include <type_traits>

// 4.3.4 prefixes SMSG_UPDATE_OBJECT with the map id. Every sender must provide it, so the
// default constructor is gone; a new call site cannot silently emit map 0 (issues #193, #194).
static_assert(!std::is_default_constructible_v<UpdateData>);

TEST(UpdateDataTest, BuildPacketStartsWithTheMapId)
{
    UpdateData data(648);
    WorldPacket packet;
    data.BuildPacket(packet);

    uint16 map = 0;
    packet >> map;
    EXPECT_EQ(map, 648);
}
