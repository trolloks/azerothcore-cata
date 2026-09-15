/*
 * This file is part of the AzerothCore Project. See AUTHORS file for Copyright information
 *
 * You may redistribute it and/or modify it under the terms of the GNU General Public License
 * version 2 or, at your option, any later version.
 */

#include "MovementPackets.h"
#include "IntegrationTestFixture.h"
#include "Object.h"
#include "Util.h"
#include <gtest/gtest.h>
#include <cstring>
#include <limits>
#include <span>

namespace
{
    // Synthetic fixtures from apps/cata/generate_movement_fixtures.py and its pinned reference sequences.
    std::string const Idle = "000040400000803F00000040936C400002030504030201";
    std::string const Sparse = "00004040000080BF00000040D124C01100";
    std::string const OptionalFields =
        "000070400000A03F000020C02FD8BFF84001000020050603090207000400000CC1FF00000040410000F0402A000000"
        "0000C840317151112C0000002B0000006181210000C03F000088C04D0000000000B0400000403F000000BF000080BE"
        "0000803E44332211";

    WorldPacket ClientPacket(OpcodeClient opcode, std::string const& hex)
    {
        std::vector<uint8> bytes(hex.size() / 2);
        Acore::Impl::HexStrToByteArray(hex, bytes.data(), bytes.size());
        WorldPacket packet(opcode, bytes.size());
        if (!bytes.empty())
            packet.append(bytes.data(), bytes.size());
        return packet;
    }

    std::string MovementUpdate(MovementInfo const& info)
    {
        WorldPacket packet(SMSG_MOVE_UPDATE);
        WorldPackets::Movement::WriteMovementUpdate(packet, info);
        return ByteArrayToHexStr(std::span<uint8 const>(packet.contents(), packet.size()));
    }
}

TEST(MovementPacketsTest, ReadsIdleHeartbeatAndWritesMovementUpdate)
{
    WorldPacket packet = ClientPacket(MSG_MOVE_HEARTBEAT, Idle);
    MovementInfo info;
    WorldPackets::Movement::ReadHeartbeat(packet, info);
    EXPECT_EQ(info.guid, ObjectGuid(uint64(0x01020304)));
    EXPECT_EQ(info.flags, 0u);
    EXPECT_EQ(info.flags2, 0u);
    EXPECT_FLOAT_EQ(info.pos.GetPositionX(), 1.0f);
    EXPECT_FLOAT_EQ(info.pos.GetPositionY(), 2.0f);
    EXPECT_FLOAT_EQ(info.pos.GetPositionZ(), 3.0f);
    EXPECT_EQ(info.time, 0x01020304u);
    EXPECT_EQ(packet.rpos(), packet.size());
    EXPECT_EQ(MovementUpdate(info), "53784000000040000000803F0000404004030201030502");

    packet = ClientPacket(MSG_MOVE_HEARTBEAT, Sparse);
    WorldPackets::Movement::ReadHeartbeat(packet, info);
    EXPECT_EQ(info.guid, ObjectGuid(uint64(0x0010000000000001)));
    EXPECT_EQ(info.time, 0u);
    EXPECT_FLOAT_EQ(info.pos.GetOrientation(), 0.0f);
    EXPECT_EQ(MovementUpdate(info), "32684000000040000080BF11000040400000000000");
}

