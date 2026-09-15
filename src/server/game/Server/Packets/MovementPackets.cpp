/*
 * This file is part of the AzerothCore Project. See AUTHORS file for Copyright information
 *
 * You may redistribute it and/or modify it under the terms of the GNU General Public License
 * version 2 or, at your option, any later version.
 */

#include "MovementPackets.h"
#include "Object.h"
#include <G3D/Vector3.h>

uint32 WorldPackets::Movement::MovementFlagsToClient(uint32 flags)
{
    // AC keeps its internal flag positions. Cata moved transport and spline presence out of this field.
    return (flags & 0x000001FF) | ((flags & 0x07FFFC00) >> 1) | ((flags & 0xF0000000) >> 2);
}

uint16 WorldPackets::Movement::ExtraMovementFlagsToClient(uint16 flags)
{
    return (flags & 0x03C3) | ((flags & 0x0038) >> 1) | ((flags & 0x0004) << 3) |
        ((flags & 0x1C00) << 3) | ((flags & 0xE000) >> 3);
}

void WorldPackets::Movement::ReadHeartbeat(WorldPacket& packet, MovementInfo& info)
{
    info = MovementInfo();
    packet >> info.pos.m_positionZ >> info.pos.m_positionX >> info.pos.m_positionY;
    bool hasPitch = !packet.ReadBit();
    bool hasTimestamp = !packet.ReadBit();
    bool hasFallData = packet.ReadBit();
    bool hasFlags2 = !packet.ReadBit();
    bool hasTransport = packet.ReadBit();
    for (uint8 index : { 7, 1, 0, 4, 2 })
        info.guid[index] = packet.ReadBit();
    bool hasOrientation = !packet.ReadBit();
    info.guid[5] = packet.ReadBit();
    info.guid[3] = packet.ReadBit();
    bool hasSplineElevation = !packet.ReadBit();
    bool hasSpline = packet.ReadBit();
    packet.ReadBit();
    info.guid[6] = packet.ReadBit();
    bool hasFlags = !packet.ReadBit();
    bool hasVehicleId = false;
    bool hasTransportTime2 = false;
    if (hasTransport)
    {
        hasVehicleId = packet.ReadBit();
        info.transport.guid[4] = packet.ReadBit();
        info.transport.guid[2] = packet.ReadBit();
        hasTransportTime2 = packet.ReadBit();
        for (uint8 index : { 5, 7, 6, 0, 3, 1 })
            info.transport.guid[index] = packet.ReadBit();
    }
    bool hasFallDirection = hasFallData && packet.ReadBit();
    if (hasFlags)
    {
        uint32 flags = packet.ReadBits(30);
        info.flags = (flags & 0x000001FF) | ((flags & 0x03FFFE00) << 1) | ((flags & 0x3C000000) << 2);
    }
    if (hasFlags2)
    {
        uint16 flags = packet.ReadBits(12);
        info.flags2 = (flags & 0x03C3) | ((flags & 0x001C) << 1) | ((flags & 0x0020) >> 3) |
            ((flags & 0xE000) >> 3) | ((flags & 0x1C00) << 3);
    }
    if (hasTransport)
        info.AddMovementFlag(MOVEMENTFLAG_ONTRANSPORT);
    if (hasSpline)
        info.AddMovementFlag(MOVEMENTFLAG_SPLINE_ENABLED);
    if (hasTransportTime2)
        info.AddExtraMovementFlag(MOVEMENTFLAG2_INTERPOLATED_MOVEMENT);
    for (uint8 index : { 3, 6, 1, 7, 2, 5, 0, 4 })
        packet.ReadByteSeq(info.guid[index]);
    if (hasTransport)
    {
        packet >> info.transport.pos.m_positionZ >> info.transport.seat;
        info.transport.pos.SetOrientation(packet.read<float>());
        packet.ReadByteSeq(info.transport.guid[4]);
        packet >> info.transport.pos.m_positionY >> info.transport.time >> info.transport.pos.m_positionX;
        for (uint8 index : { 5, 1, 3, 7 })
            packet.ReadByteSeq(info.transport.guid[index]);
        if (hasVehicleId)
            packet >> info.transport.vehicleId;
        if (hasTransportTime2)
            packet >> info.transport.time2;
        for (uint8 index : { 2, 0, 6 })
            packet.ReadByteSeq(info.transport.guid[index]);
    }
    if (hasOrientation)
        info.pos.SetOrientation(packet.read<float>());
    if (hasFallData)
    {
        packet >> info.jump.zspeed >> info.fallTime;
        if (hasFallDirection)
            packet >> info.jump.xyspeed >> info.jump.cosAngle >> info.jump.sinAngle;
    }
    if (hasPitch)
        info.pitch = G3D::wrap(packet.read<float>(), float(-M_PI), float(M_PI));
    if (hasSplineElevation)
        packet >> info.splineElevation;
    if (hasTimestamp)
        packet >> info.time;
    if (packet.rpos() != packet.size())
        throw ByteBufferInvalidValueException("heartbeat payload", "trailing bytes");
}

void WorldPackets::Movement::WriteMovementUpdate(WorldPacket& packet, MovementInfo const& info)
{
    uint32 flags = MovementFlagsToClient(info.flags);
    uint16 flags2 = ExtraMovementFlagsToClient(info.flags2) & 0x0FFF;
    bool hasFallDirection = info.HasMovementFlag(MOVEMENTFLAG_FALLING);
    bool hasFallData = hasFallDirection || info.fallTime != 0;
    bool hasOrientation = !G3D::fuzzyEq(info.pos.GetOrientation(), 0.0f);
    bool hasTransport = !info.transport.guid.IsEmpty();
    bool hasVehicleId = hasTransport && info.transport.vehicleId != 0;
    bool hasTransportTime2 = hasTransport && info.transport.time2 != 0;
    bool hasPitch = info.HasMovementFlag(MOVEMENTFLAG_SWIMMING | MOVEMENTFLAG_FLYING) ||
        info.HasExtraMovementFlag(MOVEMENTFLAG2_ALWAYS_ALLOW_PITCHING);
    bool hasSplineElevation = info.HasMovementFlag(MOVEMENTFLAG_SPLINE_ELEVATION);
    ObjectGuid const& guid = info.guid;
    ObjectGuid const& transport = info.transport.guid;

    packet.WriteBit(hasFallData);
    packet.WriteBit(guid[3]);
    packet.WriteBit(guid[6]);
    packet.WriteBit(!flags2);
    packet.WriteBit(info.HasMovementFlag(MOVEMENTFLAG_SPLINE_ENABLED));
    packet.WriteBit(false); // Timestamp is always present in server movement updates.
    packet.WriteBit(guid[0]);
    packet.WriteBit(guid[1]);
    if (flags2)
        packet.WriteBits(flags2, 12);
    packet.WriteBit(guid[7]);
    packet.WriteBit(!flags);
    packet.WriteBit(!hasOrientation);
    packet.WriteBit(guid[2]);
    packet.WriteBit(!hasSplineElevation);
    packet.WriteBit(false); // Height change failed.
    packet.WriteBit(guid[4]);
    if (hasFallData)
        packet.WriteBit(hasFallDirection);
    packet.WriteBit(guid[5]);
    packet.WriteBit(hasTransport);
    if (flags)
        packet.WriteBits(flags, 30);
    if (hasTransport)
    {
        packet.WriteBit(transport[3]);
        packet.WriteBit(hasVehicleId);
        for (uint8 index : { 6, 1, 7, 0, 4 })
            packet.WriteBit(transport[index]);
        packet.WriteBit(hasTransportTime2);
        packet.WriteBit(transport[5]);
        packet.WriteBit(transport[2]);
    }
    packet.WriteBit(!hasPitch);
    packet.FlushBits();
    packet.WriteByteSeq(guid[5]);
    if (hasFallData)
    {
        if (hasFallDirection)
            packet << info.jump.xyspeed << info.jump.sinAngle << info.jump.cosAngle;
        packet << info.jump.zspeed << info.fallTime;
    }
    if (hasSplineElevation)
        packet << info.splineElevation;
    packet.WriteByteSeq(guid[7]);
    packet << info.pos.GetPositionY();
    packet.WriteByteSeq(guid[3]);
    if (hasTransport)
    {
        if (hasVehicleId)
            packet << info.transport.vehicleId;
        packet.WriteByteSeq(transport[6]);
        packet << info.transport.seat;
        packet.WriteByteSeq(transport[5]);
        packet << info.transport.pos.GetPositionX();
        packet.WriteByteSeq(transport[1]);
        packet << info.transport.pos.GetOrientation();
        packet.WriteByteSeq(transport[2]);
        if (hasTransportTime2)
            packet << info.transport.time2;
        packet.WriteByteSeq(transport[0]);
        packet << info.transport.pos.GetPositionZ();
        for (uint8 index : { 7, 4, 3 })
            packet.WriteByteSeq(transport[index]);
        packet << info.transport.pos.GetPositionY() << info.transport.time;
    }
    packet.WriteByteSeq(guid[4]);
    packet << info.pos.GetPositionX();
    packet.WriteByteSeq(guid[6]);
    packet << info.pos.GetPositionZ() << info.time;
    packet.WriteByteSeq(guid[2]);
    if (hasPitch)
        packet << info.pitch;
    packet.WriteByteSeq(guid[0]);
    if (hasOrientation)
        packet << info.pos.GetOrientation();
    packet.WriteByteSeq(guid[1]);
}

