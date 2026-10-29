/*
 * This file is part of the AzerothCore Project. See AUTHORS file for Copyright information
 *
 * You may redistribute it and/or modify it under the terms of the GNU General Public License
 * version 2 or, at your option, any later version.
 */

#ifndef MovementPackets_h__
#define MovementPackets_h__

#include "ObjectGuid.h"
#include "Packet.h"
#include "Position.h"
#include <optional>

struct MovementInfo;
struct Position;

namespace WorldPackets
{
    namespace Movement
    {
        // Generic bit-packed field tokens shared by every MSG_MOVE_* ground-movement opcode.
        // Each opcode transmits the same fields in its own Blizzard-obfuscated order; see the
        // per-opcode sequences in MovementPackets.cpp and GetMovementSequence().
        enum MovementStatusElements : uint8
        {
            MSEHasGuidByte0, MSEHasGuidByte1, MSEHasGuidByte2, MSEHasGuidByte3,
            MSEHasGuidByte4, MSEHasGuidByte5, MSEHasGuidByte6, MSEHasGuidByte7,
            MSEGuidByte0, MSEGuidByte1, MSEGuidByte2, MSEGuidByte3,
            MSEGuidByte4, MSEGuidByte5, MSEGuidByte6, MSEGuidByte7,
            MSEHasTransportGuidByte0, MSEHasTransportGuidByte1, MSEHasTransportGuidByte2, MSEHasTransportGuidByte3,
            MSEHasTransportGuidByte4, MSEHasTransportGuidByte5, MSEHasTransportGuidByte6, MSEHasTransportGuidByte7,
            MSETransportGuidByte0, MSETransportGuidByte1, MSETransportGuidByte2, MSETransportGuidByte3,
            MSETransportGuidByte4, MSETransportGuidByte5, MSETransportGuidByte6, MSETransportGuidByte7,
            MSEHasTransportData,
            MSEHasVehicleId,
            MSEHasTransportTime2,
            MSEHasMovementFlags,
            MSEHasMovementFlags2,
            MSEHasTimestamp,
            MSEHasOrientation,
            MSEHasPitch,
            MSEHasSplineElevation,
            MSEHasSpline,
            MSEHasFallData,
            MSEHasFallDirection,
            MSEMovementFlags,
            MSEMovementFlags2,
            MSEZeroBit,
            MSEPositionX,
            MSEPositionY,
            MSEPositionZ,
            MSEOrientation,
            MSETransportPositionX,
            MSETransportPositionY,
            MSETransportPositionZ,
            MSETransportOrientation,
            MSETransportSeat,
            MSETransportTime,
            MSETransportTime2,
            MSETransportVehicleId,
            MSEPitch,
            MSEFallTime,
            MSEFallVerticalSpeed,
            MSEFallCosAngle,
            MSEFallSinAngle,
            MSEFallHorizontalSpeed,
            MSESplineElevation,
            MSETimestamp,
            MSECounter,
            MSEEnd,
        };

        uint32 MovementFlagsToClient(uint32 flags);
        uint16 ExtraMovementFlagsToClient(uint16 flags);
        void ReadHeartbeat(WorldPacket& packet, MovementInfo& info);
        void WriteMovementUpdate(WorldPacket& packet, MovementInfo const& info);
        MovementStatusElements const* GetMovementSequence(uint16 opcode);
        void ReadGroundMovement(WorldPacket& packet, MovementInfo& info, MovementStatusElements const* sequence,
            uint32* counter = nullptr);
        void WriteGroundMovement(WorldPacket& packet, MovementInfo const& info, MovementStatusElements const* sequence,
            uint32 counter = 0);
        MovementStatusElements const* GetMovementFlagSequence(uint16 opcode);
        void WriteTeleport(WorldPacket& packet, ObjectGuid guid, uint32 counter, Position const& pos,
            ObjectGuid transport = ObjectGuid::Empty);
        void ReadTeleportAck(WorldPacket& packet, ObjectGuid& guid, uint32& counter, uint32& time);
        void ReadTimeSkipped(WorldPacket& packet, ObjectGuid& guid, uint32& time);
        void WriteTimeSkipped(WorldPacket& packet, ObjectGuid const& guid, uint32 time);
        void WriteTeleportUpdate(WorldPacket& packet, MovementInfo const& info);

        void WriteRunSpeedChange(WorldPacket& packet, ObjectGuid const& guid, uint32 counter, float speed);
        void ReadRunSpeedChangeAck(WorldPacket& packet, MovementInfo& info, uint32& counter, float& speed);
        void WriteRunSpeedUpdate(WorldPacket& packet, MovementInfo const& info, float speed);

        struct ShipTransferPending
        {
            int32 ID = -1;
            int32 OriginMapID = -1;
        };

        /// Sent when a far teleport starts; the client answers CMSG_SUSPEND_TOKEN_RESPONSE and the
        /// server then sends NewWorld (4.3.4 flow; the 3.3.5a TransferPending + NewWorld sequence
        /// hangs the client, #327).
        class TransferPending final : public ServerPacket
        {
        public:
            TransferPending() : ServerPacket(SMSG_TRANSFER_PENDING, 12) { }

            WorldPacket const* Write() override;

            int32 MapID = -1;
            std::optional<ShipTransferPending> Ship;
            std::optional<int32> TransferSpellID;
        };

        class SuspendToken final : public ServerPacket
        {
        public:
            SuspendToken() : ServerPacket(SMSG_SUSPEND_TOKEN, 4 + 1) { }

            WorldPacket const* Write() override;

            uint32 SequenceIndex = 1;
            uint8 Reason = 1;
        };

        class NewWorld final : public ServerPacket
        {
        public:
            NewWorld() : ServerPacket(SMSG_NEW_WORLD, 4 + 4 + 4 + 4 + 4) { }

            WorldPacket const* Write() override;

            int32 MapID = 0;
            Position Pos;
        };

        class MoveSetActiveMover final : public ServerPacket
        {
        public:
            MoveSetActiveMover(ObjectGuid moverGuid) : ServerPacket(SMSG_MOVE_SET_ACTIVE_MOVER, 8), MoverGUID(moverGuid) { }

            WorldPacket const* Write() override;

            ObjectGuid MoverGUID;
        };
    }
}

#endif // MovementPackets_h__