TEST(MovementPacketsTest, ReadsOptionalFieldsAndPreservesTheirWireLayout)
{
    WorldPacket packet = ClientPacket(MSG_MOVE_HEARTBEAT, OptionalFields);
    MovementInfo info;
    WorldPackets::Movement::ReadHeartbeat(packet, info);
    EXPECT_EQ(info.guid, ObjectGuid(uint64(0x0807060504030201)));
    EXPECT_EQ(info.flags, uint32(MOVEMENTFLAG_FALLING | MOVEMENTFLAG_SPLINE_ELEVATION | MOVEMENTFLAG_ONTRANSPORT));
    EXPECT_EQ(info.flags2, MOVEMENTFLAG2_ALWAYS_ALLOW_PITCHING | MOVEMENTFLAG2_INTERPOLATED_MOVEMENT);
    EXPECT_FLOAT_EQ(info.pos.GetPositionX(), 1.25f);
    EXPECT_FLOAT_EQ(info.pos.GetPositionY(), -2.5f);
    EXPECT_FLOAT_EQ(info.pos.GetPositionZ(), 3.75f);
    EXPECT_FLOAT_EQ(info.pos.GetOrientation(), 1.5f);
    EXPECT_EQ(info.time, 0x11223344u);
    EXPECT_EQ(info.transport.guid, ObjectGuid(uint64(0x1020304050607080)));
    EXPECT_FLOAT_EQ(info.transport.pos.GetPositionX(), 6.25f);
    EXPECT_FLOAT_EQ(info.transport.pos.GetPositionY(), 7.5f);
    EXPECT_FLOAT_EQ(info.transport.pos.GetPositionZ(), -8.75f);
    EXPECT_FLOAT_EQ(info.transport.pos.GetOrientation(), 2.0f);
    EXPECT_EQ(info.transport.seat, -1);
    EXPECT_EQ(info.transport.time, 42u);
    EXPECT_EQ(info.transport.time2, 43u);
    EXPECT_EQ(info.transport.vehicleId, 44u);
    EXPECT_EQ(info.fallTime, 77u);
    EXPECT_FLOAT_EQ(info.jump.zspeed, -4.25f);
    EXPECT_FLOAT_EQ(info.jump.xyspeed, 5.5f);
    EXPECT_FLOAT_EQ(info.jump.cosAngle, 0.75f);
    EXPECT_FLOAT_EQ(info.jump.sinAngle, -0.5f);
    EXPECT_FLOAT_EQ(info.pitch, -0.25f);
    EXPECT_FLOAT_EQ(info.splineElevation, 0.25f);
    EXPECT_EQ(MovementUpdate(info),
        "E301093C2000800FFC070000B040000000BF0000403F000088C04D0000000000803E09000020C0052C00000021FF31"
        "0000C8407100000040612B0000008100000CC11141510000F0402A000000040000A03F060000704044332211020000"
        "80BE000000C03F03");
}

TEST(MovementPacketsTest, RejectsTruncatedAndTrailingHeartbeatData)
{
    for (std::string const& fixture : { Idle, Sparse, OptionalFields })
    {
        for (std::size_t length = 0; length < fixture.size(); length += 2)
        {
            WorldPacket packet = ClientPacket(MSG_MOVE_HEARTBEAT, fixture.substr(0, length));
            MovementInfo info;
            EXPECT_THROW(WorldPackets::Movement::ReadHeartbeat(packet, info), ByteBufferException) << length;
        }
        WorldPacket packet = ClientPacket(MSG_MOVE_HEARTBEAT, fixture + "00");
        MovementInfo info;
        EXPECT_THROW(WorldPackets::Movement::ReadHeartbeat(packet, info), ByteBufferInvalidValueException);
    }
}

TEST(MovementPacketsTest, TranslatesInternalFlagsAtTheWireBoundary)
{
    using namespace WorldPackets::Movement;
    EXPECT_EQ(MovementFlagsToClient(MOVEMENTFLAG_WALKING), 0x100u);
    EXPECT_EQ(MovementFlagsToClient(MOVEMENTFLAG_DISABLE_GRAVITY), 0x200u);
    EXPECT_EQ(MovementFlagsToClient(MOVEMENTFLAG_ROOT), 0x400u);
    EXPECT_EQ(MovementFlagsToClient(MOVEMENTFLAG_FALLING), 0x800u);
    EXPECT_EQ(MovementFlagsToClient(MOVEMENTFLAG_SWIMMING), 0x100000u);
    EXPECT_EQ(MovementFlagsToClient(MOVEMENTFLAG_FLYING), 0x1000000u);
    EXPECT_EQ(MovementFlagsToClient(MOVEMENTFLAG_SPLINE_ELEVATION), 0x2000000u);
    EXPECT_EQ(MovementFlagsToClient(MOVEMENTFLAG_WATERWALKING), 0x4000000u);
    EXPECT_EQ(MovementFlagsToClient(MOVEMENTFLAG_FALLING_SLOW), 0x8000000u);
    EXPECT_EQ(MovementFlagsToClient(MOVEMENTFLAG_HOVER), 0x10000000u);
    EXPECT_EQ(MovementFlagsToClient(MOVEMENTFLAG_DISABLE_COLLISION), 0x20000000u);
    EXPECT_EQ(MovementFlagsToClient(MOVEMENTFLAG_ONTRANSPORT | MOVEMENTFLAG_SPLINE_ENABLED), 0u);
    EXPECT_EQ(ExtraMovementFlagsToClient(MOVEMENTFLAG2_FULL_SPEED_TURNING), 0x4u);
    EXPECT_EQ(ExtraMovementFlagsToClient(MOVEMENTFLAG2_FULL_SPEED_PITCHING), 0x8u);
    EXPECT_EQ(ExtraMovementFlagsToClient(MOVEMENTFLAG2_ALWAYS_ALLOW_PITCHING), 0x10u);
    EXPECT_EQ(ExtraMovementFlagsToClient(MOVEMENTFLAG2_INTERPOLATED_MOVEMENT), 0x2000u);
}