void WorldPackets::Movement::WriteRunSpeedChange(WorldPacket& packet, ObjectGuid const& guid, uint32 counter, float speed)
{
    for (uint8 index : { 6, 1, 5, 2, 7, 0, 3, 4 })
        packet.WriteBit(guid[index]);
    packet.FlushBits();
    for (uint8 index : { 5, 3, 1, 4 })
        packet.WriteByteSeq(guid[index]);
    packet << counter << speed;
    for (uint8 index : { 6, 0, 7, 2 })
        packet.WriteByteSeq(guid[index]);
}

void WorldPackets::Movement::ReadRunSpeedChangeAck(
    WorldPacket& packet, MovementInfo& info, uint32& counter, float& speed)
{
    info = MovementInfo();
    packet >> counter >> info.pos.m_positionX >> speed >> info.pos.m_positionZ >> info.pos.m_positionY;
    for (uint8 index : { 2, 4, 1, 7 })
        info.guid[index] = packet.ReadBit();
    bool hasOrientation = !packet.ReadBit();
    bool hasFallData = packet.ReadBit();
    info.guid[0] = packet.ReadBit();
    bool hasSpline = packet.ReadBit();
    bool hasTransport = packet.ReadBit();
    bool hasTimestamp = !packet.ReadBit();
    bool hasFlags2 = !packet.ReadBit();
    info.guid[6] = packet.ReadBit();
    packet.ReadBit();
    bool hasSplineElevation = !packet.ReadBit();
    bool hasPitch = !packet.ReadBit();
    info.guid[5] = packet.ReadBit();
    bool hasFlags = !packet.ReadBit();
    info.guid[3] = packet.ReadBit();
    bool hasVehicleId = false;
    bool hasTransportTime2 = false;
    if (hasTransport)
    {
        hasVehicleId = packet.ReadBit();
        info.transport.guid[5] = packet.ReadBit();
        hasTransportTime2 = packet.ReadBit();
        for (uint8 index : { 3, 2, 0, 7, 6, 1, 4 })
            info.transport.guid[index] = packet.ReadBit();
    }
    if (hasFlags)
    {
        uint32 flags = packet.ReadBits(30);
        info.flags = (flags & 0x000001FF) | ((flags & 0x03FFFE00) << 1) | ((flags & 0x3C000000) << 2);
    }
    bool hasFallDirection = hasFallData && packet.ReadBit();
    if (hasFlags2)
    {
        uint16 flags = packet.ReadBits(12);
        info.flags2 = (flags & 0x03C3) | ((flags & 0x001C) << 1) | ((flags & 0x0020) >> 3) |
            ((flags & 0xE000) >> 3) | ((flags & 0x1C00) << 3);
    }
    if (hasTransport)
        info.AddMovementFlag(MOVEMENTFLAG_ONTRANSPORT);
    if (hasSpline)
        info.AddMovementFlag(MOVEMENTFLAG_SPLINE_ENABLED);
    if (hasTransportTime2)
        info.AddExtraMovementFlag(MOVEMENTFLAG2_INTERPOLATED_MOVEMENT);
    for (uint8 index : { 6, 4, 1, 3, 5, 2, 7, 0 })
        packet.ReadByteSeq(info.guid[index]);
    if (hasTransport)
    {
        packet >> info.transport.pos.m_positionZ;
        packet.ReadByteSeq(info.transport.guid[6]);
        packet.ReadByteSeq(info.transport.guid[1]);
        packet >> info.transport.pos.m_positionY;
        packet.ReadByteSeq(info.transport.guid[0]);
        packet.ReadByteSeq(info.transport.guid[5]);
        if (hasTransportTime2)
            packet >> info.transport.time2;
        packet >> info.transport.pos.m_positionX >> info.transport.time;
        packet.ReadByteSeq(info.transport.guid[7]);
        info.transport.pos.SetOrientation(packet.read<float>());
        packet.ReadByteSeq(info.transport.guid[3]);
        if (hasVehicleId)
            packet >> info.transport.vehicleId;
        packet.ReadByteSeq(info.transport.guid[2]);
        packet >> info.transport.seat;
        packet.ReadByteSeq(info.transport.guid[4]);
    }
    if (hasFallData)
    {
        packet >> info.jump.zspeed;
        if (hasFallDirection)
            packet >> info.jump.xyspeed >> info.jump.sinAngle >> info.jump.cosAngle;
        packet >> info.fallTime;
    }
    if (hasSplineElevation)
        packet >> info.splineElevation;
    if (hasPitch)
        info.pitch = G3D::wrap(packet.read<float>(), float(-M_PI), float(M_PI));
    if (hasTimestamp)
        packet >> info.time;
    if (hasOrientation)
        info.pos.SetOrientation(packet.read<float>());
    if (packet.rpos() != packet.size())
        throw ByteBufferInvalidValueException("run-speed acknowledgement", "trailing bytes");
}

