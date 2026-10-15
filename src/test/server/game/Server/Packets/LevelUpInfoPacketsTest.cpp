/*
 * This file is part of the AzerothCore Project. See AUTHORS file for Copyright information
 *
 * This program is free software; you can redistribute it and/or modify it
 * under the terms of the GNU General Public License as published by the
 * Free Software Foundation; either version 2 of the License, or (at your
 * option) any later version.
 */

#include "MiscPackets.h"
#include "SharedDefines.h"
#include "Util.h"
#include <gtest/gtest.h>
#include <span>
#include <string>

namespace
{
    std::string PayloadHex(ByteBuffer const* packet)
    {
        return ByteArrayToHexStr(std::span<uint8 const>(packet->contents(), packet->size()));
    }
}

// Cata 4.3.4 SMSG_LEVELUP_INFO: int32 level, int32 health delta, a fixed 6 int32 power deltas,
// then MAX_STATS int32 stat deltas. Deliberately decoupled from MAX_POWERS (7 after POWER_RUNE).
TEST(LevelUpInfoPacketsTest, WritesCataLayoutWithSignedDeltas)
{
    WorldPackets::Misc::LevelUpInfo packet;
    packet.Level = 2;
    packet.HealthDelta = -9;
    packet.PowerDelta[0] = 50;
    packet.PowerDelta[5] = -1;
    for (uint8 i = STAT_STRENGTH; i < MAX_STATS; ++i)
        packet.StatDelta[i] = 1;

    WorldPacket const* payload = packet.Write();

    EXPECT_EQ(payload->size(), 4u + 4u + 6u * 4u + MAX_STATS * 4u);
    EXPECT_EQ(PayloadHex(payload),
              "02000000F7FFFFFF3200000000000000000000000000000000000000FFFFFFFF"
              "0100000001000000010000000100000001000000");
}