TEST(MovementPacketsTest, ReadsRunSpeedAcknowledgementAndWritesBothServerPackets)
{
    struct Fixture
    {
        char const* Ack;
        uint64 Guid;
        char const* Request;
        char const* Update;
    };
    for (Fixture const& fixture : std::initializer_list<Fixture>{
        {
            "403020100000803F000028410000404000000040AA26C00200030504030201",
            0x01020304,
            "56000240302010000028410503",
            "000040400000803F0000004000002841756C000403020105000302"
        },
        {
            "40302010000080BF0000284100004040000000400A76801100",
            0x0010000000000001,
            "8440302010000028411100",
            "00004040000080BF0000004000002841E50C00000000001100"
        },
        {
            "403020100000A03F0000284100007040000020C0F6917FF08002002020060403050702090000000CC121710000F04081"
            "312B0000000000C8402A0000001100000040512C00000061FF41000088C00000B040000000BF0000403F4D0000000000"
            "803E000080BE443322110000C03F",
            0x0807060504030201,
            "FF07050304403020100000284106000902",
            "000070400000A03F000020C00000284198404210004006FFFC41310000C840000000407181212A00000011FF2B000000"
            "0000F04051612C00000000000CC1443322110000403F0000B040000000BF000088C04D000000000080BE060000803E07"
            "09040000C03F00050203"
        }
    })
    {
        WorldPacket packet = ClientPacket(CMSG_MOVE_FORCE_RUN_SPEED_CHANGE_ACK, fixture.Ack);
        MovementInfo info;
        uint32 counter;
        float speed;
        WorldPackets::Movement::ReadRunSpeedChangeAck(packet, info, counter, speed);
        EXPECT_EQ(counter, 0x10203040u);
        EXPECT_FLOAT_EQ(speed, 10.5f);
        EXPECT_EQ(info.guid, ObjectGuid(fixture.Guid));
        EXPECT_EQ(packet.rpos(), packet.size());

        WorldPacket request(SMSG_MOVE_SET_RUN_SPEED);
        WorldPackets::Movement::WriteRunSpeedChange(request, info.guid, counter, speed);
        EXPECT_EQ(ByteArrayToHexStr(std::span<uint8 const>(request.contents(), request.size())), fixture.Request);
        WorldPacket update(SMSG_MOVE_UPDATE_RUN_SPEED);
        WorldPackets::Movement::WriteRunSpeedUpdate(update, info, speed);
        EXPECT_EQ(ByteArrayToHexStr(std::span<uint8 const>(update.contents(), update.size())), fixture.Update);

        std::string hex = fixture.Ack;
        for (std::size_t length = 0; length < hex.size(); length += 2)
        {
            packet = ClientPacket(CMSG_MOVE_FORCE_RUN_SPEED_CHANGE_ACK, hex.substr(0, length));
            EXPECT_THROW(WorldPackets::Movement::ReadRunSpeedChangeAck(packet, info, counter, speed),
                ByteBufferException) << length;
        }
        packet = ClientPacket(CMSG_MOVE_FORCE_RUN_SPEED_CHANGE_ACK, hex + "00");
        EXPECT_THROW(WorldPackets::Movement::ReadRunSpeedChangeAck(packet, info, counter, speed),
            ByteBufferInvalidValueException);
        packet = ClientPacket(CMSG_MOVE_FORCE_RUN_SPEED_CHANGE_ACK, hex);
        packet.put<float>(8, std::numeric_limits<float>::quiet_NaN());
        EXPECT_THROW(WorldPackets::Movement::ReadRunSpeedChangeAck(packet, info, counter, speed),
            ByteBufferInvalidValueException);
    }
}