void WorldPackets::Movement::WriteRunSpeedUpdate(WorldPacket& packet, MovementInfo const& info, float speed)
{
    uint32 flags = MovementFlagsToClient(info.flags);
    uint16 flags2 = ExtraMovementFlagsToClient(info.flags2) & 0x0FFF;
    bool hasFallDirection = info.HasMovementFlag(MOVEMENTFLAG_FALLING);
    bool hasFallData = hasFallDirection || info.fallTime != 0;
    bool hasOrientation = !G3D::fuzzyEq(info.pos.GetOrientation(), 0.0f);
    bool hasTransport = !info.transport.guid.IsEmpty();
    bool hasVehicleId = hasTransport && info.transport.vehicleId != 0;
    bool hasTransportTime2 = hasTransport && info.transport.time2 != 0;
    bool hasPitch = info.HasMovementFlag(MOVEMENTFLAG_SWIMMING | MOVEMENTFLAG_FLYING) ||
        info.HasExtraMovementFlag(MOVEMENTFLAG2_ALWAYS_ALLOW_PITCHING);
    bool hasSplineElevation = info.HasMovementFlag(MOVEMENTFLAG_SPLINE_ELEVATION);
    ObjectGuid const& guid = info.guid;
    ObjectGuid const& transport = info.transport.guid;

    packet << info.pos.GetPositionZ() << info.pos.GetPositionX() << info.pos.GetPositionY() << speed;
    packet.WriteBit(guid[6]);
    packet.WriteBit(!flags2);
    packet.WriteBit(!hasPitch);
    packet.WriteBit(guid[2]);
    packet.WriteBit(guid[5]);
    packet.WriteBit(!hasSplineElevation);
    packet.WriteBit(info.HasMovementFlag(MOVEMENTFLAG_SPLINE_ENABLED));
    packet.WriteBit(!flags);
    packet.WriteBit(false); // Timestamp is always present in server movement updates.
    packet.WriteBit(guid[1]);
    if (flags2)
        packet.WriteBits(flags2, 12);
    packet.WriteBit(guid[3]);
    if (flags)
        packet.WriteBits(flags, 30);
    packet.WriteBit(guid[7]);
    packet.WriteBit(guid[0]);
    packet.WriteBit(!hasOrientation);
    packet.WriteBit(hasTransport);
    if (hasTransport)
    {
        packet.WriteBit(transport[5]);
        packet.WriteBit(hasTransportTime2);
        packet.WriteBit(hasVehicleId);
        for (uint8 index : { 7, 4, 2, 3, 6, 1, 0 })
            packet.WriteBit(transport[index]);
    }
    packet.WriteBit(hasFallData);
    if (hasFallData)
        packet.WriteBit(hasFallDirection);
    packet.WriteBit(guid[4]);
    packet.WriteBit(false); // Height change failed.
    packet.FlushBits();
    if (hasTransport)
    {
        packet.WriteByteSeq(transport[4]);
        packet.WriteByteSeq(transport[5]);
        packet << info.transport.pos.GetPositionX() << info.transport.pos.GetOrientation();
        for (uint8 index : { 1, 0, 6 })
            packet.WriteByteSeq(transport[index]);
        packet << info.transport.time;
        packet.WriteByteSeq(transport[7]);
        packet << info.transport.seat;
        if (hasTransportTime2)
            packet << info.transport.time2;
        packet << info.transport.pos.GetPositionY();
        packet.WriteByteSeq(transport[3]);
        packet.WriteByteSeq(transport[2]);
        if (hasVehicleId)
            packet << info.transport.vehicleId;
        packet << info.transport.pos.GetPositionZ();
    }
    packet << info.time;
    if (hasFallData)
    {
        if (hasFallDirection)
            packet << info.jump.cosAngle << info.jump.xyspeed << info.jump.sinAngle;
        packet << info.jump.zspeed << info.fallTime;
    }
    if (hasPitch)
        packet << info.pitch;
    packet.WriteByteSeq(guid[6]);
    if (hasSplineElevation)
        packet << info.splineElevation;
    for (uint8 index : { 5, 7, 4 })
        packet.WriteByteSeq(guid[index]);
    if (hasOrientation)
        packet << info.pos.GetOrientation();
    for (uint8 index : { 0, 3, 2, 1 })
        packet.WriteByteSeq(guid[index]);
}

WorldPacket const* WorldPackets::Movement::MoveSetActiveMover::Write()
{
    _worldPacket.WriteBit(MoverGUID[5]);
    _worldPacket.WriteBit(MoverGUID[7]);
    _worldPacket.WriteBit(MoverGUID[3]);
    _worldPacket.WriteBit(MoverGUID[6]);
    _worldPacket.WriteBit(MoverGUID[0]);
    _worldPacket.WriteBit(MoverGUID[4]);
    _worldPacket.WriteBit(MoverGUID[1]);
    _worldPacket.WriteBit(MoverGUID[2]);

    _worldPacket.WriteByteSeq(MoverGUID[6]);
    _worldPacket.WriteByteSeq(MoverGUID[2]);
    _worldPacket.WriteByteSeq(MoverGUID[3]);
    _worldPacket.WriteByteSeq(MoverGUID[0]);
    _worldPacket.WriteByteSeq(MoverGUID[5]);
    _worldPacket.WriteByteSeq(MoverGUID[7]);
    _worldPacket.WriteByteSeq(MoverGUID[1]);
    _worldPacket.WriteByteSeq(MoverGUID[4]);

    return &_worldPacket;
}

using namespace WorldPackets::Movement;

static MovementStatusElements const MovementStartBackward[] =
{
    MSEPositionX,
    MSEPositionZ,
    MSEPositionY,
    MSEHasTransportData,
    MSEHasGuidByte3,
    MSEHasGuidByte0,
    MSEHasGuidByte2,
    MSEHasTimestamp,
    MSEHasGuidByte7,
    MSEHasPitch,
    MSEZeroBit,
    MSEHasMovementFlags,
    MSEHasOrientation,
    MSEHasSpline,
    MSEHasMovementFlags2,
    MSEHasFallData,
    MSEHasGuidByte5,
    MSEHasGuidByte1,
    MSEHasGuidByte4,
    MSEHasGuidByte6,
    MSEHasSplineElevation,
    MSEHasTransportGuidByte3,
    MSEHasTransportGuidByte1,
    MSEHasTransportTime2,
    MSEHasTransportGuidByte2,
    MSEHasTransportGuidByte7,
    MSEHasTransportGuidByte0,
    MSEHasTransportGuidByte6,
    MSEHasTransportGuidByte5,
    MSEHasTransportGuidByte4,
    MSEHasVehicleId,
    MSEMovementFlags,
    MSEMovementFlags2,
    MSEHasFallDirection,
    MSEGuidByte6,
    MSEGuidByte7,
    MSEGuidByte4,
    MSEGuidByte1,
    MSEGuidByte5,
    MSEGuidByte0,
    MSEGuidByte2,
    MSEGuidByte3,
    MSETransportPositionZ,
    MSETransportGuidByte2,
    MSETransportVehicleId,
    MSETransportGuidByte0,
    MSETransportGuidByte5,
    MSETransportPositionY,
    MSETransportGuidByte1,
    MSETransportPositionX,
    MSETransportTime2,
    MSETransportGuidByte4,
    MSETransportOrientation,
    MSETransportSeat,
    MSETransportGuidByte7,
    MSETransportTime,
    MSETransportGuidByte6,
    MSETransportGuidByte3,
    MSEPitch,
    MSETimestamp,
    MSEFallHorizontalSpeed,
    MSEFallCosAngle,
    MSEFallSinAngle,
    MSEFallVerticalSpeed,
    MSEFallTime,
    MSEOrientation,
    MSESplineElevation,
    MSEEnd,
};

static MovementStatusElements const MovementStartForward[] =
{
    MSEPositionY,
    MSEPositionZ,
    MSEPositionX,
    MSEHasGuidByte5,
    MSEHasGuidByte2,
    MSEHasGuidByte0,
    MSEZeroBit,
    MSEHasMovementFlags,
    MSEHasGuidByte7,
    MSEHasGuidByte3,
    MSEHasGuidByte1,
    MSEHasOrientation,
    MSEHasGuidByte6,
    MSEHasSpline,
    MSEHasSplineElevation,
    MSEHasGuidByte4,
    MSEHasTransportData,
    MSEHasTimestamp,
    MSEHasPitch,
    MSEHasMovementFlags2,
    MSEHasFallData,
    MSEMovementFlags,
    MSEHasTransportGuidByte3,
    MSEHasTransportGuidByte4,
    MSEHasTransportGuidByte6,
    MSEHasTransportGuidByte2,
    MSEHasTransportGuidByte5,
    MSEHasTransportGuidByte0,
    MSEHasTransportGuidByte7,
    MSEHasTransportGuidByte1,
    MSEHasVehicleId,
    MSEHasTransportTime2,
    MSEHasFallDirection,
    MSEMovementFlags2,
    MSEGuidByte2,
    MSEGuidByte4,
    MSEGuidByte6,
    MSEGuidByte1,
    MSEGuidByte7,
    MSEGuidByte3,
    MSEGuidByte5,
    MSEGuidByte0,
    MSEFallVerticalSpeed,
    MSEFallHorizontalSpeed,
    MSEFallSinAngle,
    MSEFallCosAngle,
    MSEFallTime,
    MSETransportGuidByte3,
    MSETransportPositionY,
    MSETransportPositionZ,
    MSETransportGuidByte1,
    MSETransportGuidByte4,
    MSETransportGuidByte7,
    MSETransportOrientation,
    MSETransportGuidByte2,
    MSETransportPositionX,
    MSETransportGuidByte5,
    MSETransportVehicleId,
    MSETransportTime,
    MSETransportGuidByte6,
    MSETransportGuidByte0,
    MSETransportSeat,
    MSETransportTime2,
    MSESplineElevation,
    MSEPitch,
    MSEOrientation,
    MSETimestamp,
    MSEEnd,
};

