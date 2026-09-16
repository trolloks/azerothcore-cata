/*
 * This file is part of the AzerothCore Project. See AUTHORS file for Copyright information
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by the
 * Free Software Foundation; either version 2 of the License, or (at your
 * option) any later version.
 */

#include "Opcodes.h"
#include "SpellInfo.h"
#include "WorldPacket.h"
#include <gtest/gtest.h>

// Locks in the Cata 4.3.4 CMSG_CAST_SPELL wire layout (verified against the cata-js test
// client): castCount(u8), spellId(u32), misc(u32, unused), castFlags(u8), targetFlags(u32),
// [target pguid]. This mirrors the read order in WorldSession::HandleCastSpellOpcode.
TEST(SpellCastPacketFormatTest, ReadsMiscWordBetweenSpellIdAndCastFlags)
{
    WorldPacket packet(CMSG_CAST_SPELL);
    packet << uint8(1);            // castCount
    packet << uint32(133);         // spellId (Fireball)
    packet << uint32(0xDEADBEEF);  // misc, unused on this fork
    packet << uint8(0);            // castFlags
    packet << uint32(TARGET_FLAG_NONE); // self-cast target block

    uint8 castCount, castFlags;
    uint32 spellId, misc, targetFlags;
    packet >> castCount >> spellId >> misc >> castFlags;
    packet >> targetFlags;

    EXPECT_EQ(castCount, 1u);
    EXPECT_EQ(spellId, 133u);
    EXPECT_EQ(misc, 0xDEADBEEFu);
    EXPECT_EQ(castFlags, 0u);
    EXPECT_EQ(targetFlags, uint32(TARGET_FLAG_NONE));
    EXPECT_EQ(packet.rpos(), packet.size());
}