TEST(MovementPacketsTest, ReadsGroundMovementAndRoundTripsTheSameOpcodeLayout)
{
    struct Fixture
    {
        OpcodeClient Opcode;
        char const* Idle;
        char const* Sparse;
        char const* Optional;
        // Observer rebroadcast always forces the timestamp field present (see WriteGroundMovement),
        // so a client packet that omits it, like Sparse, re-encodes with an extra zero timestamp.
        char const* SparseRebroadcast;
    };
    // Synthetic fixtures from apps/cata/generate_movement_fixtures.py and its pinned reference sequences.
    for (Fixture const& fixture : std::initializer_list<Fixture>{
        { MSG_MOVE_START_BACKWARD, "0000803F000040400000004072D2400205030004030201", "000080BF00004040000000402AD0C01100", "0000A03F00007040000020C0F40FBFF08002000042060904030700020500000CC1612C00000081310000F040710000C8402B0000004100000040FF112A0000002151000080BE443322110000B0400000403F000000BF000088C04D0000000000C03F0000803E", "000080BF000040400000004022D0C0110000000000" },
        { MSG_MOVE_START_FORWARD, "00000040000040400000803F6B91800302000504030201", "0000004000004040000080BF28D3801100", "000020C0000070400000A03FE74C42000800FFE0200204060309050700000088C00000B040000000BF0000403F4D000000510000F04000000CC171411100000040610000C840312C0000002A0000002181FF2B0000000000803E000080BE0000C03F44332211", "0000004000004040000080BF28D180110000000000" },
        { MSG_MOVE_START_STRAFE_LEFT, "000040400000803F00000040A4DC800300020504030201", "00004040000080BF00000040B894801100", "000070400000A03F000020C055EA7FF8400100002002060503000904070000403F0000B040000000BF4D000000000088C0FF612B0000005100000CC12C00000081110000F0403171000000402A00000021410000C840443322110000C03F000080BE0000803E", "00004040000080BF00000040B09480110000000000" },
        { MSG_MOVE_START_STRAFE_RIGHT, "000000400000803F00004040E0B3400002030504030201", "00000040000080BF00004040A0E5401100", "000020C00000A03F00007040531EFFF0100800200209070503020406003171210000F040000000408161FF0000C8402C0000002A00000041112B00000000000CC151000080BE0000C03F000000BF0000403F0000B0404D000000000088C0443322110000803E", "00000040000080BF00004040A0A540110000000000" },
        { MSG_MOVE_START_TURN_LEFT, "000000400000803F000040406B0F000500030204030201", "00000040000080BF00004040291D800011", "000020C00000A03F0000704046F64043FF8400100000040907060502030000403F000000BF0000B040000088C04D000000810000C8402A000000FF00000CC14100000040612171510000F0402C0000002B000000311144332211000080BE0000C03F0000803E", "00000040000080BF00004040291D00001100000000" },
        { MSG_MOVE_START_TURN_RIGHT, "0000803F0000404000000040AC65800500030204030201", "000080BF00004040000000402C59800011", "0000A03F00007040000020C0CB2E7FF0100800200207000905020304060000F0408141712161FF00000040312C00000000000CC10000C8402A00000011512B0000000000B0400000403F000000BF4D000000000088C0000080BE0000C03F0000803E44332211", "000080BF00004040000000402C4980001100000000" },
        { MSG_MOVE_STOP, "0000803F0000004000004040AA45C00005030204030201", "000080BF00000040000040406A50C01100", "0000A03F000020C000007040C5ADBFF08002000042060500040203070941112A000000FF00000CC12C00000061810000F04071512B0000000000C840000000403121443322110000C03F000080BE0000803E000000BF0000403F0000B040000088C04D000000", "000080BF00000040000040406A40C0110000000000" },
        { MSG_MOVE_STOP_STRAFE, "00000040000040400000803FA7A8800300020504030201", "0000004000004040000080BFC5AA001100", "000020C0000070400000A03F3A4BFFF080020020200209050407060300FF2100000CC12C00000071516141312A000000000000400000C840810000F0402B00000011000000BF0000B0400000403F4D000000000088C00000803E0000C03F000080BE44332211", "0000004000004040000080BF85AA00110000000000" },
        { MSG_MOVE_STOP_TURN, "0000803F00004040000000400DAD800003050204030201", "000080BF00004040000000400567801100", "0000A03F00007040000020C0ECD87FF8400100002005020604000903070000803E0000C84031FF6151000000402B0000002C000000117181410000F04000000CC12A000000214D0000000000B040000000BF0000403F000088C044332211000080BE0000C03F", "000080BF0000404000000040056580110000000000" },
        { MSG_MOVE_SET_RUN_MODE, "000000400000803F0000404068E8C00005020304030201", "00000040000080BF00004040C8AC801100", "000020C00000A03F0000704026D77FF808040010000506000904030702000080BE2B000000510000C840FF317100000CC1611100000040412A0000002C000000810000F04021000000BF0000B0400000403F4D000000000088C00000803E443322110000C03F", "00000040000080BF0000404048AC80110000000000" },
        { MSG_MOVE_SET_WALK_MODE, "000000400000803F000040401EAA800005030204030201", "00000040000080BF00004040B6A0801100", "000020C00000A03F00007040995E7FF8400100002007060409050002036131FF00000CC15121812A000000412B000000000000400000C8402C000000110000F040710000403F0000B040000000BF000088C04D0000000000803E000080BE443322110000C03F", "00000040000080BF0000404096A080110000000000" },
    })
    {
        WorldPackets::Movement::MovementStatusElements const* sequence =
            WorldPackets::Movement::GetGroundMovementSequence(fixture.Opcode);
        ASSERT_NE(sequence, nullptr) << fixture.Opcode;

        for (auto const& [hex, expectedRebroadcast] : {
            std::pair{ fixture.Idle, fixture.Idle },
            std::pair{ fixture.Sparse, fixture.SparseRebroadcast },
            std::pair{ fixture.Optional, fixture.Optional },
        })
        {
            WorldPacket packet = ClientPacket(fixture.Opcode, hex);
            MovementInfo info;
            WorldPackets::Movement::ReadGroundMovement(packet, info, sequence);
            EXPECT_EQ(packet.rpos(), packet.size()) << fixture.Opcode << " " << hex;

            WorldPacket rewritten(fixture.Opcode);
            WorldPackets::Movement::WriteGroundMovement(rewritten, info, sequence);
            EXPECT_EQ(ByteArrayToHexStr(std::span<uint8 const>(rewritten.contents(), rewritten.size())),
                expectedRebroadcast) << fixture.Opcode;
        }

        for (std::size_t length = 0; length < std::strlen(fixture.Idle); length += 2)
        {
            WorldPacket packet = ClientPacket(fixture.Opcode, std::string(fixture.Idle).substr(0, length));
            MovementInfo info;
            EXPECT_THROW(WorldPackets::Movement::ReadGroundMovement(packet, info, sequence), ByteBufferException)
                << fixture.Opcode << " " << length;
        }
        WorldPacket trailing = ClientPacket(fixture.Opcode, std::string(fixture.Idle) + "00");
        MovementInfo info;
        EXPECT_THROW(WorldPackets::Movement::ReadGroundMovement(trailing, info, sequence),
            ByteBufferInvalidValueException) << fixture.Opcode;
    }
}