static MovementStatusElements const MovementStartStrafeLeft[] =
{
    MSEPositionZ,
    MSEPositionX,
    MSEPositionY,
    MSEHasSplineElevation,
    MSEHasGuidByte5,
    MSEHasPitch,
    MSEHasGuidByte6,
    MSEHasTimestamp,
    MSEHasGuidByte1,
    MSEZeroBit,
    MSEHasGuidByte4,
    MSEHasGuidByte0,
    MSEHasGuidByte2,
    MSEHasFallData,
    MSEHasOrientation,
    MSEHasGuidByte3,
    MSEHasMovementFlags2,
    MSEHasGuidByte7,
    MSEHasSpline,
    MSEHasMovementFlags,
    MSEHasTransportData,
    MSEHasFallDirection,
    MSEHasTransportTime2,
    MSEHasTransportGuidByte7,
    MSEHasVehicleId,
    MSEHasTransportGuidByte3,
    MSEHasTransportGuidByte1,
    MSEHasTransportGuidByte6,
    MSEHasTransportGuidByte5,
    MSEHasTransportGuidByte2,
    MSEHasTransportGuidByte4,
    MSEHasTransportGuidByte0,
    MSEMovementFlags,
    MSEMovementFlags2,
    MSEGuidByte2,
    MSEGuidByte6,
    MSEGuidByte3,
    MSEGuidByte1,
    MSEGuidByte0,
    MSEGuidByte7,
    MSEGuidByte4,
    MSEGuidByte5,
    MSEFallCosAngle,
    MSEFallHorizontalSpeed,
    MSEFallSinAngle,
    MSEFallTime,
    MSEFallVerticalSpeed,
    MSETransportSeat,
    MSETransportGuidByte2,
    MSETransportTime2,
    MSETransportGuidByte3,
    MSETransportPositionZ,
    MSETransportVehicleId,
    MSETransportGuidByte0,
    MSETransportGuidByte7,
    MSETransportPositionY,
    MSETransportGuidByte5,
    MSETransportGuidByte1,
    MSETransportOrientation,
    MSETransportTime,
    MSETransportGuidByte6,
    MSETransportGuidByte4,
    MSETransportPositionX,
    MSETimestamp,
    MSEOrientation,
    MSEPitch,
    MSESplineElevation,
    MSEEnd,
};

static MovementStatusElements const MovementStartStrafeRight[] =
{
    MSEPositionY,
    MSEPositionX,
    MSEPositionZ,
    MSEHasPitch,
    MSEHasGuidByte1,
    MSEHasOrientation,
    MSEHasGuidByte4,
    MSEHasSpline,
    MSEZeroBit,
    MSEHasGuidByte5,
    MSEHasFallData,
    MSEHasSplineElevation,
    MSEHasTimestamp,
    MSEHasMovementFlags,
    MSEHasGuidByte2,
    MSEHasGuidByte7,
    MSEHasGuidByte6,
    MSEHasGuidByte3,
    MSEHasMovementFlags2,
    MSEHasTransportData,
    MSEHasGuidByte0,
    MSEHasTransportGuidByte7,
    MSEHasTransportGuidByte2,
    MSEHasTransportGuidByte3,
    MSEHasTransportGuidByte5,
    MSEHasTransportGuidByte0,
    MSEHasTransportTime2,
    MSEHasTransportGuidByte6,
    MSEHasTransportGuidByte1,
    MSEHasTransportGuidByte4,
    MSEHasVehicleId,
    MSEMovementFlags2,
    MSEMovementFlags,
    MSEHasFallDirection,
    MSEGuidByte7,
    MSEGuidByte5,
    MSEGuidByte3,
    MSEGuidByte1,
    MSEGuidByte2,
    MSEGuidByte4,
    MSEGuidByte6,
    MSEGuidByte0,
    MSETransportGuidByte5,
    MSETransportGuidByte1,
    MSETransportGuidByte6,
    MSETransportPositionY,
    MSETransportOrientation,
    MSETransportGuidByte0,
    MSETransportGuidByte2,
    MSETransportSeat,
    MSETransportPositionX,
    MSETransportVehicleId,
    MSETransportTime,
    MSETransportGuidByte4,
    MSETransportGuidByte7,
    MSETransportTime2,
    MSETransportPositionZ,
    MSETransportGuidByte3,
    MSEPitch,
    MSEOrientation,
    MSEFallSinAngle,
    MSEFallCosAngle,
    MSEFallHorizontalSpeed,
    MSEFallTime,
    MSEFallVerticalSpeed,
    MSETimestamp,
    MSESplineElevation,
    MSEEnd,
};

static MovementStatusElements const MovementStartTurnLeft[] =
{
    MSEPositionY,
    MSEPositionX,
    MSEPositionZ,
    MSEZeroBit,
    MSEHasGuidByte1,
    MSEHasOrientation,
    MSEHasSpline,
    MSEHasMovementFlags,
    MSEHasGuidByte4,
    MSEHasGuidByte2,
    MSEHasMovementFlags2,
    MSEHasGuidByte5,
    MSEHasGuidByte7,
    MSEHasTransportData,
    MSEHasGuidByte6,
    MSEHasSplineElevation,
    MSEHasGuidByte0,
    MSEHasGuidByte3,
    MSEHasPitch,
    MSEHasTimestamp,
    MSEHasFallData,
    MSEMovementFlags2,
    MSEHasTransportGuidByte3,
    MSEHasTransportGuidByte1,
    MSEHasTransportGuidByte0,
    MSEHasTransportTime2,
    MSEHasTransportGuidByte2,
    MSEHasVehicleId,
    MSEHasTransportGuidByte4,
    MSEHasTransportGuidByte7,
    MSEHasTransportGuidByte5,
    MSEHasTransportGuidByte6,
    MSEHasFallDirection,
    MSEMovementFlags,
    MSEGuidByte0,
    MSEGuidByte4,
    MSEGuidByte7,
    MSEGuidByte5,
    MSEGuidByte6,
    MSEGuidByte3,
    MSEGuidByte2,
    MSEGuidByte1,
    MSEFallCosAngle,
    MSEFallSinAngle,
    MSEFallHorizontalSpeed,
    MSEFallVerticalSpeed,
    MSEFallTime,
    MSETransportGuidByte0,
    MSETransportPositionX,
    MSETransportTime,
    MSETransportSeat,
    MSETransportPositionZ,
    MSETransportGuidByte4,
    MSETransportOrientation,
    MSETransportGuidByte2,
    MSETransportGuidByte6,
    MSETransportGuidByte1,
    MSETransportGuidByte3,
    MSETransportPositionY,
    MSETransportVehicleId,
    MSETransportTime2,
    MSETransportGuidByte5,
    MSETransportGuidByte7,
    MSETimestamp,
    MSEPitch,
    MSEOrientation,
    MSESplineElevation,
    MSEEnd,
};

