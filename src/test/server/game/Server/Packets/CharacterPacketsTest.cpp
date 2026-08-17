/*
 * This file is part of the AzerothCore Project. See AUTHORS file for Copyright information
 *
 * This program is free software; you can redistribute it and/or modify it
 * under the terms of the GNU General Public License as published by the
 * Free Software Foundation; either version 2 of the License, or (at your
 * option) any later version.
 */

#include "CharacterPackets.h"
#include "Util.h"
#include <gtest/gtest.h>
#include <span>

namespace
{
    std::string PayloadHex(ByteBuffer const* packet)
    {
        return ByteArrayToHexStr(std::span<uint8 const>(packet->contents(), packet->size()));
    }
}

TEST(CharacterPacketsTest, ReadsEmptyEnumCharacters)
{
    WorldPackets::Character::EnumCharacters packet{ WorldPacket(CMSG_CHAR_ENUM) };
    packet.Read();

    EXPECT_EQ(packet.GetRawPacket()->rpos(), packet.GetRawPacket()->size());
}

TEST(CharacterPacketsTest, RejectsTrailingEnumCharactersBody)
{
    WorldPacket raw(CMSG_CHAR_ENUM, 1);
    raw << uint8(0);
    WorldPackets::Character::EnumCharacters packet(std::move(raw));

    EXPECT_THROW(packet.Read(), ByteBufferInvalidValueException);
}

TEST(CharacterPacketsTest, WritesSuccessfulEmptyEnumCharacters)
{
    WorldPackets::Character::EnumCharactersResult packet;
    packet.Success = true;

    EXPECT_EQ(packet.GetOpcode(), SMSG_CHAR_ENUM);
    EXPECT_EQ(PayloadHex(packet.Write()), "000001000000");
}

TEST(CharacterPacketsTest, WritesFailedEmptyEnumCharacters)
{
    WorldPackets::Character::EnumCharactersResult packet;

    EXPECT_EQ(packet.GetOpcode(), SMSG_CHAR_ENUM);
    EXPECT_EQ(PayloadHex(packet.Write()), "000000000000");
}
