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

struct MovementInfo;

namespace WorldPackets
{
    namespace Movement
    {
        // Generic bit-packed field tokens shared by every MSG_MOVE_* ground-movement opcode.
        // Each opcode transmits the same fields in its own Blizzard-obfuscated order; see the
        // per-opcode sequences in MovementPackets.cpp and GetGroundMovementSequence().
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
            MSEEnd,
        };

        uint32 MovementFlagsToClient(uint32 flags);
        uint16 ExtraMovementFlagsToClient(uint16 flags);
        void ReadHeartbeat(WorldPacket& packet, MovementInfo& info);
        void WriteMovementUpdate(WorldPacket& packet, MovementInfo const& info);
        MovementStatusElements const* GetGroundMovementSequence(uint16 opcode);
        void ReadGroundMovement(WorldPacket& packet, MovementInfo& info, MovementStatusElements const* sequence);
        void WriteGroundMovement(WorldPacket& packet, MovementInfo const& info, MovementStatusElements const* sequence);
        void WriteRunSpeedChange(WorldPacket& packet, ObjectGuid const& guid, uint32 counter, float speed);
        void ReadRunSpeedChangeAck(WorldPacket& packet, MovementInfo& info, uint32& counter, float& speed);
        void WriteRunSpeedUpdate(WorldPacket& packet, MovementInfo const& info, float speed);

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
