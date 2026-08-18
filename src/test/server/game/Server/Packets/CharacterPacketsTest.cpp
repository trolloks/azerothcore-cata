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
#include <array>
#include <span>
#include <string_view>

namespace
{
    constexpr std::string_view PopulatedCharacterPayload =
        "0000010000C08046000100000000000000000000000000000000000000000000000000000000000000000000000000000000"
        "0000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000"
        "0000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000"
        "0000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000"
        "000000000000000000000000000000000000000000070000000000000000000000000000F90FA7420000000000357E04C300"
        "000000000043617461706C616E000503CDD70BC6000101020C000000";

    std::string PayloadHex(ByteBuffer const* packet)
    {
        return ByteArrayToHexStr(std::span<uint8 const>(packet->contents(), packet->size()));
    }

    std::string PopulatedPayload(uint32 guid = 0x01020304, std::string name = "Cataplan", uint8 listPosition = 7)
    {
        WorldPackets::Character::EnumCharactersResult packet;
        packet.Success = true;

        WorldPackets::Character::EnumCharactersResult::CharacterInfo& character = packet.Characters.emplace_back();
        character.Guid = ObjectGuid::Create<HighGuid::Player>(guid);
        character.Name = std::move(name);
        character.RaceID = 1;
        character.ClassID = 1;
        character.ExperienceLevel = 1;
        character.MapID = 0;
        character.ZoneID = 12;
        character.PreloadPos = Position(-8949.95f, -132.493f, 83.5312f);
        character.ListPosition = listPosition;

        return PayloadHex(packet.Write());
    }

    WorldPacket PlayerLoginPayload(std::span<uint8 const> payload)
    {
        WorldPacket packet(CMSG_PLAYER_LOGIN, payload.size());
        packet.append(payload.data(), payload.size());
        return packet;
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

TEST(CharacterPacketsTest, WritesSuccessfulPopulatedEnumCharacters)
{
    std::string const payload = PopulatedPayload();

    EXPECT_EQ(payload.size(), 278 * 2);
    EXPECT_EQ(payload, PopulatedCharacterPayload);
}

TEST(CharacterPacketsTest, PopulatedEnumFixtureRejectsWireMutations)
{
    EXPECT_NE(PopulatedPayload(0x00020304), PopulatedCharacterPayload);
    for (uint32 byte = 0; byte < 4; ++byte)
        EXPECT_NE(PopulatedPayload(0x01020304 ^ (uint32(1) << (byte * 8))), PopulatedCharacterPayload);
    EXPECT_NE(PopulatedPayload(0x01020304, "Catapla"), PopulatedCharacterPayload);
    EXPECT_NE(PopulatedPayload(0x01020304, "Cataplam"), PopulatedCharacterPayload);
    EXPECT_NE(PopulatedPayload(0x01020304, "Cataplan", 6), PopulatedCharacterPayload);

    WorldPackets::Character::EnumCharactersResult empty;
    empty.Success = true;
    EXPECT_NE(PayloadHex(empty.Write()), PopulatedCharacterPayload);
}

TEST(CharacterPacketsTest, ReadsBuild15595PlayerLoginGuid)
{
    std::array<uint8, 5> const payload = { 0xE2, 0x03, 0x05, 0x00, 0x02 };
    WorldPackets::Character::PlayerLogin packet(PlayerLoginPayload(payload));
    packet.Read();

    ObjectGuid const expected = ObjectGuid::Create<HighGuid::Player>(0x01020304);
    EXPECT_EQ(packet.Guid, expected);
    EXPECT_TRUE(packet.Guid.IsPlayer());
    EXPECT_EQ(packet.Guid.GetCounter(), 16909060u);
}

TEST(CharacterPacketsTest, RejectsTruncatedBuild15595PlayerLoginGuid)
{
    auto expectsTruncation = [](std::span<uint8 const> payload)
    {
        WorldPacket raw(CMSG_PLAYER_LOGIN, payload.size());
        if (!payload.empty())
            raw.append(payload.data(), payload.size());
        WorldPackets::Character::PlayerLogin packet(std::move(raw));
        EXPECT_THROW(packet.Read(), ByteBufferException);
    };
    std::array<uint8, 0> const empty = {};
    std::array<uint8, 1> const firstBitByte = { 0xE2 };
    std::array<uint8, 4> const missingByte = { 0xE2, 0x03, 0x05, 0x00 };

    expectsTruncation(empty);
    expectsTruncation(firstBitByte);
    expectsTruncation(missingByte);
}

TEST(CharacterPacketsTest, Build15595PlayerLoginGuidRejectsWireMutations)
{
    std::array<uint8, 5> const payload = { 0xE2, 0x03, 0x05, 0x00, 0x02 };
    ObjectGuid const expected = ObjectGuid::Create<HighGuid::Player>(0x01020304);

    for (uint8 index = 0; index < payload.size(); ++index)
    {
        std::array<uint8, 5> mutated = payload;
        mutated[index] ^= 0x01;
        WorldPackets::Character::PlayerLogin packet(PlayerLoginPayload(mutated));
        try
        {
            packet.Read();
            EXPECT_NE(packet.Guid, expected);
        }
        catch (ByteBufferException const&)
        {
        }
    }
}