using RunSpeedAcknowledgementTest = IntegrationTestFixture;

TEST_F(RunSpeedAcknowledgementTest, TracksLatestRequestAndRejectsInvalidAcknowledgements)
{
    TestPlayer* player = CreateTestPlayer(0x01020304);
    WorldSession* session = player->GetSession();
    player->SetSpeedRate(MOVE_RUN, 1.5f);
    player->SendSpeedToController(MOVE_RUN, player);
    ASSERT_TRUE(player->m_pendingRunSpeedChange);
    EXPECT_GT(player->m_pendingRunSpeedChange->Counter, player->GetMapChangeOrderCounter());
    EXPECT_FLOAT_EQ(player->m_pendingRunSpeedChange->Speed, 10.5f);
    uint32 firstCounter = player->m_pendingRunSpeedChange->Counter;
    player->SendSpeedToController(MOVE_RUN, player);
    EXPECT_GT(player->m_pendingRunSpeedChange->Counter, firstCounter);

    std::string const ack =
        "403020100000803F000028410000404000000040AA26C00200030504030201";
    // No pending request: an unsolicited or duplicate acknowledgement cannot be accepted.
    player->m_pendingRunSpeedChange.reset();
    WorldPacket packet = ClientPacket(CMSG_MOVE_FORCE_RUN_SPEED_CHANGE_ACK, ack);
    session->HandleForceRunSpeedChangeAck(packet);
    EXPECT_FALSE(player->m_pendingRunSpeedChange);
    EXPECT_FALSE(session->IsKicked());

    // An older counter must be ignored before comparing the requested speed.
    player->m_pendingRunSpeedChange = Unit::PendingRunSpeedChange{ 0x10203041, 7.0f };
    packet = ClientPacket(CMSG_MOVE_FORCE_RUN_SPEED_CHANGE_ACK, ack);
    session->HandleForceRunSpeedChangeAck(packet);
    EXPECT_EQ(player->m_pendingRunSpeedChange->Counter, 0x10203041u);
    EXPECT_FALSE(session->IsKicked());

    // The map-change boundary is also stale, even if it matches the pending counter.
    player->m_pendingRunSpeedChange = Unit::PendingRunSpeedChange{ 0, 7.0f };
    packet = ClientPacket(CMSG_MOVE_FORCE_RUN_SPEED_CHANGE_ACK, ack);
    packet.put<uint32>(0, 0);
    session->HandleForceRunSpeedChangeAck(packet);
    EXPECT_TRUE(player->m_pendingRunSpeedChange);
    EXPECT_FALSE(session->IsKicked());

    player->m_pendingRunSpeedChange = Unit::PendingRunSpeedChange{ 0x10203040, 7.0f };
    packet = ClientPacket(CMSG_MOVE_FORCE_RUN_SPEED_CHANGE_ACK,
        "40302010000080BF0000284100004040000000400A76801100");
    session->HandleForceRunSpeedChangeAck(packet);
    EXPECT_TRUE(player->m_pendingRunSpeedChange);
    EXPECT_FALSE(session->IsKicked());

    // A matching counter with the wrong speed is rejected without consuming the pending request.
    packet = ClientPacket(CMSG_MOVE_FORCE_RUN_SPEED_CHANGE_ACK, ack);
    session->HandleForceRunSpeedChangeAck(packet);
    EXPECT_TRUE(player->m_pendingRunSpeedChange);
    EXPECT_TRUE(session->IsKicked());
    EXPECT_FLOAT_EQ(player->GetPositionX(), 0.0f);
}

using GroundMovementRoutingTest = IntegrationTestFixture;

TEST_F(GroundMovementRoutingTest, RejectsGroundMovementFromASpoofedMoverGuid)
{
    // Full relocation acceptance (grid-crossing, anti-cheat) needs a live map and is covered by
    // the real-client acceptance pass; this exercises HandleMovementOpcodes' own guid gate, which
    // must reject a packet before it reaches ProcessMovementInfo/relocation at all.
    TestPlayer* player = CreateTestPlayer(0x01020304);
    WorldSession* session = player->GetSession();

    WorldPacket spoofed = ClientPacket(MSG_MOVE_START_BACKWARD, "000080BF00004040000000402AD0C01100");
    session->HandleMovementOpcodes(spoofed);
    EXPECT_FLOAT_EQ(player->GetPositionX(), 0.0f);
    EXPECT_FLOAT_EQ(player->GetPositionY(), 0.0f);
    EXPECT_FLOAT_EQ(player->GetPositionZ(), 0.0f);
    EXPECT_FALSE(session->IsKicked());
}
