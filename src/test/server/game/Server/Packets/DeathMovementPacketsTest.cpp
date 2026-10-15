/*
 * This file is part of the AzerothCore Project. See AUTHORS file for Copyright information
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by the
 * Free Software Foundation; either version 2 of the License, or (at your
 * option) any later version.
 */

#include "IntegrationTestFixture.h"
#include "MovementPackets.h"
#include "Util.h"
#include "WorldPacket.h"
#include <span>

namespace
{
std::string Hex(WorldPacket const& packet)
{
    return ByteArrayToHexStr(std::span<uint8 const>(packet.contents(), packet.size()));
}
}

TEST(DeathMovementPacketsTest, WritesRootAndWaterWalkRequestsWithCataGuidOrder)
{
    struct Case
    {
        OpcodeServer opcode;
        char const* expected;
        char const* sparse;
    };
    for (Case const& test : {
        Case{ SMSG_MOVE_ROOT, "FF030002074433221105040906", "100044332211" },
        Case{ SMSG_MOVE_UNROOT, "FF050603443322110200090407", "804433221100" },
        Case{ SMSG_MOVE_WATER_WALK, "FF000702443322110905040306", "100044332211" },
        Case{ SMSG_MOVE_LAND_WALK, "FF060309070400050244332211", "020044332211" } })
    {
        MovementInfo info;
        info.guid = ObjectGuid(uint64(0x0807060504030201));
        WorldPacket packet(test.opcode);
        WorldPackets::Movement::WriteGroundMovement(packet, info,
            WorldPackets::Movement::GetMovementFlagSequence(test.opcode), 0x11223344);
        EXPECT_EQ(Hex(packet), test.expected);
        info.guid = ObjectGuid(uint64(1));
        WorldPacket sparse(test.opcode);
        WorldPackets::Movement::WriteGroundMovement(sparse, info,
            WorldPackets::Movement::GetMovementFlagSequence(test.opcode), 0x11223344);
        EXPECT_EQ(Hex(sparse), test.sparse);
    }
}

TEST(DeathMovementPacketsTest, WritesNearTeleportAndReadsCataAcknowledgement)
{
    ObjectGuid const mover(uint64(0x0807060504030201));
    WorldPacket packet(MSG_MOVE_TELEPORT);
    WorldPackets::Movement::WriteTeleport(packet, mover, 0x11223344, Position(1, 2, 3, 4));
    EXPECT_EQ(Hex(packet), "F3C044332211030205070000803F04000080400900004040000600000040");

    uint8 const bytes[] = { 0x44, 0x33, 0x22, 0x11, 0x88, 0x77, 0x66, 0x55,
        0xFF, 0x04, 0x02, 0x09, 0x06, 0x07, 0x03, 0x05, 0x00 };
    WorldPacket ack(MSG_MOVE_TELEPORT_ACK);
    ack.append(bytes, sizeof(bytes));
    ObjectGuid guid;
    uint32 counter, time;
    WorldPackets::Movement::ReadTeleportAck(ack, guid, counter, time);
    EXPECT_EQ(guid, mover);
    EXPECT_EQ(counter, 0x11223344u);
    EXPECT_EQ(time, 0x55667788u);
    EXPECT_EQ(ack.rpos(), ack.size());
}

TEST(DeathMovementPacketsTest, WritesTeleportObserverUpdate)
{
    MovementInfo info;
    info.guid = ObjectGuid(uint64(1));
    info.pos.Relocate(1, 2, 3, 4);
    info.time = 5;
    WorldPacket packet(SMSG_MOVE_UPDATE_TELEPORT);
    WorldPackets::Movement::WriteTeleportUpdate(packet, info);
    EXPECT_EQ(Hex(packet), "00004040000000400000803F210680000080400500000000");
}

TEST(DeathMovementPacketsTest, FlagAcknowledgementsCarryCounterAndMovement)
{
    for (uint16 opcode : { CMSG_FORCE_MOVE_ROOT_ACK, CMSG_FORCE_MOVE_UNROOT_ACK, CMSG_MOVE_WATER_WALK_ACK })
    {
        MovementInfo sent;
        sent.guid = ObjectGuid(uint64(0x0807060504030201));
        sent.pos.Relocate(1, 2, 3, 4);
        sent.time = 12345;
        sent.flags = opcode == CMSG_FORCE_MOVE_ROOT_ACK ? MOVEMENTFLAG_ROOT : MOVEMENTFLAG_WATERWALKING;
        auto const* sequence = WorldPackets::Movement::GetMovementFlagSequence(opcode);
        ASSERT_NE(sequence, nullptr);
        WorldPacket packet(opcode);
        WorldPackets::Movement::WriteGroundMovement(packet, sent, sequence, 17);
        MovementInfo received;
        uint32 counter;
        WorldPackets::Movement::ReadGroundMovement(packet, received, sequence, &counter);
        EXPECT_EQ(received.guid, sent.guid);
        EXPECT_EQ(received.flags, sent.flags);
        EXPECT_EQ(received.time, sent.time);
        EXPECT_FLOAT_EQ(received.pos.GetPositionX(), 1);
        EXPECT_FLOAT_EQ(received.pos.GetPositionY(), 2);
        EXPECT_FLOAT_EQ(received.pos.GetPositionZ(), 3);
        EXPECT_FLOAT_EQ(received.pos.GetOrientation(), 4);
        EXPECT_EQ(counter, 17u);
        EXPECT_EQ(packet.rpos(), packet.size());
    }
}

class DeathMovementHandlerTest : public IntegrationTestFixture
{
};

TEST_F(DeathMovementHandlerTest, ForeignFlagAcknowledgementsCannotChangeMoverState)
{
    TestPlayer* player = CreateTestPlayer();
    for (uint16 opcode : { CMSG_FORCE_MOVE_ROOT_ACK, CMSG_FORCE_MOVE_UNROOT_ACK, CMSG_MOVE_WATER_WALK_ACK })
    {
        MovementInfo foreign;
        foreign.guid = ObjectGuid(uint64(2));
        foreign.flags = MOVEMENTFLAG_WATERWALKING;
        WorldPacket packet(opcode);
        WorldPackets::Movement::WriteGroundMovement(packet, foreign,
            WorldPackets::Movement::GetMovementFlagSequence(opcode), 17);
        if (opcode == CMSG_MOVE_WATER_WALK_ACK)
            player->GetSession()->HandleMoveFlagChangeOpcode(packet);
        else
            player->GetSession()->HandleMoveRootAck(packet);
        EXPECT_EQ(player->m_movementInfo.flags, 0u);
        EXPECT_FLOAT_EQ(player->GetPositionX(), 0);
    }
}