static MovementStatusElements const MovementStartTurnRight[] =
{
    MSEPositionX,
    MSEPositionZ,
    MSEPositionY,
    MSEHasGuidByte3,
    MSEHasGuidByte5,
    MSEHasMovementFlags,
    MSEHasSpline,
    MSEHasGuidByte0,
    MSEHasOrientation,
    MSEHasTransportData,
    MSEHasGuidByte7,
    MSEZeroBit,
    MSEHasMovementFlags2,
    MSEHasGuidByte1,
    MSEHasTimestamp,
    MSEHasGuidByte6,
    MSEHasGuidByte2,
    MSEHasGuidByte4,
    MSEHasSplineElevation,
    MSEHasPitch,
    MSEHasFallData,
    MSEHasTransportGuidByte1,
    MSEHasTransportTime2,
    MSEHasTransportGuidByte6,
    MSEHasTransportGuidByte0,
    MSEHasTransportGuidByte3,
    MSEHasTransportGuidByte5,
    MSEHasTransportGuidByte2,
    MSEHasTransportGuidByte4,
    MSEHasTransportGuidByte7,
    MSEHasVehicleId,
    MSEMovementFlags2,
    MSEMovementFlags,
    MSEHasFallDirection,
    MSEGuidByte5,
    MSEGuidByte0,
    MSEGuidByte7,
    MSEGuidByte3,
    MSEGuidByte2,
    MSEGuidByte1,
    MSEGuidByte4,
    MSEGuidByte6,
    MSETransportPositionY,
    MSETransportGuidByte0,
    MSETransportGuidByte4,
    MSETransportGuidByte1,
    MSETransportGuidByte6,
    MSETransportGuidByte2,
    MSETransportSeat,
    MSETransportOrientation,
    MSETransportGuidByte5,
    MSETransportVehicleId,
    MSETransportPositionZ,
    MSETransportPositionX,
    MSETransportTime,
    MSETransportGuidByte7,
    MSETransportGuidByte3,
    MSETransportTime2,
    MSEFallHorizontalSpeed,
    MSEFallCosAngle,
    MSEFallSinAngle,
    MSEFallTime,
    MSEFallVerticalSpeed,
    MSEPitch,
    MSEOrientation,
    MSESplineElevation,
    MSETimestamp,
    MSEEnd,
};

static MovementStatusElements const MovementStop[] =
{
    MSEPositionX,
    MSEPositionY,
    MSEPositionZ,
    MSEHasGuidByte3,
    MSEHasGuidByte6,
    MSEHasSplineElevation,
    MSEHasSpline,
    MSEHasOrientation,
    MSEHasGuidByte7,
    MSEHasMovementFlags,
    MSEHasGuidByte5,
    MSEHasFallData,
    MSEHasMovementFlags2,
    MSEHasTransportData,
    MSEHasTimestamp,
    MSEHasGuidByte4,
    MSEHasGuidByte1,
    MSEZeroBit,
    MSEHasGuidByte2,
    MSEHasGuidByte0,
    MSEHasPitch,
    MSEHasTransportGuidByte7,
    MSEHasTransportGuidByte4,
    MSEHasTransportGuidByte1,
    MSEHasTransportGuidByte5,
    MSEHasTransportTime2,
    MSEHasVehicleId,
    MSEHasTransportGuidByte3,
    MSEHasTransportGuidByte6,
    MSEHasTransportGuidByte0,
    MSEHasTransportGuidByte2,
    MSEMovementFlags,
    MSEMovementFlags2,
    MSEHasFallDirection,
    MSEGuidByte6,
    MSEGuidByte3,
    MSEGuidByte0,
    MSEGuidByte4,
    MSEGuidByte2,
    MSEGuidByte1,
    MSEGuidByte5,
    MSEGuidByte7,
    MSETransportGuidByte4,
    MSETransportGuidByte7,
    MSETransportTime,
    MSETransportSeat,
    MSETransportPositionZ,
    MSETransportVehicleId,
    MSETransportGuidByte2,
    MSETransportGuidByte0,
    MSETransportPositionY,
    MSETransportGuidByte1,
    MSETransportGuidByte3,
    MSETransportTime2,
    MSETransportPositionX,
    MSETransportOrientation,
    MSETransportGuidByte5,
    MSETransportGuidByte6,
    MSETimestamp,
    MSEOrientation,
    MSEPitch,
    MSESplineElevation,
    MSEFallSinAngle,
    MSEFallCosAngle,
    MSEFallHorizontalSpeed,
    MSEFallVerticalSpeed,
    MSEFallTime,
    MSEEnd,
};

static MovementStatusElements const MovementStopStrafe[] =
{
    MSEPositionY,
    MSEPositionZ,
    MSEPositionX,
    MSEHasPitch,
    MSEHasTimestamp,
    MSEHasGuidByte2,
    MSEHasFallData,
    MSEHasGuidByte7,
    MSEHasSplineElevation,
    MSEHasGuidByte3,
    MSEHasOrientation,
    MSEHasMovementFlags2,
    MSEHasTransportData,
    MSEHasMovementFlags,
    MSEHasSpline,
    MSEHasGuidByte0,
    MSEZeroBit,
    MSEHasGuidByte6,
    MSEHasGuidByte5,
    MSEHasGuidByte1,
    MSEHasGuidByte4,
    MSEHasTransportGuidByte7,
    MSEHasTransportGuidByte0,
    MSEHasTransportGuidByte4,
    MSEHasTransportGuidByte2,
    MSEHasTransportGuidByte6,
    MSEHasTransportGuidByte5,
    MSEHasTransportTime2,
    MSEHasTransportGuidByte1,
    MSEHasTransportGuidByte3,
    MSEHasVehicleId,
    MSEMovementFlags,
    MSEHasFallDirection,
    MSEMovementFlags2,
    MSEGuidByte2,
    MSEGuidByte7,
    MSEGuidByte3,
    MSEGuidByte4,
    MSEGuidByte5,
    MSEGuidByte6,
    MSEGuidByte1,
    MSEGuidByte0,
    MSETransportSeat,
    MSETransportGuidByte6,
    MSETransportPositionZ,
    MSETransportVehicleId,
    MSETransportGuidByte1,
    MSETransportGuidByte3,
    MSETransportGuidByte2,
    MSETransportGuidByte4,
    MSETransportGuidByte5,
    MSETransportTime,
    MSETransportOrientation,
    MSETransportPositionX,
    MSETransportGuidByte0,
    MSETransportPositionY,
    MSETransportTime2,
    MSETransportGuidByte7,
    MSEFallSinAngle,
    MSEFallHorizontalSpeed,
    MSEFallCosAngle,
    MSEFallTime,
    MSEFallVerticalSpeed,
    MSESplineElevation,
    MSEOrientation,
    MSEPitch,
    MSETimestamp,
    MSEEnd,
};

static MovementStatusElements const MovementStopTurn[] =
{
    MSEPositionX,
    MSEPositionZ,
    MSEPositionY,
    MSEHasGuidByte5,
    MSEHasGuidByte4,
    MSEHasFallData,
    MSEZeroBit,
    MSEHasGuidByte1,
    MSEHasGuidByte0,
    MSEHasSpline,
    MSEHasMovementFlags,
    MSEHasGuidByte2,
    MSEHasGuidByte6,
    MSEHasPitch,
    MSEHasTransportData,
    MSEHasGuidByte3,
    MSEHasSplineElevation,
    MSEHasTimestamp,
    MSEHasMovementFlags2,
    MSEHasOrientation,
    MSEHasGuidByte7,
    MSEHasTransportGuidByte2,
    MSEHasTransportGuidByte7,
    MSEHasVehicleId,
    MSEHasTransportGuidByte4,
    MSEHasTransportTime2,
    MSEHasTransportGuidByte5,
    MSEHasTransportGuidByte0,
    MSEHasTransportGuidByte3,
    MSEHasTransportGuidByte6,
    MSEHasTransportGuidByte1,
    MSEHasFallDirection,
    MSEMovementFlags,
    MSEMovementFlags2,
    MSEGuidByte3,
    MSEGuidByte2,
    MSEGuidByte6,
    MSEGuidByte4,
    MSEGuidByte0,
    MSEGuidByte7,
    MSEGuidByte1,
    MSEGuidByte5,
    MSESplineElevation,
    MSETransportPositionX,
    MSETransportGuidByte5,
    MSETransportSeat,
    MSETransportGuidByte2,
    MSETransportGuidByte3,
    MSETransportOrientation,
    MSETransportTime2,
    MSETransportVehicleId,
    MSETransportGuidByte7,
    MSETransportGuidByte1,
    MSETransportGuidByte0,
    MSETransportGuidByte4,
    MSETransportPositionY,
    MSETransportPositionZ,
    MSETransportTime,
    MSETransportGuidByte6,
    MSEFallTime,
    MSEFallHorizontalSpeed,
    MSEFallSinAngle,
    MSEFallCosAngle,
    MSEFallVerticalSpeed,
    MSETimestamp,
    MSEPitch,
    MSEOrientation,
    MSEEnd,
};

static MovementStatusElements const MovementSetRunMode[] =
{
    MSEPositionY,
    MSEPositionX,
    MSEPositionZ,
    MSEHasTimestamp,
    MSEHasMovementFlags2,
    MSEHasGuidByte1,
    MSEHasSpline,
    MSEHasMovementFlags,
    MSEHasGuidByte7,
    MSEHasTransportData,
    MSEZeroBit,
    MSEHasGuidByte0,
    MSEHasGuidByte3,
    MSEHasSplineElevation,
    MSEHasGuidByte5,
    MSEHasPitch,
    MSEHasGuidByte6,
    MSEHasGuidByte4,
    MSEHasFallData,
    MSEHasOrientation,
    MSEHasGuidByte2,
    MSEHasTransportGuidByte6,
    MSEHasTransportGuidByte4,
    MSEHasVehicleId,
    MSEHasTransportGuidByte5,
    MSEHasTransportGuidByte3,
    MSEHasTransportGuidByte1,
    MSEHasTransportTime2,
    MSEHasTransportGuidByte7,
    MSEHasTransportGuidByte0,
    MSEHasTransportGuidByte2,
    MSEHasFallDirection,
    MSEMovementFlags2,
    MSEMovementFlags,
    MSEGuidByte3,
    MSEGuidByte6,
    MSEGuidByte0,
    MSEGuidByte7,
    MSEGuidByte4,
    MSEGuidByte1,
    MSEGuidByte5,
    MSEGuidByte2,
    MSEPitch,
    MSETransportTime2,
    MSETransportGuidByte3,
    MSETransportPositionX,
    MSETransportSeat,
    MSETransportGuidByte5,
    MSETransportGuidByte1,
    MSETransportPositionZ,
    MSETransportGuidByte2,
    MSETransportGuidByte7,
    MSETransportOrientation,
    MSETransportGuidByte4,
    MSETransportTime,
    MSETransportVehicleId,
    MSETransportGuidByte0,
    MSETransportPositionY,
    MSETransportGuidByte6,
    MSEFallSinAngle,
    MSEFallHorizontalSpeed,
    MSEFallCosAngle,
    MSEFallTime,
    MSEFallVerticalSpeed,
    MSESplineElevation,
    MSETimestamp,
    MSEOrientation,
    MSEEnd,
};

static MovementStatusElements const MovementSetWalkMode[] =
{
    MSEPositionY,
    MSEPositionX,
    MSEPositionZ,
    MSEHasGuidByte6,
    MSEHasSpline,
    MSEHasTimestamp,
    MSEHasGuidByte0,
    MSEHasGuidByte1,
    MSEHasMovementFlags,
    MSEHasPitch,
    MSEHasGuidByte7,
    MSEHasSplineElevation,
    MSEHasGuidByte4,
    MSEHasOrientation,
    MSEHasTransportData,
    MSEHasGuidByte2,
    MSEHasGuidByte5,
    MSEHasGuidByte3,
    MSEZeroBit,
    MSEHasMovementFlags2,
    MSEHasFallData,
    MSEHasTransportGuidByte2,
    MSEHasTransportGuidByte0,
    MSEHasTransportGuidByte6,
    MSEHasTransportGuidByte1,
    MSEHasTransportGuidByte3,
    MSEHasTransportTime2,
    MSEHasTransportGuidByte5,
    MSEHasVehicleId,
    MSEHasTransportGuidByte4,
    MSEHasTransportGuidByte7,
    MSEHasFallDirection,
    MSEMovementFlags,
    MSEMovementFlags2,
    MSEGuidByte5,
    MSEGuidByte6,
    MSEGuidByte4,
    MSEGuidByte7,
    MSEGuidByte3,
    MSEGuidByte0,
    MSEGuidByte2,
    MSEGuidByte1,
    MSETransportGuidByte2,
    MSETransportGuidByte5,
    MSETransportSeat,
    MSETransportPositionZ,
    MSETransportGuidByte3,
    MSETransportGuidByte6,
    MSETransportGuidByte0,
    MSETransportTime,
    MSETransportGuidByte4,
    MSETransportTime2,
    MSETransportOrientation,
    MSETransportPositionX,
    MSETransportVehicleId,
    MSETransportGuidByte7,
    MSETransportPositionY,
    MSETransportGuidByte1,
    MSEFallCosAngle,
    MSEFallHorizontalSpeed,
    MSEFallSinAngle,
    MSEFallVerticalSpeed,
    MSEFallTime,
    MSESplineElevation,
    MSEPitch,
    MSETimestamp,
    MSEOrientation,
    MSEEnd,
};
MovementStatusElements const* WorldPackets::Movement::GetGroundMovementSequence(uint16 opcode)
{
    switch (opcode)
    {
        case MSG_MOVE_START_BACKWARD:     return MovementStartBackward;
        case MSG_MOVE_START_FORWARD:      return MovementStartForward;
        case MSG_MOVE_START_STRAFE_LEFT:  return MovementStartStrafeLeft;
        case MSG_MOVE_START_STRAFE_RIGHT: return MovementStartStrafeRight;
        case MSG_MOVE_START_TURN_LEFT:    return MovementStartTurnLeft;
        case MSG_MOVE_START_TURN_RIGHT:   return MovementStartTurnRight;
        case MSG_MOVE_STOP:               return MovementStop;
        case MSG_MOVE_STOP_STRAFE:        return MovementStopStrafe;
        case MSG_MOVE_STOP_TURN:          return MovementStopTurn;
        case MSG_MOVE_SET_RUN_MODE:       return MovementSetRunMode;
        case MSG_MOVE_SET_WALK_MODE:      return MovementSetWalkMode;
        default:                         return nullptr;
    }
}

void WorldPackets::Movement::ReadGroundMovement(WorldPacket& packet, MovementInfo& info, MovementStatusElements const* sequence)
{
    info = MovementInfo();
    bool hasTransportData = false;
    bool hasVehicleId = false;
    bool hasTransportTime2 = false;
    bool hasMovementFlags = false;
    bool hasMovementFlags2 = false;
    bool hasTimestamp = false;
    bool hasOrientation = false;
    bool hasPitch = false;
    bool hasSplineElevation = false;
    bool hasSpline = false;
    bool hasFallData = false;
    bool hasFallDirection = false;

    for (; *sequence != MSEEnd; ++sequence)
    {
        switch (*sequence)
        {
            case MSEHasGuidByte0: info.guid[0] = packet.ReadBit(); break;
            case MSEHasGuidByte1: info.guid[1] = packet.ReadBit(); break;
            case MSEHasGuidByte2: info.guid[2] = packet.ReadBit(); break;
            case MSEHasGuidByte3: info.guid[3] = packet.ReadBit(); break;
            case MSEHasGuidByte4: info.guid[4] = packet.ReadBit(); break;
            case MSEHasGuidByte5: info.guid[5] = packet.ReadBit(); break;
            case MSEHasGuidByte6: info.guid[6] = packet.ReadBit(); break;
            case MSEHasGuidByte7: info.guid[7] = packet.ReadBit(); break;
            case MSEGuidByte0: packet.ReadByteSeq(info.guid[0]); break;
            case MSEGuidByte1: packet.ReadByteSeq(info.guid[1]); break;
            case MSEGuidByte2: packet.ReadByteSeq(info.guid[2]); break;
            case MSEGuidByte3: packet.ReadByteSeq(info.guid[3]); break;
            case MSEGuidByte4: packet.ReadByteSeq(info.guid[4]); break;
            case MSEGuidByte5: packet.ReadByteSeq(info.guid[5]); break;
            case MSEGuidByte6: packet.ReadByteSeq(info.guid[6]); break;
            case MSEGuidByte7: packet.ReadByteSeq(info.guid[7]); break;
            case MSEHasTransportGuidByte0: if (hasTransportData) info.transport.guid[0] = packet.ReadBit(); break;
            case MSEHasTransportGuidByte1: if (hasTransportData) info.transport.guid[1] = packet.ReadBit(); break;
            case MSEHasTransportGuidByte2: if (hasTransportData) info.transport.guid[2] = packet.ReadBit(); break;
            case MSEHasTransportGuidByte3: if (hasTransportData) info.transport.guid[3] = packet.ReadBit(); break;
            case MSEHasTransportGuidByte4: if (hasTransportData) info.transport.guid[4] = packet.ReadBit(); break;
            case MSEHasTransportGuidByte5: if (hasTransportData) info.transport.guid[5] = packet.ReadBit(); break;
            case MSEHasTransportGuidByte6: if (hasTransportData) info.transport.guid[6] = packet.ReadBit(); break;
            case MSEHasTransportGuidByte7: if (hasTransportData) info.transport.guid[7] = packet.ReadBit(); break;
            case MSETransportGuidByte0: if (hasTransportData) packet.ReadByteSeq(info.transport.guid[0]); break;
            case MSETransportGuidByte1: if (hasTransportData) packet.ReadByteSeq(info.transport.guid[1]); break;
            case MSETransportGuidByte2: if (hasTransportData) packet.ReadByteSeq(info.transport.guid[2]); break;
            case MSETransportGuidByte3: if (hasTransportData) packet.ReadByteSeq(info.transport.guid[3]); break;
            case MSETransportGuidByte4: if (hasTransportData) packet.ReadByteSeq(info.transport.guid[4]); break;
            case MSETransportGuidByte5: if (hasTransportData) packet.ReadByteSeq(info.transport.guid[5]); break;
            case MSETransportGuidByte6: if (hasTransportData) packet.ReadByteSeq(info.transport.guid[6]); break;
            case MSETransportGuidByte7: if (hasTransportData) packet.ReadByteSeq(info.transport.guid[7]); break;
            case MSEHasTransportData: hasTransportData = packet.ReadBit(); break;
            case MSEHasVehicleId: if (hasTransportData) hasVehicleId = packet.ReadBit(); break;
            case MSEHasTransportTime2: if (hasTransportData) hasTransportTime2 = packet.ReadBit(); break;
            case MSEHasMovementFlags: hasMovementFlags = !packet.ReadBit(); break;
            case MSEHasMovementFlags2: hasMovementFlags2 = !packet.ReadBit(); break;
            case MSEHasTimestamp: hasTimestamp = !packet.ReadBit(); break;
            case MSEHasOrientation: hasOrientation = !packet.ReadBit(); break;
            case MSEHasPitch: hasPitch = !packet.ReadBit(); break;
            case MSEHasSplineElevation: hasSplineElevation = !packet.ReadBit(); break;
            case MSEHasSpline: hasSpline = packet.ReadBit(); break;
            case MSEHasFallData: hasFallData = packet.ReadBit(); break;
            case MSEHasFallDirection: if (hasFallData) hasFallDirection = packet.ReadBit(); break;
            case MSEZeroBit: packet.ReadBit(); break;
            case MSEMovementFlags:
                if (hasMovementFlags)
                {
                    uint32 flags = packet.ReadBits(30);
                    info.flags = (flags & 0x000001FF) | ((flags & 0x03FFFE00) << 1) | ((flags & 0x3C000000) << 2);
                }
                break;
            case MSEMovementFlags2:
                if (hasMovementFlags2)
                {
                    uint16 flags = packet.ReadBits(12);
                    info.flags2 = (flags & 0x03C3) | ((flags & 0x001C) << 1) | ((flags & 0x0020) >> 3) |
                        ((flags & 0xE000) >> 3) | ((flags & 0x1C00) << 3);
                }
                break;
            case MSEPositionX: packet >> info.pos.m_positionX; break;
            case MSEPositionY: packet >> info.pos.m_positionY; break;
            case MSEPositionZ: packet >> info.pos.m_positionZ; break;
            case MSEOrientation: if (hasOrientation) info.pos.SetOrientation(packet.read<float>()); break;
            case MSETransportPositionX: if (hasTransportData) packet >> info.transport.pos.m_positionX; break;
            case MSETransportPositionY: if (hasTransportData) packet >> info.transport.pos.m_positionY; break;
            case MSETransportPositionZ: if (hasTransportData) packet >> info.transport.pos.m_positionZ; break;
            case MSETransportOrientation: if (hasTransportData) info.transport.pos.SetOrientation(packet.read<float>()); break;
            case MSETransportSeat: if (hasTransportData) packet >> info.transport.seat; break;
            case MSETransportTime: if (hasTransportData) packet >> info.transport.time; break;
            case MSETransportTime2: if (hasTransportData && hasTransportTime2) packet >> info.transport.time2; break;
            case MSETransportVehicleId: if (hasTransportData && hasVehicleId) packet >> info.transport.vehicleId; break;
            case MSEPitch: if (hasPitch) info.pitch = G3D::wrap(packet.read<float>(), float(-M_PI), float(M_PI)); break;
            case MSEFallTime: if (hasFallData) packet >> info.fallTime; break;
            case MSEFallVerticalSpeed: if (hasFallData) packet >> info.jump.zspeed; break;
            case MSEFallCosAngle: if (hasFallData && hasFallDirection) packet >> info.jump.cosAngle; break;
            case MSEFallSinAngle: if (hasFallData && hasFallDirection) packet >> info.jump.sinAngle; break;
            case MSEFallHorizontalSpeed: if (hasFallData && hasFallDirection) packet >> info.jump.xyspeed; break;
            case MSESplineElevation: if (hasSplineElevation) packet >> info.splineElevation; break;
            case MSETimestamp: if (hasTimestamp) packet >> info.time; break;
            case MSEEnd: break;
        }
    }

    if (hasTransportData)
        info.AddMovementFlag(MOVEMENTFLAG_ONTRANSPORT);
    if (hasSpline)
        info.AddMovementFlag(MOVEMENTFLAG_SPLINE_ENABLED);
    if (hasTransportTime2)
        info.AddExtraMovementFlag(MOVEMENTFLAG2_INTERPOLATED_MOVEMENT);

    if (packet.rpos() != packet.size())
        throw ByteBufferInvalidValueException("ground movement payload", "trailing bytes");
}

void WorldPackets::Movement::WriteGroundMovement(WorldPacket& packet, MovementInfo const& info, MovementStatusElements const* sequence)
{
    uint32 flags = MovementFlagsToClient(info.flags);
    uint16 flags2 = ExtraMovementFlagsToClient(info.flags2) & 0x0FFF;
    bool hasFallDirection = info.HasMovementFlag(MOVEMENTFLAG_FALLING);
    bool hasFallData = hasFallDirection || info.fallTime != 0;
    bool hasOrientation = !G3D::fuzzyEq(info.pos.GetOrientation(), 0.0f);
    bool hasTransport = !info.transport.guid.IsEmpty();
    bool hasVehicleId = hasTransport && info.transport.vehicleId != 0;
    bool hasTransportTime2 = hasTransport && info.transport.time2 != 0;
    bool hasPitch = info.HasMovementFlag(MOVEMENTFLAG_SWIMMING | MOVEMENTFLAG_FLYING) ||
        info.HasExtraMovementFlag(MOVEMENTFLAG2_ALWAYS_ALLOW_PITCHING);
    bool hasSplineElevation = info.HasMovementFlag(MOVEMENTFLAG_SPLINE_ELEVATION);
    bool hasSpline = info.HasMovementFlag(MOVEMENTFLAG_SPLINE_ENABLED);
    ObjectGuid const& guid = info.guid;
    ObjectGuid const& transport = info.transport.guid;

    for (; *sequence != MSEEnd; ++sequence)
    {
        switch (*sequence)
        {
            case MSEHasGuidByte0: packet.WriteBit(guid[0]); break;
            case MSEHasGuidByte1: packet.WriteBit(guid[1]); break;
            case MSEHasGuidByte2: packet.WriteBit(guid[2]); break;
            case MSEHasGuidByte3: packet.WriteBit(guid[3]); break;
            case MSEHasGuidByte4: packet.WriteBit(guid[4]); break;
            case MSEHasGuidByte5: packet.WriteBit(guid[5]); break;
            case MSEHasGuidByte6: packet.WriteBit(guid[6]); break;
            case MSEHasGuidByte7: packet.WriteBit(guid[7]); break;
            case MSEHasTransportGuidByte0: if (hasTransport) packet.WriteBit(transport[0]); break;
            case MSEHasTransportGuidByte1: if (hasTransport) packet.WriteBit(transport[1]); break;
            case MSEHasTransportGuidByte2: if (hasTransport) packet.WriteBit(transport[2]); break;
            case MSEHasTransportGuidByte3: if (hasTransport) packet.WriteBit(transport[3]); break;
            case MSEHasTransportGuidByte4: if (hasTransport) packet.WriteBit(transport[4]); break;
            case MSEHasTransportGuidByte5: if (hasTransport) packet.WriteBit(transport[5]); break;
            case MSEHasTransportGuidByte6: if (hasTransport) packet.WriteBit(transport[6]); break;
            case MSEHasTransportGuidByte7: if (hasTransport) packet.WriteBit(transport[7]); break;
            case MSEHasTransportData: packet.WriteBit(hasTransport); break;
            case MSEHasVehicleId: if (hasTransport) packet.WriteBit(hasVehicleId); break;
            case MSEHasTransportTime2: if (hasTransport) packet.WriteBit(hasTransportTime2); break;
            case MSEHasMovementFlags: packet.WriteBit(!flags); break;
            case MSEHasMovementFlags2: packet.WriteBit(!flags2); break;
            case MSEHasTimestamp: packet.WriteBit(false); break; // Timestamp is always present in server movement updates.
            case MSEHasOrientation: packet.WriteBit(!hasOrientation); break;
            case MSEHasPitch: packet.WriteBit(!hasPitch); break;
            case MSEHasSplineElevation: packet.WriteBit(!hasSplineElevation); break;
            case MSEHasSpline: packet.WriteBit(hasSpline); break;
            case MSEHasFallData: packet.WriteBit(hasFallData); break;
            case MSEHasFallDirection: if (hasFallData) packet.WriteBit(hasFallDirection); break;
            case MSEZeroBit: packet.WriteBit(false); break;
            case MSEMovementFlags: if (flags) packet.WriteBits(flags, 30); break;
            case MSEMovementFlags2: if (flags2) packet.WriteBits(flags2, 12); break;
            case MSEPositionX: packet << info.pos.GetPositionX(); break;
            case MSEPositionY: packet << info.pos.GetPositionY(); break;
            case MSEPositionZ: packet << info.pos.GetPositionZ(); break;
            case MSEOrientation: packet.FlushBits(); if (hasOrientation) packet << info.pos.GetOrientation(); break;
            case MSEGuidByte0: packet.FlushBits(); packet.WriteByteSeq(guid[0]); break;
            case MSEGuidByte1: packet.FlushBits(); packet.WriteByteSeq(guid[1]); break;
            case MSEGuidByte2: packet.FlushBits(); packet.WriteByteSeq(guid[2]); break;
            case MSEGuidByte3: packet.FlushBits(); packet.WriteByteSeq(guid[3]); break;
            case MSEGuidByte4: packet.FlushBits(); packet.WriteByteSeq(guid[4]); break;
            case MSEGuidByte5: packet.FlushBits(); packet.WriteByteSeq(guid[5]); break;
            case MSEGuidByte6: packet.FlushBits(); packet.WriteByteSeq(guid[6]); break;
            case MSEGuidByte7: packet.FlushBits(); packet.WriteByteSeq(guid[7]); break;
            case MSETransportGuidByte0: packet.FlushBits(); if (hasTransport) packet.WriteByteSeq(transport[0]); break;
            case MSETransportGuidByte1: packet.FlushBits(); if (hasTransport) packet.WriteByteSeq(transport[1]); break;
            case MSETransportGuidByte2: packet.FlushBits(); if (hasTransport) packet.WriteByteSeq(transport[2]); break;
            case MSETransportGuidByte3: packet.FlushBits(); if (hasTransport) packet.WriteByteSeq(transport[3]); break;
            case MSETransportGuidByte4: packet.FlushBits(); if (hasTransport) packet.WriteByteSeq(transport[4]); break;
            case MSETransportGuidByte5: packet.FlushBits(); if (hasTransport) packet.WriteByteSeq(transport[5]); break;
            case MSETransportGuidByte6: packet.FlushBits(); if (hasTransport) packet.WriteByteSeq(transport[6]); break;
            case MSETransportGuidByte7: packet.FlushBits(); if (hasTransport) packet.WriteByteSeq(transport[7]); break;
            case MSETransportPositionX: packet.FlushBits(); if (hasTransport) packet << info.transport.pos.GetPositionX(); break;
            case MSETransportPositionY: packet.FlushBits(); if (hasTransport) packet << info.transport.pos.GetPositionY(); break;
            case MSETransportPositionZ: packet.FlushBits(); if (hasTransport) packet << info.transport.pos.GetPositionZ(); break;
            case MSETransportOrientation: packet.FlushBits(); if (hasTransport) packet << info.transport.pos.GetOrientation(); break;
            case MSETransportSeat: packet.FlushBits(); if (hasTransport) packet << info.transport.seat; break;
            case MSETransportTime: packet.FlushBits(); if (hasTransport) packet << info.transport.time; break;
            case MSETransportTime2: packet.FlushBits(); if (hasTransport && hasTransportTime2) packet << info.transport.time2; break;
            case MSETransportVehicleId: packet.FlushBits(); if (hasTransport && hasVehicleId) packet << info.transport.vehicleId; break;
            case MSEPitch: packet.FlushBits(); if (hasPitch) packet << info.pitch; break;
            case MSEFallTime: packet.FlushBits(); if (hasFallData) packet << info.fallTime; break;
            case MSEFallVerticalSpeed: packet.FlushBits(); if (hasFallData) packet << info.jump.zspeed; break;
            case MSEFallCosAngle: packet.FlushBits(); if (hasFallData && hasFallDirection) packet << info.jump.cosAngle; break;
            case MSEFallSinAngle: packet.FlushBits(); if (hasFallData && hasFallDirection) packet << info.jump.sinAngle; break;
            case MSEFallHorizontalSpeed: packet.FlushBits(); if (hasFallData && hasFallDirection) packet << info.jump.xyspeed; break;
            case MSESplineElevation: packet.FlushBits(); if (hasSplineElevation) packet << info.splineElevation; break;
            case MSETimestamp: packet.FlushBits(); packet << info.time; break;
            case MSEEnd: break;
        }
    }
}
